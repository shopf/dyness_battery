"""Dyness Battery Integration for Home Assistant."""
import asyncio
import hashlib
import hmac
import base64
import json
import logging
import time
from email.utils import formatdate
from datetime import timedelta

import aiohttp
import async_timeout

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import Platform

_LOGGER = logging.getLogger(__name__)

DOMAIN = "dyness_battery"
PLATFORMS = [Platform.SENSOR]

_MIN_CALL_INTERVAL = 1.5
_RATE_LIMIT_BACKOFF = 10
_MAX_RETRIES = 3
_BMS_SUFFIXES = ("-BMS", "-BDU")

def _get_gmt_time() -> str:
    return formatdate(timeval=None, localtime=False, usegmt=True)

def _get_md5(body: str) -> str:
    md5 = hashlib.md5(body.encode("utf-8")).digest()
    return base64.b64encode(md5).decode("utf-8")

def _get_signature(api_secret: str, content_md5: str, date: str, path: str) -> str:
    string_to_sign = f"POST\n{content_md5}\napplication/json\n{date}\n{path}"
    sig = hmac.new(api_secret.encode("utf-8"), string_to_sign.encode("utf-8"), "sha1").digest()
    return base64.b64encode(sig).decode("utf-8")

def _build_headers(api_id: str, api_secret: str, body: str, sign_path: str) -> dict:
    date = _get_gmt_time()
    content_md5 = _get_md5(body)
    signature = _get_signature(api_secret, content_md5, date, sign_path)
    return {
        "Content-Type": "application/json;charset=UTF-8",
        "Content-MD5": content_md5,
        "Date": date,
        "Authorization": f"API {api_id}:{signature}",
    }

def _to_float(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None

def _is_success(result: dict) -> bool:
    code = result.get("code")
    return str(code) in ("0", "200") or code == 0

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = DynessDataCoordinator(
        hass, entry.data["api_id"], entry.data["api_secret"], entry.data["api_base"],
        device_sn=entry.data.get("device_sn"), dongle_sn=entry.data.get("dongle_sn"),
    )
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

class DynessDataCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, api_id, api_secret, api_base, device_sn=None, dongle_sn=None):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(minutes=5))
        self.api_id, self.api_secret, self.api_base = api_id, api_secret, api_base
        self.device_sn, self.dongle_sn = device_sn, dongle_sn
        self.station_info, self.device_info, self.storage_info = {}, {}, {}
        self.realtime_data, self.module_data = {}, {}
        self._bound_sns = set()
        self._module_sns = []
        self._last_call_time = 0.0

    async def _call(self, session: aiohttp.ClientSession, path: str, body_dict: dict) -> dict:
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            await asyncio.sleep(_MIN_CALL_INTERVAL - elapsed)
        url, body = f"{self.api_base}/openapi/ems-device{path}", json.dumps(body_dict, separators=(',', ':'))
        for attempt in range(_MAX_RETRIES + 1):
            self._last_call_time = time.monotonic()
            headers = _build_headers(self.api_id, self.api_secret, body, path)
            try:
                async with session.post(url, headers=headers, data=body) as response:
                    if response.status == 429:
                        if attempt < _MAX_RETRIES:
                            await asyncio.sleep(_RATE_LIMIT_BACKOFF * (2 ** attempt))
                            continue
                        return {}
                    return json.loads(await response.text())
            except Exception:
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
        return {}

    async def _async_update_data(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with async_timeout.timeout(90):
                    if not self.device_sn:
                        res = await self._call(session, "/v1/device/storage/list", {})
                        if _is_success(res):
                            devs = (res.get("data", {}) or {}).get("list", [])
                            bms = next((d for d in devs if str(d.get("deviceSn", "")).endswith(_BMS_SUFFIXES)), None) or (devs[0] if devs else None)
                            self.device_sn = bms.get("deviceSn", "") if bms else None

                    if self.device_sn and self.device_sn not in self._bound_sns:
                        await self._call(session, "/v1/device/bindSn", {"deviceSn": self.device_sn})
                        self._bound_sns.add(self.device_sn)

                    rt_res = await self._call(session, "/v1/device/realTime/data", {"deviceSn": self.device_sn})
                    if _is_success(rt_res):
                        raw = rt_res.get("data", []) or []
                        self.realtime_data = {item["pointId"]: item["pointValue"] for item in raw if isinstance(item, dict)}
                        
                        sub_raw = self.realtime_data.get("SUB", "")
                        if sub_raw:
                            candidates = [s.strip() for s in str(sub_raw).split(",") if s.strip()]
                            self._module_sns = [s for s in candidates if not s.endswith(_BMS_SUFFIXES)]
                            for sn in self._module_sns:
                                if sn not in self._bound_sns:
                                    await self._call(session, "/v1/device/bindSn", {"deviceSn": sn})
                                    self._bound_sns.add(sn)

                    new_module_data = {}
                    for sn in self._module_sns:
                        m_res = await self._call(session, "/v1/device/realTime/data", {"deviceSn": sn})
                        if _is_success(m_res):
                            mid = sn.split("-")[-1] if "-" in sn else sn[-8:]
                            new_module_data[mid] = _parse_module_points(sn, mid, {item["pointId"]: item["pointValue"] for item in m_res.get("data", [])})
                    self.module_data = new_module_data

                    if not self.station_info:
                        res = await self._call(session, "/v1/station/info", {"deviceSn": self.device_sn})
                        self.station_info = res.get("data", {}) or {}
                    
                    res = await self._call(session, "/v1/device/getLastPowerDataBySn", {"pageNo": 1, "pageSize": 1, "deviceSn": self.device_sn})
                    data = res.get("data", [{}])[-1] if isinstance(res.get("data"), list) else {}
                    
                    data["batteryCapacity"] = _to_float(self.station_info.get("batteryCapacity"))
                    rt = self.realtime_data
                    if "1400" in rt:
                        mapping = {
                            "soh": "1500", "tempMax": "3000", "tempMin": "3300", 
                            "cellVoltageMax": "2400", "cellVoltageMin": "2700", 
                            "cycleCount": "1800", "energyChargeTotal": "1900",
                            "chargeLimit": "2000", "dischargeLimit": "2100", 
                            "fanStatus": "3800", "heatingStatus": "3900", 
                            "maxCellBox": "2500", "minCellBox": "2800"
                        }
                        for k, v in mapping.items():
                            data[k] = rt.get(v)
                    
                    vmax, vmin = _to_float(data.get("cellVoltageMax")), _to_float(data.get("cellVoltageMin"))
                    if vmax and vmin: data["cellVoltageDiffMv"] = round((vmax - vmin) * 1000, 1)
                    
                    data["module_data"] = self.module_data
                    return data
            except Exception as e: raise UpdateFailed(f"Error: {e}")

def _parse_module_points(sn, mid, pts):
    """Correctly parses T14 modules: 30 cells, 2 temps, NO module-level SOC from cloud."""
    def g(key): return pts.get(key) if pts.get(key) not in (None, "") else None
    d = {"sn": sn, "module_id": mid}
    
    # Correct Cell Mapping (11200 - 14100)
    cells = []
    for i in range(1, 31):
        pid = str(11100 + i * 100)
        val = _to_float(pts.get(pid))
        if val is not None:
            d[f"cell_{i:02d}"] = val
            cells.append(val)
    
    if cells:
        d["cell_voltage_max"], d["cell_voltage_min"] = max(cells), min(cells)
        d["cell_voltage_spread_mv"] = round((max(cells) - min(cells)) * 1000, 1)
    
    # Temperature Sensors
    d["cell_temp_1"], d["cell_temp_2"] = _to_float(g("14300")), _to_float(g("14400"))
    return d