"""Dyness Battery Integration für Home Assistant."""
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
SCAN_INTERVAL = timedelta(minutes=5)

# ── API rate-limit settings ───────────────────────────────────────────────────
_MIN_CALL_INTERVAL = 1.5   # minimum seconds between consecutive API calls
_RATE_LIMIT_BACKOFF = 10   # initial wait (s) on HTTP 429; doubles each retry
_MAX_RETRIES = 3            # max retry attempts on 429 or transient errors


def _get_gmt_time() -> str:
    return formatdate(timeval=None, localtime=False, usegmt=True)


def _get_md5(body: str) -> str:
    md5 = hashlib.md5(body.encode("utf-8")).digest()
    return base64.b64encode(md5).decode("utf-8")


def _get_signature(api_secret: str, content_md5: str, date: str, path: str) -> str:
    string_to_sign = (
        "POST" + "\n" + content_md5 + "\n" +
        "application/json" + "\n" + date + "\n" + path
    )
    sig = hmac.new(
        api_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        "sha1"
    ).digest()
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
    """Safely convert to float, returning None on failure."""
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(v):
    """Safely convert to int, returning None on failure."""
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _parse_module_data(sn: str, pts: dict) -> dict:
    """
    Parse raw module-level point data into a clean dict.
    Called for each DYNESS sub-module SN after fetching realTime/data.

    Point ID reference (all 131 points confirmed from live DL5.0C data):
      Identity  : 10000=serial, 10100=firmware, 10150=parent_bms, 10200=cell_count
      Cells     : 10300+(N-1)*100  for N=1..16
      Temps     : 12400=bms_board, 12500=ntc1, 12600=ntc2
      Electrical: 13400=current_a, 13500=voltage_v, 13600=ir_mohm, 13700=module_count
      Health    : 13900=cycle_count, 14000=soh_pct, 14100=rated_ah
      Alarms    : 14300=alarm1_bitmask, 15200=alarm2_bitmask
      Protection: 16300=protection1_bitmask
      Thresholds: 18100=cell_ovv, 18200=cell_uvv, 18300=cell_uvp,
                  18400=chg_temp_hi, 18500=chg_temp_lo, 18600=max_chg_a,
                  18700=mod_ovv, 18800=mod_uvv, 18900=mod_uvp,
                  19000=dch_temp_hi, 19100=dch_temp_lo, 19200=max_dch_a
    """
    mid = sn.split("-")[-1]   # DYNESS01 etc.

    d: dict = {
        "sn":         sn,
        "module_id":  mid,
        # Identity
        "serial_number":    pts.get("10000"),
        "firmware_version": pts.get("10100"),
        "parent_bms_sn":    pts.get("10150"),
        # Temperatures
        "bms_board_temp":   _to_float(pts.get("12400")),
        "cell_temp_1":      _to_float(pts.get("12500")),
        "cell_temp_2":      _to_float(pts.get("12600")),
        # Electrical
        "module_current":           _to_float(pts.get("13400")),
        "module_voltage":           _to_float(pts.get("13500")),
        "internal_resistance_mohm": _to_float(pts.get("13600")),
        # Health
        "cycle_count":      _to_float(pts.get("13900")),
        "soh":              _to_float(pts.get("14000")),
        "rated_capacity_ah": _to_float(pts.get("14100")),
        # Alarm / protection bitmasks
        "alarm_status_1":      _to_int(pts.get("14300")),
        "alarm_status_2":      _to_int(pts.get("15200")),
        "protection_status_1": _to_int(pts.get("16300")),
        # Protection thresholds (factory defaults, same for all modules)
        "cell_overvoltage_limit_v":     _to_float(pts.get("18100")),
        "cell_undervoltage_limit_v":    _to_float(pts.get("18200")),
        "cell_undervoltage_protect_v":  _to_float(pts.get("18300")),
        "charge_temp_upper_c":          _to_float(pts.get("18400")),
        "charge_temp_lower_c":          _to_float(pts.get("18500")),
        "max_charge_current_a":         _to_float(pts.get("18600")),
        "module_overvoltage_v":         _to_float(pts.get("18700")),
        "module_undervoltage_v":        _to_float(pts.get("18800")),
        "discharge_temp_upper_c":       _to_float(pts.get("19000")),
        "discharge_temp_lower_c":       _to_float(pts.get("19100")),
        "max_discharge_current_a":      _to_float(pts.get("19200")),
    }

    # ── Derive per-module architecture from reported data ────────────────────
    cells_per_module = _to_int(pts.get("10200")) or 16
    # LFP nominal cell voltage (3.2 V) is a fixed electrochemical constant
    nominal_pack_v = cells_per_module * 3.2

    # ── Cell voltages (10300–(10200 + cells_per_module*100)) ─────────────────
    cells = []
    for i in range(1, cells_per_module + 1):
        pid = str(10200 + i * 100)
        v = _to_float(pts.get(pid))
        d[f"cell_{i}_v"] = v
        if v is not None:
            cells.append(v)

    # ── Cell statistics ──────────────────────────────────────────────────────
    if cells:
        cmax   = max(cells)
        cmin   = min(cells)
        spread = round(cmax - cmin, 4)
        d["cell_voltage_max"]       = cmax
        d["cell_voltage_min"]       = cmin
        d["cell_voltage_spread_mv"] = round(spread * 1000, 1)

    # ── Capacity derived ─────────────────────────────────────────────────────
    rated_ah  = d.get("rated_capacity_ah") or 100.0
    soh_pct   = d.get("soh") or 100.0
    rated_kwh = round(rated_ah * nominal_pack_v / 1000, 3)
    d["rated_capacity_kwh"] = rated_kwh
    d["usable_kwh"]         = round(rated_kwh * (soh_pct / 100), 3)

    # ── Internal resistance per cell ─────────────────────────────────────────
    if d.get("internal_resistance_mohm") is not None:
        d["internal_resistance_per_cell_mohm"] = round(
            d["internal_resistance_mohm"] / cells_per_module, 4
        )

    # ── Alarm summary ────────────────────────────────────────────────────────
    d["has_any_alarm"] = (
        (_to_int(pts.get("14300")) or 0) != 0
        or (_to_int(pts.get("15200")) or 0) != 0
    )
    d["has_any_protection"] = (_to_int(pts.get("16300")) or 0) != 0

    return d


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = DynessDataCoordinator(
        hass,
        entry.data["api_id"],
        entry.data["api_secret"],
        entry.data["api_base"],
    )
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class DynessDataCoordinator(DataUpdateCoordinator):

    def __init__(self, hass, api_id, api_secret, api_base):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api_id     = api_id
        self.api_secret = api_secret
        self.api_base   = api_base
        self.station_info  = {}
        self.device_info   = {}
        self.storage_info  = {}
        self.realtime_data = {}

        # Discovered at runtime
        self.device_sn: str | None = None
        self._module_sns: list[str] = []
        self._modules_bound: bool = False

        # API telemetry
        self._last_call_time: float = 0.0
        self._api_call_count: int = 0
        self._api_status: str = "ok"

    async def _call(self, session: aiohttp.ClientSession, path: str, body_dict: dict) -> dict:
        """
        Rate-paced API POST with exponential-backoff retry on HTTP 429.

        - Enforces _MIN_CALL_INTERVAL between calls to avoid rate-limiting.
        - On HTTP 429: waits _RATE_LIMIT_BACKOFF × 2^attempt seconds and retries.
        - On transient connection errors: short back-off and retry.
        - Updates self._api_status and self._api_call_count.
        """
        # Pace: sleep if we called the API too recently
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            await asyncio.sleep(_MIN_CALL_INTERVAL - elapsed)

        url = f"{self.api_base}/openapi/ems-device{path}"
        body = json.dumps(body_dict, separators=(',', ':'))

        for attempt in range(_MAX_RETRIES + 1):
            self._last_call_time = time.monotonic()
            self._api_call_count += 1
            headers = _build_headers(self.api_id, self.api_secret, body, path)

            try:
                async with session.post(url, headers=headers, data=body) as response:
                    if response.status == 429:
                        wait = _RATE_LIMIT_BACKOFF * (2 ** attempt)
                        _LOGGER.warning(
                            "Dyness: rate limited (429) on %s — retry %d/%d in %ds",
                            path, attempt + 1, _MAX_RETRIES, wait,
                        )
                        self._api_status = "rate_limited"
                        if attempt < _MAX_RETRIES:
                            await asyncio.sleep(wait)
                            continue
                        return {}

                    raw_text = await response.text()
                    _LOGGER.debug("Dyness %s: %s", path, raw_text)
                    result = json.loads(raw_text)
                    if self._api_status == "rate_limited":
                        _LOGGER.info("Dyness: rate limit cleared, API responding normally")
                    self._api_status = "ok"
                    return result

            except aiohttp.ClientError as e:
                self._api_status = "offline"
                _LOGGER.warning("Dyness %s connection error (attempt %d/%d): %s",
                                path, attempt + 1, _MAX_RETRIES, e)
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise

        return {}

    async def _async_update_data(self):
        async with aiohttp.ClientSession() as session:
            try:
                # Increased timeout to accommodate module fetching (4 modules × ~4s each)
                async with async_timeout.timeout(90):

                    # ── Auto-discover BMS SN (once) ───────────────────────────
                    if not self.device_sn:
                        try:
                            sl_result = await self._call(
                                session, "/v1/device/storage/list", {}
                            )
                            if str(sl_result.get("code", "")) in ("0", "200"):
                                device_list = (sl_result.get("data", {}) or {}).get("list", [])
                                bms = next(
                                    (d for d in device_list
                                     if str(d.get("deviceSn", "")).endswith("-BMS")),
                                    device_list[0] if device_list else None,
                                )
                                if bms:
                                    sn = bms.get("deviceSn", "")
                                    self.device_sn = sn if sn.endswith("-BMS") else sn + "-BMS"
                                    _LOGGER.info("Dyness: discovered BMS SN %s", self.device_sn)
                                else:
                                    raise UpdateFailed(
                                        "Dyness: no devices found on this API account. "
                                        "Check API credentials."
                                    )
                        except UpdateFailed:
                            raise
                        except Exception as e:
                            raise UpdateFailed(f"Dyness: BMS discovery failed: {e}") from e

                    # ── Static data (loaded once at startup) ─────────────────
                    if not self.station_info:
                        try:
                            result = await self._call(
                                session, "/v1/station/info", {"deviceSn": self.device_sn}
                            )
                            if str(result.get("code", "")) in ("0", "200"):
                                self.station_info = result.get("data", {}) or {}
                        except Exception as e:
                            _LOGGER.warning("Dyness station/info unreachable: %s", e)

                    if not self.device_info:
                        try:
                            result = await self._call(
                                session, "/v1/device/household/storage/detail",
                                {"deviceSn": self.device_sn}
                            )
                            if str(result.get("code", "")) in ("0", "200"):
                                self.device_info = result.get("data", {}) or {}
                        except Exception as e:
                            _LOGGER.warning("Dyness household/storage/detail unreachable: %s", e)

                    # ── Work status (every update) ────────────────────────────
                    try:
                        result = await self._call(session, "/v1/device/storage/list", {})
                        if str(result.get("code", "")) in ("0", "200"):
                            device_list = (result.get("data", {}) or {}).get("list", [])
                            match = next(
                                (d for d in device_list if d.get("deviceSn") == self.device_sn),
                                device_list[0] if device_list else {}
                            )
                            self.storage_info = match
                    except Exception as e:
                        _LOGGER.warning("Dyness storage/list unreachable: %s", e)

                    # ── BMS realTime/data (every update) ─────────────────────
                    try:
                        rt_result = await self._call(
                            session, "/v1/device/realTime/data", {"deviceSn": self.device_sn}
                        )
                        if str(rt_result.get("code", "")) in ("0", "200"):
                            raw = rt_result.get("data", []) or []
                            self.realtime_data = {
                                item["pointId"]: item["pointValue"]
                                for item in raw
                                if isinstance(item, dict) and "pointId" in item
                            }
                            _LOGGER.debug(
                                "Dyness realTime/data: %d points loaded",
                                len(self.realtime_data)
                            )
                    except Exception as e:
                        _LOGGER.warning("Dyness realTime/data unreachable: %s", e)

                    # ── Auto-discover module SNs from BMS SUB point ───────────
                    # Point "SUB" = comma-separated sub-device SNs (e.g. DYNESS01,DYNESS02,...)
                    # Point "400" = module count (fallback when SUB is absent on fresh install)
                    if not self._module_sns and self.realtime_data:
                        sub_raw = self.realtime_data.get("SUB", "")
                        if sub_raw:
                            self._module_sns = [
                                s.strip() for s in str(sub_raw).split(",") if s.strip()
                            ]
                            _LOGGER.info(
                                "Dyness: discovered %d module(s) via SUB: %s",
                                len(self._module_sns), self._module_sns
                            )
                        else:
                            # Fallback: construct SNs from module count (point 400) + BMS prefix
                            try:
                                mod_count = int(self.realtime_data.get("400") or 0)
                                if mod_count > 0 and self.device_sn:
                                    prefix = (
                                        self.device_sn[:-4]
                                        if self.device_sn.endswith("-BMS")
                                        else self.device_sn
                                    )
                                    self._module_sns = [
                                        f"{prefix}-DYNESS{i:02d}"
                                        for i in range(1, mod_count + 1)
                                    ]
                                    _LOGGER.info(
                                        "Dyness: constructed %d module SN(s) from point 400: %s",
                                        len(self._module_sns), self._module_sns
                                    )
                            except (TypeError, ValueError):
                                pass

                    # ── Bind module SNs (once per session) ───────────────────
                    if self._module_sns and not self._modules_bound:
                        for sn in self._module_sns:
                            try:
                                bind_r = await self._call(
                                    session, "/v1/device/bindSn", {"deviceSn": sn}
                                )
                                _LOGGER.debug(
                                    "Dyness bindSn %s: code=%s", sn, bind_r.get("code")
                                )
                            except Exception as e:
                                _LOGGER.warning("Dyness bindSn %s failed: %s", sn, e)
                        self._modules_bound = True

                    # ── Fetch per-module realTime/data ────────────────────────
                    # Pacing is handled by self._call; no extra sleep needed.
                    module_data: dict = {}
                    for sn in self._module_sns:
                        try:
                            m_result = await self._call(
                                session, "/v1/device/realTime/data", {"deviceSn": sn}
                            )
                            if str(m_result.get("code", "")) in ("0", "200"):
                                m_raw = m_result.get("data", []) or []
                                m_pts = {
                                    str(p["pointId"]): p["pointValue"]
                                    for p in m_raw
                                    if isinstance(p, dict) and "pointId" in p
                                }
                                mid = sn.split("-")[-1]   # DYNESS01 etc.
                                module_data[mid] = _parse_module_data(sn, m_pts)
                                _LOGGER.debug(
                                    "Dyness module %s: %d points loaded", mid, len(m_pts)
                                )
                            else:
                                _LOGGER.warning(
                                    "Dyness module %s: code=%s", sn, m_result.get("code")
                                )
                        except Exception as e:
                            _LOGGER.warning("Dyness module %s data failed: %s", sn, e)

                    # ── Power data (every update) — required ──────────────────
                    result = await self._call(
                        session, "/v1/device/getLastPowerDataBySn",
                        {"pageNo": 1, "pageSize": 1, "deviceSn": self.device_sn}
                    )

                    code = str(result.get("code", ""))
                    if code not in ("0", "200"):
                        _LOGGER.error(
                            "Dyness getLastPowerDataBySn failed – code %s: %s (deviceSn=%s)",
                            code, result.get("info"), self.device_sn
                        )
                        raise UpdateFailed(
                            f"Dyness API error (code {code}): "
                            f"{result.get('info', 'Unknown')} – deviceSn={self.device_sn}"
                        )

                    data = result.get("data", {})

                    # API returns list — take newest valid entry
                    if isinstance(data, list):
                        valid = [d for d in data if d.get("soc") is not None]
                        if not valid:
                            _LOGGER.warning(
                                "Dyness: all %d power data points have soc=null "
                                "(device offline or no current data, deviceSn=%s)",
                                len(data), self.device_sn
                            )
                        data = valid[-1] if valid else (data[-1] if data else {})

                    # ── Merge static fields ───────────────────────────────────
                    data["batteryCapacity"]           = self.station_info.get("batteryCapacity")
                    data["deviceCommunicationStatus"] = self.device_info.get("deviceCommunicationStatus")
                    data["firmwareVersion"]            = self.device_info.get("firmwareVersion")
                    data["workStatus"]                 = self.storage_info.get("workStatus")

                    # ── Device-type detection and realTime/data point mapping ─
                    # Junior Box / DL5.0C → point "800" = SOC
                    # Tower               → point "1400" = SOC
                    rt = self.realtime_data
                    if "800" in rt:
                        data["packVoltage"]          = rt.get("600")
                        data["soh"]                  = rt.get("1200")   # min SOH %
                        data["tempMax"]              = rt.get("1800")
                        data["tempMin"]              = rt.get("2000")
                        data["cellVoltageMax"]       = rt.get("1300")
                        data["cellVoltageMin"]       = rt.get("1500")
                        data["energyChargeDay"]      = rt.get("7200")
                        data["energyDischargeDay"]   = rt.get("7400")
                        data["energyChargeTotal"]    = rt.get("7100")
                        data["energyDischargeTotal"] = rt.get("7300")
                        # Additional BMS points
                        data["avgSoh"]               = rt.get("1100")   # average SOH %
                        data["packCurrentA"]         = rt.get("700")    # BMS pack current (A)
                    elif "1400" in rt:
                        data["soh"]                  = rt.get("1500")
                        data["tempMax"]              = rt.get("3000")
                        data["tempMin"]              = rt.get("3300")
                        data["cellVoltageMax"]       = rt.get("2400")
                        data["cellVoltageMin"]       = rt.get("2700")
                        data["cycleCount"]           = rt.get("1800")
                        data["energyChargeTotal"]    = rt.get("1900")
                        data["avgSoh"]               = rt.get("1400")   # Tower SOC doubles as ref
                        data["packCurrentA"]         = rt.get("700")

                    # ── Cell voltage spread (existing cellVoltageDiff in V) ───
                    try:
                        vmax = float(data.get("cellVoltageMax") or 0)
                        vmin = float(data.get("cellVoltageMin") or 0)
                        if vmax > 0 and vmin > 0:
                            data["cellVoltageDiff"]   = round(vmax - vmin, 4)
                            data["cellVoltageDiffMv"] = round((vmax - vmin) * 1000, 1)
                    except (ValueError, TypeError):
                        pass

                    # ── Battery status string ─────────────────────────────────
                    try:
                        power = float(data.get("realTimePower") or 0)
                        data["batteryStatus"] = (
                            "Charging"    if power >  10 else
                            "Discharging" if power < -10 else
                            "Standby"
                        )
                    except (ValueError, TypeError):
                        pass

                    # ── API telemetry ─────────────────────────────────────────
                    data["apiStatus"]    = self._api_status
                    data["apiCallCount"] = self._api_call_count

                    # ── Module data and pack totals ───────────────────────────
                    n_modules = len(self._module_sns) or 1
                    data["moduleCount"]  = len(self._module_sns)
                    data["module_sns"]   = list(self._module_sns)
                    data["module_data"]  = module_data

                    # Corrected total capacity:
                    # station/info only reports one module — multiply by discovered count
                    bc = _to_float(data.get("batteryCapacity")) or 0.0
                    total_kwh = round(bc * n_modules, 3)
                    data["totalBatteryCapacity"] = total_kwh

                    # Usable and remaining capacity
                    try:
                        soc     = float(data.get("soc") or 0)
                        soh_pct = float(data.get("soh") or 100)
                        usable    = round(total_kwh * (soh_pct / 100), 3)
                        remaining = round(usable * (soc / 100), 3)
                        data["usableKwh"]    = usable
                        data["remainingKwh"] = remaining
                    except (ValueError, TypeError):
                        pass

                    return data

            except UpdateFailed:
                raise
            except aiohttp.ClientError as err:
                _LOGGER.error("Dyness connection error: %s", err)
                raise UpdateFailed(f"Dyness API connection error: {err}") from err
            except Exception as err:
                _LOGGER.error("Dyness unexpected error: %s", err, exc_info=True)
                raise UpdateFailed(f"Unexpected error: {err}") from err
