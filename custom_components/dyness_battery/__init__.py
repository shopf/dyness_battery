"""Dyness Battery Integration für Home Assistant."""
import asyncio
import hashlib
import hmac
import base64
import json
import logging
import time as _time  # Alias zwingend nötig: das Paket enthält ein Submodul
                        # 'time.py' (HA-Plattform 'time'), dessen Import sonst
                        # den Namen 'time' im Paket-Namensraum überschreibt.
from email.utils import formatdate
from datetime import timedelta, datetime, timezone

import aiohttp
import async_timeout

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import Platform

_LOGGER = logging.getLogger(__name__)

DOMAIN = "dyness_battery"
PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.SELECT, Platform.SWITCH,
             Platform.TIME, Platform.BUTTON]

# Schutzmechanismus fürs Schreiben (Control-Patch): verhindert, dass eine kaputte
# Automation oder versehentliches Klicken den Dyness-Server mit Schreibanfragen
# flutet. Betrifft NUR die Schreib-Methoden, nie den Lese-Poll-Zyklus.
WRITE_MAX_CALLS = 6           # max. Schreibvorgänge...
WRITE_WINDOW_SECONDS = 600    # ...innerhalb von 10 Minuten...
WRITE_COOLDOWN_SECONDS = 900  # ...sonst 15 Minuten Sperre + Auto-Deaktivierung

# Gültige workMode-Werte für /v2/SetBaseSetting laut Junior-Box-PDF.
# WICHTIG: Punkt "6400" aus realTime/data liefert KEINEN Wert aus dieser Menge
# (Praxistest zeigte "16") und wird daher NICHT verwendet
# BESTÄTIGT WIRKUNGSLOS auf echter Junior-Box-Hardware (App<->HA Änderungen
# in beide Richtungen ignoriert
_FIXED_WORK_MODE = "3"  # wirkungsloser Platzhalter
VALID_WORK_MODES = {"0", "1", "3", "6"}

DOD_MIN = 20
DOD_MAX = 100

POWER_LIMIT_MIN = 152
POWER_LIMIT_MAX = 800
POWER_STEP = 8

GROUP_POWER_MIN = 152
GROUP_POWER_MAX = 800


def _tou_group_valid(g: dict) -> tuple[bool, str | None, dict]:
    """Prüft eine Zeitfenster-Gruppe auf Vollständigkeit/Plausibilität vor dem Senden.

    Erlaubt: entweder komplett unkonfiguriert (power=0, start=end=00:00) ODER
    vollständig gültig (power 152-800W als Vielfaches von 8, startTime !=
    endTime, endTime nach startTime — kein Übernachtfenster über Mitternacht
    hinweg erlaubt).

    Rückgabe: (ok, reason_key, reason_kwargs) — reason_key ist ein Schlüssel
    in _NOTIFY_REASONS für die mehrsprachige Anzeige
    """
    power = int(g.get("power", 0))
    start = g.get("startTime", "00:00")
    end = g.get("endTime", "00:00")

    if power == 0 and start == "00:00" and end == "00:00":
        return True, None, {}  # unkonfiguriert — erlaubt

    if power == 0:
        return False, "power_zero", {"min": GROUP_POWER_MIN}
    if not (GROUP_POWER_MIN <= power <= GROUP_POWER_MAX):
        return False, "power_range", {"min": GROUP_POWER_MIN, "max": GROUP_POWER_MAX}
    if power % POWER_STEP != 0:
        return False, "power_step", {"step": POWER_STEP, "min": GROUP_POWER_MIN, "next": GROUP_POWER_MIN + POWER_STEP}
    if start == end:
        return False, "start_eq_end", {}
    if end <= start:
        return False, "end_before_start", {}
    return True, None, {}


# ── Mehrsprachige Persistent Notifications ──────────────────────────────────
# Der Text wird einmalig beim Erstellen festgelegt und gilt für alle Nutzer serverweit.
_NOTIFY_FALLBACK_LANG = "en"

_NOTIFY_REASONS = {
    "power_zero": {
        "de": "Leistung muss beim Konfigurieren > 0 sein (mind. {min}W)",
        "en": "Power must be > 0 when configuring (min. {min}W)",
        "es": "La potencia debe ser > 0 al configurar (mín. {min}W)",
        "fr": "La puissance doit être > 0 lors de la configuration (min. {min}W)",
        "pt": "A potência deve ser > 0 ao configurar (mín. {min}W)",
    },
    "power_range": {
        "de": "Leistung muss zwischen {min}W und {max}W liegen",
        "en": "Power must be between {min}W and {max}W",
        "es": "La potencia debe estar entre {min}W y {max}W",
        "fr": "La puissance doit être comprise entre {min}W et {max}W",
        "pt": "A potência deve estar entre {min}W e {max}W",
    },
    "power_step": {
        "de": "Leistung muss ein Vielfaches von {step}W sein (z.B. {min}, {next}, ...)",
        "en": "Power must be a multiple of {step}W (e.g. {min}, {next}, ...)",
        "es": "La potencia debe ser un múltiplo de {step}W (p. ej. {min}, {next}, ...)",
        "fr": "La puissance doit être un multiple de {step}W (ex. {min}, {next}, ...)",
        "pt": "A potência deve ser um múltiplo de {step}W (ex. {min}, {next}, ...)",
    },
    "start_eq_end": {
        "de": "Start- und Endzeit dürfen nicht identisch sein",
        "en": "Start and end time must not be identical",
        "es": "La hora de inicio y fin no pueden ser iguales",
        "fr": "Les heures de début et de fin ne peuvent pas être identiques",
        "pt": "As horas de início e fim não podem ser iguais",
    },
    "end_before_start": {
        "de": "Endzeit darf nicht vor der Startzeit liegen (kein Übernachtfenster)",
        "en": "End time must not be before start time (no overnight window)",
        "es": "La hora de fin no puede ser anterior a la de inicio (sin franja nocturna)",
        "fr": "L'heure de fin ne doit pas précéder l'heure de début (pas de plage nocturne)",
        "pt": "A hora de fim não pode ser anterior à de início (sem janela noturna)",
    },
    "overlap": {
        "de": "Zeitfenster überschneidet sich mit Gruppe {other}",
        "en": "Time window overlaps with group {other}",
        "es": "La franja horaria se superpone con el grupo {other}",
        "fr": "La plage horaire chevauche le groupe {other}",
        "pt": "A janela horária sobrepõe-se ao grupo {other}",
    },
}

_NOTIFY_TEXTS = {
    "write_blocked": {
        "de": {
            "title": "Dyness Battery: Schreiben gesperrt",
            "message": (
                "Zu viele Schreibvorgänge in kurzer Zeit (≥ {max_calls} in {window_min} Min). "
                "Schreiben ist jetzt für {cooldown_min} Minuten gesperrt und deaktiviert. "
                "Bitte die auslösende Automation prüfen, danach switch.dyness_write_enabled "
                "wieder einschalten."
            ),
        },
        "en": {
            "title": "Dyness Battery: Writing locked",
            "message": (
                "Too many write operations in a short time (≥ {max_calls} in {window_min} min). "
                "Writing is now locked and disabled for {cooldown_min} minutes. "
                "Please check the triggering automation, then turn "
                "switch.dyness_write_enabled back on."
            ),
        },
        "es": {
            "title": "Dyness Battery: Escritura bloqueada",
            "message": (
                "Demasiadas escrituras en poco tiempo (≥ {max_calls} en {window_min} min). "
                "La escritura está ahora bloqueada y desactivada durante {cooldown_min} minutos. "
                "Revisa la automatización que lo provocó y luego vuelve a activar "
                "switch.dyness_write_enabled."
            ),
        },
        "fr": {
            "title": "Dyness Battery: Écriture bloquée",
            "message": (
                "Trop d'écritures en peu de temps (≥ {max_calls} en {window_min} min). "
                "L'écriture est désormais bloquée et désactivée pendant {cooldown_min} minutes. "
                "Vérifiez l'automatisation à l'origine, puis réactivez "
                "switch.dyness_write_enabled."
            ),
        },
        "pt": {
            "title": "Dyness Battery: Escrita bloqueada",
            "message": (
                "Muitas gravações em pouco tempo (≥ {max_calls} em {window_min} min). "
                "A escrita está agora bloqueada e desativada por {cooldown_min} minutos. "
                "Verifique a automação que causou isso e reative depois "
                "switch.dyness_write_enabled."
            ),
        },
    },
    "tou_invalid": {
        "de": {
            "title": "Dyness Battery: Zeitfenster nicht gesendet",
            "message": (
                "Folgende Zeitfenster waren unvollständig/unplausibel und wurden NICHT "
                "an Dyness gesendet: {details}. Bitte Werte korrigieren — beim nächsten "
                "Lesepoll werden die betroffenen Gruppen auf den letzten bekannten "
                "Gerätestand zurückgesetzt."
            ),
            "group_label": "Gruppe {group}",
        },
        "en": {
            "title": "Dyness Battery: Time windows not sent",
            "message": (
                "The following time windows were incomplete/implausible and were NOT "
                "sent to Dyness: {details}. Please correct the values — on the next "
                "poll, the affected groups will be reset to the last known device state."
            ),
            "group_label": "Group {group}",
        },
        "es": {
            "title": "Dyness Battery: Franjas horarias no enviadas",
            "message": (
                "Las siguientes franjas horarias estaban incompletas/no eran plausibles "
                "y NO se enviaron a Dyness: {details}. Corrige los valores — en el "
                "próximo sondeo, los grupos afectados volverán al último estado "
                "conocido del dispositivo."
            ),
            "group_label": "Grupo {group}",
        },
        "fr": {
            "title": "Dyness Battery: Plages horaires non envoyées",
            "message": (
                "Les plages horaires suivantes étaient incomplètes/peu plausibles et "
                "n'ont PAS été envoyées à Dyness : {details}. Merci de corriger les "
                "valeurs — au prochain sondage, les groupes concernés seront réinitialisés "
                "au dernier état connu de l'appareil."
            ),
            "group_label": "Groupe {group}",
        },
        "pt": {
            "title": "Dyness Battery: Janelas horárias não enviadas",
            "message": (
                "As seguintes janelas horárias estavam incompletas/implausíveis e NÃO "
                "foram enviadas para a Dyness: {details}. Corrija os valores — na "
                "próxima consulta, os grupos afetados serão repostos para o último "
                "estado conhecido do dispositivo."
            ),
            "group_label": "Grupo {group}",
        },
    },
    "tou_autostart": {
        "de": {
            "title": "Dyness Battery: Zeitfenster {group}",
            "message": (
                "Da Zeitfenster {group} noch AUS war, werden alle Daten 30 Sekunden "
                "gesammelt und dann zu Dyness gesendet. Dabei wird auch der Schalter "
                "mit aktiviert."
            ),
        },
        "en": {
            "title": "Dyness Battery: Time window {group}",
            "message": (
                "Since time window {group} was still OFF, all data will be collected "
                "for 30 seconds and then sent to Dyness. This will also turn on the "
                "switch."
            ),
        },
        "es": {
            "title": "Dyness Battery: Franja horaria {group}",
            "message": (
                "Como la franja horaria {group} seguía APAGADA, todos los datos se "
                "recopilarán durante 30 segundos y luego se enviarán a Dyness. Esto "
                "también activará el interruptor."
            ),
        },
        "fr": {
            "title": "Dyness Battery: Plage horaire {group}",
            "message": (
                "Comme la plage horaire {group} était encore désactivée, toutes les "
                "données seront collectées pendant 30 secondes puis envoyées à Dyness. "
                "Cela activera également l'interrupteur."
            ),
        },
        "pt": {
            "title": "Dyness Battery: Janela horária {group}",
            "message": (
                "Como a janela horária {group} ainda estava DESLIGADA, todos os dados "
                "serão recolhidos durante 30 segundos e depois enviados para a Dyness. "
                "Isto também ativará o interruptor."
            ),
        },
    },
    "refresh_reminder": {
        "de": {
            "title": "Dyness Battery: Serverdaten abgerufen",
            "message": (
                "Start/Ende/Leistung/Modus sowie Leistungsgrenze/Entladetiefe wurden neu "
                "von Dyness geholt.\n\n"
                "Hinweis: Der Schalter \"Zeitfenster X aktiv\" kann NICHT automatisch mit "
                "der App abgeglichen werden (dafür gibt es keinen zuverlässigen "
                "Auslesepunkt). Hast du ein Zeitfenster in der App deaktiviert? Dann "
                "bitte den zugehörigen Schalter hier in Home Assistant manuell von EIN "
                "auf AUS umstellen."
            ),
        },
        "en": {
            "title": "Dyness Battery: Server data fetched",
            "message": (
                "Start/end/power/mode as well as power limit/depth of discharge were "
                "re-fetched from Dyness.\n\n"
                "Note: The \"Time Window X Active\" switch CANNOT be automatically "
                "synced with the app (there is no reliable readout point for this). "
                "Did you disable a time window in the app? Then please manually switch "
                "the corresponding switch here in Home Assistant from ON to OFF."
            ),
        },
        "es": {
            "title": "Dyness Battery: Datos del servidor obtenidos",
            "message": (
                "Inicio/fin/potencia/modo, así como el límite de potencia/profundidad "
                "de descarga, se han vuelto a obtener de Dyness.\n\n"
                "Nota: El interruptor \"Franja horaria X activa\" NO se puede "
                "sincronizar automáticamente con la app (no existe un punto de lectura "
                "fiable para ello). ¿Has desactivado una franja horaria en la app? "
                "Entonces cambia manualmente el interruptor correspondiente aquí en "
                "Home Assistant de ENCENDIDO a APAGADO."
            ),
        },
        "fr": {
            "title": "Dyness Battery: Données serveur récupérées",
            "message": (
                "Début/fin/puissance/mode ainsi que limite de puissance/profondeur de "
                "décharge ont été récupérés à nouveau depuis Dyness.\n\n"
                "Remarque : l'interrupteur \"Plage horaire X active\" NE PEUT PAS être "
                "synchronisé automatiquement avec l'appli (aucun point de lecture fiable "
                "n'existe pour cela). Avez-vous désactivé une plage horaire dans "
                "l'appli ? Merci de basculer manuellement l'interrupteur correspondant "
                "ici dans Home Assistant de ACTIVÉ à DÉSACTIVÉ."
            ),
        },
        "pt": {
            "title": "Dyness Battery: Dados do servidor obtidos",
            "message": (
                "Início/fim/potência/modo, bem como limite de potência/profundidade de "
                "descarga, foram obtidos novamente da Dyness.\n\n"
                "Nota: O interruptor \"Janela horária X ativa\" NÃO pode ser "
                "sincronizado automaticamente com a app (não existe um ponto de leitura "
                "fiável para isso). Desativaste uma janela horária na app? Então muda "
                "manualmente o interruptor correspondente aqui no Home Assistant de "
                "LIGADO para DESLIGADO."
            ),
        },
    },
}


def _notify_lang(hass) -> str:
    lang = (getattr(hass.config, "language", None) or _NOTIFY_FALLBACK_LANG)[:2].lower()
    return lang if lang in ("de", "en", "es", "fr", "pt") else _NOTIFY_FALLBACK_LANG


def _notify_reason_text(hass, reason_key: str, **kwargs) -> str:
    lang = _notify_lang(hass)
    template = _NOTIFY_REASONS[reason_key].get(lang, _NOTIFY_REASONS[reason_key][_NOTIFY_FALLBACK_LANG])
    return template.format(**kwargs)


def _notify_text(hass, notify_key: str, **kwargs) -> tuple[str, str]:
    lang = _notify_lang(hass)
    entry = _NOTIFY_TEXTS[notify_key].get(lang, _NOTIFY_TEXTS[notify_key][_NOTIFY_FALLBACK_LANG])
    title = entry["title"].format(**kwargs)
    message = entry["message"].format(**kwargs)
    return title, message

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
# bei Sub-Modul-Calls. 1.5s (original) war zu konservativ.
_MIN_CALL_INTERVAL = 1.0
_RATE_LIMIT_BACKOFF = 10
_MAX_RETRIES = 3
# Sub-Modul-Calls: kein Retry bei 429 — sofort aufgeben und letzten bekannten Wert
# beibehalten. Retries mit langen Wartezeiten blockieren den gesamten Update-Zyklus
# und führen zu stagnierenden Sensoren.
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
SCHEMA_POWERBRICK    = "powerbrick"
SCHEMA_POWERBRICK_SC = "powerbrick_sc"
SCHEMA_CYGNI         = "cygni"
SCHEMA_UNKNOWN       = "unknown"

# Explizite Model → Schema Mapping
# Neue Modelle hier eintragen — kein Code-Logik-Anfassen nötig.
# Prefix-Match greift automatisch für Varianten (z.B. STACK100-12S, Cygni 5.0HS).
_MODEL_SCHEMA_MAP: dict[str, str] = {
    # Tower Familie
    "TOWER-T14":        SCHEMA_TOWER,   # modelCode 25
    "TOWER-T17":        SCHEMA_TOWER,   # modelCode 26
    "TOWER-PRO-TP7":    SCHEMA_TOWER,   # modelCode 7 "Tower Pro TP7"  → TOWER-PRO-TP7
    "TOWER-PRO-TP11":   SCHEMA_TOWER,   # "Tower Pro TP11" → TOWER-PRO-TP11
    "TOWER-PRO-TP15":   SCHEMA_TOWER,   # "Tower Pro TP15" → TOWER-PRO-TP15
    "TOWER-TP7":        SCHEMA_TOWER,   # Fallback falls API ohne "Pro"
    "TOWER-TP11":       SCHEMA_TOWER,
    "TOWER-TP15":       SCHEMA_TOWER,
    # Stack100 Familie
    "STACK100-7S":      SCHEMA_STACK100,   # modelCode 50
    "STACK100-8S":      SCHEMA_STACK100,
    "STACK100-10S":     SCHEMA_STACK100,   # modelCode 53
    # DL5 Familie
    "DL5.0C":           SCHEMA_DL5,   # modelCode 15
    # PowerBox G2
    "POWERBOX-G2":      SCHEMA_POWERBOX_G2,   # modelCode 42
    # PowerBox Pro / PowerHaus
    "POWERBOX-PRO":     SCHEMA_POWERBOX_PRO,   # modelCode 16
    "POWERHAUS":        SCHEMA_POWERBOX_PRO,   # modelCode 145
    # PowerDepot G2
    "POWERDEPOT-G2":    SCHEMA_POWERDEPOT,   # modelCode 144
    "POWERDEPOT-H5B":   SCHEMA_POWERDEPOT,   # modelCode 21
    # PowerBrick Familie
    "POWERBRICK-PRO":   SCHEMA_POWERBRICK,
    "POWERBRICK-SC":    SCHEMA_POWERBRICK_SC,   # modelCode 226
    "POWERBRICK-PLUS":  SCHEMA_POWERBRICK_SC,   # modelCode 328
    "POWERBRICK":       SCHEMA_POWERBRICK,   # modelCode 43
    # Junior Box
    "JUNIOR-BOX":       SCHEMA_JUNIOR,   # modelCode 1
    # Cygni Hybrid-Wechselrichter
    "CYGNI":            SCHEMA_CYGNI,   # Cygni 10.0HS-M8 modelCode 192
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
    if not model:
        # Leerer Modellname = transientes Startup-Problem (Rate-Limit beim ersten API-Call)
        # → kein echter Fehler, wird beim nächsten Zyklus automatisch korrigiert
        _LOGGER.debug(
            "Dyness: Modellname noch nicht verfügbar (Startup/Rate-Limit) — "
            "Schema-Erkennung via Points, wird beim nächsten Zyklus wiederholt."
        )
    else:
        # Echter unbekannter Modellname → WARNING, User soll Issue erstellen
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


# ── Control-Patch: Zeitfenster-Gruppen (TOU) ─────────────────────

def _hhmm_int_to_str(raw) -> str | None:
    """Wandelt einen rohen HHMM-Integer (z.B. 800 -> '08:00') in HH:mm um."""
    v = _to_float(raw)
    if v is None:
        return None
    v = int(v)
    hh, mm = divmod(v, 100)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return f"{hh:02d}:{mm:02d}"


def _tou_groups_overlap_errors(groups: dict) -> list[tuple[int, str, dict]]:
    """Prüft alle konfigurierten Zeitfenster auf Überschneidungen (cross-group).

    Unkonfigurierte Gruppen (power=0, 00:00/00:00) werden übersprungen.
    Gibt eine Liste von (group_num, reason_key, reason_kwargs) zurück — gleiche
    Signatur wie _tou_group_valid, damit der Aufrufer beides einheitlich behandeln kann.
    Jede überschneidende Gruppe wird genau einmal gemeldet (Paar A↔B → Fehler für A).
    """
    windows: list[tuple[int, int, int]] = []  # (start_min, end_min, group_num)
    for i in range(1, 5):
        g = groups.get(i, _DEFAULT_TOU_GROUP)
        power = int(g.get("power", 0))
        start = g.get("startTime", "00:00")
        end   = g.get("endTime",   "00:00")
        if power == 0 and start == "00:00" and end == "00:00":
            continue  # unkonfiguriert — von Überschneidungsprüfung ausgenommen
        try:
            sh, sm = map(int, start.split(":"))
            eh, em = map(int, end.split(":"))
        except (ValueError, AttributeError):
            continue
        windows.append((sh * 60 + sm, eh * 60 + em, i))

    errors: list[tuple[int, str, dict]] = []
    already_flagged: set[int] = set()
    for idx_a in range(len(windows)):
        s_a, e_a, g_a = windows[idx_a]
        for idx_b in range(idx_a + 1, len(windows)):
            s_b, e_b, g_b = windows[idx_b]
            if s_a < e_b and s_b < e_a:
                if g_a not in already_flagged:
                    errors.append((g_a, "overlap", {"other": g_b}))
                    already_flagged.add(g_a)
                if g_b not in already_flagged:
                    errors.append((g_b, "overlap", {"other": g_a}))
                    already_flagged.add(g_b)
    return errors


_DEFAULT_TOU_GROUP = {
    "state": "0", "startTime": "00:00", "endTime": "00:00",
    "power": 0, "mode": "16", "week": "0,1,2,3,4,5,6",
}


def _decode_tou_group(rt: dict, base_point: int) -> dict | None:
    """Dekodiert Start/Ende/Leistung/Modus einer der 4 Zeitfenster-Gruppen.

    base_point ist 8100 (Gruppe 1), 8500 (Gruppe 2), 8900 (Gruppe 3) oder 9300
    (Gruppe 4). Power-Wert = Rohwert // 32, untere 5 Bit = mode (16=Load
    Priority, 17=Battery Priority; 255=Shutdown passt nicht in 5 Bit und wird
    daher hier nicht erkannt — fällt dann auf "16" zurück).

    WICHTIG zu "State" (base_point selbst, z.B. "8100"): Praxistest zeigte den
    Wert 127 (=0b1111111, 7 gesetzte Bits) gleichzeitig bei ALLEN 4 Gruppen,
    unabhängig davon ob eine Gruppe per SetWorkModeSetting als state="0" oder
    "1" gesendet wurde — und unabhängig davon waren auch für "deaktivierte"
    Gruppen Start/Ende/Power weiterhin vollständig befüllt. Das spricht dafür,
    dass dieser Punkt tatsächlich eine Wochentage-Bitmaske ist (alle Gruppen
    wurden mit week="alle Tage" gesendet), NICHT der Ein/Aus-Zustand. Es gibt
    also aktuell keinen bekannten, verlässlichen Rohpunkt fürs Ein/Aus-Readback
    — diese Funktion liefert "state" deshalb bewusst NICHT mehr zurück; der
    Aufrufer muss den zuletzt bekannten/gesetzten state-Wert selbst beibehalten.
    """
    raw_start = rt.get(str(base_point + 100))
    raw_end = rt.get(str(base_point + 200))
    raw_power = rt.get(str(base_point + 300))
    if raw_power is None:
        return None
    power_raw = _to_float(raw_power)
    if power_raw is None:
        return None
    power_raw = int(power_raw)
    power = power_raw // 32
    mode_bits = power_raw % 32
    mode = str(mode_bits) if mode_bits in (16, 17) else "16"
    start = _hhmm_int_to_str(raw_start) or "00:00"
    end   = _hhmm_int_to_str(raw_end)   or "00:00"
    # Normalisierung: Wenn power=0 aber Zeiten gesetzt (inkonsistenter Gerätezustand,
    # z.B. deaktivierte Gruppe mit noch gespeicherten Zeiten), auf "unkonfiguriert"
    # normalisieren. Verhindert, dass ein schiefer Telemetrie-Import beim nächsten
    # Write aller 4 Gruppen die Validierung für eine unbeteiligte Gruppe bricht.
    if power == 0:
        start = "00:00"
        end   = "00:00"
    return {
        "startTime": start,
        "endTime":   end,
        "power": power,
        "mode":  mode,
    }


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
        config_entry=entry,
    )
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    from .services import async_setup_services
    await async_setup_services(hass)
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
        # Services sind domain-weit (nicht per Entry) — erst entfernen wenn
        # kein weiterer Entry mehr aktiv ist, damit andere Geräte sie noch nutzen können.
        if not hass.data.get(DOMAIN):
            from .services import SERVICE_SET_BASE_SETTING, SERVICE_SET_WORK_SCHEDULE
            for svc in (SERVICE_SET_BASE_SETTING, SERVICE_SET_WORK_SCHEDULE):
                if hass.services.has_service(DOMAIN, svc):
                    hass.services.async_remove(DOMAIN, svc)
    return unload_ok


class DynessDataCoordinator(DataUpdateCoordinator):

    def __init__(self, hass, api_id, api_secret, api_base,
                 device_sn=None, dongle_sn=None, config_entry=None):
        super().__init__(hass, _LOGGER, name=DOMAIN,
                         update_interval=timedelta(minutes=5))
        self.api_id       = api_id
        self.api_secret   = api_secret
        self.api_base     = api_base
        self.device_sn    = device_sn
        self.dongle_sn    = dongle_sn
        self.config_entry = config_entry

        self.station_info  = {}
        self.device_info   = {}
        self.storage_info  = {}
        self.realtime_data = {}
        self.module_data: dict[str, dict] = {}  # mid → Sensordaten
        self.running_data: dict = {}             # getLastRunningDataBySn

        self._bound: bool = False
        self._bound_sns: set = set()  # Bereits gebundene Sub-Modul SNs
        # Bekannte Sub-Modul-SNs — werden aus config_entry.data vorgeladen damit
        # nach einem HA-Neustart sofort alle Module abgefragt werden können, ohne
        # auf die nächste vollständige SUB-Antwort der API warten zu müssen.
        _persisted = config_entry.data.get("known_module_sns", []) if config_entry else []
        self._module_sns: list[str] = list(_persisted)
        # known_single_sub_sn wird bewusst nicht persistiert — wird nach dem
        # ersten erfolgreichen Poll aus SUB gesetzt. Persistenz würde
        # async_update_entry aus _async_update_data erfordern.
        self._single_sub_sn: str | None = None
        self._last_call_time: float = 0.0
        self._storage_list_cycle: int = 0  # Zähler für storage/list Throttling
        # Optimierung: getLastRunningDataBySn überspringen wenn einmal
        # alle Felder null waren — typisch bei reinen Batteriesystemen ohne Wechselrichter.
        # Wird auf False zurückgesetzt wenn sich das Gerät-Schema ändert (z.B. Wechselrichter
        # nachgerüstet), was einen HA-Reload erfordert.
        self._running_data_all_null: bool = False
        # BMS-Einträge in storage_list für batteryCapacity-Korrektur
        self._storage_list_total: int = 0
        self._storage_list_bms_count: int = 0
        # Alarm-Delay — Zeitpunkt des ersten Auftretens pro Alarm-Label
        self._alarm_first_seen: dict[str, datetime] = {}

        # ── Control-Patch: TOU-Zeitgruppen (SCHEMA_JUNIOR) ──────────────────
        self.tou_groups: dict[int, dict] = {i: dict(_DEFAULT_TOU_GROUP) for i in range(1, 5)}
        # _tou_pending[i] = "HA hat diese Gruppe mindestens einmal erfolgreich
        # geschrieben" — bleibt danach DAUERHAFT True (kein Reset über Zeit/
        # Abgleich). Dyness übernimmt Änderungen laut Praxistest quasi sofort;
        # das realTime/data-Readback selbst ist aber unzuverlässig/flackrig
        # (vermutlich gecachte/lastverteilte Replikate) und wird deshalb für
        # bereits von HA kontrollierte Felder komplett ignoriert. Nur bei einem
        # fehlgeschlagenen Schreibvorgang wird wieder auf Telemetrie vertraut.
        # Ein HA-Neustart setzt den Zustand zurück (erneuter Telemetrie-Import).
        self._tou_pending: dict[int, bool] = {i: False for i in range(1, 5)}
        self._tou_write_unsub: dict[int, callable] = {i: None for i in range(1, 5)}
        # Merkt sich "state" vom Beginn eines Bearbeitungszyklus (erster Edit
        # nach abgelaufenem/keinem Timer), damit eine automatische Aktivierung
        # bei fehlgeschlagener Validierung wieder zurückgenommen werden kann.
        # Der Schalter soll sich für den Nutzer NICHT sichtbar ändern,
        # wenn am Ende gar nichts gesendet wird.
        self._tou_state_before_edit: dict[int, str | None] = {i: None for i in range(1, 5)}

        # ── Control-Patch: Basis-Einstellung (SetBaseSetting) ───────────────
        self.base_setting: dict = {"work_mode": _FIXED_WORK_MODE, "power_limit": 800, "discharge_depth": 70}
        self._base_setting_pending: bool = False  # gleiche Semantik wie _tou_pending
        self._base_setting_write_unsub = None

        # ── Control-Patch: Write-Guard (Rate-Limit/Circuit-Breaker) ─────────
        self.write_enabled: bool = False  # startet NACH JEDEM Neustart bewusst aus
        self._write_timestamps: list[float] = []
        self._write_blocked_until: float | None = None

    async def _guard_check_and_record(self) -> None:
        """Prüft, ob geschrieben werden darf. Wirft HomeAssistantError wenn nicht.

        Wird ausschließlich von async_set_base_setting()/async_set_work_schedule()
        aufgerufen - NIE vom Lese-Update-Zyklus. Lesen funktioniert also immer,
        unabhängig vom Zustand dieses Guards.
        """
        now = _time.monotonic()

        if self._write_blocked_until and now < self._write_blocked_until:
            remaining = int(self._write_blocked_until - now)
            raise HomeAssistantError(
                f"Dyness: Schreiben ist wegen zu vieler Schreibversuche noch "
                f"{remaining}s gesperrt (Circuit-Breaker)."
            )
        self._write_blocked_until = None

        if not self.write_enabled:
            raise HomeAssistantError(
                "Dyness: Schreiben ist deaktiviert. Bitte "
                "switch.dyness_write_enabled einschalten."
            )

        self._write_timestamps = [
            t for t in self._write_timestamps if now - t < WRITE_WINDOW_SECONDS
        ]

        if len(self._write_timestamps) >= WRITE_MAX_CALLS:
            self._write_blocked_until = now + WRITE_COOLDOWN_SECONDS
            self.write_enabled = False
            _LOGGER.error(
                "Dyness: Schreib-Ratenlimit erreicht (%d Schreibvorgänge in %ds). "
                "Schreiben für %ds gesperrt und deaktiviert.",
                WRITE_MAX_CALLS, WRITE_WINDOW_SECONDS, WRITE_COOLDOWN_SECONDS,
            )
            try:
                title, message = _notify_text(
                    self.hass, "write_blocked",
                    max_calls=WRITE_MAX_CALLS,
                    window_min=WRITE_WINDOW_SECONDS // 60,
                    cooldown_min=WRITE_COOLDOWN_SECONDS // 60,
                )
                await self.hass.services.async_call(
                    "persistent_notification", "create",
                    {
                        "title": title,
                        "message": message,
                        "notification_id": "dyness_write_blocked",
                    },
                    blocking=False,
                )
            except Exception:  # noqa: BLE001
                pass
            self.async_update_listeners()
            raise HomeAssistantError("Dyness: Schreib-Ratenlimit erreicht, Schreiben gesperrt.")

        self._write_timestamps.append(now)

    async def async_set_base_setting(self, work_mode: str, power_limit: str,
                                       discharge_depth: str) -> dict:
        """Setzt Leistungsgrenze und Entladetiefe (+ Pflichtfeld workMode).

        POST /v2/SetBaseSetting
        work_mode: laut PDF Pflichtfeld ("0"/"1"/"3"/"6"), auf der Junior Box
                   aber bestätigt wirkungslos — wird nur strukturell benötigt.
        power_limit: Leistung in W (152-800, App-Limit)
        discharge_depth: Entladetiefe in % (20-100, App-Limit)
        """
        await self._guard_check_and_record()
        if str(work_mode) not in VALID_WORK_MODES:
            raise HomeAssistantError(
                "Dyness: Ungültiger workMode-Wert (nur 0/1/3/6 erlaubt). Das Feld "
                "hat auf der Junior Box bestätigt keine Wirkung und wird nur aus "
                "Pflichtfeld-Gründen mitgeschickt."
            )
        dd = _to_float(discharge_depth)
        if dd is None or not (DOD_MIN <= dd <= DOD_MAX):
            raise HomeAssistantError(
                f"Dyness: Entladetiefe (DOD) muss zwischen {DOD_MIN}% und {DOD_MAX}% "
                f"liegen (App-Limit) — erhalten: {discharge_depth}."
            )
        pl = _to_float(power_limit)
        if pl is None or not (POWER_LIMIT_MIN <= pl <= POWER_LIMIT_MAX):
            raise HomeAssistantError(
                f"Dyness: Leistungsgrenze muss zwischen {POWER_LIMIT_MIN}W und "
                f"{POWER_LIMIT_MAX}W liegen (App-Limit) — erhalten: {power_limit}."
            )
        if int(pl) % POWER_STEP != 0:
            raise HomeAssistantError(
                f"Dyness: Leistungsgrenze muss ein Vielfaches von {POWER_STEP}W sein "
                f"(z.B. {POWER_LIMIT_MIN}, {POWER_LIMIT_MIN+POWER_STEP}, ...) — "
                f"erhalten: {power_limit}."
            )
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(self.hass)
        body = {
            "deviceSn": self.device_sn,
            "workMode": str(work_mode),
            "powerLimit": str(power_limit),
            "dischargeDepth": str(discharge_depth),
        }
        result = await self._call(session, "/v2/SetBaseSetting", body)
        _LOGGER.info("Dyness SetBaseSetting: %s -> %s", body, result)
        if not _is_success(result):
            raise HomeAssistantError(f"Dyness SetBaseSetting fehlgeschlagen: {result}")
        # KEIN async_request_refresh() hier — Lesen und Schreiben bleiben getrennt.
        # Ein sofortiger Refresh käme oft zu früh (Gerät hat die Änderung intern
        # noch nicht übernommen) und verschiebt zusätzlich den nächsten planmäßigen
        # Poll-Zyklus. Der normale Update-Intervall übernimmt die Bestätigung.
        return result

    async def async_set_work_schedule(self, groups: list[dict]) -> dict:
        """Setzt die 4 Zeitfenster-Gruppen.

        POST /v2/SetWorkModeSetting
        groups: Liste von genau 4 dicts mit batteryWorkGroup/state/mode/
                startTime/endTime/power/week (alle Werte als String)
        """
        await self._guard_check_and_record()
        if len(groups) != 4:
            raise HomeAssistantError(
                "Es müssen genau 4 Zeitfenster-Gruppen (1-4) übergeben werden."
            )
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(self.hass)
        body = {"deviceSn": self.device_sn, "workGroups": groups}
        result = await self._call(session, "/v2/SetWorkModeSetting", body)
        _LOGGER.info("Dyness SetWorkModeSetting: %s -> %s", body, result)
        if not _is_success(result):
            raise HomeAssistantError(f"Dyness SetWorkModeSetting fehlgeschlagen: {result}")
        # KEIN async_request_refresh() hier.
        return result

    async def snapshot_tou_state_before_edit(self, group_num: int) -> None:
        """MUSS von Entities als ALLERERSTE Zeile aufgerufen werden (mit await),
        bevor sie tou_groups[group_num] mutieren (auch vor einer Auto-Aktivierung).

        Nur wirksam, wenn gerade kein Sende-Timer für diese Gruppe läuft (=
        frischer Bearbeitungszyklus).

        War die Gruppe vor diesem Zyklus AUS, wird zusätzlich einmalig eine
        Persistent Notification angezeigt, die auf die anstehende Auto-
        Aktivierung hinweist.
        """
        if not self._tou_write_unsub.get(group_num):
            prev = self.tou_groups.get(group_num, {}).get("state", "0")
            self._tou_state_before_edit[group_num] = prev
            if prev != "1":
                try:
                    title, message = _notify_text(self.hass, "tou_autostart", group=group_num)
                    await self.hass.services.async_call(
                        "persistent_notification", "create",
                        {
                            "title": title,
                            "message": message,
                            "notification_id": f"dyness_tou_autostart_{group_num}",
                        },
                        blocking=False,
                    )
                except Exception:  # noqa: BLE001
                    pass

    def schedule_tou_write(self, group_num: int, delay: float = 30.0) -> None:
        """Plant das Senden ALLER 4 Gruppen nach kurzer Verzögerung (Debounce)."""
        self._tou_pending[group_num] = True
        if self._tou_write_unsub.get(group_num):
            self._tou_write_unsub[group_num]()

        async def _do_write(_now) -> None:
            self._tou_write_unsub[group_num] = None

            # Vollvalidierung ALLER 4 Gruppen vor dem Senden: die API verlangt
            # immer alle 4 zusammen, daher muss jede einzelne entweder komplett
            # unkonfiguriert oder vollständig plausibel sein.
            # Bei Verstoß: gar nichts senden, betroffene Gruppe(n) wieder für
            # Telemetrie-Readback freigeben (revertiert den unplausiblen Wert
            # beim nächsten Lesepoll automatisch) UND den Schalter "aktiv"
            # sofort auf den Zustand vor dieser Bearbeitung zurücksetzen,
            # falls er durch Auto-Aktivierung verändert wurde.
            # Der Nutzer soll keine Schalter-Änderung sehen, wenn am Ende gar
            # nichts gesendet wird.
            invalid = []
            for i in range(1, 5):
                g = self.tou_groups.get(i, _DEFAULT_TOU_GROUP)
                ok, reason_key, reason_kwargs = _tou_group_valid(g)
                if not ok:
                    invalid.append((i, reason_key, reason_kwargs))
            # Überschneidungsprüfung (cross-group): nur wenn Einzel-Validierung OK
            if not invalid:
                invalid.extend(_tou_groups_overlap_errors(self.tou_groups))
            if invalid:
                # Für das Log reicht Deutsch/Reason-Key, für die Notification
                # wird pro Systemsprache übersetzt.
                _LOGGER.error(
                    "Dyness: Zeitfenster-Sendung abgebrochen — %s",
                    "; ".join(f"Gruppe {i}: {rk}" for i, rk, _ in invalid),
                )
                reverted_any = False
                # Alle Gruppen mit Snapshot zurücksetzen: entweder sie waren
                # selbst invalid ODER sie waren die auslösende Gruppe und
                # wurden nie gesendet. Gruppen ohne Snapshot (nie in diesem
                # Zyklus bearbeitet) bleiben unverändert.
                invalid_nums = {i for i, _, _ in invalid}
                revert_candidates = invalid_nums | {group_num}
                for i in revert_candidates:
                    self._tou_pending[i] = False
                    prev_state = self._tou_state_before_edit.get(i)
                    if prev_state is not None and self.tou_groups.get(i, {}).get("state") != prev_state:
                        self.tou_groups[i]["state"] = prev_state
                        reverted_any = True
                if reverted_any:
                    self.async_update_listeners()  # Schalter sofort in der GUI aktualisieren
                try:
                    lang = _notify_lang(self.hass)
                    group_label_tpl = _NOTIFY_TEXTS["tou_invalid"].get(
                        lang, _NOTIFY_TEXTS["tou_invalid"][_NOTIFY_FALLBACK_LANG]
                    )["group_label"]
                    details = "; ".join(
                        f"{group_label_tpl.format(group=i)}: {_notify_reason_text(self.hass, rk, **kw)}"
                        for i, rk, kw in invalid
                    )
                    title, message = _notify_text(self.hass, "tou_invalid", details=details)
                    await self.hass.services.async_call(
                        "persistent_notification", "create",
                        {
                            "title": title,
                            "message": message,
                            "notification_id": "dyness_tou_invalid",
                        },
                        blocking=False,
                    )
                except Exception:  # noqa: BLE001
                    pass
                return

            groups_payload = []
            for i in range(1, 5):
                g = self.tou_groups.get(i, _DEFAULT_TOU_GROUP)
                groups_payload.append({
                    "batteryWorkGroup": str(i),
                    "state": g["state"],
                    "mode": g["mode"],
                    "startTime": g["startTime"],
                    "endTime": g["endTime"],
                    "power": str(g["power"]),
                    "week": g.get("week", "0,1,2,3,4,5,6"),
                })
            try:
                await self.async_set_work_schedule(groups_payload)
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Dyness: Auto-Write Zeitgruppen fehlgeschlagen: %s", err)
                # Fehlgeschlagen (z.B. Write-Guard blockiert, Netzwerkfehler):
                # nichts wurde übernommen. Pending freigeben, DAMIT AUCH der
                # "aktiv"-Schalter auf den Zustand vor dieser Bearbeitung
                # zurückgesetzt wird - sonst bliebe er fälschlich auf EIN
                # hängen, obwohl nie etwas gesendet wurde.
                self._tou_pending[group_num] = False
                prev_state = self._tou_state_before_edit.get(group_num)
                if prev_state is not None and self.tou_groups.get(group_num, {}).get("state") != prev_state:
                    self.tou_groups[group_num]["state"] = prev_state
                    self.async_update_listeners()
                return
            # Erfolgreich gesendet: Pending bleibt DAUERHAFT True, kein erneutes
            # Auslesen mehr für diese Gruppe.

        self._tou_write_unsub[group_num] = async_call_later(self.hass, delay, _do_write)

    def schedule_base_setting_write(self, delay: float = 30.0) -> None:
        """Plant das Senden von power_limit/discharge_depth (Debounce)."""
        self._base_setting_pending = True
        if self._base_setting_write_unsub:
            self._base_setting_write_unsub()

        async def _do_write(_now) -> None:
            self._base_setting_write_unsub = None
            try:
                await self.async_set_base_setting(
                    work_mode=str(self.base_setting["work_mode"]),
                    power_limit=str(self.base_setting["power_limit"]),
                    discharge_depth=str(self.base_setting["discharge_depth"]),
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Dyness: Auto-Write Basis-Einstellung fehlgeschlagen: %s", err)
                self._base_setting_pending = False
                return
            # Erfolgreich: Pending bleibt DAUERHAFT True, kein erneutes Auslesen.

        self._base_setting_write_unsub = async_call_later(self.hass, delay, _do_write)

    async def _call(self, session: aiohttp.ClientSession, path: str, body_dict: dict,
                    max_retries: int = _MAX_RETRIES) -> dict:
        """Rate-limitierter API-Aufruf mit optionalem Retry bei HTTP 429.
        
        max_retries=0 für Sub-Modul-Calls: sofort aufgeben bei 429 statt lange
        zu warten und den Update-Zyklus zu blockieren.
        """
        elapsed = _time.monotonic() - self._last_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            await asyncio.sleep(_MIN_CALL_INTERVAL - elapsed)
        url = f"{self.api_base}/openapi/ems-device{path}"
        body = json.dumps(body_dict, separators=(',', ':'))
        for attempt in range(max_retries + 1):
            self._last_call_time = _time.monotonic()
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
                    try:
                        return json.loads(raw_text)
                    except json.JSONDecodeError:
                        # Leere oder ungültige Antwort (z.B. Serverausfall) —
                        # {} zurückgeben statt Exception um Retry-Verzögerungen
                        # bei Sub-Modul-Calls zu vermeiden
                        _LOGGER.warning(
                            "Dyness %s: Leere oder ungültige Antwort vom Server "
                            "(vermutlich temporäre Serverunterbrechung)", path
                        )
                        return {}
            except aiohttp.ClientError as e:
                _LOGGER.warning("Dyness %s Verbindungsfehler (Versuch %d/%d): %s",
                                path, attempt + 1, max_retries + 1, e)
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

    async def _persist_module_sns(self, entry, merged: list[str]) -> None:
        """Persistiert die bekannten Sub-Modul-SNs in config_entry.data.

        Wird bewusst via async_create_task aufgerufen (nicht direkt aus
        _async_update_data), damit async_update_entry nicht im selben
        Event-Loop-Tick wie der Coordinator-Update landet und den Schedule
        destabilisiert.
        """
        try:
            self.hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, "known_module_sns": merged},
            )
            _LOGGER.debug(
                "Dyness: known_module_sns persistiert: %s", merged
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Dyness: known_module_sns konnte nicht gespeichert werden: %s", err
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
                                # BMS-Einträge zählen für batteryCapacity-Korrektur
                                # Dyness zählt den BMS-Koordinator fälschlicherweise als Batterie
                                # und gibt batteryCapacity × (n_batteries + 1) zurück
                                self._storage_list_total = len(device_list)
                                self._storage_list_bms_count = sum(
                                    1 for d in device_list
                                    if str(d.get("deviceSn", "")).endswith(_BMS_SUFFIXES)
                                )
                                if self._storage_list_bms_count > 0:
                                    _LOGGER.debug(
                                        "Dyness storage/list: %d Geräte total, %d BMS-Einträge "
                                        "(batteryCapacity wird korrigiert)",
                                        self._storage_list_total, self._storage_list_bms_count
                                    )
                        except Exception as e:
                            err_msg = str(e)
                            if "Expecting value" in err_msg or "char 0" in err_msg:
                                _LOGGER.warning(
                                    "Dyness storage/list: Leere oder ungültige Antwort vom Server "
                                    "(vermutlich temporäre Serverunterbrechung)"
                                )
                            else:
                                _LOGGER.warning("Dyness storage/list: Fehler beim Abrufen: %s", e)

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
                            # SUB wird als additive Quelle behandelt — neue Module werden
                            # in die bekannte Menge aufgenommen, aber nie entfernt.
                            # Hintergrund: Die Dyness-API liefert SUB intermittierend
                            # unvollständig. Ein Replace würde bekannte Module
                            # für mehrere Stunden aus dem Poll-Loop werfen.
                            # Bekannte SNs werden in config_entry.data persistiert, damit
                            # nach einem HA-Neustart sofort alle Module abgefragt werden.
                            sub_raw = self.realtime_data.get("SUB", "")
                            if sub_raw:
                                candidates = [s.strip() for s in str(sub_raw).split(",") if s.strip()]
                                candidates = [
                                    s for s in candidates
                                    if not s.endswith(_BMS_SUFFIXES)
                                ]
                                if len(candidates) > 1:
                                    # Mehrere Module: Union mit bekannter Liste
                                    merged = sorted(set(self._module_sns) | set(candidates))
                                    if set(merged) != set(self._module_sns):
                                        _LOGGER.info(
                                            "Dyness: Sub-Module aktualisiert: %s → %s",
                                            self._module_sns, merged,
                                        )
                                        self._module_sns = merged
                                        self._update_scan_interval()
                                        # async_update_entry NICHT direkt aus
                                        # _async_update_data aufrufen — HA feuert
                                        # intern CONFIG_ENTRY_CHANGED im selben
                                        # Event-Loop-Tick und kann den Coordinator-
                                        # Schedule destabilisieren. Stattdessen in
                                        # den nächsten Tick verschieben.
                                        if self.config_entry is not None:
                                            _entry = self.config_entry
                                            _merged = merged
                                            self.hass.async_create_task(
                                                self._persist_module_sns(_entry, _merged)
                                            )
                                elif candidates and not self._module_sns:
                                    # Einzelnes Sub-Modul — kein separates Device,
                                    # aber Zellspannungen via direktem v1-Call.
                                    # Nicht persistiert: wird nach dem ersten Poll
                                    # zuverlässig aus SUB gesetzt, Persistenz würde
                                    # async_update_entry aus _async_update_data
                                    # heraus erfordern.
                                    sn = candidates[0]
                                    if self._single_sub_sn != sn:
                                        self._single_sub_sn = sn
                                        _LOGGER.debug(
                                            "Dyness: Einzelnes Sub-Modul erkannt: %s "
                                            "(Zellspannungen via direktem Abruf)", sn,
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
                                # 429 bei Sub-Modul ist nicht kritisch (Rate-Limit bei
                                # vielen gleichzeitigen Koordinatoren) → DEBUG statt WARNING
                                _LOGGER.debug("Dyness Modul %s: Code %s", sn, code)
                                # Bei 429 oder anderen Fehlern: alten Wert beibehalten statt
                                # das Modul aus module_data zu entfernen
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
                    # Optimierung: Nach erstem All-Null-Response überspringen.
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

                    _power_data_list = data if isinstance(data, list) else []
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
                    # storage_info als Fallback wenn device_info beim Start rate-limited war
                    schema = _detect_schema(
                        self.device_info.get("deviceModelName", "")
                        or self.storage_info.get("deviceModelName", ""),
                        rt
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

                    # BMS-Koordinator in storage_list → Dyness zählt ihn als Batterie
                    # Dyness liefert: batteryCapacity = (n_real + n_bms) × kWh_pro_Einheit
                    # Korrekt: Gesamtwert direkt mit Faktor real/total setzen.
                    # WICHTIG: bc_single wird NICHT verändert — nachfolgende × n_modules
                    # Logik wird per Flag übersprungen damit kein doppelter Korrekturfaktor entsteht.
                    _bc_from_bms_fix = False
                    if (bc_single is not None
                            and self._storage_list_bms_count > 0
                            and self._storage_list_total > self._storage_list_bms_count):
                        _real = self._storage_list_total - self._storage_list_bms_count
                        data["batteryCapacity"] = round(
                            bc_single * _real / self._storage_list_total, 3
                        )
                        _bc_from_bms_fix = True
                        _LOGGER.debug(
                            "Dyness batteryCapacity: BMS-Korrektur (%d BMS / %d Geräte) "
                            "→ %s kWh (war: %s kWh)",
                            self._storage_list_bms_count, self._storage_list_total,
                            data["batteryCapacity"], bc_single,
                        )

                    n_modules = max(len(self._module_sns), 1)
                    is_tower_schema = schema == SCHEMA_TOWER
                    if schema in (SCHEMA_STACK100, SCHEMA_TOWER):
                        # Wird unten von Point 1700 überschrieben — hier nur Initialwert
                        data["batteryCapacity"] = bc_single
                    elif _bc_from_bms_fix:
                        # Gesamtwert bereits korrekt gesetzt — × n_modules überspringen
                        pass
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
                        # SOC aus Point 1400 (live, pointNameCn="SOC"),
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
                        if rt.get("800") is not None:
                            data["soc"] = rt.get("800")
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

                        # PV-Daten — nur für Junior Box (nicht DL5.0C)
                        if schema == SCHEMA_JUNIOR:
                            data["pvVoltage"]      = rt.get("4600")
                            data["pvCurrent"]      = rt.get("4700")
                            data["pvPower"]        = rt.get("4800")
                            data["pvEnergyTotal"]  = rt.get("7500")
                            data["pvEnergyToday"]  = rt.get("7600")
                            data["outEnergyTotal"] = rt.get("7700")
                            data["outEnergyToday"] = rt.get("7800")

                            # Control-Patch: TOU-Zeitgruppen Readback (NUR startTime/
                            # endTime/power/mode — "state" und "week" sind aus
                            # realTime/data nicht zuverlässig auslesbar und bleiben daher
                            # exklusiv in der Hand von Entities/Services/Automationen.
                            #
                            # Sobald HA eine Gruppe einmal erfolgreich geschrieben hat
                            # (_tou_pending[i] == True), wird sie hier DAUERHAFT nicht
                            # mehr aus der Telemetrie überschrieben
                            # im __init__ der Coordinator-Klasse (Dyness übernimmt
                            # Schreibvorgänge quasi sofort, aber das Readback selbst
                            # ist unzuverlässig/flackrig).
                            for i, base in enumerate((8100, 8500, 8900, 9300), start=1):
                                if self._tou_pending.get(i):
                                    continue
                                g = _decode_tou_group(rt, base)
                                if not g:
                                    continue
                                existing = self.tou_groups.get(i, dict(_DEFAULT_TOU_GROUP))
                                g["state"] = existing.get("state", "0")
                                g["week"] = existing.get("week", "0,1,2,3,4,5,6")
                                self.tou_groups[i] = g

                            if not self._base_setting_pending:
                                # Punkt "6400" wird NICHT mehr als work_mode-Readback
                                # verwendet — Praxistest zeigte einen Wert ("16"), der
                                # nicht im gültigen SetBaseSetting-Enum (0/1/3/6) liegt,
                                # UND ein isolierter Test bestätigte: workMode hat auf
                                # der Junior Box ohnehin keine Wirkung.
                                bpl = _to_float(rt.get("6500"))
                                bdd = _to_float(rt.get("6600"))
                                if bpl is not None and 0 <= bpl <= 800:
                                    self.base_setting["power_limit"] = int(bpl)
                                if bdd is not None and 0 <= bdd <= 100:
                                    self.base_setting["discharge_depth"] = int(bdd)
                    elif schema == SCHEMA_POWERBOX_PRO:
                        # PowerBox Pro / PowerHaus Schema
                        # batteryCapacity aus station/info = Gesamtkapazität direkt
                        # (kein × n_modules — unabhängig von Modulanzahl)
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
                        # Tower Schema (Tower T14/T17/T21 + Tower Pro TP7/TP11/TP15)
                        data["soc"]                   = rt.get("1400")
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
                            # Tower T14 Alarm-Schema
                            data["alarmSpreadV"]  = str(rt.get("5001", "0")) == "1"
                            data["alarmSpreadT"]  = str(rt.get("5002", "0")) == "1"
                            data["alarmInsul"]    = str(rt.get("5003", "0")) == "1"
                            data["alarmAfe"]      = str(rt.get("5101", "0")) == "1"
                            data["alarmBms"]      = str(rt.get("5102", "0")) == "1"
                            data["alarmSys"]      = str(rt.get("5104", "0")) == "1"
                            data["alarmTotal"]    = rt.get("9999999")

                    elif schema == SCHEMA_POWERBOX_G2:
                        # PowerBox G2 Schema — zwei API-Varianten bekannt:
                        #
                        # Variante A (600-Serie)
                        # Variante B (10xxx-Serie)
                        #
                        # Erkennung: Point 600 vorhanden → Variante A, sonst Variante B
                        _g2_var_a = rt.get("600") is not None and rt.get("600") != ""

                        if _g2_var_a:
                            # Variante A: 600-Serie
                            data["packVoltage"] = rt.get("600") if rt.get("600") is not None else data.get("packVoltage")
                            if rt.get("800") is not None:
                                data["soc"] = rt.get("800")
                            if rt.get("700") is not None:
                                data["realTimeCurrent"] = rt.get("700")
                            # realTimePower aus V×I — verhindert stale-Wert aus getLastPowerDataBySn
                            _v_g2 = _to_float(rt.get("600"))
                            _i_g2 = _to_float(rt.get("700"))
                            if _v_g2 is not None and _i_g2 is not None:
                                data["realTimePower"] = round(_v_g2 * _i_g2, 1)
                            if _i_g2 is not None:
                                if _i_g2 > 1.0:
                                    data["workStatus"] = "Charging"
                                elif _i_g2 < -1.0:
                                    data["workStatus"] = "Discharging"
                                else:
                                    data["workStatus"] = "Standby"
                            data["soh"]          = rt.get("1200")
                            data["tempBmsMax"]   = rt.get("2800")
                            data["tempBmsMin"]   = rt.get("3000")
                            data["tempMosfet"]   = rt.get("2300")
                            data["tempMax"]      = rt.get("1800")
                            data["tempMin"]      = rt.get("2000")
                            data["temp"]         = rt.get("1800")
                            data["alarmStatus1"] = rt.get("3200")
                            data["alarmStatus2"] = rt.get("3300")
                            data["alarmTotal"]   = rt.get("4100")
                            vmax_g2 = _to_float(rt.get("1300"))
                            vmin_g2 = _to_float(rt.get("1500"))
                            if vmax_g2 is not None and vmax_g2 > 0:
                                data["cellVoltageMax"] = vmax_g2
                            if vmin_g2 is not None and vmin_g2 > 0:
                                data["cellVoltageMin"] = vmin_g2
                            if vmax_g2 is not None and vmin_g2 is not None and vmax_g2 > 0 and vmin_g2 > 0:
                                data["cellVoltageDiffMv"] = round((vmax_g2 - vmin_g2) * 1000, 1)
                            data["cellVoltageMaxModule"] = rt.get("1401")
                            data["cellVoltageMaxCell"]   = rt.get("1402")
                            data["cellVoltageMinModule"] = rt.get("1601")
                            data["cellVoltageMinCell"]   = rt.get("1602")
                            cv = _to_float(rt.get("3600"))
                            dv = _to_float(rt.get("3700"))
                            cl = _to_float(rt.get("3800"))
                            dl = _to_float(rt.get("3900"))
                            if cv is not None and cv > 0:
                                data["chargeVoltageLimit"]    = cv
                            if dv is not None and dv > 0:
                                data["dischargeVoltageLimit"] = dv
                            if cl is not None:
                                data["chargeCurrentLimit"]    = cl
                            if dl is not None and dl > 0:
                                data["dischargeCurrentLimit"] = dl

                        else:
                            # Variante B: 10xxx-Serie
                            data["packVoltage"] = rt.get("13500") if rt.get("13500") is not None else data.get("packVoltage")
                            if rt.get("13400") is not None:
                                data["realTimeCurrent"] = rt.get("13400")
                            data["cycleCount"] = rt.get("13900")
                            bms_temp = _to_float(rt.get("12400"))
                            if bms_temp is not None:
                                data["tempBmsMax"] = bms_temp
                            cell_temps_g2b = [
                                _to_float(rt.get(str(12500 + i * 100)))
                                for i in range(4)
                            ]
                            valid_g2b = [t for t in cell_temps_g2b if t is not None and t > 0]
                            if valid_g2b:
                                data["tempMax"] = max(valid_g2b)
                                data["tempMin"] = min(valid_g2b) if len(valid_g2b) > 1 else None
                            cells_g2b = []
                            for i in range(1, 17):
                                v = _to_float(rt.get(str(10200 + i * 100)))
                                if v is not None and v > 0:
                                    cells_g2b.append(v)
                                    data[f"cellVoltage{i:02d}"] = v
                            if cells_g2b:
                                data["cellVoltageMax"]    = max(cells_g2b)
                                data["cellVoltageMin"]    = min(cells_g2b)
                                data["cellVoltageDiffMv"] = round((max(cells_g2b) - min(cells_g2b)) * 1000, 1)
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

                        # Kapazität (beide Varianten)
                        bc  = _to_float(data.get("batteryCapacity"))
                        soc = _to_float(data.get("soc"))
                        soh = _to_float(data.get("soh"))
                        if bc is not None and soc is not None:
                            soh_factor = (soh / 100) if (soh is not None and soh <= 100) else 1.0
                            data["usableKwh"]    = round(bc * soh_factor, 3)
                            data["remainingKwh"] = round(bc * soh_factor * soc / 100, 3)

                        _LOGGER.debug(
                            "Dyness PowerBox G2 (Variante %s): packVoltage=%s V, SOC=%s%%, "
                            "current=%s A, tempMax=%s°C, cellMax=%s V, cellMin=%s V",
                            "A" if _g2_var_a else "B",
                            data.get("packVoltage"), soc,
                            data.get("realTimeCurrent"), data.get("tempMax"),
                            data.get("cellVoltageMax"), data.get("cellVoltageMin"),
                        )

                    elif schema == SCHEMA_POWERDEPOT:
                        # PowerDepot G2 Schema
                        # Point 400 = Modulanzahl direkt vom BMS → robuster als _module_sns
                        # batteryCapacity ZUERST setzen damit usableKwh korrekt rechnet
                        n_mod_bms = _to_float(rt.get("400"))
                        if n_mod_bms is not None and n_mod_bms > 0:
                            bc_raw = _to_float(self.station_info.get("batteryCapacity"))
                            if bc_raw is not None:
                                # station/info.batteryCapacity kann mehrere BMS-Einträge umfassen
                                # (z.B. H5B: 2 BMS × 5.12 kWh = 10.24, aber Point 400 = 3 Module)
                                # → pro Modul normieren, dann × tatsächliche Modulanzahl
                                bms_n = max(self._storage_list_bms_count, 1)
                                bc_per_mod = bc_raw / bms_n
                                data["batteryCapacity"] = round(bc_per_mod * int(n_mod_bms), 3)
                                _LOGGER.debug(
                                    "Dyness PowerDepot: batteryCapacity %s / %d BMS × %d Module = %s kWh",
                                    bc_raw, bms_n, int(n_mod_bms), data["batteryCapacity"]
                                )
                        elif data.get("batteryCapacity") is None:
                            # Fallback: _module_sns Anzahl wenn Point 400 leer
                            bc_single = _to_float(self.station_info.get("batteryCapacity"))
                            n_mods = max(len(self._module_sns), 1)
                            if bc_single is not None and n_mods > 1:
                                data["batteryCapacity"] = round(bc_single * n_mods, 3)

                        data["packVoltage"] = rt.get("600") if rt.get("600") is not None else data.get("packVoltage")
                        data["realTimeCurrent"]      = rt.get("700")
                        # realTimePower aus V×I — verhindert stale-Wert aus getLastPowerDataBySn
                        _v_pd = _to_float(rt.get("600"))
                        _i_pd = _to_float(rt.get("700"))
                        if _v_pd is not None and _i_pd is not None:
                            data["realTimePower"] = round(_v_pd * _i_pd, 1)
                        data["soc"]                  = rt.get("800")
                        data["soh"]                  = rt.get("1200")
                        data["cellVoltageMax"]       = rt.get("1300")
                        data["cellVoltageMaxModule"] = rt.get("1401")
                        data["cellVoltageMaxCell"]   = rt.get("1402")
                        data["cellVoltageMin"]       = rt.get("1500")
                        data["cellVoltageMinModule"] = rt.get("1601")
                        data["cellVoltageMinCell"]   = rt.get("1602")
                        data["temp"]                 = rt.get("1800")
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
                        # Letzten bekannten Wert beibehalten wenn Point fehlt/null:
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

                        # Alarm Status 1/2 (Points 3200/3300)
                        data["alarmStatus1"] = rt.get("3200")
                        data["alarmStatus2"] = rt.get("3300")
                        data["alarmStatus"]  = (
                            str(rt.get("3200") or "0") != "0"
                            or str(rt.get("3300") or "0") != "0"
                        )

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
                        # Point 13900), nicht "cycleCount"
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

                        # workStatus: direkt aus realTimeCurrent ableiten.
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

                        # Alarm-Sensoren — korrekte Point-Mappings
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
                        # PowerBrick Schema — zwei API-Varianten bekannt:
                        #
                        # Variante A (600-Serie)
                        # Variante B (10xxx-Serie)
                        #
                        # Erkennung: Point 600 vorhanden → Variante A, sonst Variante B
                        #
                        # batteryCapacity: bc_single × Modulanzahl (Point 400)
                        bc_pb    = _to_float(self.station_info.get("batteryCapacity"))
                        n_mod_pb = _to_float(rt.get("400"))
                        if bc_pb is not None:
                            if n_mod_pb is not None and n_mod_pb > 1:
                                data["batteryCapacity"] = round(bc_pb * int(n_mod_pb), 3)
                                _LOGGER.debug(
                                    "Dyness PowerBrick: batteryCapacity %s × %d Module = %s kWh",
                                    bc_pb, int(n_mod_pb), data["batteryCapacity"]
                                )
                            else:
                                data["batteryCapacity"] = bc_pb

                        _pb_var_a = rt.get("600") is not None and rt.get("600") != ""

                        if _pb_var_a:
                            # Variante A: 600-Serie
                            data["packVoltage"]     = rt.get("600")
                            data["realTimeCurrent"] = rt.get("700")
                            _v_pb = _to_float(rt.get("600"))
                            _i_pb = _to_float(rt.get("700"))
                            if _v_pb is not None and _i_pb is not None:
                                data["realTimePower"] = round(_v_pb * _i_pb, 1)
                            data["soc"]                  = rt.get("800")
                            data["soh"]                  = rt.get("1200")
                            data["cellVoltageMax"]       = rt.get("1300")
                            data["cellVoltageMaxModule"] = rt.get("1401")
                            data["cellVoltageMaxCell"]   = rt.get("1402")
                            data["cellVoltageMin"]       = rt.get("1500")
                            data["cellVoltageMinModule"] = rt.get("1601")
                            data["cellVoltageMinCell"]   = rt.get("1602")
                            data["tempMax"]              = rt.get("1800")
                            data["tempMin"]              = rt.get("2000")
                            data["tempMosfet"]           = rt.get("2300")
                            data["tempBmsMax"]           = rt.get("2800")
                            data["tempBmsMin"]           = rt.get("3000")
                            data["alarmStatus1"]         = rt.get("3200")
                            data["alarmStatus2"]         = rt.get("3300")
                            data["alarmSpreadV"] = str(rt.get("3201", "0")) != "0"
                            data["alarmSpreadT"] = str(rt.get("3202", "0")) != "0"
                            data["alarmInsul"]   = str(rt.get("3205", "0")) != "0"
                            data["alarmAfe"]     = str(rt.get("3203", "0")) != "0"
                            data["alarmBms"]     = str(rt.get("3204", "0")) != "0"
                            # Point 4100 = total alarm status (primäre Alarmquelle).
                            # Points 3206/3207/3208 sind Top-Charge-Warnflags (kein echter Fehler)
                            data["alarmSys"]     = str(rt.get("4100", "0")) != "0"
                            cc_pb = rt.get("900")
                            if cc_pb is not None and str(cc_pb).strip() not in ("", "0"):
                                data["cycleCount"] = cc_pb
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
                            # workStatus aus Strom ableiten
                            current_pb = _to_float(rt.get("700"))
                            if current_pb is not None:
                                if current_pb > 1.0:
                                    data["workStatus"] = "Charging"
                                elif current_pb < -1.0:
                                    data["workStatus"] = "Discharging"
                                else:
                                    data["workStatus"] = "Standby"

                        else:
                            # Variante B: 10xxx-Serie
                            data["packVoltage"] = rt.get("13500") if rt.get("13500") is not None else data.get("packVoltage")
                            if rt.get("13400") is not None:
                                data["realTimeCurrent"] = rt.get("13400")
                            data["cycleCount"] = rt.get("13900")
                            # Firmware aus Point 10100
                            if data.get("firmwareVersion") is None:
                                fw_pb = rt.get("10100")
                                if fw_pb:
                                    data["firmwareVersion"] = fw_pb
                            # SOC: v2 API → Fallback getLastPowerDataBySn (Point 23800 leer)
                            _soc_pb_b = None
                            _sn_v2_pb = str(rt.get("10001", "") or "").strip()
                            if _sn_v2_pb:
                                try:
                                    _v2_res_pb = await self._call(
                                        session, "/v2/GetRealTimeDataBySN", {"deviceSn": _sn_v2_pb}
                                    )
                                    if _is_success(_v2_res_pb):
                                        _bi_pb = (_v2_res_pb.get("data") or {}).get("batteryInfo") or {}
                                        _s = _to_float(_bi_pb.get("soc"))
                                        if _s is not None and 0 < _s <= 100:
                                            _soc_pb_b = _s
                                except Exception as _e_pb:
                                    _LOGGER.debug("PowerBrick Var-B: v2-Abruf fehlgeschlagen: %s", _e_pb)
                            if _soc_pb_b is None and _power_data_list:
                                for _entry in reversed(_power_data_list):
                                    _s = _to_float(_entry.get("soc"))
                                    if _s is not None and 0 < _s <= 100:
                                        _soc_pb_b = _s
                                        break
                            if _soc_pb_b is not None:
                                data["soc"] = _soc_pb_b
                            # Temperaturen
                            bms_pb = _to_float(rt.get("12400"))
                            if bms_pb is not None:
                                data["tempBmsMax"] = bms_pb
                            cell_temps_pb = [
                                _to_float(rt.get(str(12500 + i * 100))) for i in range(4)
                            ]
                            valid_pb = [t for t in cell_temps_pb if t is not None and t > 0]
                            if valid_pb:
                                data["tempMax"] = max(valid_pb)
                                data["tempMin"] = min(valid_pb) if len(valid_pb) > 1 else None
                            # Zellspannungen 10300-11800
                            cells_pb_b = []
                            for i in range(1, 17):
                                v = _to_float(rt.get(str(10200 + i * 100)))
                                if v is not None and v > 0:
                                    cells_pb_b.append(v)
                                    data[f"cellVoltage{i:02d}"] = v
                            if cells_pb_b:
                                data["cellVoltageMax"]    = max(cells_pb_b)
                                data["cellVoltageMin"]    = min(cells_pb_b)
                                data["cellVoltageDiffMv"] = round((max(cells_pb_b) - min(cells_pb_b)) * 1000, 1)
                            cv_pb = _to_float(rt.get("18700"))
                            dv_pb = _to_float(rt.get("18800"))
                            cl_pb = _to_float(rt.get("18600"))
                            dl_pb = _to_float(rt.get("19200"))
                            if cv_pb is not None and cv_pb > 0:
                                data["chargeVoltageLimit"]    = cv_pb
                            if dv_pb is not None and dv_pb > 0:
                                data["dischargeVoltageLimit"] = dv_pb
                            if cl_pb is not None and cl_pb > 0:
                                data["chargeCurrentLimit"]    = cl_pb
                            if dl_pb is not None and dl_pb > 0:
                                data["dischargeCurrentLimit"] = dl_pb

                        # Individuelle Zellspannungen (Variante A)
                        if _pb_var_a:
                            cells_pb = []
                            for i in range(1, 17):
                                v = _to_float(rt.get(str(10200 + i * 100)))
                                if v is not None and v > 0:
                                    cells_pb.append(v)
                                    data[f"cellVoltage{i:02d}"] = v
                            if cells_pb:
                                data["cellVoltageDiffMv"] = round((max(cells_pb) - min(cells_pb)) * 1000, 1)

                            # Single-Sub-Modul-Poll: self._single_sub_sn gesetzt
                            # wenn genau 1 SUB erkannt — Master hat keine Einzelzellen.
                            if not cells_pb and self._single_sub_sn:
                                _sub_sn = self._single_sub_sn
                                _LOGGER.debug(
                                    "Dyness PowerBrick: Keine Zellen vom Master — "
                                    "frage Sub-Modul %s direkt ab", _sub_sn
                                )
                                try:
                                    _sub_rt_res = await self._call(
                                        session, "/v1/device/realTime/data",
                                        {"deviceSn": _sub_sn}
                                    )
                                    if _is_success(_sub_rt_res):
                                        _sub_pts = {
                                            p["pointId"]: p.get("pointValue", "")
                                            for p in (_sub_rt_res.get("data") or [])
                                            if p.get("pointId")
                                        }
                                        _sub_cells = []
                                        for i in range(1, 17):
                                            v = _to_float(_sub_pts.get(str(10200 + i * 100)))
                                            if v is not None and v > 0:
                                                _sub_cells.append(v)
                                                data[f"cellVoltage{i:02d}"] = v
                                        if _sub_cells:
                                            data["cellVoltageMax"]    = max(_sub_cells)
                                            data["cellVoltageMin"]    = min(_sub_cells)
                                            data["cellVoltageDiffMv"] = round(
                                                (max(_sub_cells) - min(_sub_cells)) * 1000, 1
                                            )
                                        _bms_t = _to_float(_sub_pts.get("12400"))
                                        if _bms_t is not None:
                                            data["tempBmsMax"] = _bms_t
                                        _cell_temps = [
                                            _to_float(_sub_pts.get(str(12500 + j * 100)))
                                            for j in range(4)
                                        ]
                                        _valid_t = [t for t in _cell_temps if t is not None and t > 0]
                                        if _valid_t:
                                            data["tempMax"] = max(_valid_t)
                                            data["tempMin"] = min(_valid_t) if len(_valid_t) > 1 else data.get("tempMin")
                                        if data.get("cycleCount") is None:
                                            _cc = _sub_pts.get("13900")
                                            if _cc:
                                                data["cycleCount"] = _cc
                                        _LOGGER.debug(
                                            "Dyness PowerBrick Sub-Modul %s: %d Zellen, tempBmsMax=%s°C",
                                            _sub_sn, len(_sub_cells), data.get("tempBmsMax"),
                                        )
                                except Exception as _e_sub:
                                    _LOGGER.debug(
                                        "Dyness PowerBrick: Sub-Modul-Abruf fehlgeschlagen: %s", _e_sub
                                    )

                        # Kapazitätsberechnung (beide Varianten)
                        bc_val = _to_float(data.get("batteryCapacity"))
                        soc_pb = _to_float(data.get("soc"))
                        soh_pb = _to_float(data.get("soh"))
                        if bc_val is not None and soc_pb is not None:
                            soh_f = (soh_pb / 100) if (soh_pb is not None and soh_pb <= 100) else 1.0
                            data["usableKwh"]    = round(bc_val * soh_f, 3)
                            data["remainingKwh"] = round(bc_val * soh_f * soc_pb / 100, 3)

                        _LOGGER.debug(
                            "Dyness PowerBrick (Variante %s): SOC=%s%%, packVoltage=%s V, "
                            "current=%s A, usableKwh=%s kWh",
                            "A" if _pb_var_a else "B",
                            soc_pb, data.get("packVoltage"),
                            data.get("realTimeCurrent"), data.get("usableKwh"),
                        )

                    elif schema == SCHEMA_POWERBRICK_SC:
                        # PowerBrick SC / PowerBrick Plus
                        # 5-stelliges Point-Schema (10xxx-19xxx).

                        bc_sc = _to_float(self.station_info.get("batteryCapacity"))
                        if bc_sc is not None:
                            data["batteryCapacity"] = bc_sc

                        data["packVoltage"]     = rt.get("13500")
                        data["realTimeCurrent"] = rt.get("13400")
                        data["tempBms"]         = rt.get("12400")
                        data["tempMax"]         = rt.get("12500")
                        data["tempMin"]         = rt.get("12600")
                        data["tempMosfet"]      = rt.get("12700")
                        data["tempBmsMax"]      = rt.get("12800")
                        if data.get("firmwareVersion") is None:
                            fw_sc = rt.get("10100")
                            if fw_sc:
                                data["firmwareVersion"] = fw_sc
                        data["cycleCount"]      = rt.get("13900")

                        # SOC: Dual-Fallback
                        # Versuch 1: v2/GetRealTimeDataBySN → batteryInfo.soc
                        #   EU-Geräte (PowerBrick Plus, modelCode 328): v2 liefert SOC korrekt.
                        # Versuch 2: getLastPowerDataBySn → soc
                        #   APAC-Geräte (PowerBrick SC, modelCode 226): v2 code 500,
                        #   aber getLastPowerDataBySn liefert SOC als String.
                        # Point 23800 ist bei beiden Varianten leer.
                        _soc_raw_sc = None
                        _sn_v2_sc = str(rt.get("10001", "") or "").strip()
                        if _sn_v2_sc:
                            try:
                                _v2_res = await self._call(
                                    session, "/v2/GetRealTimeDataBySN", {"deviceSn": _sn_v2_sc}
                                )
                                if _is_success(_v2_res):
                                    _bi = (_v2_res.get("data") or {}).get("batteryInfo") or {}
                                    _soc_v2 = _to_float(_bi.get("soc"))
                                    if _soc_v2 is not None and 0 < _soc_v2 <= 100:
                                        _soc_raw_sc = _soc_v2
                                        _LOGGER.debug(
                                            "Dyness PowerBrick SC/Plus: SOC=%s%% (v2 API, sn=%s)",
                                            _soc_raw_sc, _sn_v2_sc,
                                        )
                            except Exception as _e_sc:
                                _LOGGER.debug(
                                    "Dyness PowerBrick SC/Plus: v2-Abruf fehlgeschlagen: %s", _e_sc
                                )
                        # Fallback: getLastPowerDataBySn (APAC-Geräte, v2 liefert code 500)
                        if _soc_raw_sc is None and _power_data_list:
                            for _entry in reversed(_power_data_list):
                                _s = _entry.get("soc")
                                if _s is not None:
                                    _soc_fb = _to_float(_s)
                                    if _soc_fb is not None and 0 < _soc_fb <= 100:
                                        _soc_raw_sc = _soc_fb
                                        _LOGGER.debug(
                                            "Dyness PowerBrick SC/Plus: SOC=%s%% "
                                            "(getLastPowerDataBySn, Fallback)", _soc_raw_sc,
                                        )
                                        break
                        if _soc_raw_sc is not None:
                            data["soc"] = _soc_raw_sc
                        else:
                            _LOGGER.debug(
                                "Dyness PowerBrick SC/Plus: Kein SOC verfügbar "
                                "(v2 code 500 + getLastPowerDataBySn leer)."
                            )

                        # Zellspannungen: Points 10300, 10400, ..., 11800 (16 Zellen)
                        cells_sc = []
                        for i in range(1, 17):
                            v = _to_float(rt.get(str(10200 + i * 100)))
                            if v is not None and v > 0:
                                cells_sc.append(v)
                                data[f"cellVoltage{i:02d}"] = v
                        if cells_sc:
                            data["cellVoltageMax"] = max(cells_sc)
                            data["cellVoltageMin"] = min(cells_sc)
                            data["cellVoltageDiffMv"] = round((max(cells_sc) - min(cells_sc)) * 1000, 1)

                        # Strom- und Spannungslimits
                        cv_sc = _to_float(rt.get("18700"))
                        dv_sc = _to_float(rt.get("18800"))
                        cl_sc = _to_float(rt.get("18600"))
                        dl_sc = _to_float(rt.get("19200"))
                        if cv_sc is not None and cv_sc > 0:
                            data["chargeVoltageLimit"]    = cv_sc
                        if dv_sc is not None and dv_sc > 0:
                            data["dischargeVoltageLimit"] = dv_sc
                        if cl_sc is not None and cl_sc > 0:
                            data["chargeCurrentLimit"]    = cl_sc
                        if dl_sc is not None and dl_sc > 0:
                            data["dischargeCurrentLimit"] = dl_sc

                        # workStatus aus Strom ableiten
                        current_sc = _to_float(rt.get("13400"))
                        if current_sc is not None:
                            if current_sc > 1.0:
                                data["workStatus"] = "Charging"
                            elif current_sc < -1.0:
                                data["workStatus"] = "Discharging"
                            else:
                                data["workStatus"] = "Standby"

                        # Kapazitätsberechnung auf Basis des SOC aus getLastPowerDataBySn
                        soc_display = _to_float(data.get("soc"))
                        if bc_sc is not None and soc_display is not None:
                            data["usableKwh"]    = round(bc_sc, 3)
                            data["remainingKwh"] = round(bc_sc * soc_display / 100, 3)

                        _LOGGER.debug(
                            "Dyness PowerBrick SC/Plus: packVoltage=%s V, SOC=%s%%, "
                            "current=%s A, cells=%d, cycleCount=%s, firmware=%s",
                            data.get("packVoltage"), data.get("soc"),
                            current_sc, len(cells_sc),
                            data.get("cycleCount"), data.get("firmwareVersion"),
                        )

                    elif schema == SCHEMA_CYGNI:
                        # Cygni 10.0HS-M8 Schema — Hybrid-Wechselrichter
                        #
                        # Besonderheiten:
                        # - Keine Sub-Module (SUB leer)
                        # - INVERTIERTE Polarität: negativ = Laden, positiv = Entladen
                        #   (entgegengesetzt zu allen anderen Dyness-Modellen!)
                        # - getLastRunningDataBySn ist primäre Datenquelle (vollständig)
                        # - getLastPowerDataBySn liefert nur SOC/Power (unvollständig)
                        # - batteryCapacity aus station/info = Gesamtkapazität direkt
                        #   (30.72 kWh = 4 × 7.68 kWh)

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

                    # Alarm-Delay — Notification erst nach konfigurierbarer
                    # Mindestdauer auslösen. Verhindert Benachrichtigungen bei kurzzeitigen
                    # Transient-Ereignissen (z.B. Pack-High-Voltage beim Balancing auf 100% SOC).
                    # Sensor alarmText zeigt immer den aktuellen Stand — unabhängig vom Delay.
                    alarm_delay_min = 0
                    if self.config_entry is not None:
                        # options speichert den Wert als String (vol.In mit str-Keys)
                        alarm_delay_min = int(
                            self.config_entry.options.get("alarm_delay_minutes", 0)
                        )
                    alarm_delay = timedelta(minutes=alarm_delay_min)
                    now_utc = datetime.now(timezone.utc)
                    active_labels = set(alarm_texts)

                    # Tracking: weggefallene Alarme entfernen, neue registrieren
                    for label in list(self._alarm_first_seen):
                        if label not in active_labels:
                            del self._alarm_first_seen[label]
                    for label in active_labels:
                        if label not in self._alarm_first_seen:
                            self._alarm_first_seen[label] = now_utc

                    # Nur Alarme melden die Delay überschritten haben
                    reportable = [
                        label for label in alarm_texts
                        if (now_utc - self._alarm_first_seen[label]) >= alarm_delay
                    ]

                    if alarm_texts:
                        data["alarmText"] = ", ".join(alarm_texts)
                        # Notification nur wenn alarmSys=True (Point 4100 gesetzt).
                        # Top-Charge-Warnflags (3208 etc.) befüllen alarmText als
                        # Diagnose-Information, triggern aber keine HA-Notification.
                        _alarm_sys_active = bool(data.get("alarmSys"))
                        if reportable and _alarm_sys_active:
                            self.hass.async_create_task(
                                self.hass.services.async_call(
                                    "persistent_notification", "create", {
                                        "title": "⚠️ Dyness Battery Alarm",
                                        "message": (
                                            f"Active alarms detected on {self.device_sn}:\n"
                                            + "\n".join(f"• {t}" for t in reportable)
                                            + "\n\nPlease contact Dyness support if the issue persists."
                                        ),
                                        "notification_id": f"dyness_alarm_{self.device_sn}",
                                    }
                                )
                            )
                        else:
                            _LOGGER.debug(
                                "Dyness: %d Alarm(e) aktiv (alarmSys=%s), Delay %d Min noch nicht "
                                "erreicht oder kein echter Fehler — keine Benachrichtigung",
                                len(alarm_texts), _alarm_sys_active, alarm_delay_min,
                            )
                    else:
                        data["alarmText"] = "OK"
                        self._alarm_first_seen.clear()
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

                    # ── Kapazitäts-Override aus Benutzereinstellungen ────────
                    _cap_override_raw = str(
                        self.config_entry.options.get("battery_capacity_override", "") or ""
                    ).strip()
                    if _cap_override_raw:
                        try:
                            _cap_override = float(_cap_override_raw.replace(",", "."))
                            if _cap_override > 0:
                                _LOGGER.debug(
                                    "Dyness: batteryCapacity Override: %s kWh (Benutzer)",
                                    _cap_override,
                                )
                                data["batteryCapacity"] = _cap_override
                        except ValueError:
                            pass

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
                    if schema not in (SCHEMA_STACK100, SCHEMA_POWERDEPOT, SCHEMA_POWERBOX_G2,
                                      SCHEMA_POWERBRICK, SCHEMA_POWERBRICK_SC,
                                      SCHEMA_POWERBOX_PRO):
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
