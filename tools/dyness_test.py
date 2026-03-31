"""
Dyness API Tester
Testet alle bekannten Endpunkte der Dyness Open API und gibt die Antworten aus.

Verwendung:
    pip install requests
    python3 dyness_test.py

Die Ausgabe zeigt welche Endpunkte für dein Gerät funktionieren.
Bitte teile sie als Issue auf GitHub wenn du ein anderes Dyness-Modell testest!
https://github.com/shopf/dyness_battery

WARNUNG: unBindSn ist im Skript deaktiviert! Niemals aktivieren ohne danach
         sofort bindSn erneut aufzurufen — sonst verlierst du den API-Zugriff.
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
DEVICE_SN  = "DEINE_BATTERIE_SN"   # z.B. R07ABCDEF123456XX-BMS
DONGLE_SN  = "DEIN_DONGLE_SN"      # z.B. R07ABCDEF123456XX (ohne -BMS)
API_BASE   = "https://open-api.dyness.com/openapi/ems-device"
# =============================================


def get_md5(body: str) -> str:
    md5 = hashlib.md5(body.encode("utf-8")).digest()
    return base64.b64encode(md5).decode("utf-8")


def get_signature(secret: str, content_md5: str, date: str, path: str) -> str:
    string_to_sign = f"POST\n{content_md5}\napplication/json\n{date}\n{path}"
    sig = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), "sha1").digest()
    return base64.b64encode(sig).decode("utf-8")


def api_call(path: str, body_dict: dict, method: str = "POST") -> dict:
    url = f"{API_BASE}{path}"
    body = json.dumps(body_dict, separators=(',', ':')) if body_dict else "{}"
    date = formatdate(timeval=None, localtime=False, usegmt=True)
    content_md5 = get_md5(body)
    signature = get_signature(API_SECRET, content_md5, date, path)
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Content-MD5": content_md5,
        "Date": date,
        "Authorization": f"API {API_ID}:{signature}",
    }
    if method == "GET":
        response = requests.get(url, headers=headers, timeout=15)
    else:
        response = requests.post(url, headers=headers, data=body, timeout=15)
    return response.json()


def test(name: str, path: str, body: dict, method: str = "POST", known: str = ""):
    label = f"[{known}]" if known else ""
    print(f"\n{'='*60}")
    print(f"Endpunkt: {name} {label}")
    print(f"Path: {path}")
    print(f"Body: {json.dumps(body, indent=2)}")
    print(f"{'='*60}")
    try:
        result = api_call(path, body, method)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"FEHLER: {e}")


if __name__ == "__main__":

    # ══════════════════════════════════════════════════════════
    # GERÄT BINDEN
    # ══════════════════════════════════════════════════════════

    test("Gerät binden",
         "/v1/device/bindSn",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN},
         known="Junior Box: OK")

    # ⚠️  ACHTUNG: unBindSn ist absichtlich deaktiviert!
    # Es entbindet das Gerät dauerhaft von der API.
    # Danach funktioniert KEIN anderer Endpunkt mehr.
    # Nur aktivieren wenn du weißt was du tust — und danach
    # sofort bindSn erneut aufrufen!
    #
    # test("Gerät entbinden  ⚠️  GEFÄHRLICH!",
    #      "/v1/device/unBindSn",
    #      {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    # ══════════════════════════════════════════════════════════
    # GERÄTEINFORMATIONEN
    # ══════════════════════════════════════════════════════════

    test("Household Storage Detail",
         "/v1/device/household/storage/detail",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN},
         known="Junior Box: OK")

    test("Storage Geräteliste",
         "/v1/device/storage/list",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN},
         known="Junior Box: OK — liefert workStatus")

    test("Anlageninfo",
         "/v1/station/info",
         {"deviceSn": DEVICE_SN},
         known="Junior Box: OK")

    test("Firmware-Version prüfen",
         "/v1/device/checkVersion",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    test("Household Geräteliste",
         "/v1/device/houseHold/list",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    test("Inverter-Liste",
         "/v1/device/inverter/list",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    test("Inverter Lastdetails",
         "/v1/device/inverter/load/detail",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    # ══════════════════════════════════════════════════════════
    # ECHTZEIT- & LEISTUNGSDATEN
    # ══════════════════════════════════════════════════════════

    test("Letzte Leistungsdaten",
         "/v1/device/getLastPowerDataBySn",
         {"pageNo": 1, "pageSize": 1, "deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN},
         known="Junior Box: OK")

    test("Letzte Betriebsdaten",
         "/v1/device/getLastRunningDataBySn",
         {"deviceSn": DEVICE_SN},
         known="Junior Box: alle Felder null")

    test("Echtzeit-Daten (realTime/data)",
         "/v1/device/realTime/data",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    test("Gerät lesen (einzelne/mehrere Punkte)",
         "/v1/device/read",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    # ══════════════════════════════════════════════════════════
    # ENERGIEDATEN / HISTORISCH
    # ══════════════════════════════════════════════════════════

    test("Energiedaten nach Datum",
         "/v1/device/getEnergyDataBySn",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN, "date": "2026-03-12"},
         known="Junior Box: Fehler ohne date-Parameter")

    test("Historische Daten exportieren",
         "/v1/device/history/export/task",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    # ══════════════════════════════════════════════════════════
    # EINSTELLUNGEN LESEN
    # ══════════════════════════════════════════════════════════

    test("Batterie-Einstellungen",
         "/v1/battery/setting/detail",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN},
         known="Junior Box: Network Anomaly")

    test("Peak Control Einstellungen",
         "/v1/peakControl/setting/detail",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN},
         known="Junior Box: Network Anomaly")

    test("Laststeuerung Einstellungen",
         "/v1/loadControl/setting/detail",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    test("Erweiterte Einstellungen",
         "/v1/advanced/setting/detail",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    test("Basis-Einstellungen lesen",
         "/v1/base/read",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    test("Sicherheitseinstellungen",
         "/v1/safety/setting/detail",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    test("Systemzeit-Einstellungen",
         "/v1/systemTime/setting/detail",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    test("Fehlerwarnung Einstellungen",
         "/v1/faultWarn/setting/detail",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    test("Erkennung/Inspektion Einstellungen",
         "/v1/detection/setting/single/detail",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    test("Inverter-Einstellungen Detail",
         "/v1/inverter/setting/detail",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    test("Inverter-Einstellung einzeln lesen",
         "/v1/inverter/setting/single/detail",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    # ══════════════════════════════════════════════════════════
    # LADE-/ENTLADE-KONFIGURATION
    # ══════════════════════════════════════════════════════════

    test("Lade-/Entlade-Konfiguration",
         "/v1/device/singleGetChargeDischargeConfig",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN},
         known="Junior Box: Device connection timed out")

    test("Nur Entlade-Konfiguration",
         "/v1/device/singleGetDischargeConfig",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    # ══════════════════════════════════════════════════════════
    # ALARM & FEHLER
    # ══════════════════════════════════════════════════════════

    test("Alarm-/Fehlerliste",
         "/v1/alarm/query",
         {"deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN})

    # ══════════════════════════════════════════════════════════
    # GRUPPEN
    # ══════════════════════════════════════════════════════════

    test("Gruppen-Liste",
         "/v1/group/getGroupList",
         {})

    test("Safety-Code Liste",
         "/v1/group/getSafelyList",
         {})

    test("System-Verknüpfungsliste (Inverter ↔ Datenlogger)",
         "/v1/group/getSystemList",
         {})

    print(f"\n{'='*60}")
    print("Test abgeschlossen!")
    print("Bitte teile diese Ausgabe auf GitHub wenn du ein neues")
    print("Dyness-Modell testest: https://github.com/shopf/dyness_battery")
    print(f"{'='*60}\n")
