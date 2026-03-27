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

# API Rate-Limit: max ~60 Calls/Stunde = 1/Minute
# Pro Update: 3 Basis-Calls + 2 pro Sub-Modul
# 1-2 Module → 5 Min, 3-4 Module → 10 Min, 5+ Module → 15 Min
_MIN_CALL_INTERVAL = 1.5
_RATE_LIMIT_BACKOFF = 10
_MAX_RETRIES = 3

# Gültige BMS-Suffixe
_BMS_SUFFIXES = ("-BMS", "-BDU")


def _scan_interval_for_modules(n: int) -> timedelta:
    """Dynamisches Scan-Intervall basierend auf Modulanzahl."""
    if n <= 2:
        return timedelta(minutes=5)
    elif n <= 4:
        return timedelta(minutes=10)
    else:
        return timedelta(minutes=15)


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
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _is_success(result: dict) -> bool:
    """Prüft ob API-Antwort erfolgreich — akzeptiert code als String oder Integer."""
    code = result.get("code")
    return str(code) in ("0", "200") or code == 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = DynessDataCoordinator(
        hass,
        entry.data["api_id"],
        entry.data["api_secret"],
        entry.data["api_base"],
        device_sn=entry.data.get("device_sn"),
        dongle_sn=entry.data.get("dongle_sn"),
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

    def __init__(self, hass, api_id, api_secret, api_base,
                 device_sn=None, dongle_sn=None):
        super().__init__(hass, _LOGGER, name=DOMAIN,
                         update_interval=timedelta(minutes=5))
        self.api_id     = api_id
        self.api_secret = api_secret
        self.api_base   = api_base
        self.device_sn  = device_sn
        self.dongle_sn  = dongle_sn

        self.station_info  = {}
        self.device_info   = {}
        self.storage_info  = {}
        self.realtime_data = {}
        self.module_data: dict[str, dict] = {}  # mid → Sensordaten

        self._bound: bool = False
        self._module_sns: list[str] = []
        self._last_call_time: float = 0.0

    async def _call(self, session: aiohttp.ClientSession, path: str, body_dict: dict) -> dict:
        """Rate-limitierter API-Aufruf mit Retry bei HTTP 429."""
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            await asyncio.sleep(_MIN_CALL_INTERVAL - elapsed)
        url = f"{self.api_base}/openapi/ems-device{path}"
        body = json.dumps(body_dict, separators=(',', ':'))
        for attempt in range(_MAX_RETRIES + 1):
            self._last_call_time = time.monotonic()
            headers = _build_headers(self.api_id, self.api_secret, body, path)
            try:
                async with session.post(url, headers=headers, data=body) as response:
                    if response.status == 429:
                        wait = _RATE_LIMIT_BACKOFF * (2 ** attempt)
                        _LOGGER.warning(
                            "Dyness: Rate-Limit (429) auf %s – Retry %d/%d in %ds",
                            path, attempt + 1, _MAX_RETRIES, wait,
                        )
                        if attempt < _MAX_RETRIES:
                            await asyncio.sleep(wait)
                            continue
                        return {}
                    raw_text = await response.text()
                    _LOGGER.debug("Dyness %s: %s", path, raw_text)
                    return json.loads(raw_text)
            except aiohttp.ClientError as e:
                _LOGGER.warning("Dyness %s Verbindungsfehler (Versuch %d/%d): %s",
                                path, attempt + 1, _MAX_RETRIES, e)
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
        return {}

    def _update_scan_interval(self):
        """Passt das Scan-Intervall dynamisch an die Modulanzahl an."""
        n = len(self._module_sns)
        new_interval = _scan_interval_for_modules(n)
        if self.update_interval != new_interval:
            self.update_interval = new_interval
            _LOGGER.info(
                "Dyness: %d Modul(e) erkannt → Scan-Intervall auf %d Min gesetzt",
                n, int(new_interval.total_seconds() / 60)
            )

    async def _async_update_data(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with async_timeout.timeout(90):

                    # ── Auto-Discovery BMS SN (einmalig) ─────────────────────
                    if not self.device_sn:
                        try:
                            sl_result = await self._call(session, "/v1/device/storage/list", {})
                            if _is_success(sl_result):
                                device_list = (sl_result.get("data", {}) or {}).get("list", [])
                                bms = (
                                    next((d for d in device_list
                                          if str(d.get("deviceSn", "")).endswith(_BMS_SUFFIXES)), None)
                                    or (device_list[0] if device_list else None)
                                )
                                if bms:
                                    self.device_sn = bms.get("deviceSn", "")
                                    _LOGGER.info("Dyness: BMS SN ermittelt: %s", self.device_sn)
                                else:
                                    raise UpdateFailed(
                                        "Dyness: Keine Geräte auf diesem API-Account. "
                                        "Bitte API-Zugangsdaten prüfen."
                                    )
                        except UpdateFailed:
                            raise
                        except Exception as e:
                            raise UpdateFailed(f"Dyness: BMS-Erkennung fehlgeschlagen: {e}") from e

                    # ── Gerät binden (einmalig) ───────────────────────────────
                    if not self._bound:
                        try:
                            bind_body = {"deviceSn": self.device_sn}
                            if self.dongle_sn:
                                bind_body["collectorSn"] = self.dongle_sn
                            bind_result = await self._call(session, "/v1/device/bindSn", bind_body)
                            bind_code = str(bind_result.get("code", ""))
                            if bind_code in ("0", "200", "500") or bind_result.get("code") in (0, 500):
                                self._bound = True
                                if bind_code == "500" or bind_result.get("code") == 500:
                                    _LOGGER.debug("Dyness bindSn: bereits gebunden – OK")
                                else:
                                    _LOGGER.debug("Dyness bindSn erfolgreich")
                            else:
                                _LOGGER.warning(
                                    "Dyness bindSn: Code %s – Integration läuft trotzdem weiter.",
                                    bind_code
                                )
                                self._bound = True
                        except UpdateFailed:
                            raise
                        except Exception as e:
                            _LOGGER.warning("Dyness bindSn nicht erreichbar: %s", e)
                            self._bound = True

                    # ── Statische Daten (einmalig) ────────────────────────────
                    if not self.station_info:
                        try:
                            result = await self._call(
                                session, "/v1/station/info", {"deviceSn": self.device_sn}
                            )
                            if _is_success(result):
                                self.station_info = result.get("data", {}) or {}
                        except Exception as e:
                            _LOGGER.warning("Dyness station/info nicht erreichbar: %s", e)

                    if not self.device_info:
                        try:
                            body = {"deviceSn": self.device_sn}
                            if self.dongle_sn:
                                body["collectorSn"] = self.dongle_sn
                            result = await self._call(
                                session, "/v1/device/household/storage/detail", body
                            )
                            if _is_success(result):
                                self.device_info = result.get("data", {}) or {}
                        except Exception as e:
                            _LOGGER.warning("Dyness household/storage/detail nicht erreichbar: %s", e)

                    # ── WorkStatus (bei jedem Update) ─────────────────────────
                    try:
                        result = await self._call(session, "/v1/device/storage/list", {})
                        if _is_success(result):
                            device_list = (result.get("data", {}) or {}).get("list", [])
                            match = next(
                                (d for d in device_list if d.get("deviceSn") == self.device_sn),
                                device_list[0] if device_list else {}
                            )
                            self.storage_info = match
                    except Exception as e:
                        _LOGGER.warning("Dyness storage/list nicht erreichbar: %s", e)

                    # ── realTime/data BMS (bei jedem Update) ──────────────────
                    try:
                        body = {"deviceSn": self.device_sn}
                        if self.dongle_sn:
                            body["collectorSn"] = self.dongle_sn
                        rt_result = await self._call(session, "/v1/device/realTime/data", body)
                        if _is_success(rt_result):
                            raw = rt_result.get("data", []) or []
                            self.realtime_data = {
                                item["pointId"]: item["pointValue"]
                                for item in raw
                                if isinstance(item, dict) and "pointId" in item
                            }
                            _LOGGER.debug("Dyness realTime/data: %d Punkte", len(self.realtime_data))

                            # ── Sub-Modul Discovery via SUB Point ─────────────
                            if not self._module_sns:
                                sub_raw = self.realtime_data.get("SUB", "")
                                if sub_raw:
                                    candidates = [s.strip() for s in str(sub_raw).split(",") if s.strip()]
                                    # Nur echte Modul-SNs — keine BMS-SNs
                                    candidates = [s for s in candidates if not s.endswith(_BMS_SUFFIXES)]
                                    # Nur bei mehreren Modulen extra API-Calls machen
                                    # Ein einzelnes Sub-Modul = Junior Box / einfaches Gerät
                                    # → kein eigener realTime/data Abruf nötig
                                    if len(candidates) > 1:
                                        self._module_sns = candidates
                                        _LOGGER.info(
                                            "Dyness: %d Sub-Modul(e) entdeckt: %s",
                                            len(self._module_sns), self._module_sns
                                        )
                                        self._update_scan_interval()
                                    else:
                                        _LOGGER.debug(
                                            "Dyness: Einzelnes Sub-Modul %s – kein separater Abruf",
                                            candidates
                                        )
                        else:
                            _LOGGER.debug(
                                "Dyness realTime/data: Code %s – %s",
                                rt_result.get("code"), rt_result.get("info")
                            )
                    except Exception as e:
                        _LOGGER.warning("Dyness realTime/data nicht erreichbar: %s", e)

                    # ── Per-Modul realTime/data ───────────────────────────────
                    new_module_data: dict[str, dict] = {}
                    for sn in self._module_sns:
                        try:
                            m_result = await self._call(
                                session, "/v1/device/realTime/data", {"deviceSn": sn}
                            )
                            if _is_success(m_result):
                                m_raw = m_result.get("data", []) or []
                                m_pts = {
                                    item["pointId"]: item["pointValue"]
                                    for item in m_raw
                                    if isinstance(item, dict) and "pointId" in item
                                }
                                mid = sn.split("-")[-1] if "-" in sn else sn[-8:]
                                new_module_data[mid] = _parse_module_points(sn, mid, m_pts)
                                _LOGGER.debug("Dyness Modul %s: %d Punkte", mid, len(m_pts))
                            else:
                                _LOGGER.warning("Dyness Modul %s: Code %s", sn, m_result.get("code"))
                        except Exception as e:
                            _LOGGER.warning("Dyness Modul %s nicht erreichbar: %s", sn, e)
                    if new_module_data:
                        self.module_data = new_module_data

                    # ── Leistungsdaten (bei jedem Update) ────────────────────
                    body = {"pageNo": 1, "pageSize": 1, "deviceSn": self.device_sn}
                    if self.dongle_sn:
                        body["collectorSn"] = self.dongle_sn
                    result = await self._call(
                        session, "/v1/device/getLastPowerDataBySn", body
                    )
                    code = str(result.get("code", ""))
                    if code not in ("0", "200") and result.get("code") != 0:
                        _LOGGER.error(
                            "Dyness getLastPowerDataBySn fehlgeschlagen – Code %s: %s (deviceSn=%s)",
                            code, result.get("info"), self.device_sn
                        )
                        raise UpdateFailed(
                            f"Dyness API Fehler (Code {code}): {result.get('info', 'Unbekannt')} "
                            f"– deviceSn={self.device_sn}"
                        )

                    data = result.get("data", {})
                    if isinstance(data, list):
                        valid = [d for d in data if d.get("soc") is not None]
                        if not valid:
                            _LOGGER.warning(
                                "Dyness: Alle %d Datenpunkte haben soc=null (deviceSn=%s)",
                                len(data), self.device_sn
                            )
                        data = valid[-1] if valid else (data[-1] if data else {})

                    # ── Statische Felder ──────────────────────────────────────
                    data["batteryCapacity"]           = self.station_info.get("batteryCapacity")
                    data["deviceCommunicationStatus"] = self.device_info.get("deviceCommunicationStatus")
                    data["firmwareVersion"]            = self.device_info.get("firmwareVersion")
                    data["workStatus"]                 = self.storage_info.get("workStatus")

                    # ── realTime/data Felder ──────────────────────────────────
                    rt = self.realtime_data
                    if "800" in rt:
                        # Junior Box / DL5.0C / PowerHaus Schema
                        data["packVoltage"]           = rt.get("600")
                        data["soh"]                   = rt.get("1200")
                        data["temp"]                  = rt.get("1800")
                        data["cellVoltageMax"]         = rt.get("1300")
                        data["cellVoltageMin"]         = rt.get("1500")
                        data["energyChargeDay"]        = rt.get("7200")
                        data["energyDischargeDay"]     = rt.get("7400")
                        data["energyChargeTotal"]      = rt.get("7100")
                        data["energyDischargeTotal"]   = rt.get("7300")
                        data["tempMosfet"]             = rt.get("2300")
                        data["tempBmsMax"]             = rt.get("2800")
                        data["tempBmsMin"]             = rt.get("3000")
                        data["alarmStatus1"]           = rt.get("3200")
                        data["alarmStatus2"]           = rt.get("3300")
                        data["alarmTotal"]             = rt.get("4100")
                    elif "1400" in rt:
                        # Tower Schema
                        data["soh"]                   = rt.get("1500")
                        data["tempMax"]               = rt.get("3000")
                        data["tempMin"]               = rt.get("3300")
                        data["cellVoltageMax"]         = rt.get("2400")
                        data["cellVoltageMin"]         = rt.get("2700")
                        data["cycleCount"]             = rt.get("1800")
                        data["energyChargeTotal"]      = rt.get("1900")

                    # ── Berechnete Felder ─────────────────────────────────────
                    try:
                        vmax = _to_float(data.get("cellVoltageMax"))
                        vmin = _to_float(data.get("cellVoltageMin"))
                        if vmax is not None and vmin is not None and vmax > 0 and vmin > 0:
                            data["cellVoltageDiffMv"] = round((vmax - vmin) * 1000, 1)
                    except (ValueError, TypeError):
                        pass

                    try:
                        power = float(data.get("realTimePower") or 0)
                        data["batteryStatus"] = (
                            "Charging"    if power >  10 else
                            "Discharging" if power < -10 else
                            "Standby"
                        )
                    except (ValueError, TypeError):
                        pass

                    try:
                        bc  = _to_float(data.get("batteryCapacity"))
                        soc = _to_float(data.get("soc"))
                        soh = _to_float(data.get("soh"))
                        if bc is not None and soc is not None and soh is not None:
                            usable    = round(bc * (soh / 100), 3)
                            remaining = round(usable * (soc / 100), 3)
                            data["usableKwh"]    = usable
                            data["remainingKwh"] = remaining
                    except (ValueError, TypeError):
                        pass

                    # ── Modul-Daten anhängen ──────────────────────────────────
                    data["module_data"]  = self.module_data
                    data["moduleCount"]  = len(self._module_sns)

                    return data

            except UpdateFailed:
                raise
            except asyncio.TimeoutError as err:
                _LOGGER.warning("Dyness API Timeout – wird beim nächsten Update erneut versucht")
                raise UpdateFailed("Dyness API Timeout") from err
            except aiohttp.ClientError as err:
                _LOGGER.error("Dyness Verbindungsfehler: %s", err)
                raise UpdateFailed(f"Verbindungsfehler zur Dyness API: {err}") from err
            except Exception as err:
                _LOGGER.error("Dyness unerwarteter Fehler: %s", err, exc_info=True)
                raise UpdateFailed(f"Unerwarteter Fehler: {err}") from err


def _parse_module_points(sn: str, mid: str, pts: dict) -> dict:
    """Parst Sub-Modul Datenpunkte (DL5.0C Modul-Schema)."""
    def g(key): return pts.get(key) if pts.get(key) != "" else None

    d = {
        "sn":           sn,
        "module_id":    mid,
        "soh":          _to_float(g("14000")),
        "cycle_count":  _to_float(g("13900")),
        "cell_voltage_max": _to_float(g("10300")),  # erste Zelle als Proxy
        "bms_temp":     _to_float(g("12400")),
        "cell_temp_1":  _to_float(g("12500")),
        "cell_temp_2":  _to_float(g("12600")),
        "voltage":      _to_float(g("13500")),
        "current":      _to_float(g("13400")),
        "alarm_1":      pts.get("14300"),
        "alarm_2":      pts.get("15200"),
    }

    # Zellspannungen sammeln für Max/Min
    cells = []
    for i in range(1, 17):
        pid = str(10200 + i * 100)
        v = _to_float(pts.get(pid))
        if v is not None:
            cells.append(v)
    if cells:
        d["cell_voltage_max"] = max(cells)
        d["cell_voltage_min"] = min(cells)
        d["cell_voltage_spread_mv"] = round((max(cells) - min(cells)) * 1000, 1)

    d["has_alarm"] = (
        (int(pts.get("14300") or 0) != 0) or
        (int(pts.get("15200") or 0) != 0)
    )
    return d
