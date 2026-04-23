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
        self.running_data: dict = {}             # getLastRunningDataBySn

        self._bound: bool = False
        self._bound_sns: set = set()  # Bereits gebundene Sub-Modul SNs
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
                            # Sub-Modul Discovery — bei jedem Update prüfen ob neue Module dazugekommen sind
                            sub_raw = self.realtime_data.get("SUB", "")
                            if sub_raw:
                                import re
                                candidates = [s.strip() for s in str(sub_raw).split(",") if s.strip()]
                                candidates = [
                                    s for s in candidates
                                    if not s.endswith(_BMS_SUFFIXES)
                                    and not re.search(r'-BDU-\d+$', s)
                                ]
                                if len(candidates) > 1:
                                    if set(candidates) != set(self._module_sns):
                                        _LOGGER.info(
                                            "Dyness: Sub-Module aktualisiert: %s → %s",
                                            self._module_sns, candidates
                                        )
                                        self._module_sns = candidates
                                        self._update_scan_interval()
                                elif not self._module_sns:
                                    _LOGGER.debug(
                                        "Dyness: Einzelnes Sub-Modul — kein separater Abruf (%s)",
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
                            # Sub-Modul binden falls noch nicht gebunden
                            if sn not in self._bound_sns:
                                bind_res = await self._call(
                                    session, "/v1/device/bindSn", {"deviceSn": sn}
                                )
                                bind_code = str(bind_res.get("code", ""))
                                if bind_code in ("0", "200", "500") or bind_res.get("code") in (0, 500):
                                    self._bound_sns.add(sn)
                                    _LOGGER.info("Dyness Sub-Modul gebunden: %s", sn)
                                else:
                                    _LOGGER.warning(
                                        "Dyness Sub-Modul Binding fehlgeschlagen: %s Code %s",
                                        sn, bind_code
                                    )
                                    continue  # Abruf überspringen wenn Binding fehlschlägt
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

                    # ── getLastRunningDataBySn (bei jedem Update) ─────────────
                    try:
                        run_body = {"deviceSn": self.device_sn}
                        if self.dongle_sn:
                            run_body["collectorSn"] = self.dongle_sn
                        run_result = await self._call(
                            session, "/v1/device/getLastRunningDataBySn", run_body
                        )
                        if _is_success(run_result):
                            self.running_data = run_result.get("data", {}) or {}
                            _LOGGER.debug(
                                "Dyness getLastRunningDataBySn: %d Felder",
                                len(self.running_data)
                            )
                        else:
                            _LOGGER.debug(
                                "Dyness getLastRunningDataBySn: Code %s – %s",
                                run_result.get("code"), run_result.get("info")
                            )
                    except Exception as e:
                        _LOGGER.warning("Dyness getLastRunningDataBySn nicht erreichbar: %s", e)

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
                    # batteryCapacity aus station/info = Kapazität eines Moduls
                    # Bei mehreren Modulen mit Modulanzahl multiplizieren
                    bc_single = _to_float(self.station_info.get("batteryCapacity"))
                    n_modules = max(len(self._module_sns), 1)
                    if bc_single is not None and n_modules > 1:
                        data["batteryCapacity"] = round(bc_single * n_modules, 3)
                        _LOGGER.debug(
                            "Dyness: batteryCapacity %s × %d Module = %s kWh",
                            bc_single, n_modules, data["batteryCapacity"]
                        )
                    else:
                        data["batteryCapacity"] = bc_single
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
                        # Max Lade-/Entladestrom — nur bei Multi-Modul Geräten (DL5.0C etc.)
                        # Junior Box / PowerHaus liefern unzuverlässige Werte (nicht konform mit Datenblatt)
                        if len(self._module_sns) > 0:
                            cl = _to_float(rt.get("3800"))
                            dl = _to_float(rt.get("3900"))
                            if cl is not None and cl > 0:
                                data["chargeCurrentLimit"] = cl
                            if dl is not None and dl > 0:
                                data["dischargeCurrentLimit"] = dl
                    elif "1400" in rt:
                        # Tower Schema
                        data["soh"]                   = rt.get("1500")
                        data["tempMax"]               = rt.get("3000")
                        data["tempMin"]               = rt.get("3300")
                        data["cellVoltageMax"]         = rt.get("2400")
                        data["cellVoltageMin"]         = rt.get("2700")
                        data["cycleCount"]             = rt.get("1800")
                        data["energyChargeTotal"]      = rt.get("1900")
                        # Point 1600 = verbleibende Kapazität kWh (direkt vom Tower)
                        tower_remaining = _to_float(rt.get("1600"))
                        if tower_remaining is not None and tower_remaining > 0:
                            data["remainingKwh"] = tower_remaining
                        # Alarm-Bits als Boolean-Sensoren (Tower T14 verifiziert)
                        data["alarmSpreadV"]  = str(rt.get("5001", "0")) == "1"
                        data["alarmSpreadT"]  = str(rt.get("5002", "0")) == "1"
                        data["alarmInsul"]    = str(rt.get("5003", "0")) == "1"
                        data["alarmAfe"]      = str(rt.get("5101", "0")) == "1"
                        data["alarmBms"]      = str(rt.get("5102", "0")) == "1"
                        data["alarmSys"]      = str(rt.get("5104", "0")) == "1"
                        data["alarmTotal"]    = rt.get("9999999")

                    # ── Temperatur-Logik ─────────────────────────────────────
                    # Wenn tempMax == tempMin → nur tempMax behalten (ein Sensor)
                    # Wenn verschieden → beide behalten (zwei Sensoren)
                    t_max = _to_float(data.get("tempMax"))
                    t_min = _to_float(data.get("tempMin"))
                    if t_max is not None and t_min is not None and t_max == t_min:
                        data.pop("tempMin", None)  # Doppelten Sensor vermeiden

                    # BMS Temp: gleiche Logik
                    bms_max = _to_float(data.get("tempBmsMax"))
                    bms_min = _to_float(data.get("tempBmsMin"))
                    if bms_max is not None and bms_min is not None and bms_max == bms_min:
                        data.pop("tempBmsMin", None)

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

                    # ── getLastRunningDataBySn Felder ─────────────────────────
                    rd = self.running_data
                    if rd:
                        _GRID_STATUS = {"0": "Off Grid", "1": "On Grid"}
                        _RUN_MODE    = {"0": "Self-use", "1": "Feed-in Priority", "2": "Backup", "3": "Manual"}

                        # Leistung
                        for key, rdkey in [
                            ("pvPower",       "pvPower"),
                            ("loadPower",     "loadPower"),
                            ("gridPower",     "activePower"),
                            ("pv1Power",      "pv1Power"),
                            ("pv2Power",      "pv2Power"),
                            ("pv3Power",      "pv3Power"),
                            ("pv4Power",      "pv4Power"),
                        ]:
                            v = _to_float(rd.get(rdkey))
                            if v is not None:
                                data[key] = v

                        # Energie
                        for key, rdkey in [
                            ("pvEnergyToday",    "dayGeneration"),
                            ("loadEnergyToday",  "dayElectricity"),
                            ("gridImportToday",  "buyEnergy"),
                            ("gridExportToday",  "sellEnergy"),
                            ("pvEnergyTotal",    "totalGeneration"),
                            ("loadEnergyTotal",  "totalElectricity"),
                            ("gridImportTotal",  "totalBuyEnergy"),
                            ("gridExportTotal",  "totalSellEnergy"),
                        ]:
                            v = _to_float(rd.get(rdkey))
                            if v is not None:
                                data[key] = v

                        # Temperaturen Inverter
                        for key, rdkey in [
                            ("tempInternal",  "internalTemperature"),
                            ("tempModule",    "moduleTemperature"),
                            ("tempHeatSink",  "heatDissipationTemperature"),
                        ]:
                            v = _to_float(rd.get(rdkey))
                            if v is not None:
                                data[key] = v

                        # Grid / Status
                        data["gridStatus"]         = _GRID_STATUS.get(str(rd.get("gridStatus", "")), rd.get("gridStatus"))
                        data["runModel"]           = _RUN_MODE.get(str(rd.get("runModel", "")), rd.get("runModel"))
                        data["inverterWorkStatus"] = rd.get("workStatus")

                        # Grid Messung
                        for key, rdkey in [
                            ("gridVoltage",   "rvoltage"),
                            ("gridCurrent",   "rcurrent"),
                            ("gridFrequency", "gridFrequencyR"),
                            ("busVoltage",    "busVoltage"),
                            ("pv1Voltage",    "pv1Voltage"),
                            ("pv2Voltage",    "pv2Voltage"),
                            ("pv3Voltage",    "pv3Voltage"),
                            ("pv1Current",    "pv1Current"),
                            ("pv2Current",    "pv2Current"),
                            ("pv3Current",    "pv3Current"),
                        ]:
                            v = _to_float(rd.get(rdkey))
                            if v is not None:
                                data[key] = v

                        # Charge/Discharge Limit aus running_data (zuverlässiger als Points 3800/3900)
                        cl = _to_float(rd.get("chargingLimitCurrent"))
                        dl = _to_float(rd.get("dischargeLimitCurrent"))
                        if cl is not None and cl > 0:
                            data["chargeCurrentLimit"]    = cl
                        if dl is not None and dl > 0:
                            data["dischargeCurrentLimit"] = dl

                        # Fallback: SOC/Power aus running_data wenn getLastPowerDataBySn nichts liefert
                        if data.get("soc") is None:
                            soc_rd = rd.get("batterySoc")
                            if soc_rd is not None:
                                data["soc"] = str(soc_rd)
                        if data.get("realTimePower") is None:
                            bp = _to_float(rd.get("batteryPower"))
                            if bp is not None:
                                data["realTimePower"] = bp

                    # ── Alarm-Text Dekodierung ────────────────────────────────
                    _ALARM_BITS_1 = {
                        "3201": "Cell voltage consistency warning",
                        "3202": "MOSFET high temperature",
                        "3203": "Cell low temperature",
                        "3204": "Cell high temperature",
                        "3205": "Cell low voltage",
                        "3206": "Cell high voltage",
                        "3207": "Pack low voltage",
                        "3208": "Pack high voltage",
                    }
                    _ALARM_BITS_2 = {
                        "3305": "Internal communication error",
                        "3306": "Discharge overcurrent",
                        "3307": "Charge overcurrent",
                        "3308": "Cell temperature consistency warning",
                    }
                    alarm_texts = []
                    for pid, label in {**_ALARM_BITS_1, **_ALARM_BITS_2}.items():
                        if str(rt.get(pid, "0")) == "1":
                            alarm_texts.append(label)
                    # Tower Alarm-Bits
                    _ALARM_BITS_TOWER = {
                        "5001": "Voltage spread alarm",
                        "5002": "Temperature spread alarm",
                        "5003": "Low insulation alarm",
                        "5101": "AFE communication error",
                        "5102": "BMS communication error",
                        "5104": "System fault",
                    }
                    for pid, label in _ALARM_BITS_TOWER.items():
                        if str(rt.get(pid, "0")) == "1":
                            alarm_texts.append(label)

                    if alarm_texts:
                        data["alarmText"] = ", ".join(alarm_texts)
                        # Persistent Notification in HA
                        self.hass.async_create_task(
                            self.hass.services.async_call(
                                "persistent_notification", "create", {
                                    "title": "⚠️ Dyness Battery Alarm",
                                    "message": (
                                        f"Active alarms detected on {self.device_sn}:\n"
                                        + "\n".join(f"• {t}" for t in alarm_texts)
                                        + "\n\nPlease contact Dyness support if the issue persists."
                                    ),
                                    "notification_id": f"dyness_alarm_{self.device_sn}",
                                }
                            )
                        )
                    else:
                        data["alarmText"] = "OK"
                        # Notification löschen wenn kein Alarm mehr
                        self.hass.async_create_task(
                            self.hass.services.async_call(
                                "persistent_notification", "dismiss", {
                                    "notification_id": f"dyness_alarm_{self.device_sn}",
                                }
                            )
                        )

                    # ── stationName als Gerätename ─────────────────────────────
                    data["stationName"] = self.device_info.get("stationName") or                                           self.storage_info.get("stationName") or                                           "Dyness Battery"

                    # ── Voltage Limits ─────────────────────────────────────────
                    if "800" in rt:
                        cv = _to_float(rt.get("3600"))
                        dv = _to_float(rt.get("3700"))
                        if cv is not None and cv > 0:
                            data["chargeVoltageLimit"] = cv
                        if dv is not None and dv > 0:
                            data["dischargeVoltageLimit"] = dv

                    # ── Cell-Nummer mit Max/Min Spannung ───────────────────────
                    if "800" in rt:
                        data["cellVoltageMaxModule"] = rt.get("1401")
                        data["cellVoltageMaxCell"]   = rt.get("1402")
                        data["cellVoltageMinModule"] = rt.get("1601")
                        data["cellVoltageMinCell"]   = rt.get("1602")

                    # ── Balancing Status ───────────────────────────────────────
                    if "800" in rt:
                        bal = rt.get("4000")
                        if bal is not None:
                            data["balancingStatus"] = str(bal) != "0"

                    # ── Modul-Daten anhängen ──────────────────────────────────
                    n_modules = max(len(self._module_sns), 1)
                    data["module_data"]  = self.module_data
                    data["moduleCount"]  = len(self._module_sns)

                    try:
                        bc  = _to_float(data.get("batteryCapacity"))  # bereits × n_modules
                        soc = _to_float(data.get("soc"))
                        soh = _to_float(data.get("soh"))
                        if bc is not None and soc is not None and soh is not None:
                            usable    = round(bc * (soh / 100), 3)
                            remaining = round(usable * (soc / 100), 3)
                            data["usableKwh"]    = usable
                            data["remainingKwh"] = remaining
                    except (ValueError, TypeError):
                        pass

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
    """Parst Sub-Modul Datenpunkte.

    Smart Detection:
    - Stack100:  Point 11000 (Modul-Nr) + 10010 (Sub-SN) → 16 Zellen (11200-12700)
    - Tower T14: Point 10000 fehlt, Point 11200 vorhanden → 30 Zellen (11200-14100)
    - DL5.0C:    Point 10000 vorhanden, Point 10300 vorhanden → 16 Zellen (10300-11800)
    """
    def g(key): return pts.get(key) if pts.get(key) not in (None, "") else None

    d = {"sn": sn, "module_id": mid}
    has_module_sn = pts.get("10000") is not None
    is_stack100 = pts.get("10010") is not None and pts.get("11000") is not None
    is_tower    = not has_module_sn and not is_stack100 and pts.get("11200") is not None
    is_dl5      = has_module_sn and not is_stack100 and pts.get("10300") is not None

    if is_stack100:
        # Stack100: 16 Zellen, Points 11200-12700 (Schritte 100)
        # Temperaturen: 14300-14600 (4 Sensoren)
        cells = []
        for i in range(1, 17):
            pid = str(11100 + i * 100)  # 11200, 11300, ..., 12700
            v = _to_float(pts.get(pid))
            d[f"cell_{i:02d}"] = v
            if v is not None and v > 0:
                cells.append(v)
        # Temperaturen
        temps = [_to_float(pts.get(str(14300 + i * 100))) for i in range(4)]
        temps_valid = [t for t in temps if t is not None and t > 0]
        if temps_valid:
            d["cell_temp_1"] = temps_valid[0] if len(temps_valid) > 0 else None
            d["cell_temp_2"] = temps_valid[1] if len(temps_valid) > 1 else None
        d["module_number"] = _to_float(pts.get("11000"))

    elif is_tower:
        # Tower T14: 30 Zellen, Points 11200-14100
        d["cell_temp_1"] = _to_float(g("14300"))
        d["cell_temp_2"] = _to_float(g("14400"))
        cells = []
        for i in range(1, 31):
            pid = str(11100 + i * 100)
            v = _to_float(pts.get(pid))
            d[f"cell_{i:02d}"] = v
            if v is not None and v > 0:
                cells.append(v)

    elif is_dl5:
        # DL5.0C / PowerBox Pro: 16 Zellen, Points 10300-11800
        # SOC/SOH nur wenn plausibel (≤ 100%) — PowerBox Pro liefert hier andere Werte
        soc_raw = _to_float(g("14000"))
        soh_raw = _to_float(g("14100"))
        d["soc"] = soc_raw if soc_raw is not None and soc_raw <= 100 else None
        d["soh"] = soh_raw if soh_raw is not None and soh_raw <= 100 else None
        d["cycle_count"] = _to_float(g("13900"))
        d["remain_ah"]   = _to_float(g("13600"))
        d["total_ah"]    = _to_float(g("13800"))
        d["bms_temp"]    = _to_float(g("12400"))
        d["cell_temp_1"] = _to_float(g("12500"))
        d["cell_temp_2"] = _to_float(g("12600"))
        d["voltage"]     = _to_float(g("13500"))
        d["current"]     = _to_float(g("13400"))
        cells = []
        for i in range(1, 17):
            pid = str(10200 + i * 100)
            v = _to_float(pts.get(pid))
            d[f"cell_{i:02d}"] = v
            if v is not None and v > 0:
                cells.append(v)
        alarm = any(int(pts.get(str(14300 + i * 100)) or 0) != 0 for i in range(16))
        d["has_alarm"] = alarm
    else:
        cells = []

    if cells:
        d["cell_voltage_max"]       = max(cells)
        d["cell_voltage_min"]       = min(cells)
        d["cell_voltage_spread_mv"] = round((max(cells) - min(cells)) * 1000, 1)

    return d
