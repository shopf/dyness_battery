"""
Dyness v2 Control-Capability Probe v1.1
=======================================
Ergänzung zu dyness_test.py (shopf/dyness_battery): prüft, welche v2-STEUER-
Endpunkte ein Gerät kennt — OHNE echte Steuerwerte zu senden (nur die SN).

Verwendung:   pip install requests   ->   python3 dyness_control_probe.py
              (--yes überspringt die Sicherheitsabfrage, z. B. für Automatisierung)
Aenderungen:  siehe CHANGELOG.md
Ergebnis bitte teilen: https://github.com/shopf/dyness_battery
"""

# ==============================================================
#  HIER DEINE ZUGANGSDATEN EINTRAGEN  (nur diese 2 Zeilen!)
# ==============================================================
API_ID     = "DEINE_API_ID"
API_SECRET = "DEIN_API_SECRET"
# Alles Weitere ist optional. Nicht in Europa? Dann weiter unten bei
# "REGION" API_BASE_V2 anpassen.
# ==============================================================

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import hashlib, hmac, base64, json, re, time, atexit, requests
from datetime import datetime, timezone
from email.utils import formatdate

SCRIPT_VERSION = "1.1"

# ===== REGION (in Europa nichts ändern) =====
# v1 wird nur für die Geräte-Discovery genutzt (wie in dyness_test.py)
API_BASE_V1 = "https://open-api.dyness.com/openapi/ems-device"

# v2-Region wählen (nur eine Zeile einkommentieren)
API_BASE_V2 = "https://eu-openapi.dyness.com/openapi/ems-device"        # Europa v2
# API_BASE_V2 = "https://apacopen-api.dyness.com/openapi/ems-device"    # Asia-Pacific v2
# API_BASE_V2 = "https://na-openapi.dyness.com/openapi/ems-device"      # North America v2
# API_BASE_V2 = "https://latam-openapi.dyness.com/openapi/ems-device"   # Latin America v2
# API_BASE_V2 = "https://me-openapi.dyness.com/openapi/ems-device"      # Middle East v2
# API_BASE_V2 = "https://af-openapi.dyness.com/openapi/ems-device"      # Africa v2
# API_BASE_V2 = "https://sa-openapi.dyness.com/openapi/ems-device"      # South America v2
# API_BASE_V2 = "https://global-openapi.dyness.com/openapi/ems-device"  # Global v2
# API_BASE_V2 = "https://openapi.dyness.com.cn/openapi/ems-device"      # China v2

# Optional: Wenn Auto-Discovery fehlschlägt, hier SN(s) manuell eintragen
MANUAL_DEVICE_SNS = []   # z.B. ["R07E01234567890F-BMS"]

# ===== OPTIONEN (normalerweise nicht ändern) =====
CALL_DELAY    = 2.0    # Mindestabstand zwischen zwei API-Calls in Sekunden (Rate-Limit-Schutz)
MAX_RETRIES   = 4      # Wiederholungen pro Call bei HTTP 429
RETRY_BACKOFF = 3.0    # Wartezeit vor Retry: 3 s, 6 s, 12 s, 24 s ...
ANONYMIZE     = True   # True: SNs maskieren, Stationsdaten entfernen
# =============================================

# ==============================================================
#  SICHERHEIT & HINTERGRUND — BITTE VOR DEM ERSTEN START LESEN
# ==============================================================
# ZWECK
#   dyness_test.py prüft die LESE-Endpunkte der v2-API gründlich. Was fehlte:
#   ein Test der SCHREIB-/STEUER-Endpunkte. Aus den offiziellen Dyness-Protokoll-
#   PDFs (Junior Box, High-voltage battery, Low-voltage battery, Cygni HA/HS,
#   AquaVolt/AquaVolt_LV/SolarCube, HT-A/LS Series) ergeben sich 10 v2-Set-
#   Endpunkte, je nach Produktfamilie unterschiedlich zusammengestellt:
#
#     /v2/SetBaseSetting            (Junior Box, Cygni, AquaVolt/SolarCube, HT-A/LS)
#     /v2/SetWorkModeSetting        (Junior Box, Cygni, AquaVolt/SolarCube, HT-A/LS)
#     /v2/SetBatterySetting         (High-/Low-voltage battery, Cygni, AquaVolt/SolarCube, HT-A/LS)
#     /v2/SetLoadControlSetting     (Cygni, AquaVolt/SolarCube)
#     /v2/SetPeakControlSetting     (Cygni, AquaVolt/SolarCube)
#     /v2/SetAdvancedSetting        (Cygni, AquaVolt/SolarCube, HT-A/LS)
#     /v2/SetGeneratorControlSetting(Cygni)
#     /v2/SetCtrlDataBySN           (HT-A/LS — generischer Register-Zugriff)
#     /v2/SetDspSetting             (HT-A/LS)
#     /v2/SetArmSetting             (HT-A/LS)
#
#   Welche Produktbezeichnung (Tower T14, TP7, DL5.0C, Stack100, PowerBox Pro/G2,
#   PowerHaus, ...) zu welcher Dokument-Familie gehört, ist NICHT zweifelsfrei aus
#   den PDFs ablesbar (dort stehen nur interne Modell-Codes wie "HT-A"/"LS").
#   Deshalb testet dieses Script bewusst ALLE 10 Endpunkte gegen jede gefundene
#   Seriennummer — das beantwortet empirisch, was wirklich geht.
#
# SICHERHEITSKONZEPT
#   Gesendet wird NUR {"deviceSn": "..."} — ohne die eigentlichen Steuerparameter
#   (workMode, powerLimit, loadSwitch, ...). Das reicht, um zu erkennen, ob der
#   Endpunkt für dieses Gerät existiert:
#     - 200/Erfolg                  -> Endpunkt vorhanden, Aufruf akzeptiert (bei rein
#                                      optionalen Zusatzfeldern ändert sich am Gerät
#                                      nichts, da keine Werte übergeben wurden)
#     - "Parameter fehlt/erforderlich" -> Endpunkt vorhanden, braucht aber echte Werte
#     - 404 / "nicht konfiguriert" / "device type mismatch"
#                                   -> Endpunkt für dieses Gerät NICHT implementiert
#   Bei einigen Endpunkten (v.a. Junior Box: SetBaseSetting, SetWorkModeSetting) sind
#   laut PDF ALLE Zusatzfelder Pflichtfelder. Ein deviceSn-only-Aufruf wird dort
#   voraussichtlich mit einem Validierungsfehler abgelehnt, OHNE dass am Gerät etwas
#   verändert wird (die Validierung passiert serverseitig vor der Weiterleitung an die
#   Hardware — Standardverhalten praktisch aller Cloud-APIs; eine 100%ige Garantie
#   kann es aber nicht geben, da das Verhalten des Dyness-Servers nicht einsehbar ist).
#
#   ECHTE Steuerbefehle mit echten Werten sendet dieses Script NIE. Das bleibt bewusst
#   ein manueller, expliziter zweiter Schritt. Vor dem ersten Schreib-Endpunkt fragt
#   das Script deshalb ausdrücklich nach einer Bestätigung (JA eintippen).
# ==============================================================

ASSUME_YES = ("--yes" in sys.argv[1:]) or ("-y" in sys.argv[1:])

SEP  = "=" * 70
SEP2 = "-" * 70


# ── Auth Helpers (identisch zu dyness_test.py) ────────────────────────────

def get_md5(body: str) -> str:
    return base64.b64encode(hashlib.md5(body.encode("utf-8")).digest()).decode("utf-8")


def get_signature(secret: str, method: str, content_md5: str, date: str, path: str) -> str:
    sts = f"{method}\n{content_md5}\napplication/json\n{date}\n{path}"
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), sts.encode("utf-8"), "sha1").digest()
    ).decode("utf-8")


# ── HTTP-Layer (identisch zu dyness_test.py v4.3, hier nur POST) ──────────

STATS = {"calls": 0, "rate_limited": 0, "rate_limited_final": 0, "empty": 0, "errors": 0}
_LAST_CALL = [0.0]
_SNS = set()   # alle bisher gesehenen Seriennummern (für ANONYMIZE)

_SN_POINTS = {"SUB", "310", "10001", "10010", "10150"}
_SN_SUFFIX_RE = re.compile(r"(-(?:BMS|BDU|INV|EMS)|-BDU-\d+|-\d{2})$")


def _register_sn(v):
    if not isinstance(v, str):
        return
    for part in v.split(","):
        part = part.strip()
        if len(part) >= 10 and " " not in part:
            _SNS.add(part)
            base = _SN_SUFFIX_RE.sub("", part)
            if len(base) >= 10:
                _SNS.add(base)


def _scan_sns(obj):
    """Sammelt SNs aus einer API-Antwort (Keys ...Sn sowie SN-Points)."""
    if isinstance(obj, dict):
        if obj.get("pointId") in _SN_POINTS and "pointValue" in obj:
            _register_sn(obj["pointValue"])
        for k, v in obj.items():
            if isinstance(k, str) and k.lower().endswith("sn") and isinstance(v, str):
                _register_sn(v)
            elif isinstance(v, (dict, list)):
                _scan_sns(v)
    elif isinstance(obj, list):
        for x in obj:
            _scan_sns(x)


def _throttle():
    wait = CALL_DELAY - (time.monotonic() - _LAST_CALL[0])
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL[0] = time.monotonic()


def _is_rate_limited(res: dict) -> bool:
    return (str(res.get("code")) == "429" or res.get("_http_status") == 429
            or str(res.get("sourceCode", "")).upper() == "TOO_MANY_REQUESTS")


def _decode(r) -> dict:
    """HTTP-Antwort -> dict. Leere/ungültige Antworten werden markiert."""
    try:
        j = r.json()
    except Exception:
        STATS["empty"] += 1
        text = (r.text or "")
        j = {"code": str(r.status_code), "info": text[:300],
             "_empty_response": not text.strip()}
    if not isinstance(j, dict):
        j = {"code": "ERR", "info": "unerwartetes JSON-Format", "data": j}
    j["_http_status"] = r.status_code
    _scan_sns(j)
    return j


def _with_retry(label: str, once) -> dict:
    """Führt einen Call mit Mindestabstand aus; wiederholt bei HTTP 429."""
    res = {}
    for attempt in range(MAX_RETRIES + 1):
        _throttle()
        STATS["calls"] += 1
        res = once()
        if _is_rate_limited(res):
            STATS["rate_limited"] += 1
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF * (2 ** attempt)
                print(f"  ⏳ 429 TOO_MANY_REQUESTS ({label}) — warte {wait:.0f}s, "
                      f"Versuch {attempt + 2}/{MAX_RETRIES + 1}")
                time.sleep(wait)
                continue
            STATS["rate_limited_final"] += 1
        break
    return res


def _post(base_url: str, path: str, body_dict: dict) -> dict:
    body = json.dumps(body_dict, separators=(',', ':'), sort_keys=True)

    def once():
        date = formatdate(timeval=None, localtime=False, usegmt=True)  # pro Versuch neu
        md5  = get_md5(body)
        sig  = get_signature(API_SECRET, "POST", md5, date, path)
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Content-MD5":  md5,
            "Date":         date,
            "Authorization": f"API {API_ID}:{sig}",
        }
        try:
            r = requests.post(f"{base_url}{path}", headers=headers,
                              data=body.encode("utf-8"), timeout=15)
        except Exception as e:
            STATS["errors"] += 1
            return {"error": str(e), "code": "ERR", "_http_status": None}
        return _decode(r)

    return _with_retry(path, once)


def api_v1(path, body): return _post(API_BASE_V1, path, body)
def api_v2(path, body): return _post(API_BASE_V2, path, body)


# ── Anonymisierung (stdout-Filter, identisch zu dyness_test.py v4.3) ──────

_PII = (r"stationName|stationId|position|imgAddress|imageUrl|address|latitude|"
        r"longitude|ownerName|userName|nickName|email|phone|mobile")
_RE_PII_JSON = re.compile(r'("(?:%s)"\s*:\s*)(?:"[^"]*"|-?[\d.]+)' % _PII)
_RE_PII_KV   = re.compile(r'^(\s*(?:%s)\s*=\s*)(\S.*?)(\s{2,}\(.*)?$' % _PII)
_SN_CACHE = {"n": -1, "sorted": []}


def _mask_sn(sn: str) -> str:
    m = _SN_SUFFIX_RE.search(sn)
    suf = m.group(1) if m else ""
    base = sn[:len(sn) - len(suf)] if suf else sn
    if len(base) <= 8:
        return "x" * len(base) + suf
    return base[:4] + "x" * (len(base) - 8) + base[-4:] + suf


def _anon_line(line: str) -> str:
    if not ANONYMIZE:
        return line
    if _SN_CACHE["n"] != len(_SNS):
        _SN_CACHE["n"] = len(_SNS)
        _SN_CACHE["sorted"] = sorted(_SNS, key=len, reverse=True)
    for sn in _SN_CACHE["sorted"]:
        if sn in line:
            line = line.replace(sn, _mask_sn(sn))
    line = _RE_PII_JSON.sub(r'\1"<entfernt>"', line)
    m = _RE_PII_KV.match(line)
    if m:
        line = m.group(1) + "<entfernt>" + (m.group(3) or "")
    return line


class _AnonWriter:
    """Zeilenweiser Filter vor stdout — maskiert SNs und entfernt Stationsdaten."""
    def __init__(self, inner):
        self._inner = inner
        self._buf = ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._inner.write(_anon_line(line) + "\n")
        return len(text)

    def flush(self):
        if self._buf:
            self._inner.write(_anon_line(self._buf))
            self._buf = ""
        self._inner.flush()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _install_anonymizer():
    if ANONYMIZE and not isinstance(sys.stdout, _AnonWriter):
        sys.stdout = _AnonWriter(sys.stdout)
        atexit.register(sys.stdout.flush)


# ── Endpoint-Registry (aus den offiziellen PDFs extrahiert) ────────────
# required_extra: Zusatzfelder, die laut PDF für MINDESTENS eine Produkt-
# familie Pflicht sind (informativ, wird für die Klassifikation genutzt).
# doc_families: in welchen der Dokumente dieser Pfad überhaupt vorkommt.

SET_ENDPOINTS = [
    {
        "path": "/v2/SetBaseSetting",
        "required_extra": ["workMode/safetyCountry (produktabhängig)"],
        "doc_families": ["Junior Box", "Cygni HA/HS", "AquaVolt/SolarCube", "HT-A/LS"],
    },
    {
        "path": "/v2/SetWorkModeSetting",
        "required_extra": ["workMode oder workGroups (produktabhängig)"],
        "doc_families": ["Junior Box", "Cygni HA/HS", "AquaVolt/SolarCube", "HT-A/LS"],
    },
    {
        "path": "/v2/SetBatterySetting",
        "required_extra": ["onGridDischargeDod (nur Pflicht bei Cygni)"],
        "doc_families": ["High-voltage battery", "Low-voltage battery",
                          "Cygni HA/HS", "AquaVolt/SolarCube", "HT-A/LS"],
    },
    {
        "path": "/v2/SetLoadControlSetting",
        "required_extra": ["loadSwitch"],
        "doc_families": ["Cygni HA/HS", "AquaVolt/SolarCube"],
    },
    {
        "path": "/v2/SetPeakControlSetting",
        "required_extra": ["peakControlEnable"],
        "doc_families": ["Cygni HA/HS", "AquaVolt/SolarCube"],
    },
    {
        "path": "/v2/SetAdvancedSetting",
        "required_extra": ["gridPowerLimitGroup.gridPowerLimitSwitch (produktabhängig)"],
        "doc_families": ["Cygni HA/HS", "AquaVolt/SolarCube", "HT-A/LS"],
    },
    {
        "path": "/v2/SetGeneratorControlSetting",
        "required_extra": [],  # generatorWorkMode ist laut PDF optional
        "doc_families": ["Cygni HA/HS"],
    },
    {
        "path": "/v2/SetCtrlDataBySN",
        "required_extra": ["controlData (Register-Map, Pflicht)"],
        "doc_families": ["HT-A/LS"],
    },
    {
        "path": "/v2/SetDspSetting",
        "required_extra": [],  # alle Zusatzfelder optional laut PDF
        "doc_families": ["HT-A/LS"],
    },
    {
        "path": "/v2/SetArmSetting",
        "required_extra": [],  # alle Zusatzfelder optional laut PDF
        "doc_families": ["HT-A/LS"],
    },
]

# Muster, die auf "Endpunkt existiert, aber Pflichtfeld fehlt" hindeuten.
VALIDATION_HINTS = [
    "required", "must not be null", "not be empty", "is null",
    "参数", "不能为空", "missing", "invalid parameter", "parameter error",
]
# Muster, die auf "Endpunkt/Feature für dieses Gerät nicht vorhanden" hindeuten.
NOT_SUPPORTED_HINTS = [
    "not configured", "not support", "unsupported", "device type",
    "no such", "not found", "does not exist",
]
# Muster, die auf "Gerät existiert/ist für v2 registriert, ist aber gerade
# nicht erreichbar" hindeuten -- WICHTIG: das ist etwas anderes als "nicht
# konfiguriert"! Ein offline gemeldetes Gerät könnte im Prinzip steuerbar
# sein, sobald es wieder online ist -- wir wissen es nur gerade nicht.
OFFLINE_HINTS = ["offline", "device is offline", "not online", "disconnected"]
# Beobachtet in echten Logs: keine Aussage über das Gerät.
TRANSIENT_HINTS = ["network anomaly"]
NOT_BOUND_HINTS = ["not bound"]

# Die tatsächlichen Pflichtfeld-Namen aus den PDFs, je Endpunkt (nur die
# Felder, die in MINDESTENS einem der Dokumente als "Yes"/Pflicht markiert
# sind). Taucht einer dieser Namen in der Fehlermeldung auf, ist das ein
# STARKES Indiz, dass der Server dieses Gerät kennt und die Anfrage
# feldbezogen validiert hat -- also ein echter Beleg, kein generisches
# No-Op-200.
KNOWN_FIELD_NAMES = {
    "/v2/SetBaseSetting": ["workmode", "safetycountry", "powerlimit", "dischargedepth"],
    "/v2/SetWorkModeSetting": ["workmode", "workgroups", "basicmode"],
    "/v2/SetBatterySetting": ["ongriddischargedod", "heatingperiodlist",
                              "batterycommunicationprotocolcode"],
    "/v2/SetLoadControlSetting": ["loadswitch"],
    "/v2/SetPeakControlSetting": ["peakcontrolenable", "peakcontrolen"],
    "/v2/SetAdvancedSetting": ["gridpowerlimitgroup", "dredfunctionswitch"],
    "/v2/SetGeneratorControlSetting": ["generatorworkmode"],
    "/v2/SetCtrlDataBySN": ["controldata"],
    "/v2/SetDspSetting": ["againgrid"],
    "/v2/SetArmSetting": ["extelectricmeter"],
}

# Klassen, die KEINE Aussage über die Steuerbarkeit erlauben.
INCONCLUSIVE = {"RATE_LIMIT", "LEERE_ANTWORT", "VORUEBERGEHEND", "NICHT_GEBUNDEN", "UNKLAR"}


def classify(path: str, result: dict) -> str:
    """Ordnet die Server-Antwort einer Kategorie zu."""
    # Technische Nicht-Antworten zuerst: sie sagen nichts über das Gerät aus.
    if _is_rate_limited(result):
        return "RATE_LIMIT"
    if result.get("_empty_response"):
        return "LEERE_ANTWORT"

    http_status = result.get("_http_status")
    code = str(result.get("code", ""))
    info = str(result.get("info", "")).lower()

    if http_status == 404 or code == "404":
        return "NICHT_VERFUEGBAR"
    if any(h in info for h in OFFLINE_HINTS):
        return "GERAET_OFFLINE"
    if any(h in info for h in TRANSIENT_HINTS):
        return "VORUEBERGEHEND"
    if any(h in info for h in NOT_BOUND_HINTS):
        return "NICHT_GEBUNDEN"
    if any(h in info for h in NOT_SUPPORTED_HINTS):
        return "NICHT_VERFUEGBAR"

    field_hit = any(f in info for f in KNOWN_FIELD_NAMES.get(path, []))
    if any(h in info for h in VALIDATION_HINTS) or field_hit:
        # Feldname aus dem PDF erkannt -> starker Beleg für echte Anbindung
        return "VALIDIERT" if field_hit else "PFLICHTFELD_FEHLT"

    if code in ("200", "0"):
        # Erfolg OHNE jeden Hinweis auf ein gerätespezifisches Feld ist
        # NICHT zwingend ein Beweis für echte Steuerbarkeit -- die v2-API
        # akzeptiert leere/No-Op-Settings-Updates offenbar generisch, auch
        # für Endpunkte, die laut PDF für dieses Produkt gar nicht
        # vorgesehen sind (siehe GetStatusInfBySN/GetTotalEnergyDataBySN,
        # die für die Junior Box ebenfalls 200+null statt 404 liefern).
        return "OK_GENERISCH"

    return "UNKLAR"


LABELS = {
    "VALIDIERT":         "✅ steuerbar (Server nennt echten Parameter aus dem PDF)",
    "PFLICHTFELD_FEHLT": "🟡 Endpunkt vorhanden (Validierungsfehler, Feld unklar)",
    "OK_GENERISCH":      "⚠️  200 OK, aber OHNE Feldbezug — evtl. nur generisches No-Op",
    "NICHT_VERFUEGBAR":  "❌ nicht verfügbar für dieses Gerät",
    "GERAET_OFFLINE":    "📡 Gerät offline (v2 kennt es, kann es aber gerade nicht erreichen)",
    "RATE_LIMIT":        "⏳ Rate-Limit (HTTP 429) auch nach Wiederholungen — keine Aussage möglich",
    "LEERE_ANTWORT":     "📭 leere/ungültige Antwort (Server-Störung?) — keine Aussage möglich",
    "VORUEBERGEHEND":    "⏳ vorübergehender Server-Fehler ('Network Anomaly') — später erneut testen",
    "NICHT_GEBUNDEN":    "🔗 SN nicht an das Konto gebunden — keine Aussage möglich",
    "UNKLAR":            "❔ unklar — Rohantwort prüfen",
}


def probe_body(sn: str, path: str) -> dict:
    """Baut den *sicheren* Test-Body: nur deviceSn, keine echten Steuerwerte.
    Dieses Script sendet bewusst NIE operative Werte (siehe SICHERHEITSKONZEPT)."""
    return {"deviceSn": sn}


def probe_device(sn: str) -> dict:
    """Testet alle bekannten Set-Endpunkte gegen eine Seriennummer und
    gibt {path: (klasse, rohantwort)} zurück."""
    results = {}
    for ep in SET_ENDPOINTS:
        body = probe_body(sn, ep["path"])
        res = api_v2(ep["path"], body)
        klass = classify(ep["path"], res)
        results[ep["path"]] = (klass, res)
    return results


def print_device_report(sn: str, model_name: str, results: dict):
    print(SEP)
    print(f"  Steuer-Endpunkt-Report: {sn}  [{model_name}]")
    print(SEP)
    any_validated = any_maybe = any_generic = any_offline = False
    n_inconclusive = 0
    all_not_available = True
    for ep in SET_ENDPOINTS:
        klass, res = results[ep["path"]]
        print(f"  {ep['path']:<32} {LABELS[klass]}")
        # Rohantwort IMMER zeigen
        print(f"      -> code={res.get('code', '')!r}  info={res.get('info', '')!r}  "
              f"http={res.get('_http_status')}")
        if klass == "VALIDIERT":
            any_validated = True
        elif klass == "PFLICHTFELD_FEHLT":
            any_maybe = True
        elif klass == "OK_GENERISCH":
            any_generic = True
        elif klass == "GERAET_OFFLINE":
            any_offline = True
        elif klass in INCONCLUSIVE:
            n_inconclusive += 1
        if klass != "NICHT_VERFUEGBAR":
            all_not_available = False
    print(SEP2)
    if any_validated:
        verdict = ("STEUERBAR — Server hat mindestens einen echten, im PDF "
                   "dokumentierten Parameter beim Namen genannt.")
    elif any_maybe:
        verdict = ("VERMUTLICH STEUERBAR — Endpunkt(e) melden einen Validierungsfehler "
                   "(Pflichtfeld fehlt), aber ohne erkennbaren Feldnamen. Echte Werte "
                   "aus dem PDF nötig, um das sicher zu bestätigen.")
    elif any_generic:
        verdict = ("UNSICHER — nur generische 200-OK-Antworten ohne Feldbezug. Das kann "
                   "ein No-Op-Erfolg sein, der nichts über echte Steuerbarkeit aussagt "
                   "(vgl. GetStatusInfBySN/GetTotalEnergyDataBySN-Verhalten). Nicht als "
                   "'steuerbar' werten, ohne dies mit echten Parametern gegenzuprüfen.")
    elif any_offline:
        verdict = ("UNKLAR (GERÄT OFFLINE) — v2 kennt dieses Gerät grundsätzlich, kann es "
                   "aber gerade nicht erreichen. Keine Aussage über Steuerbarkeit möglich, "
                   "solange das Gerät nicht online ist. Bitte später erneut testen.")
    elif n_inconclusive:
        verdict = (f"UNVOLLSTÄNDIG — {n_inconclusive} von {len(SET_ENDPOINTS)} Endpunkten ohne "
                   "verwertbare Antwort (Rate-Limit, leere Antwort, Server-Fehler, nicht "
                   "gebunden oder unklar). Daraus folgt KEIN Urteil über die Steuerbarkeit. "
                   "Bitte erneut testen (HA-Integration vorher kurz deaktivieren).")
    elif all_not_available:
        verdict = ("KOMPLETT 'NOT CONFIGURED' — das betrifft hier ALLE Endpunkte, nicht nur "
                   "die Steuerung. Das deutet eher auf 'v2 ist für dieses Gerät/Konto generell "
                   "nicht freigeschaltet' hin, nicht auf 'Steuerung fehlt'. Siehe Gesamt-Diagnose "
                   "unten.")
    else:
        verdict = "KEINE STEUERUNGSMÖGLICHKEIT GEFUNDEN — nur Monitoring über v2 möglich."
    print(f"  ERGEBNIS: {verdict}")
    print(SEP)
    print()


# ── Geräte-Discovery (wie in dyness_test.py) ──────────────────────────────

def discover_devices():
    if MANUAL_DEVICE_SNS:
        for sn in MANUAL_DEVICE_SNS:
            _register_sn(sn)
        return [{"deviceSn": sn, "deviceModelName": "?"} for sn in MANUAL_DEVICE_SNS]

    res = api_v1("/v1/device/storage/list", {})
    entries = (res.get("data") or {}).get("list") or []
    if not entries:
        print("⚠️  Keine Geräte über v1 gefunden. Bitte MANUAL_DEVICE_SNS setzen.")
        return []
    return entries


def to_v2_sn(v1_sn: str) -> str:
    """v1 nutzt oft Suffixe wie -BMS/-BDU/-INV/-EMS, v2 laut Doku i.d.R. ohne."""
    base = re.sub(r'-(BMS|BDU|INV|EMS)$', '', v1_sn)
    return base if base != v1_sn else v1_sn


def base_sn(sn: str) -> str:
    """Normalisiert eine SN auf ihren 'Kern' (ohne bekannte Suffixe), um
    verschiedene Schreibweisen desselben physischen Geräts zu erkennen."""
    return re.sub(r'-(BMS|BDU|INV|EMS)$', '', sn)


def gather_sn_candidates(v1_sn: str, device_list_entries: list) -> list:
    """Baut eine geordnete, deduplizierte Liste möglicher v2-SN-Schreibweisen
    für ein physisches Gerät: Original-SN, Suffix-befreite Variante, sowie
    alle Treffer aus /v2/GetDeviceList mit demselben SN-Kern. Manche
    Accounts/Geräte akzeptieren nur EINE bestimmte Schreibweise -- ein
    einzelner Fallback-Versuch reicht dafür nicht immer aus."""
    candidates = [v1_sn, to_v2_sn(v1_sn)]
    core = base_sn(v1_sn)
    for entry in device_list_entries:
        cand_sn = entry.get("deviceSn", "")
        if cand_sn and base_sn(cand_sn) == core and cand_sn not in candidates:
            candidates.append(cand_sn)
    # Reihenfolge beibehalten, Duplikate entfernen
    seen = set()
    out = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def fetch_v2_device_list() -> list:
    """Einmaliger GetDeviceList-Aufruf, um alle bekannten SN-Schreibweisen
    (mit/ohne Suffix) und deren communicationStatus einzusammeln."""
    res = api_v2("/v2/GetDeviceList", {})
    data = res.get("data")
    if isinstance(data, list):
        return data
    return []


# ── Sicherheitsabfrage vor dem ersten Schreib-Endpunkt ────────────────────

def confirm_write_probe(plan: list) -> bool:
    """Zeigt, was gesendet wird, und verlangt eine ausdrückliche Bestätigung."""
    print(SEP)
    print("  ⚠️  BITTE VOR DEM START LESEN")
    print(SEP)
    print(f"  Als Nächstes werden {len(SET_ENDPOINTS)} SCHREIB-Endpunkte (/v2/Set...) je Gerät")
    print("  angesprochen — mit NUR der Seriennummer, OHNE Steuerwerte.")
    print("  Erwartet wird eine Fehlermeldung wie 'Pflichtfeld fehlt'. An deinem Gerät")
    print("  soll sich dadurch nichts ändern. Eine 100%ige Garantie gibt es nicht, weil")
    print("  die Prüfung auf Dyness-Seite passiert (siehe SICHERHEIT in dieser Datei).")
    print()
    print("  Geräte:")
    for item in plan:
        print(f"    {item['v2_sn']}  [{item['model']}]")
    print("  Endpunkte:")
    for ep in SET_ENDPOINTS:
        print(f"    POST {ep['path']}")
    print(SEP)
    if ASSUME_YES:
        print("  --yes angegeben: Sicherheitsabfrage übersprungen.")
        print()
        return True
    print("  Zum Fortfahren JA eintippen (alles andere bricht ab): ", end="", flush=True)
    try:
        answer = input().strip().upper()
    except EOFError:
        print()
        print("  Keine Eingabe möglich (kein Terminal). Abgebrochen — mit --yes startbar.")
        return False
    print()
    return answer in ("JA", "YES")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    _install_anonymizer()
    print(SEP)
    print(f"  Dyness v2 Control-Capability Probe v{SCRIPT_VERSION}")
    print(f"  v2-Domain: {API_BASE_V2}")
    print(f"  Start: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC   |   "
          f"CALL_DELAY={CALL_DELAY}s  MAX_RETRIES={MAX_RETRIES}  ANONYMIZE={ANONYMIZE}")
    print("  Hinweis: Bitte NICHT parallel zu einem laufenden HA-Poll starten (sonst HTTP 429).")
    if ANONYMIZE:
        print("  Anonymisierung aktiv: Seriennummern maskiert (erste/letzte 4 Zeichen bleiben).")
    print(SEP)
    print()

    devices = discover_devices()
    if not devices:
        return

    # Einmalig GetDeviceList holen -- liefert oft mehrere SN-Schreibweisen
    # (mit/ohne Suffix) pro physischem Gerät.
    device_list_entries = fetch_v2_device_list()
    if device_list_entries:
        print("  v2 GetDeviceList meldet folgende SN-Schreibweisen:")
        for e in device_list_entries:
            print(f"    {e.get('deviceSn','?'):<28} Modell: {e.get('deviceModel','?'):<20} "
                  f"comm={e.get('communicationStatus','?')} work={e.get('workStatus','?')}")
        print()

    # ── Phase 1: nur LESEN — Erreichbarkeit je SN-Kandidat prüfen ─────
    plan = []
    for dev in devices:
        v1_sn = dev.get("deviceSn", "")
        if not v1_sn:
            continue
        model = dev.get("deviceModelName", "?")
        candidates = gather_sn_candidates(v1_sn, device_list_entries)

        v2_sn = candidates[0]
        chosen = False
        checked = []
        for cand in candidates:
            check = api_v2("/v2/GetDeviceInfBySN", {"deviceSn": cand})
            klass = classify("/v2/GetDeviceInfBySN", check)
            checked.append((cand, klass, check.get("info", "")))
            # Nur eine echte Antwort darf einen Kandidaten "wählen" — Rate-Limit,
            # leere Antworten usw. sagen nichts über die SN-Schreibweise.
            if klass not in ("NICHT_VERFUEGBAR",) + tuple(INCONCLUSIVE - {"UNKLAR"}) and not chosen:
                v2_sn = cand
                chosen = True

        if len(candidates) > 1:
            print(f"  SN-Kandidaten für {v1_sn}:")
            for cand, klass, info in checked:
                marker = " <-- gewählt" if cand == v2_sn else ""
                print(f"    {cand:<28} {LABELS.get(klass, klass):<55} ({info!r}){marker}")
            print()
        plan.append({"v1_sn": v1_sn, "model": model, "v2_sn": v2_sn, "chosen": chosen})

    if not plan:
        return

    # ── Phase 2: Bestätigung vor dem ersten SCHREIB-Endpunkt ───────────
    if not confirm_write_probe(plan):
        print("  Abgebrochen. Es wurde KEIN Schreib-Endpunkt angesprochen.")
        _print_stats()
        return

    # ── Phase 3: Schreib-Endpunkte (nur SN) ────────────────────────────
    print(f"  Bestätigung: {'--yes' if ASSUME_YES else 'interaktiv (JA)'}")
    print()
    summary = []
    all_checks_ever_not_available = True   # für Gesamt-Diagnose unten
    for item in plan:
        v2_sn, model, chosen = item["v2_sn"], item["model"], item["chosen"]
        results = probe_device(v2_sn)
        print_device_report(v2_sn, model, results)

        by_class = lambda k: [p for p, (c, _) in results.items() if c == k]
        validated = by_class("VALIDIERT")
        maybe_paths = by_class("PFLICHTFELD_FEHLT")
        generic = by_class("OK_GENERISCH")
        offline = by_class("GERAET_OFFLINE")
        not_avail = by_class("NICHT_VERFUEGBAR")
        inconclusive = [p for p, (c, _) in results.items() if c in INCONCLUSIVE]
        summary.append((v2_sn, model, validated, maybe_paths, generic, offline,
                        not_avail, inconclusive))

        if not chosen or len(not_avail) < len(SET_ENDPOINTS):
            all_checks_ever_not_available = False

    # ── Gesamt-Zusammenfassung ─────────────────────────────────────────
    print(SEP)
    print("  GESAMT-ZUSAMMENFASSUNG")
    print(SEP)
    for sn, model, validated, maybe_paths, generic, offline, not_avail, inconclusive in summary:
        if validated:
            status = f"STEUERBAR ({', '.join(validated)})"
        elif maybe_paths:
            status = f"VERMUTLICH STEUERBAR ({', '.join(maybe_paths)} — echte Werte nötig)"
        elif generic:
            status = f"UNSICHER, nur generisches 200-OK ({', '.join(generic)})"
        elif offline and len(offline) == len(SET_ENDPOINTS):
            status = "GERÄT OFFLINE (v2 kennt es, aktuell nicht erreichbar)"
        elif inconclusive:
            status = (f"UNVOLLSTÄNDIG ({len(inconclusive)} von {len(SET_ENDPOINTS)} Endpunkten "
                      "ohne verwertbare Antwort — kein Urteil)")
        elif len(not_avail) == len(SET_ENDPOINTS):
            status = "ALLE Endpunkte 'not configured' (siehe Diagnose unten)"
        else:
            status = "KEINE STEUERUNGSMÖGLICHKEIT (nur Monitoring)"
        print(f"  {sn:<28} [{model:<14}] -> {status}")
    print(SEP)

    if all_checks_ever_not_available:
        print()
        print("⚠️  DIAGNOSE: Bei JEDEM Gerät waren ALLE v2-Endpunkte (Lesen UND")
        print("   Schreiben) 'not configured'/nicht erreichbar. Das ist vermutlich")
        print("   kein reines Steuerungs-Problem, sondern deutet darauf hin, dass")
        print("   v2 für dieses Konto/diese Geräte(-region) noch gar nicht")
        print("   freigeschaltet ist -- unabhängig von Get vs. Set. Mögliche Ursachen:")
        print("   - Gerät ist (laut v1 bindSn-Antwort) auf ein anderes Konto")
        print("     gebunden und muss ggf. erst auf das API-Konto umgehängt werden")
        print("   - v2 ist für diese Region/diesen Account noch nicht aktiviert")
        print("   -> Beim Dyness-Support gezielt nach v2-Freischaltung fragen,")
        print("      nicht nur nach der Steuerungsfrage.")

    _print_stats()
    print()
    print("Bitte diesen Report als Kommentar/Issue teilen:")
    print("https://github.com/shopf/dyness_battery")


def _print_stats():
    print()
    print(f"  Aufruf-Statistik: Calls: {STATS['calls']}   HTTP-429: {STATS['rate_limited']} "
          f"(nach Retries weiter 429: {STATS['rate_limited_final']})   "
          f"leere/ungültige Antworten: {STATS['empty']}   Verbindungsfehler: {STATS['errors']}")
    if STATS["rate_limited_final"]:
        print("  ⚠️  Einzelne Antworten fehlen wegen Rate-Limit. Bitte HA-Integration kurz "
              "deaktivieren und das Skript erneut starten.")


if __name__ == "__main__":
    main()
