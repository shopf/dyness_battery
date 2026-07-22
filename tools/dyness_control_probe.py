"""
Dyness v2 Control-Capability Probe v1.0
=========================================
Ergänzung zu dyness_test.py (shopf/dyness_battery)

ZWECK
-----
dyness_test.py prüft bereits sehr gründlich die LESE-Endpunkte der v2-API
(GetDeviceInfBySN, GetRealTimeDataBySN, GetAlarmInfBySN, GetStatusInfBySN,
GetTotalEnergyDataBySN, GetParallelInfBySN, GetDeviceList).

Was bisher fehlt: ein Test der SCHREIB-/STEUER-Endpunkte. Aus den
offiziellen Dyness-Protokoll-PDFs (Junior Box, High-voltage battery,
Low-voltage battery, Cygni HA/HS, AquaVolt/AquaVolt_LV/SolarCube,
HT-A/LS Series) ergeben sich insgesamt 10 unterschiedliche v2-Set-Endpunkte,
die je nach Produktfamilie unterschiedlich zusammengestellt sind:

  /v2/SetBaseSetting            (Junior Box, Cygni, AquaVolt/SolarCube, HT-A/LS)
  /v2/SetWorkModeSetting        (Junior Box, Cygni, AquaVolt/SolarCube, HT-A/LS)
  /v2/SetBatterySetting         (High-/Low-voltage battery, Cygni, AquaVolt/SolarCube, HT-A/LS)
  /v2/SetLoadControlSetting     (Cygni, AquaVolt/SolarCube)
  /v2/SetPeakControlSetting     (Cygni, AquaVolt/SolarCube)
  /v2/SetAdvancedSetting        (Cygni, AquaVolt/SolarCube, HT-A/LS)
  /v2/SetGeneratorControlSetting(Cygni)
  /v2/SetCtrlDataBySN           (HT-A/LS — generischer Register-Zugriff)
  /v2/SetDspSetting             (HT-A/LS)
  /v2/SetArmSetting             (HT-A/LS)

Welche Produktbezeichnung (Tower T14, TP7, DL5.0C, Stack100, PowerBox Pro/G2,
PowerHaus, ...) tatsächlich zu welcher Dokument-Familie gehört, ist NICHT
zweifelsfrei aus den PDFs ablesbar (dort stehen nur interne Modell-Codes wie
"HT-A"/"LS"). Deshalb testet dieses Script bewusst ALLE 10 bekannten
Set-Endpunkte gegen jede gefundene Seriennummer — das beantwortet empirisch
und zuverlässig "was geht wirklich", unabhängig von der (unsicheren)
Namens-Zuordnung.

SICHERHEITSKONZEPT — WICHTIG
----------------------------
Dieses Script sendet standardmäßig NUR {"deviceSn": "..."} an jeden
Set-Endpunkt — also OHNE die eigentlichen Steuerparameter (workMode,
powerLimit, loadSwitch, ...). Das reicht, um zu erkennen, ob der Endpunkt
für dieses Gerät überhaupt existiert:

  - 200/Erfolg                    -> Endpunkt vorhanden, hat Aufruf akzeptiert
                                      (bei rein optionalen Zusatzfeldern
                                      ändert sich am Gerät nichts, da keine
                                      Werte übergeben wurden)
  - Fehlermeldung "Parameter
    fehlt / erforderlich"         -> Endpunkt vorhanden, benötigt aber
                                      echte Werte (siehe jeweiliges PDF)
  - 404 / "nicht konfiguriert" /
    "device type mismatch"        -> Endpunkt für dieses Gerät NICHT
                                      implementiert

ACHTUNG: Bei einigen Endpunkten (v.a. Junior Box: SetBaseSetting,
SetWorkModeSetting) sind laut PDF ALLE Zusatzfelder (workMode, powerLimit,
dischargeDepth, workGroups) PFLICHTFELDER. Ein deviceSn-only-Aufruf wird
dort aller Voraussicht nach mit einem Validierungsfehler abgelehnt, OHNE
dass am Gerät etwas verändert wird (Validierung passiert serverseitig vor
der Befehlsweiterleitung an die Hardware — das ist Standardverhalten bei
praktisch allen Cloud-APIs, aber eine 100%ige Garantie kann ich dir nicht
geben, da ich das Verhalten des Dyness-Servers nicht selbst beobachten kann).

ECHTE Steuerbefehle mit echten Werten (die dein Gerät TATSÄCHLICH umstellen
würden, z.B. Betriebsmodus oder Leistungsgrenzen) sendet dieses Script NIE
automatisch. Das bleibt bewusst ein manueller, expliziter zweiter Schritt.

Verwendung:
    pip install requests
    python3 dyness_control_probe.py

Bitte Ergebnis als Kommentar/Issue teilen:
https://github.com/shopf/dyness_battery
"""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import hashlib, hmac, base64, json, re, requests
from email.utils import formatdate

# ===== HIER DEINE ZUGANGSDATEN EINTRAGEN =====
API_ID     = "DEINE_API_ID"
API_SECRET = "DEIN_API_SECRET"

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

# Sicherheitsschalter — siehe Kommentar oben. NICHT auf True setzen, ohne
# die Werte in probe_body() vorher an die eigene Situation angepasst und
# verstanden zu haben. Standard: aus.
ALLOW_REQUIRED_FIELD_PROBE = False
# =============================================

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


def _post(base_url: str, path: str, body_dict: dict) -> dict:
    date = formatdate(timeval=None, localtime=False, usegmt=True)
    body = json.dumps(body_dict, separators=(',', ':'), sort_keys=True)
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
        try:
            j = r.json()
        except Exception:
            j = {"code": str(r.status_code), "info": r.text[:300]}
        j["_http_status"] = r.status_code
        return j
    except Exception as e:
        return {"error": str(e), "code": "ERR", "_http_status": None}


def api_v1(path, body): return _post(API_BASE_V1, path, body)
def api_v2(path, body): return _post(API_BASE_V2, path, body)


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


def classify(path: str, result: dict) -> str:
    """Ordnet die Server-Antwort einer von vier Kategorien zu."""
    http_status = result.get("_http_status")
    code = str(result.get("code", ""))
    info = str(result.get("info", "")).lower()

    if http_status == 404 or code == "404":
        return "NICHT_VERFUEGBAR"
    if any(h in info for h in OFFLINE_HINTS):
        return "GERAET_OFFLINE"
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
    "VALIDIERT":        "✅ steuerbar (Server nennt echten Parameter aus dem PDF)",
    "PFLICHTFELD_FEHLT": "🟡 Endpunkt vorhanden (Validierungsfehler, Feld unklar)",
    "OK_GENERISCH":     "⚠️  200 OK, aber OHNE Feldbezug — evtl. nur generisches No-Op",
    "NICHT_VERFUEGBAR": "❌ nicht verfügbar für dieses Gerät",
    "GERAET_OFFLINE":   "📡 Gerät offline (v2 kennt es, kann es aber gerade nicht erreichen)",
    "UNKLAR":           "❔ unklar — Rohantwort prüfen",
}


def probe_body(sn: str, path: str) -> dict:
    """Baut den *sicheren* Test-Body: nur deviceSn, keine echten
    Steuerwerte. Wird ALLOW_REQUIRED_FIELD_PROBE genutzt, ändert sich
    hier nichts — der Schalter dient nur der Dokumentation/Erweiterung
    und sendet bewusst weiterhin keine operativen Werte, solange du diese
    Funktion nicht selbst um echte Werte ergänzt."""
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
    any_validated = False
    any_maybe = False
    any_generic = False
    any_offline = False
    all_not_available = True
    for ep in SET_ENDPOINTS:
        klass, res = results[ep["path"]]
        label = LABELS[klass]
        print(f"  {ep['path']:<32} {label}")
        # Rohantwort IMMER zeigen
        info = res.get("info", "")
        code = res.get("code", "")
        print(f"      -> code={code!r}  info={info!r}  http={res.get('_http_status')}")
        if klass == "VALIDIERT":
            any_validated = True
        elif klass == "PFLICHTFELD_FEHLT":
            any_maybe = True
        elif klass == "OK_GENERISCH":
            any_generic = True
        elif klass == "GERAET_OFFLINE":
            any_offline = True
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


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    print(SEP)
    print("  Dyness v2 Control-Capability Probe")
    print(f"  v2-Domain: {API_BASE_V2}")
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

    summary = []
    all_checks_ever_not_available = True   # für Gesamt-Diagnose unten
    for dev in devices:
        v1_sn = dev.get("deviceSn", "")
        model = dev.get("deviceModelName", "?")
        candidates = gather_sn_candidates(v1_sn, device_list_entries)

        # Erreichbarkeit über ALLE Kandidaten prüfen (nicht nur einen Fallback)
        v2_sn = candidates[0]
        chosen = False
        checked = []
        for cand in candidates:
            check = api_v2("/v2/GetDeviceInfBySN", {"deviceSn": cand})
            klass = classify("/v2/GetDeviceInfBySN", check)
            checked.append((cand, klass, check.get("info", "")))
            if klass not in ("NICHT_VERFUEGBAR",) and not chosen:
                v2_sn = cand
                chosen = True

        if len(candidates) > 1:
            print(f"  SN-Kandidaten für {v1_sn}:")
            for cand, klass, info in checked:
                marker = " <-- gewählt" if cand == v2_sn else ""
                print(f"    {cand:<28} {LABELS.get(klass, klass):<55} ({info!r}){marker}")
            print()

        results = probe_device(v2_sn)
        print_device_report(v2_sn, model, results)

        validated = [p for p, (k, _) in results.items() if k == "VALIDIERT"]
        maybe_paths = [p for p, (k, _) in results.items() if k == "PFLICHTFELD_FEHLT"]
        generic = [p for p, (k, _) in results.items() if k == "OK_GENERISCH"]
        offline = [p for p, (k, _) in results.items() if k == "GERAET_OFFLINE"]
        not_avail = [p for p, (k, _) in results.items() if k == "NICHT_VERFUEGBAR"]
        summary.append((v2_sn, model, validated, maybe_paths, generic, offline, not_avail))

        if not chosen or len(not_avail) < len(SET_ENDPOINTS):
            all_checks_ever_not_available = False

    # ── Gesamt-Zusammenfassung ─────────────────────────────────────────
    print(SEP)
    print("  GESAMT-ZUSAMMENFASSUNG")
    print(SEP)
    for sn, model, validated, maybe_paths, generic, offline, not_avail in summary:
        if validated:
            status = f"STEUERBAR ({', '.join(validated)})"
        elif maybe_paths:
            status = f"VERMUTLICH STEUERBAR ({', '.join(maybe_paths)} — echte Werte nötig)"
        elif generic:
            status = f"UNSICHER, nur generisches 200-OK ({', '.join(generic)})"
        elif offline and len(offline) == len(SET_ENDPOINTS):
            status = "GERÄT OFFLINE (v2 kennt es, aktuell nicht erreichbar)"
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

    print()
    print("Bitte diesen Report als Kommentar/Issue teilen:")
    print("https://github.com/shopf/dyness_battery")


if __name__ == "__main__":
    main()
