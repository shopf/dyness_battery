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

def _build_headers(api_id, api_secret, body, path):
    date = formatdate(timeval=None, localtime=False, usegmt=True)
    content_md5 = base64.b64encode(hashlib.md5(body.encode("utf-8")).digest()).decode("utf-8")
    sig_str = f"POST\n{content_md5}\napplication/json\n{date}\n{path}"
    signature = base64.b64encode(hmac.new(api_secret.encode("utf-8"), sig_str.encode("utf-8"), "sha1").digest()).decode("utf-8")
    return {"Content-Type": "application/json;charset=UTF-8", "Content-MD5": content_md5, "Date": date, "Authorization": f"API {api_id}:{signature}"}

def _to_float(v):
    try: return float(v) if v not in (None, "") else None
    except: return None

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    coordinator = DynessDataCoordinator(hass, entry.data["api_id"], entry.data["api_secret"], entry.data["api_base"], entry.data.get("device_sn"))
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

class DynessDataCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, api_id, api_secret, api_base, device_sn):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(minutes=5))
        self.api_id, self.api_secret, self.api_base, self.device_sn = api_id, api_secret, api_base, device_sn
        self.realtime_data, self.module_data, self._bound_sns = {}, {}, set()

    async def _call(self, session, path, body):
        url, body_str = f"{self.api_base}/openapi/ems-device{path}", json.dumps(body, separators=(',', ':'))
        headers = _build_headers(self.api_id, self.api_secret, body_str, path)
        async with session.post(url, headers=headers, data=body_str) as res:
            return json.loads(await res.text())

    async def _async_update_data(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with async_timeout.timeout(30):
                    # 1. Ensure BDU is bound
                    if self.device_sn not in self._bound_sns:
                        await self._call(session, "/v1/device/bindSn", {"deviceSn": self.device_sn})
                        self._bound_sns.add(self.device_sn)

                    # 2. Fetch Real-Time Point IDs
                    rt_res = await self._call(session, "/v1/device/realTime/data", {"deviceSn": self.device_sn})
                    rt = {str(i["pointId"]): i["pointValue"] for i in rt_res.get("data", [])}
                    self.realtime_data = rt

                    # 3. Map Data (Point IDs for stability)
                    data = {
                        "soc": _to_float(rt.get("1400")),           # SOC
                        "realTimePower": _to_float(rt.get("1300")),  # Power
                        "realTimeCurrent": _to_float(rt.get("1200")),# Current
                        "packVoltage": _to_float(rt.get("1100")),    # Voltage
                        "soh": _to_float(rt.get("1500")),            # Health
                        "cycleCount": _to_float(rt.get("1800")),     # Cycles
                        "workStatus": rt.get("1000", "Unknown"),     # Mode
                        "master_alarm": str(rt.get("9999999")) == "1", # Alarm
                        "insulation_pos": _to_float(rt.get("2200")), #
                        "insulation_neg": _to_float(rt.get("2300")), #
                        "balancing": str(rt.get("4000")) == "1",
                    }

                    # 4. Handle Sub-Modules (01-04)
                    sub_sns = [s.strip() for s in str(rt.get("SUB", "")).split(",") if s.strip() and "-BDU" not in s]
                    for sn in sub_sns:
                        if sn not in self._bound_sns:
                            await self._call(session, "/v1/device/bindSn", {"deviceSn": sn})
                            self._bound_sns.add(sn)
                        m_res = await self._call(session, "/v1/device/realTime/data", {"deviceSn": sn})
                        m_rt = {str(i["pointId"]): i["pointValue"] for i in m_res.get("data", [])}
                        mid = sn.split("-")[-1] if "-" in sn else sn[-2:]
                        self.module_data[mid] = _parse_module(sn, mid, m_rt)

                    data["module_data"] = self.module_data
                    return data
            except Exception as e: raise UpdateFailed(f"API Error: {e}")

def _parse_module(sn, mid, pts):
    d = {"sn": sn, "module_id": mid}
    # Map 30 cells per pack
    for i in range(1, 31):
        d[f"cell_{i:02d}"] = _to_float(pts.get(str(11100 + i * 100)))
    d["temp_1"] = _to_float(pts.get("14300"))
    return d