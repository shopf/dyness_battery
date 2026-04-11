"""
Dyness API Tester v2
Testet alle bekannten Endpunkte der Dyness Open API.

Verwendung:
    pip install requests
    python3 dyness_test.py

Nur API ID und Secret nötig — Gerät und Module werden automatisch erkannt.
Bitte Output als Issue auf GitHub teilen wenn du ein neues Dyness-Modell testest!
https://github.com/shopf/dyness_battery

WARNUNG: unBindSn ist deaktiviert! Niemals aktivieren ohne danach sofort
         bindSn erneut aufzurufen — sonst verlierst du den API-Zugriff.
"""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import hashlib
import hmac
import base64
import json
import requests
from email.utils import formatdate

# ===== HIER DEINE ZUGANGSDATEN EINTRAGEN =====
API_ID     = "DEINE_API_ID"
API_SECRET = "DEIN_API_SECRET"
API_BASE   = "https://open-api.dyness.com/openapi/ems-device"

# Optional: Wenn Auto-Discovery fehlschlägt, hier manuell eintragen
DEVICE_SN  = ""   # z.B. R07ABCDEF123456XX-BMS  (leer lassen für Auto-Discovery)
DONGLE_SN  = ""   # z.B. R07ABCDEF123456XX       (leer lassen für Auto-Discovery)
# =============================================

SEP = "=" * 60


def get_md5(body: str) -> str:
    return base64.b64encode(hashlib.md5(body.encode("utf-8")).digest()).decode("utf-8")


def get_signature(secret: str, content_md5: str, date: str, path: str) -> str:
    sts = f"POST\n{content_md5}\napplication/json\n{date}\n{path}"
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), sts.encode("utf-8"), "sha1").digest()
    ).decode("utf-8")


def api_call(path: str, body_dict: dict) -> dict:
    url = f"{API_BASE}{path}"
    body = json.dumps(body_dict, separators=(',', ':'))
    date = formatdate(timeval=None, localtime=False, usegmt=True)
    md5 = get_md5(body)
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Content-MD5": md5,
        "Date": date,
        "Authorization": f"API {API_ID}:{get_signature(API_SECRET, md5, date, path)}",
    }
    try:
        r = requests.post(url, headers=headers, data=body, timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def print_result(label: str, path: str, body: dict, result: dict):
    print(SEP)
    print(f"Endpunkt: {label}")
    print(f"Path: {path}")
    print(f"Body: {json.dumps(body, indent=2, ensure_ascii=False)}")
    print(SEP)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print()


def get_rt_points(result: dict) -> dict:
    """Wandelt realTime/data Liste in Dict um."""
    raw = result.get("data", []) or []
    return {item["pointId"]: item["pointValue"]
            for item in raw if isinstance(item, dict) and "pointId" in item}


# ── Auto-Discovery ────────────────────────────────────────────────────────────
print(SEP)
print("Dyness API Tester v2 — Auto-Discovery")
print(SEP)
print()

device_sn = DEVICE_SN.strip()
dongle_sn = DONGLE_SN.strip()

if not device_sn:
    print("► Suche Geräte auf diesem Account...")
    sl = api_call("/v1/device/storage/list", {})
    print_result("Storage Geräteliste (Auto-Discovery)", "/v1/device/storage/list", {}, sl)
    code = str(sl.get("code", ""))
    if code in ("0", "200") or sl.get("code") == 0:
        devs = (sl.get("data", {}) or {}).get("list", [])
        if devs:
            # Bevorzuge -BMS oder -BDU Suffix
            bms = next((d for d in devs if str(d.get("deviceSn","")).endswith(("-BMS","-BDU"))), devs[0])
            device_sn = bms.get("deviceSn", "")
            dongle_sn = bms.get("collectorSn", "") or ""
            print(f"✅ Gerät gefunden: {device_sn}")
            if dongle_sn:
                print(f"   Dongle SN:    {dongle_sn}")
            if len(devs) > 1:
                print(f"   Weitere Geräte auf diesem Account:")
                for d in devs:
                    print(f"   - {d.get('deviceSn')} ({d.get('deviceModelName','?')})")
        else:
            print("❌ Keine Geräte gefunden. Bitte DEVICE_SN manuell eintragen.")
            sys.exit(1)
    else:
        print(f"❌ API Fehler: {sl.get('info')} — Bitte DEVICE_SN manuell eintragen.")
        sys.exit(1)
else:
    print(f"► Verwende manuell eingetragene SN: {device_sn}")

print()
body_sn = {"deviceSn": device_sn}
body_full = {"deviceSn": device_sn, "collectorSn": dongle_sn} if dongle_sn else body_sn

# ── Gerät binden ──────────────────────────────────────────────────────────────
res = api_call("/v1/device/bindSn", body_full)
print_result("Gerät binden", "/v1/device/bindSn", body_full, res)

# ── Household Storage Detail ──────────────────────────────────────────────────
res = api_call("/v1/device/household/storage/detail", body_full)
print_result("Household Storage Detail", "/v1/device/household/storage/detail", body_full, res)

# ── Storage Liste (workStatus) ────────────────────────────────────────────────
res = api_call("/v1/device/storage/list", body_full)
print_result("Storage Geräteliste [liefert workStatus]", "/v1/device/storage/list", body_full, res)

# ── Anlageninfo ───────────────────────────────────────────────────────────────
res = api_call("/v1/station/info", body_sn)
print_result("Anlageninfo [batteryCapacity]", "/v1/station/info", body_sn, res)

# ── realTime/data Master ──────────────────────────────────────────────────────
res = api_call("/v1/device/realTime/data", body_full)
print_result("Echtzeit-Daten Master (realTime/data)", "/v1/device/realTime/data", body_full, res)

rt = get_rt_points(res)

# SUB Point auswerten
sub_raw = rt.get("SUB", "")
battery_count = rt.get("400", "?")
print(f"► Point 400 (Batterieanzahl): {battery_count}")
print(f"► Point SUB (Sub-Module):     {sub_raw!r}")
print()

# Sub-Module ermitteln
import re
sub_sns = []
if sub_raw:
    candidates = [s.strip() for s in str(sub_raw).split(",") if s.strip()]
    filtered = [s for s in candidates
                if not s.endswith(("-BMS", "-BDU"))
                and not re.search(r'-BDU-\d+$', s)]
    if len(filtered) > 1:
        sub_sns = filtered
        print(f"► {len(sub_sns)} parallele Sub-Modul(e) erkannt: {sub_sns}")
    elif len(filtered) == 1:
        print(f"► 1 Sub-Modul gefunden ({filtered[0]}) — kein separater Abruf nötig (Junior Box / Einzelmodul)")
    else:
        print("► Keine abfragbaren Sub-Module im SUB Point.")
else:
    print("► SUB Point leer — kein Multi-Modul Setup.")
print()

# ── Leistungsdaten ────────────────────────────────────────────────────────────
body_power = {"pageNo": 1, "pageSize": 3, "deviceSn": device_sn}
if dongle_sn:
    body_power["collectorSn"] = dongle_sn
res = api_call("/v1/device/getLastPowerDataBySn", body_power)
print_result("Letzte Leistungsdaten [SOC/Power]", "/v1/device/getLastPowerDataBySn", body_power, res)

# ── Sub-Module abfragen ───────────────────────────────────────────────────────
for sn in sub_sns:
    print(SEP)
    print(f"Sub-Modul: {sn}")
    print(SEP)
    m_body = {"deviceSn": sn}
    if dongle_sn:
        m_body["collectorSn"] = dongle_sn
    res = api_call("/v1/device/realTime/data", m_body)
    print_result(f"realTime/data Sub-Modul {sn}", "/v1/device/realTime/data", m_body, res)
    m_pts = get_rt_points(res)
    # Wichtige Points ausgeben
    print(f"  Point 10000 (Modul-SN):    {m_pts.get('10000', '—')}")
    print(f"  Point 14000 (SOC):         {m_pts.get('14000', '—')}")
    print(f"  Point 14100 (SOH):         {m_pts.get('14100', '—')}")
    print(f"  Point 13900 (Zyklen):      {m_pts.get('13900', '—')}")
    print(f"  Point 10300 (Cell 1 V):    {m_pts.get('10300', '—')}")
    print(f"  Point 11200 (Tower Cell?): {m_pts.get('11200', '—')}")
    print()

# ── Weitere Standard-Endpunkte ────────────────────────────────────────────────
for label, path, body in [
    ("Firmware-Version", "/v1/device/checkVersion", body_full),
    ("Letzte Betriebsdaten", "/v1/device/getLastRunningDataBySn", body_sn),
    ("Energiedaten nach Datum", "/v1/device/getEnergyDataBySn",
     {**body_full, "date": "2026-03-12"}),
    ("Alarm-/Fehlerliste", "/v1/alarm/query", body_full),
    ("Gruppen-Liste", "/v1/group/getGroupList", {}),
    ("Safety-Code Liste", "/v1/group/getSafelyList", {}),
    ("System-Verknüpfungsliste", "/v1/group/getSystemList", {}),
]:
    res = api_call(path, body)
    print_result(label, path, body, res)

print(SEP)
print("Test abgeschlossen!")
print("Bitte teile diese Ausgabe auf GitHub wenn du ein neues")
print("Dyness-Modell testest: https://github.com/shopf/dyness_battery")
print(SEP)
