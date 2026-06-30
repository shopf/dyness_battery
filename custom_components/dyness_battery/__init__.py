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

# Entitäten die in früheren Versionen existierten aber entfernt wurden.
# Diese werden beim Setup automatisch aus der Entity-Registry gelöscht.
STALE_ENTITY_KEYS = {
    # v2.0.0: alarmStatus1 / alarmStatus2 ersetzt durch alarmText + Alarm-Bit-Sensoren
    "alarmStatus1",
    "alarmStatus2",
    # Veraltete Tower-Alarm-Duplikate (al* ohne alarm*-Präfix)
    "alSpreadV",
    "alSpreadT",
    "alInsul",
    "alAfe",
    "alBms",
    "alSys",
}

# API Rate-Limit: max ~60 Calls/Stunde = 1/Minute
# Pro Update: 3 Basis-Calls + 2 pro Sub-Modul
# 1-2 Module → 5 Min, 3-4 Module → 10 Min, 5+ Module → 15 Min
# Rate-Limiting: Dyness API erlaubt ≤ 2 Anfragen/Sekunde (offiziell bestätigt via Dyness-Doku).
# 1.0s hat sich als stabiler Kompromiss erwiesen. 0.5s/0.8s verursachten 429-Burst-Fehler
# bei Sub-Modul-Calls (Issue #26). 1.5s (original) war zu konservativ.
_MIN_CALL_INTERVAL = 1.0
_RATE_LIMIT_BACKOFF = 10
_MAX_RETRIES = 3
# Sub-Modul-Calls: kein Retry bei 429 — sofort aufgeben und letzten bekannten Wert
# beibehalten. Retries mit langen Wartezeiten blockieren den gesamten Update-Zyklus
# und führen zu stagnierenden Sensoren (Issue #26, v2.3.9.1).
_MODULE_MAX_RETRIES = 0

# Gültige BMS-Suffixe
_BMS_SUFFIXES = ("-BMS", "-BDU")

# ── Schema-Konstanten ─────────────────────────────────────────────────────────
SCHEMA_TOWER        = "tower"
SCHEMA_STACK100     = "stack100"
SCHEMA_DL5          = "dl5"
SCHEMA_POWERBOX_PRO = "powerbox_pro"
SCHEMA_POWERBOX_G2  = "powerbox_g2"
SCHEMA_POWERDEPOT   = "powerdepot"
SCHEMA_JUNIOR       = "junior"
SCHEMA_POWERBRICK   = "powerbrick"
SCHEMA_CYGNI        = "cygni"
SCHEMA_UNKNOWN      = "unknown"

# Explizite Model → Schema Mapping
# Neue Modelle hier eintragen — kein Code-Logik-Anfassen nötig.
# Prefix-Match greift automatisch für Varianten (z.B. STACK100-12S, Cygni 5.0HS).
_MODEL_SCHEMA_MAP: dict[str, str] = {
    # Tower Familie — exakte Namen aus API verifiziert
    "TOWER-T14":        SCHEMA_TOWER,
    "TOWER-T17":        SCHEMA_TOWER,   # modelCode 26, verifiziert via Log (#31)
    "TOWER-PRO-TP7":    SCHEMA_TOWER,   # "Tower Pro TP7"  → TOWER-PRO-TP7
    "TOWER-PRO-TP11":   SCHEMA_TOWER,   # "Tower Pro TP11" → TOWER-PRO-TP11
    "TOWER-PRO-TP15":   SCHEMA_TOWER,   # "Tower Pro TP15" → TOWER-PRO-TP15
    "TOWER-TP7":        SCHEMA_TOWER,   # Fallback falls API ohne "Pro"
    "TOWER-TP11":       SCHEMA_TOWER,
    "TOWER-TP15":       SCHEMA_TOWER,
    # Stack100 Familie
    "STACK100-8S":      SCHEMA_STACK100,
    "STACK100-10S":     SCHEMA_STACK100,
    # DL5 Familie
    "DL5.0C":           SCHEMA_DL5,
    # PowerBox G2 (modelCode 42) — eigenes Schema mit 5-stelligen Points, verifiziert
    "POWERBOX-G2":      SCHEMA_POWERBOX_G2,
    # PowerBox Pro / PowerHaus — eigenes Schema, verifiziert via Log
    "POWERBOX-PRO":     SCHEMA_POWERBOX_PRO,
    "POWERHAUS":        SCHEMA_POWERBOX_PRO,
    # PowerDepot G2 (modelCode 144) — eigenes Schema
    "POWERDEPOT-G2":    SCHEMA_POWERDEPOT,
    # PowerBrick (modelCode 43) — HV standalone, Point-Schema ähnlich PowerDepot G2
    # Verifiziert via Issue #36 Log (deviceModelName = "PowerBrick", 14.336 kWh, 1 Modul)
    "POWERBRICK":       SCHEMA_POWERBRICK,
    # Junior Box
    "JUNIOR-BOX":       SCHEMA_JUNIOR,
    # Cygni Hybrid-Wechselrichter
    "CYGNI":            SCHEMA_CYGNI,
}


def _detect_schema(device_model_name: str, rt: dict) -> str:
    """Schema-Erkennung: primär via deviceModelName, Fallback via Points.

    Neue Geräte werden ausschließlich in _MODEL_SCHEMA_MAP eingetragen.
    Der Point-Fallback bleibt als Sicherheitsnetz für noch unbekannte Modelle.
    """
    model = (device_model_name or "").upper().replace(" ", "-")

    # Exakter Match
    if model in _MODEL_SCHEMA_MAP:
        return _MODEL_SCHEMA_MAP[model]

    # Prefix-Match für Varianten (z.B. STACK100-12S, Cygni 10.0HS)
    for key, schema in _MODEL_SCHEMA_MAP.items():
        prefix = key.split("-")[0]
        if model.startswith(prefix):
            _LOGGER.info(
                "Dyness: Unbekannte Modell-Variante '%s' → Schema '%s' per Prefix-Match ('%s')",
                model, schema, prefix,
            )
            return schema

    # Fallback: Point-Heuristik (letzter Ausweg für komplett unbekannte Modelle)
    _LOGGER.warning(
        "Dyness: Unbekanntes Modell '%s' — Schema-Erkennung via Points (Fallback). "
        "Bitte ein Issue mit Log-Datei erstellen.",
        model,
    )
    if "1400" in rt and ("2400" in rt or "2700" in rt):
        return SCHEMA_TOWER
    if "800" in rt:
        return SCHEMA_JUNIOR
    if ("13400" in rt or "12400" in rt) and "800" not in rt and "1400" not in rt:
        return SCHEMA_POWERDEPOT
    return SCHEMA_UNKNOWN


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
    # ── Veraltete Entitäten aus der Entity-Registry entfernen ────────────────
    await _async_cleanup_stale_entities(hass, entry)

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


async def _async_cleanup_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Löscht veraltete Entitäten aus der Entity-Registry.

    Prüft alle registrierten Entitäten dieser Integration und entfernt jene,
    deren unique_id auf einen veralteten Sensor-Key hinweist (STALE_ENTITY_KEYS).
    Funktioniert sowohl für Pack-Level als auch für Modul-Sensoren.
    """
    from homeassistant.helpers import entity_registry as er

    entity_registry = er.async_get(hass)
    stale_entities = [
        entity
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if any(
            entity.unique_id == f"{entry.entry_id}_{key}"          # Pack-Level
            or entity.unique_id.endswith(f"_{key}")                 # Modul-Level (entry_id_mid_key)
            for key in STALE_ENTITY_KEYS
        )
    ]
    if stale_entities:
        _LOGGER.info(
            "Dyness: Bereinige %d veraltete Entität(en): %s",
            len(stale_entities),
            [e.unique_id for e in stale_entities],
        )
        for entity in stale_entities:
            entity_registry.async_remove(entity.entity_id)
    else:
        _LOGGER.debug("Dyness: Keine veralteten Entitäten gefunden.")


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
        self._storage_list_cycle: int = 0  # Zähler für storage/list Throttling
        # Optimierung (Issue #32): getLastRunningDataBySn überspringen wenn einmal
        # alle Felder null waren — typisch bei reinen Batteriesystemen ohne Wechselrichter.
        # Wird auf False zurückgesetzt wenn sich das Gerät-Schema ändert (z.B. Wechselrichter
        # nachgerüstet), was einen HA-Reload erfordert.
        self._running_data_all_null: bool = False

    async def _call(self, session: aiohttp.ClientSession, path: str, body_dict: dict,
                    max_retries: int = _MAX_RETRIES) -> dict:
        """Rate-limitierter API-Aufruf mit optionalem Retry bei HTTP 429.
        
        max_retries=0 für Sub-Modul-Calls: sofort aufgeben bei 429 statt lange
        zu warten und den Update-Zyklus zu blockieren (Issue #26).
        """
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            await asyncio.sleep(_MIN_CALL_INTERVAL - elapsed)
        url = f"{self.api_base}/openapi/ems-device{path}"
        body = json.dumps(body_dict, separators=(',', ':'))
        for attempt in range(max_retries + 1):
            self._last_call_time = time.monotonic()
            headers = _build_headers(self.api_id, self.api_secret, body, path)
            try:
                async with session.post(url, headers=headers, data=body) as response:
                    if response.status == 429:
                        wait = _RATE_LIMIT_BACKOFF * (2 ** attempt)
                        _LOGGER.warning(
                            "Dyness: Rate-Limit (429) auf %s – Retry %d/%d in %ds",
                            path, attempt + 1, max_retries, wait,
                        )
                        if attempt < max_retries:
                            await asyncio.sleep(wait)
                            continue
                        return {"code": "429", "sourceCode": "TOO_MANY_REQUESTS",
                                "data": None, "info": "TOO_MANY_REQUESTS"}
                    raw_text = await response.text()
                    _LOGGER.debug("Dyness %s: %s", path, raw_text)
                    return json.loads(raw_text)
            except aiohttp.ClientError as e:
                _LOGGER.warning("Dyness %s Verbindungsfehler (Versuch %d/%d): %s",
                                path, attempt + 1, max_retries, e)
                if attempt < max_retries:
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

                    # ── device_info + storage/list (alle 3 Zyklen) ───────────
                    # Beide Endpunkte werden im gleichen Zyklus aktualisiert.
                    # deviceCommunicationStatus kommt je nach Gerät aus storage/list
                    # ODER aus household/storage/detail — beide müssen aktuell sein.
                    # Fix: device_info nicht mehr nur einmalig laden sondern ebenfalls
                    # alle 3 Zyklen — verhindert veralteten Communication Status.
                    self._storage_list_cycle = (self._storage_list_cycle + 1) % 3
                    if self._storage_list_cycle == 0 or not self.device_info or not self.storage_info:
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
                                candidates = [s.strip() for s in str(sub_raw).split(",") if s.strip()]
                                candidates = [
                                    s for s in candidates
                                    if not s.endswith(_BMS_SUFFIXES)
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
                                session, "/v1/device/realTime/data", {"deviceSn": sn},
                                max_retries=_MODULE_MAX_RETRIES
                            )
                            if _is_success(m_result):
                                m_raw = m_result.get("data", []) or []
                                m_pts = {
                                    item["pointId"]: item["pointValue"]
                                    for item in m_raw
                                    if isinstance(item, dict) and "pointId" in item
                                }
                                mid = sn  # volle SN als Key — konsistent mit Entity unique_id und known_module_ids
                                new_module_data[mid] = _parse_module_points(sn, mid, m_pts)
                                _LOGGER.debug("Dyness Modul %s: %d Punkte", mid, len(m_pts))
                            else:
                                code = m_result.get("code")
                                _LOGGER.warning("Dyness Modul %s: Code %s", sn, code)
                                # Bei 429 oder anderen Fehlern: alten Wert beibehalten statt
                                # das Modul aus module_data zu entfernen (Issue #26 — Slave
                                # Sensoren wurden Unavailable wenn nur ein Sub-Modul-Call
                                # rate-limited wurde, weil new_module_data das Modul nicht
                                # enthielt und self.module_data dann überschrieben wurde).
                                if sn in self.module_data:
                                    new_module_data[sn] = self.module_data[sn]
                                    _LOGGER.debug(
                                        "Dyness Modul %s: Letzten bekannten Wert beibehalten "
                                        "(Code %s)", sn, code
                                    )
                        except Exception as e:
                            _LOGGER.warning("Dyness Modul %s nicht erreichbar: %s", sn, e)
                            # Auch bei Exception: alten Wert beibehalten
                            if sn in self.module_data:
                                new_module_data[sn] = self.module_data[sn]
                    if new_module_data:
                        self.module_data = new_module_data

                    # ── getLastRunningDataBySn (bei jedem Update) ─────────────
                    # Optimierung (Issue #32): Nach erstem All-Null-Response überspringen.
                    # Spart 1 API-Call/Zyklus bei reinen Batteriesystemen (Tower, PowerDepot,
                    # PowerBrick, Stack100 etc.) ohne Wechselrichter.
                    if self._running_data_all_null:
                        _LOGGER.debug(
                            "Dyness getLastRunningDataBySn: übersprungen "
                            "(alle Felder waren null beim letzten Aufruf)"
                        )
                    else:
                        try:
                            run_body = {"deviceSn": self.device_sn}
                            if self.dongle_sn:
                                run_body["collectorSn"] = self.dongle_sn
                            run_result = await self._call(
                                session, "/v1/device/getLastRunningDataBySn", run_body
                            )
                            if _is_success(run_result):
                                self.running_data = run_result.get("data", {}) or {}
                                all_null = all(
                                    v is None or v == ""
                                    for v in self.running_data.values()
                                )
                                if all_null:
                                    self._running_data_all_null = True
                                    _LOGGER.debug(
                                        "Dyness getLastRunningDataBySn: Alle %d Felder null "
                                        "— kein Wechselrichter verbunden. Endpoint wird ab "
                                        "jetzt übersprungen (spart 1 Call/Zyklus).",
                                        len(self.running_data)
                                    )
                                else:
                                    _LOGGER.debug(
                                        "Dyness getLastRunningDataBySn: %d Felder, "
                                        "firmwareVersion=%s",
                                        len(self.running_data),
                                        self.running_data.get("firmwareVersion")
                                    )
                            else:
                                _LOGGER.debug(
                                    "Dyness getLastRunningDataBySn: Code %s – %s",
                                    run_result.get("code"), run_result.get("info")
                                )
                        except Exception as e:
                            _LOGGER.warning("Dyness getLastRunningDataBySn nicht erreichbar: %s", e)

                    # ── Leistungsdaten (bei jedem Update) ────────────────────
                    # UpdateFailed nur noch bei Totalausfall.
                    # Bei Teilerfolg (z.B. realTime/data OK, getLastPowerDataBySn fehlerhaft)
                    # letzten gültigen Stand behalten statt alle Sensoren unavailable zu machen.
                    body = {"pageNo": 1, "pageSize": 1, "deviceSn": self.device_sn}
                    if self.dongle_sn:
                        body["collectorSn"] = self.dongle_sn
                    result = await self._call(
                        session, "/v1/device/getLastPowerDataBySn", body
                    )
                    code = str(result.get("code", ""))
                    _power_data_ok = code in ("0", "200") or result.get("code") == 0
                    if not _power_data_ok:
                        _LOGGER.warning(
                            "Dyness getLastPowerDataBySn fehlgeschlagen – Code %s: %s (deviceSn=%s) "
                            "— behalte letzten Stand",
                            code, result.get("info"), self.device_sn,
                        )
                        # Totalausfall: auch realTime/data leer → jetzt UpdateFailed
                        if not self.realtime_data and not self.running_data:
                            raise UpdateFailed(
                                f"Dyness API Fehler (Code {code}): {result.get('info', 'Unbekannt')} "
                                f"– deviceSn={self.device_sn}"
                            )
                        # Teilerfolg: anderen Daten wurden bereits aktualisiert → weiter
                        data = {}
                    else:
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
                    # deviceCommunicationStatus: aus storage/list (bei jedem Update aktuell)
                    # statt device_info (einmalig beim Start — veraltet nach Neuverbindung).
                    comm_status = self.storage_info.get("deviceCommunicationStatus")
                    if comm_status is None:
                        comm_status = self.device_info.get("deviceCommunicationStatus")
                    data["deviceCommunicationStatus"] = comm_status
                    data["firmwareVersion"]            = self.device_info.get("firmwareVersion")
                    data["workStatus"]                 = self.storage_info.get("workStatus")

                    # ── realTime/data Felder ──────────────────────────────────
                    rt = self.realtime_data

                    def _rt_set(key: str, point: str) -> None:
                        """Setzt data[key] nur wenn Point in rt vorhanden und nicht None.
                        Verhindert Unavailable beim ersten leeren Zyklus nach Reload."""
                        v = rt.get(point)
                        if v is not None:
                            data[key] = v

                    # Schema-Erkennung via deviceModelName (primär) + Point-Fallback
                    schema = _detect_schema(
                        self.device_info.get("deviceModelName", ""), rt
                    )
                    data["_schema"] = schema
                    _LOGGER.debug("Dyness: Schema erkannt: %s", schema)

                    # PowerBox G2: firmware aus storage_info (device_info liefert null)
                    if schema == SCHEMA_POWERBOX_G2:
                        fw = self.storage_info.get("firmwareVersion")
                        if fw:
                            data["firmwareVersion"] = fw

                    # batteryCapacity:
                    # - Stack100 + Tower: direkt aus BMS-Point (1700) — überschreibt station_info.
                    #   station_info kann veraltet sein (z.B. nach Modulerweiterung 7→13 Module).
                    # - DL5 Master/Slave: station_info × n_sub_modules
                    # - Alle anderen: station_info × n_modules
                    bc_single = _to_float(self.station_info.get("batteryCapacity"))
                    n_modules = max(len(self._module_sns), 1)
                    is_tower_schema = schema == SCHEMA_TOWER
                    if schema in (SCHEMA_STACK100, SCHEMA_TOWER):
                        # Wird unten von Point 1700 überschrieben — hier nur Initialwert
                        data["batteryCapacity"] = bc_single
                    elif bc_single is not None and n_modules > 1:
                        data["batteryCapacity"] = round(bc_single * n_modules, 3)
                        _LOGGER.debug(
                            "Dyness: batteryCapacity %s × %d Module = %s kWh",
                            bc_single, n_modules, data["batteryCapacity"]
                        )
                    else:
                        data["batteryCapacity"] = bc_single
                    if schema == SCHEMA_STACK100:
                        # Stack100 Schema — Points direkt vom BMS Master
                        data["packVoltage"] = rt.get("1100") if rt.get("1100") is not None else data.get("packVoltage")
                        # Fix 3 (Issue #28): SOC aus Point 1400 (live, pointNameCn="SOC"),
                        # nicht aus getLastPowerDataBySn (kann veraltet sein, z.B. Vortag).
                        if rt.get("1400") is not None:
                            data["soc"] = rt.get("1400")
                        data["soh"]            = rt.get("1500")
                        data["cycleCount"]     = rt.get("1800")
                        data["energyChargeTotal"] = rt.get("1900")

                        # Kapazität direkt aus BMS — zuverlässiger als station_info × n_modules.
                        # Bleibt korrekt auch nach Modulerweiterungen (z.B. 7 → 13 Module).
                        stack_remaining = _to_float(rt.get("1600"))
                        stack_usable    = _to_float(rt.get("1700"))
                        if stack_remaining is not None and stack_remaining > 0:
                            data["remainingKwh"]    = stack_remaining
                        if stack_usable is not None and stack_usable > 0:
                            data["usableKwh"]       = stack_usable
                            data["batteryCapacity"] = stack_usable  # Point 1700 > station_info

                        # Zellspannungen Master-Ebene
                        data["cellVoltageMax"]       = rt.get("2400")
                        data["cellVoltageMin"]       = rt.get("2700")
                        data["cellVoltageMaxModule"] = rt.get("2500")
                        data["cellVoltageMaxCell"]   = rt.get("2600")
                        data["cellVoltageMinModule"] = rt.get("2800")
                        data["cellVoltageMinCell"]   = rt.get("2900")

                        # Temperaturen
                        data["tempMax"] = rt.get("3000")
                        data["tempMin"] = rt.get("3300")

                        # Strom- und Spannungslimits
                        cl = _to_float(rt.get("2000"))
                        dl = _to_float(rt.get("2100"))
                        if cl is not None and cl > 0:
                            data["chargeCurrentLimit"]    = cl
                        if dl is not None and dl > 0:
                            data["dischargeCurrentLimit"] = dl

                        # Balancing
                        bal = rt.get("4000")
                        if bal is not None:
                            data["balancingStatus"] = str(bal) != "0"

                        # Alarm-Bits
                        data["alarmSpreadV"] = str(rt.get("5001", "0")) == "1"
                        data["alarmSpreadT"] = str(rt.get("5002", "0")) == "1"
                        data["alarmInsul"]   = str(rt.get("5003", "0")) == "1"
                        data["alarmAfe"]     = str(rt.get("5101", "0")) == "1"
                        data["alarmBms"]     = str(rt.get("5102", "0")) == "1"
                        data["alarmSys"]     = str(rt.get("5104", "0")) == "1"
                        data["alarmTotal"]   = rt.get("9999999")

                        _LOGGER.debug(
                            "Dyness Stack100: usable=%.2f kWh remaining=%.2f kWh "
                            "cellMax=%s V cellMin=%s V",
                            stack_usable or 0, stack_remaining or 0,
                            data.get("cellVoltageMax"), data.get("cellVoltageMin"),
                        )

                    elif schema in (SCHEMA_JUNIOR, SCHEMA_DL5):
                        data["packVoltage"]            = rt.get("600") if rt.get("600") is not None else data.get("packVoltage")
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
                    elif schema == SCHEMA_POWERBOX_PRO:
                        # PowerBox Pro / PowerHaus Schema — verifiziert via Log
                        # batteryCapacity aus station/info = Gesamtkapazität direkt
                        # (kein × n_modules — unabhängig von Modulanzahl)
                        # Points verifiziert:
                        # 600  = Pack Voltage, 700 = Current, 800 = SOC
                        # 1200 = SOH, 1300 = Cell Voltage Max, 1500 = Cell Voltage Min
                        # 1800 = Temp Max, 2000 = Temp Min, 2300 = MOSFET Temp
                        # 3000 = BMS Temp Max, 3600/3700 = Voltage Limits
                        # 3800/3900 = Charge/Discharge Current Limit
                        # 3200–3500 = Alarm-Bits (gleiche Struktur wie Junior/DL5)
                        # 4000 = Ah-Wert (kein Balancing-Flag — nicht nutzen)
                        # 900/1000/1100/1900 = leer oder Modul-Nummern → kein Cycle Count
                        data["packVoltage"] = rt.get("600") if rt.get("600") is not None else data.get("packVoltage")
                        data["soh"]            = rt.get("1200")
                        data["cellVoltageMax"] = rt.get("1300")
                        data["cellVoltageMin"] = rt.get("1500")
                        data["cellVoltageMaxModule"] = rt.get("1401")
                        data["cellVoltageMaxCell"]   = rt.get("1402")
                        data["cellVoltageMinModule"] = rt.get("1601")
                        data["cellVoltageMinCell"]   = rt.get("1602")
                        data["tempMax"]        = rt.get("1800")
                        data["tempMin"]        = rt.get("2000")
                        data["tempMosfet"]     = rt.get("2300")
                        data["tempBmsMax"]     = rt.get("3000")

                        cv = _to_float(rt.get("3600"))
                        dv = _to_float(rt.get("3700"))
                        if cv is not None and cv > 0:
                            data["chargeVoltageLimit"]    = cv
                        if dv is not None and dv > 0:
                            data["dischargeVoltageLimit"] = dv
                        cl = _to_float(rt.get("3800"))
                        dl = _to_float(rt.get("3900"))
                        if cl is not None and cl > 0:
                            data["chargeCurrentLimit"]    = cl
                        if dl is not None and dl > 0:
                            data["dischargeCurrentLimit"] = dl

                        # Alarm-Bits — gleiche Struktur wie Junior/DL5
                        data["alarmStatus1"] = rt.get("3200")
                        data["alarmStatus2"] = rt.get("3300")
                        data["alarmTotal"]   = rt.get("4100")

                        # usableKwh: batteryCapacity (station/info = Gesamtkapazität)
                        bc  = _to_float(data.get("batteryCapacity"))
                        soc = _to_float(data.get("soc"))
                        soh = _to_float(rt.get("1200"))
                        if bc is not None and soc is not None:
                            soh_factor = (soh / 100) if (soh is not None and soh <= 100) else 1.0
                            data["usableKwh"]    = round(bc * soh_factor, 3)
                            data["remainingKwh"] = round(bc * soh_factor * soc / 100, 3)

                    elif schema == SCHEMA_TOWER:
                        # Tower Schema (Tower T14 + Tower Pro TP7/TP11/TP15)
                        data["soh"]                   = rt.get("1500")
                        data["tempMax"]               = rt.get("3000")
                        data["tempMin"]               = rt.get("3300")
                        data["cellVoltageMax"]         = rt.get("2400")
                        data["cellVoltageMin"]         = rt.get("2700")
                        data["cycleCount"]             = rt.get("1800")
                        data["energyChargeTotal"]      = rt.get("1900")
                        # Point 1600 = Verbleibende Kapazität kWh (direkt vom Tower/TP7-BMS)
                        tower_remaining = _to_float(rt.get("1600"))
                        if tower_remaining is not None and tower_remaining > 0:
                            data["remainingKwh"] = tower_remaining
                        # Point 1700 = Nutzbare (Nenn-)Kapazität kWh (Tower Pro TP7)
                        # Überschreibt batteryCapacity aus station/info falls vorhanden
                        tower_usable = _to_float(rt.get("1700"))
                        if tower_usable is not None and tower_usable > 0:
                            data["usableKwh"] = tower_usable
                            # batteryCapacity angleichen falls abweichend
                            if data.get("batteryCapacity") is None:
                                data["batteryCapacity"] = tower_usable
                        # Tower Pro TP7: Alarm-Flags (4400-4805, je Flag-Register + Bit-Aufgliederung)
                        # Tower T14: Alarm-Bits direkt (5001-5104)
                        if rt.get("4400") is not None:
                            # Tower Pro TP7 Alarm-Schema
                            data["alarmSpreadV"] = str(rt.get("4402", "0")) == "1"  # Einzelzellspannung zu hoch - Alarm Stufe 1
                            data["alarmSpreadT"] = str(rt.get("4403", "0")) == "1"  # Ladetemperatur zu hoch - Alarm Stufe 1
                            data["alarmInsul"]   = False  # TP7 hat keinen separaten Isolationsfehler-Bit
                            data["alarmAfe"]     = False
                            data["alarmBms"]     = False
                            data["alarmSys"]     = False
                            # Gesamtalarm: irgendein Flag-Register ≠ 0
                            flags = [rt.get(str(f), "0") for f in [4400, 4500, 4600, 4700, 4800, 4900]]
                            data["alarmTotal"] = str(int(any(str(f) != "0" for f in flags)))
                        else:
                            # Tower T14 Alarm-Schema (verifiziert)
                            data["alarmSpreadV"]  = str(rt.get("5001", "0")) == "1"
                            data["alarmSpreadT"]  = str(rt.get("5002", "0")) == "1"
                            data["alarmInsul"]    = str(rt.get("5003", "0")) == "1"
                            data["alarmAfe"]      = str(rt.get("5101", "0")) == "1"
                            data["alarmBms"]      = str(rt.get("5102", "0")) == "1"
                            data["alarmSys"]      = str(rt.get("5104", "0")) == "1"
                            data["alarmTotal"]    = rt.get("9999999")

                    elif schema == SCHEMA_POWERBOX_G2:
                        # PowerBox G2 Schema (modelCode 42) — verifiziert via Log
                        # Master-Points: 5-stellig (10xxx/12xxx/13xxx/14xxx)
                        # Sub-Modul-Points identisch mit PowerDepot G2
                        # batteryCapacity aus station/info = Gesamtkapazität direkt
                        data["packVoltage"] = rt.get("13500") if rt.get("13500") is not None else data.get("packVoltage")
                        data["cycleCount"]     = rt.get("13900")

                        # Temperaturen
                        bms_temp = _to_float(rt.get("12400"))
                        if bms_temp is not None:
                            data["tempBmsMax"] = bms_temp
                        cell_temps = [
                            _to_float(rt.get(str(12500 + i * 100)))
                            for i in range(4)
                        ]
                        valid_temps = [t for t in cell_temps if t is not None and t > 0]
                        if valid_temps:
                            data["tempMax"] = max(valid_temps)
                            data["tempMin"] = min(valid_temps) if len(valid_temps) > 1 else None

                        # Zellspannungen aus Sub-Modul-Daten (10300–11800)
                        cells = []
                        for i in range(1, 17):
                            v = _to_float(rt.get(str(10200 + i * 100)))
                            if v is not None and v > 0:
                                cells.append(v)
                        if cells:
                            data["cellVoltageMax"] = max(cells)
                            data["cellVoltageMin"] = min(cells)

                        # Kapazität: station/info = Gesamtkapazität direkt (kein × n_modules)
                        bc  = _to_float(data.get("batteryCapacity"))
                        soc = _to_float(data.get("soc"))
                        soh = _to_float(data.get("soh"))
                        if bc is not None and soc is not None:
                            soh_factor = (soh / 100) if (soh is not None and soh <= 100) else 1.0
                            data["usableKwh"]    = round(bc * soh_factor, 3)
                            data["remainingKwh"] = round(bc * soh_factor * soc / 100, 3)

                        # Voltage + Current Limits
                        cv = _to_float(rt.get("18700"))
                        dv = _to_float(rt.get("18800"))
                        cl = _to_float(rt.get("18600"))
                        dl = _to_float(rt.get("19200"))
                        if cv is not None and cv > 0:
                            data["chargeVoltageLimit"]    = cv
                        if dv is not None and dv > 0:
                            data["dischargeVoltageLimit"] = dv
                        if cl is not None and cl > 0:
                            data["chargeCurrentLimit"]    = cl
                        if dl is not None and dl > 0:
                            data["dischargeCurrentLimit"] = dl

                        _LOGGER.debug(
                            "Dyness PowerBox G2: packVoltage=%s V, SOC=%s%%, "
                            "cells=%d, tempMax=%s°C",
                            data.get("packVoltage"), soc,
                            len(cells), data.get("tempMax"),
                        )

                    elif schema == SCHEMA_POWERDEPOT:
                        # PowerDepot G2 Schema (modelCode 144) — vollständig verifiziert
                        # Point 400 = Modulanzahl direkt vom BMS → robuster als _module_sns
                        # batteryCapacity ZUERST setzen damit usableKwh korrekt rechnet
                        n_mod_bms = _to_float(rt.get("400"))
                        if n_mod_bms is not None and n_mod_bms > 0:
                            bc_single = _to_float(self.station_info.get("batteryCapacity"))
                            if bc_single is not None:
                                data["batteryCapacity"] = round(bc_single * int(n_mod_bms), 3)
                        elif data.get("batteryCapacity") is None:
                            # Fallback: _module_sns Anzahl wenn Point 400 leer
                            bc_single = _to_float(self.station_info.get("batteryCapacity"))
                            n_mods = max(len(self._module_sns), 1)
                            if bc_single is not None and n_mods > 1:
                                data["batteryCapacity"] = round(bc_single * n_mods, 3)

                        data["packVoltage"] = rt.get("600") if rt.get("600") is not None else data.get("packVoltage")
                        data["realTimeCurrent"]      = rt.get("700")
                        data["soc"]                  = rt.get("800")
                        data["soh"]                  = rt.get("1200")
                        data["cellVoltageMax"]       = rt.get("1300")
                        data["cellVoltageMaxModule"] = rt.get("1401")
                        data["cellVoltageMaxCell"]   = rt.get("1402")
                        data["cellVoltageMin"]       = rt.get("1500")
                        data["cellVoltageMinModule"] = rt.get("1601")
                        data["cellVoltageMinCell"]   = rt.get("1602")
                        data["tempMax"]              = rt.get("1800")
                        data["tempMaxModule"]        = rt.get("1901")
                        data["tempMin"]              = rt.get("2000")
                        data["tempMinModule"]        = rt.get("2101")
                        data["tempMosfet"]           = rt.get("2300")
                        data["tempBmsMax"]           = rt.get("2800")
                        data["tempBmsMin"]           = rt.get("3000")

                        cv = _to_float(rt.get("3600"))
                        dv = _to_float(rt.get("3700"))
                        cl = _to_float(rt.get("3800"))
                        dl = _to_float(rt.get("3900"))
                        if cv is not None and cv > 0:
                            data["chargeVoltageLimit"]    = cv
                        if dv is not None and dv > 0:
                            data["dischargeVoltageLimit"] = dv
                        # Letzten bekannten Wert beibehalten wenn Point fehlt/null (Issue #29):
                        # chargeCurrentLimit verschwindet wenn ein Zyklus keinen Wert liefert,
                        # weil data[] jedes Mal neu aufgebaut wird.
                        if cl is not None and cl > 0:
                            data["chargeCurrentLimit"]    = cl
                        elif self.data and self.data.get("chargeCurrentLimit"):
                            data["chargeCurrentLimit"]    = self.data["chargeCurrentLimit"]
                        if dl is not None and dl > 0:
                            data["dischargeCurrentLimit"] = dl
                        elif self.data and self.data.get("dischargeCurrentLimit"):
                            data["dischargeCurrentLimit"] = self.data["dischargeCurrentLimit"]

                        # Alarm Status 1/2 (Points 3200/3300) — bei anderen Schemas
                        # gesetzt, im POWERDEPOT-Block bisher übersehen, obwohl die
                        # Points im realTime/data vorhanden sind (Issue #29).
                        data["alarmStatus1"] = rt.get("3200")
                        data["alarmStatus2"] = rt.get("3300")

                        # Kapazität aus BMS-Modulanzahl
                        bc  = _to_float(data.get("batteryCapacity"))
                        soc_pd = _to_float(rt.get("800"))
                        soh_pd = _to_float(rt.get("1200"))
                        if bc is not None and soc_pd is not None:
                            soh_factor = (soh_pd / 100) if (soh_pd is not None and soh_pd <= 100) else 1.0
                            data["usableKwh"]    = round(bc * soh_factor, 3)
                            data["remainingKwh"] = round(bc * soh_factor * soc_pd / 100, 3)

                        # cycleCount aus Sub-Modul-Daten aggregieren (Point 13900)
                        # kein Master-Point verfügbar → Mittelwert über alle Module
                        # WICHTIG: self.module_data direkt verwenden, da data["module_data"]
                        # erst nach dem Schema-Block gesetzt wird (Zeile ~1249).
                        # WICHTIG 2: _parse_module_points liefert "cycle_count" (snake_case,
                        # Point 13900), nicht "cycleCount" — vorheriger Fix-Versuch griff
                        # wegen Key-Mismatch ins Leere (Issue #29 Folgereport).
                        mod_cycles = [
                            _to_float(m.get("cycle_count"))
                            for m in self.module_data.values()
                            if m.get("cycle_count") is not None
                        ]
                        if mod_cycles:
                            data["cycleCount"] = round(sum(mod_cycles) / len(mod_cycles), 0)

                        # temp (Durchschnitt) aus Sub-Modul BMS-Temperatur (Point 12400).
                        # _parse_module_points liefert "bms_temp", nicht "temp" — gleicher
                        # Key-Mismatch wie bei cycle_count oben.
                        mod_temps = [
                            _to_float(m.get("bms_temp"))
                            for m in self.module_data.values()
                            if m.get("bms_temp") is not None
                        ]
                        if mod_temps:
                            data["temp"] = round(sum(mod_temps) / len(mod_temps), 1)

                        # workStatus: direkt aus realTimeCurrent ableiten (Issue #29, seit v2.3.5).
                        # batteryStatus wird erst NACH dem Schema-Block berechnet (Zeile ~1269),
                        # deshalb ist data.get("batteryStatus") hier immer None → workStatus
                        # blieb dauerhaft "Standby". Fix: Strom aus Point 700 direkt auswerten.
                        current_pd = _to_float(rt.get("700"))
                        if current_pd is not None:
                            if current_pd > 1.0:
                                data["workStatus"] = "Charging"
                            elif current_pd < -1.0:
                                data["workStatus"] = "Discharging"
                            else:
                                data["workStatus"] = "Standby"
                        else:
                            # Fallback: Alarm-Bits prüfen
                            alarm_bits_pd = [
                                rt.get("3200"), rt.get("3201"), rt.get("3202"),
                                rt.get("3300"), rt.get("3400"), rt.get("3500"),
                            ]
                            all_clear_pd = all(
                                v is None or str(v) in ("0", "0.0", "")
                                for v in alarm_bits_pd
                            )
                            if all_clear_pd:
                                data["workStatus"] = "Standby"

                        # Alarm-Sensoren — korrekte Point-Mappings (verifiziert)
                        # 3200 = Sammelbyte 1, 3201=Voltage Spread, 3202=MOSFET Temp
                        # 3300 = Sammelbyte 2, 3400=AFE Comm, 3500=System Fault
                        data["alarmStatus"]  = (
                            str(rt.get("3200", "0")) != "0"
                            or str(rt.get("3300", "0")) != "0"
                        )
                        data["alarmSpreadV"] = str(rt.get("3201", "0")) != "0"
                        data["alarmSpreadT"] = str(rt.get("3202", "0")) != "0"
                        data["alarmAfe"]     = str(rt.get("3400", "0")) != "0"
                        data["alarmSys"]     = str(rt.get("3500", "0")) != "0"

                        _LOGGER.debug(
                            "Dyness PowerDepot G2: n_modules=%s, batteryCapacity=%s kWh, "
                            "SOC=%s%%, usableKwh=%s kWh, workStatus=%s",
                            n_mod_bms, data.get("batteryCapacity"),
                            soc_pd, data.get("usableKwh"), data.get("workStatus"),
                        )

                    elif schema == SCHEMA_POWERBRICK:
                        # PowerBrick Schema (modelCode 43) — Standalone-Batteriesystem
                        # Verifiziert via Issue #36 Log (14.336 kWh, 1 Modul, Portugal)
                        #
                        # Point-Schema nahezu identisch zu SCHEMA_POWERDEPOT G2,
                        # aber immer Einzelmodul (Point 400 = 1) → kein Sub-Modul-Abruf.
                        # batteryCapacity direkt aus station/info — kein Multiplikator nötig.
                        #
                        # Verifizierte Points:
                        # 600   = Pack Voltage (V)
                        # 700   = Current (A)
                        # 800   = SOC (%)
                        # 1200  = SOH (%)
                        # 1300  = Cell Voltage Max (V)
                        # 1401  = Cell Voltage Max Module
                        # 1402  = Cell Voltage Max Cell
                        # 1500  = Cell Voltage Min (V)
                        # 1601  = Cell Voltage Min Module
                        # 1602  = Cell Voltage Min Cell
                        # 1800  = Temp Max (°C)
                        # 2000  = Temp Min (°C)
                        # 2300  = MOSFET Temp Max (°C)
                        # 2800  = BMS Temp Max (°C)
                        # 3000  = BMS Temp Min (°C)
                        # 3200  = Alarm Status 1 (Sammelbyte)
                        # 3201-3208 = Alarm Bits
                        # 3300  = Alarm Status 2 (Sammelbyte)
                        # 3600  = Charge Voltage Limit (V)
                        # 3700  = Discharge Voltage Limit (V)
                        # 3800  = Max Charge Current (A)
                        # 3900  = Max Discharge Current (A)
                        # 4000  = Battery Status (Lade-/Entladestatus)
                        # 4100  = Total Alarm Flag

                        # batteryCapacity direkt aus station/info (Einzelmodul, kein Multiplikator)
                        bc_pb = _to_float(self.station_info.get("batteryCapacity"))
                        if bc_pb is not None:
                            data["batteryCapacity"] = bc_pb

                        data["packVoltage"]          = rt.get("600")
                        data["realTimeCurrent"]       = rt.get("700")
                        data["soc"]                   = rt.get("800")
                        data["soh"]                   = rt.get("1200")
                        data["cellVoltageMax"]        = rt.get("1300")
                        data["cellVoltageMaxModule"]  = rt.get("1401")
                        data["cellVoltageMaxCell"]    = rt.get("1402")
                        data["cellVoltageMin"]        = rt.get("1500")
                        data["cellVoltageMinModule"]  = rt.get("1601")
                        data["cellVoltageMinCell"]    = rt.get("1602")
                        data["tempMax"]               = rt.get("1800")
                        data["tempMin"]               = rt.get("2000")
                        data["tempMosfet"]            = rt.get("2300")
                        data["tempBmsMax"]            = rt.get("2800")
                        data["tempBmsMin"]            = rt.get("3000")

                        cv_pb = _to_float(rt.get("3600"))
                        dv_pb = _to_float(rt.get("3700"))
                        cl_pb = _to_float(rt.get("3800"))
                        dl_pb = _to_float(rt.get("3900"))
                        if cv_pb is not None and cv_pb > 0:
                            data["chargeVoltageLimit"]    = cv_pb
                        if dv_pb is not None and dv_pb > 0:
                            data["dischargeVoltageLimit"] = dv_pb
                        if cl_pb is not None and cl_pb > 0:
                            data["chargeCurrentLimit"]    = cl_pb
                        if dl_pb is not None and dl_pb > 0:
                            data["dischargeCurrentLimit"] = dl_pb

                        data["alarmStatus1"] = rt.get("3200")
                        data["alarmStatus2"] = rt.get("3300")

                        # SOC/SOH-basierte Kapazitätsberechnung
                        bc_val  = _to_float(data.get("batteryCapacity"))
                        soc_pb  = _to_float(rt.get("800"))
                        soh_pb  = _to_float(rt.get("1200"))
                        if bc_val is not None and soc_pb is not None:
                            soh_f = (soh_pb / 100) if (soh_pb is not None and soh_pb <= 100) else 1.0
                            data["usableKwh"]    = round(bc_val * soh_f, 3)
                            data["remainingKwh"] = round(bc_val * soh_f * soc_pb / 100, 3)

                        # Alarm-Bits PowerBrick (Points 3201-3208, Issue #36):
                        data["alarmSpreadV"] = str(rt.get("3201", "0")) != "0"
                        data["alarmSpreadT"] = str(rt.get("3202", "0")) != "0"
                        data["alarmInsul"]   = str(rt.get("3205", "0")) != "0"
                        data["alarmAfe"]     = str(rt.get("3203", "0")) != "0"
                        data["alarmBms"]     = str(rt.get("3204", "0")) != "0"
                        data["alarmSys"]     = (
                            str(rt.get("3206", "0")) != "0"
                            or str(rt.get("3207", "0")) != "0"
                            or str(rt.get("3208", "0")) != "0"
                        )

                        # Fix 1: Cycle Count (Point 900 = Average Cycle Count)
                        # Wird im Standby befüllt; bei aktivem Laden/Entladen ggf. leer.
                        cc_pb = rt.get("900")
                        if cc_pb is not None and str(cc_pb).strip() not in ("", "0"):
                            data["cycleCount"] = cc_pb

                        # workStatus: direkt aus realTimeCurrent ableiten (gleicher Fix wie POWERDEPOT).
                        current_pb = _to_float(rt.get("700"))
                        if current_pb is not None:
                            if current_pb > 1.0:
                                data["workStatus"] = "Charging"
                            elif current_pb < -1.0:
                                data["workStatus"] = "Discharging"
                            else:
                                data["workStatus"] = "Standby"
                        else:
                            alarm_bits_pb = [
                                rt.get("3200"), rt.get("3201"), rt.get("3202"),
                                rt.get("3300"), rt.get("3400"), rt.get("3500"),
                            ]
                            if all(v is None or str(v) in ("0", "0.0", "")
                                   for v in alarm_bits_pb):
                                data["workStatus"] = "Standby"

                        _LOGGER.debug(
                            "Dyness PowerBrick: batteryCapacity=%s kWh, SOC=%s%%, "
                            "usableKwh=%s kWh, workStatus=%s",
                            data.get("batteryCapacity"), soc_pb,
                            data.get("usableKwh"), data.get("workStatus"),
                        )

                    elif schema == SCHEMA_CYGNI:
                        # Cygni 10.0HS-M8 Schema (modelCode 192) — Hybrid-Wechselrichter
                        # Verifiziert via API-Log (Discussion #18)
                        #
                        # Besonderheiten:
                        # - Keine Sub-Module (SUB leer)
                        # - INVERTIERTE Polarität: negativ = Laden, positiv = Entladen
                        #   (entgegengesetzt zu allen anderen Dyness-Modellen!)
                        # - getLastRunningDataBySn ist primäre Datenquelle (vollständig)
                        # - getLastPowerDataBySn liefert nur SOC/Power (unvollständig)
                        # - batteryCapacity aus station/info = Gesamtkapazität direkt
                        #   (30.72 kWh = 4 × 7.68 kWh)
                        #
                        # Points aus realTime/data:
                        # 170  = Battery Voltage (V)
                        # 171  = Battery Current (A)
                        # 172  = Battery Power (W) — invertiert!
                        # 173  = Battery Status
                        # 2003 = Battery Temperature (°C)
                        # 2004 = Charge Current Limit (A)
                        # 2005 = Discharge Current Limit (A)
                        # 2010 = SOC (%)
                        # 2011 = SOH (%)
                        # 164  = Internal Temperature
                        # 165  = Heat Dissipation Temperature
                        # 166  = Module Temperature

                        data["packVoltage"] = rt.get("170") if rt.get("170") is not None else data.get("packVoltage")
                        data["soc"]         = rt.get("2010")
                        data["soh"]         = rt.get("2011")
                        data["temp"]        = rt.get("2003")

                        cl = _to_float(rt.get("2004"))
                        dl = _to_float(rt.get("2005"))
                        if cl is not None and cl > 0:
                            data["chargeCurrentLimit"]    = cl
                        if dl is not None and dl > 0:
                            data["dischargeCurrentLimit"] = dl

                        # Invertierte Polarität korrigieren
                        raw_power   = _to_float(rt.get("172"))
                        raw_current = _to_float(rt.get("171"))
                        if raw_power is not None:
                            data["realTimePower"]   = raw_power * -1
                        if raw_current is not None:
                            data["realTimeCurrent"] = raw_current * -1

                        # Kapazität: station/info = Gesamtkapazität direkt
                        bc  = _to_float(data.get("batteryCapacity"))
                        soc_c = _to_float(rt.get("2010"))
                        soh_c = _to_float(rt.get("2011"))
                        if bc is not None and soc_c is not None:
                            soh_factor = (soh_c / 100) if (soh_c is not None and soh_c <= 100) else 1.0
                            data["usableKwh"]    = round(bc * soh_factor, 3)
                            data["remainingKwh"] = round(bc * soh_factor * soc_c / 100, 3)

                        _LOGGER.debug(
                            "Dyness Cygni: packVoltage=%s V, SOC=%s%%, SOH=%s%%, "
                            "power=%s W (invertiert korrigiert), temp=%s°C",
                            data.get("packVoltage"), soc_c, soh_c,
                            data.get("realTimePower"), data.get("temp"),
                        )

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
                    if schema in (SCHEMA_JUNIOR, SCHEMA_DL5):
                        cv = _to_float(rt.get("3600"))
                        dv = _to_float(rt.get("3700"))
                        if cv is not None and cv > 0:
                            data["chargeVoltageLimit"] = cv
                        if dv is not None and dv > 0:
                            data["dischargeVoltageLimit"] = dv

                    # ── Cell-Nummer mit Max/Min Spannung ───────────────────────
                    if schema in (SCHEMA_JUNIOR, SCHEMA_DL5):
                        data["cellVoltageMaxModule"] = rt.get("1401")
                        data["cellVoltageMaxCell"]   = rt.get("1402")
                        data["cellVoltageMinModule"] = rt.get("1601")
                        data["cellVoltageMinCell"]   = rt.get("1602")

                    # ── Balancing Status ───────────────────────────────────────
                    if schema in (SCHEMA_JUNIOR, SCHEMA_DL5):
                        bal = rt.get("4000")
                        if bal is not None:
                            data["balancingStatus"] = str(bal) != "0"

                    # ── Modul-Daten anhängen ──────────────────────────────────
                    n_modules = max(len(self._module_sns), 1)
                    data["module_data"]  = self.module_data
                    data["moduleCount"]  = len(self._module_sns)

                    # ── usableKwh / remainingKwh Berechnung ──────────────────
                    # Stack100: bereits via Point 1600/1700 gesetzt → überspringen.
                    # PowerDepot G2 + PowerBox G2: bereits im Schema-Block korrekt
                    #   berechnet (batteryCapacity × n_modules × SOH × SOC) → überspringen.
                    #   WICHTIG: der generische Block würde die korrekte Berechnung mit
                    #   Ah-Werten aus Sub-Modul Points (13600/13800) überschreiben, die
                    #   nur einen Teilbereich der Kapazität repräsentieren und systematisch
                    #   zu niedrig sind (z.B. 5.52 kWh statt 15.36 kWh bei 3 Modulen).
                    # DL5: Strategie 1 (Ah-basiert) deaktiviert — Ah-Werte aus 13600/13800
                    #   repräsentieren nur einen Teilbereich der Kapazität und unterschätzen
                    #   systematisch (z.B. 3.5 kWh statt 10.24 kWh). batteryCapacity × SOC zuverlässiger.
                    # Alle anderen: Strategie 1 (Ah) wenn verfügbar, sonst SOC-Fallback.
                    if schema not in (SCHEMA_STACK100, SCHEMA_POWERDEPOT, SCHEMA_POWERBOX_G2, SCHEMA_POWERBRICK):
                        try:
                            mod_data = data.get("module_data", {})
                            total_remain_kwh = 0.0
                            total_usable_kwh = 0.0
                            valid_modules    = 0
                            if schema != SCHEMA_DL5:
                                for mod in mod_data.values():
                                    remain_ah = _to_float(mod.get("remain_ah"))
                                    total_ah  = _to_float(mod.get("total_ah"))
                                    voltage   = _to_float(mod.get("voltage"))
                                    if (remain_ah is not None and total_ah is not None
                                            and voltage is not None
                                            and total_ah > 0 and voltage > 10):
                                        total_remain_kwh += remain_ah * voltage / 1000
                                        total_usable_kwh += total_ah  * voltage / 1000
                                        valid_modules    += 1
                            if valid_modules > 0 and total_usable_kwh > 0:
                                data["usableKwh"]    = round(total_usable_kwh, 3)
                                data["remainingKwh"] = round(total_remain_kwh, 3)
                                _LOGGER.debug(
                                    "Dyness: usableKwh=%.3f remainingKwh=%.3f (aus %d Modulen via Ah)",
                                    total_usable_kwh, total_remain_kwh, valid_modules,
                                )
                            else:
                                # Fallback: batteryCapacity × SOH × SOC
                                bc  = _to_float(data.get("batteryCapacity"))
                                soc = _to_float(data.get("soc"))
                                soh = _to_float(data.get("soh"))
                                if bc is not None and soc is not None:
                                    soh_factor = (soh / 100) if (soh is not None and soh <= 100) else 1.0
                                    usable    = round(bc * soh_factor, 3)
                                    remaining = round(usable * (soc / 100), 3)
                                    data["usableKwh"]    = usable
                                    data["remainingKwh"] = remaining
                                    _LOGGER.debug(
                                        "Dyness: usableKwh=%.3f remainingKwh=%.3f "
                                        "(SOC-Fallback: bc=%.3f × soh=%.1f%% × soc=%.1f%%)",
                                        usable, remaining, bc, soh_factor * 100, soc,
                                    )
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
    is_stack100   = pts.get("10010") is not None and pts.get("11000") is not None
    is_tower      = not has_module_sn and not is_stack100 and pts.get("11200") is not None
    is_dl5        = has_module_sn and not is_stack100 and pts.get("10300") is not None
    # Tower Pro TP7 Sub-Module haben das gleiche Schema wie Stack100 (10010+11000),
    # aber 30 Zellen (Point 11100 = 30) statt 16 und andere Temperaturen.
    cell_count_pt = _to_float(pts.get("11100")) if is_stack100 else None
    is_tp7_module = is_stack100 and cell_count_pt is not None and int(cell_count_pt) == 30

    if is_stack100 and not is_tp7_module:
        # Stack100: physische Zellanzahl aus Point 11100 lesen und speichern.
        # cell_count wird in sensor.py genutzt um nur vorhandene Zellen zu registrieren.
        phys_cells = int(cell_count_pt) if cell_count_pt is not None else 16
        d["cell_count"] = phys_cells
        # 16 Zellen, Points 11200-12700 (Schritte 100)
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

    elif is_tp7_module:
        # Tower Pro TP7 Sub-Module: 30 Zellen, Points 11200-14100 (Schritte 100)
        # Temperaturen: 14300-15000 (bis zu 8 Sensoren, aktive per Point 14200)
        # SOC, SOH, Spannung, Strom, Zyklen nur auf Master-Ebene verfügbar — nicht pro Modul
        d["is_tp7"] = True
        n_temps = int(_to_float(pts.get("14200")) or 0)
        cells = []
        for i in range(1, 31):
            pid = str(11100 + i * 100)  # 11200, ..., 14100
            v = _to_float(pts.get(pid))
            d[f"cell_{i:02d}"] = v
            if v is not None and v > 0:
                cells.append(v)
        temps_valid = []
        for i in range(8):
            t = _to_float(pts.get(str(14300 + i * 100)))
            if t is not None and t > 0:
                temps_valid.append(t)
        if temps_valid:
            d["cell_temp_1"] = temps_valid[0]
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
        # Point 14000 / 14100 können entweder SOC/SOH in % (≤100)
        # oder Ah-Kapazitätswerte sein (>100, z.B. 132 Ah, 200 Ah).
        # Powerbox Pro liefert Ah-Werte → als remain_ah/total_ah speichern.
        if soc_raw is not None and soc_raw <= 100:
            d["soc"] = soc_raw
        if soh_raw is not None and soh_raw <= 100:
            d["soh"] = soh_raw
        # Ah-Kapazität: 14000/14100 bevorzugt (direkte Messung vom BMS),
        # 13600/13800 als Fallback (alternative Einheit, weniger zuverlässig).
        cap14000 = _to_float(g("14000"))
        cap14100 = _to_float(g("14100"))
        if cap14000 is not None and cap14000 > 100:
            # Ah-Werte (nicht SOC%) → für kWh-Berechnung verwenden
            d["remain_ah"] = cap14000
            d["total_ah"]  = cap14100 if cap14100 is not None else None
        else:
            # Fallback: Point 13600/13800
            d["remain_ah"] = _to_float(g("13600"))
            d["total_ah"]  = _to_float(g("13800"))
        d["cycle_count"] = _to_float(g("13900"))
        d["bms_temp"]    = _to_float(g("12400"))
        d["cell_temp_1"] = _to_float(g("12500"))
        d["cell_temp_2"] = _to_float(g("12600"))
        d["voltage"]     = _to_float(g("13500"))
        d["current"]     = _to_float(g("13400"))
        # Firmware per Sub-Modul (Point 10100) — PowerBox G2 hat zwei verschiedene Versionen
        fw = pts.get("10100")
        if fw:
            d["firmwareVersion"] = str(fw)
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
