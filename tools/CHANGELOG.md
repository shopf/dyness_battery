# Changelog der Hilfsskripte (`tools/`)

Gilt gemeinsam für `dyness_test.py` und `dyness_control_probe.py`. Die Skripte selbst enthalten bewusst keine Versionshistorie mehr, damit die Zugangsdaten direkt am Dateianfang stehen.

---

## dyness_test.py

### v4.3 — Robustheit + Diagnose
- **Rate-Limit-Schutz:** Mindestabstand zwischen Calls (`CALL_DELAY`) und automatischer Retry mit Backoff bei HTTP 429 (`TOO_MANY_REQUESTS`). Vorher entstanden bei parallelem HA-Betrieb Lücken in den Modul-Calls.
- **Leere/ungültige Antworten** werden erkannt und gezählt (typisch für eine Störung auf Dyness-Seite, kein Fehler des Geräts).
- **`ANONYMIZE`:** Seriennummern werden maskiert, Stationsname/Standort/Bild-URL entfernt — das Log kann direkt ins GitHub-Issue kopiert werden.
- **Einzelmodul** (genau 1 `SUB`) wird jetzt zusätzlich abgefragt (Diagnose). Die Integration nutzt das bisher für PowerBrick-Zellspannungen. Vorher: "kein separater v1-Abruf". Antwort "not bound" gilt als Info, nicht als Fehler.
- **Stack100 mit leerem `SUB`:** Diagnose-Probe mit abgeleiteten Modul-SNs (`<SN>-01` ... `<SN>-NN` aus Point 4300). Reine Diagnose des Skripts — sie zeigt, ob die Module abrufbar wären, und sagt nichts darüber, was die Integration tut.
- **v2-Datenqualität:** erwartete vs. gelieferte Zellen/Temperaturen, auch je Modul in `bmsInfoList`. "200 OK" allein gilt nicht mehr als "verfügbar".
- **Vergleich v1 `workStatus` <-> v2 `batteryStatus`** (+ Stromrichtung). Verglichen wird nur, wenn v1 ein echtes Statuswort liefert (z. B. nicht bei `RunMode`).
- Fix: Zellspannungs-Differenz wurde bei Werten mit Einheit (`"3.345V"`) stillschweigend nicht berechnet.
- Fix: `GetDeviceInfBySN` wurde im Fehlerfall für jeden Kandidaten doppelt aufgerufen.
- Fix: `200` mit `data: null` wurde in der Zusammenfassung als "verfügbar" gewertet.
- Zusammenfassung: keine pauschalen "x % weniger Calls"-Aussagen mehr.
- Layout: Zugangsdaten stehen jetzt in den ersten Zeilen der Datei.

### v4.2 — v2-only Geräte (z. B. DYNE 8.0L-1P-A / Dyness LV)
- Auto-Discovery-Fallback: liefert `/v1/device/storage/list` 0 Geräte, wird automatisch `/v2/GetDeviceList` als Discovery-Quelle genutzt (v2-only Geräte erscheinen nur dort).
- SN-Kandidaten: `GetDeviceList` liefert ggf. mehrere SN-Schreibweisen pro physischem Gerät (z. B. mit/ohne `-BMS`-Suffix) — alle werden systematisch für `GetDeviceInfBySN` durchprobiert statt nur 2 Varianten.
- `print_summary` erkennt das "v2-only"-Muster und gibt eine klare Diagnose aus.
- Neue Flag-Variable `v2_only_device` für Downstream-Logik.

### v4.1 — Erweiterung aus der Swagger-Dokumentation
- `/v1/device/getBindDeviceSnListByCurrentUserId` (GET, einfacher Discovery-Call)
- `/v1/device/houseHold/list` (liefert `masterSlaveStatus` + `parentCode`)
- `/v1/storage/detail` (GET, kompakte Alternative zu `household/storage/detail`)
- `/v1/device/getEnergyDataBySn` (Energiezähler Tag/Gesamt)
- `/v1/alarm/query` FIX: korrekter Body mit `deviceSnList` (Array) statt `deviceSn`
- GET-Endpunkte korrekt implementiert: kein JSON-Body, Query-Parameter, Signatur-String-Methode `GET` statt `POST` (vorher fälschlich als POST mit leeren/fehlenden Pflichtfeldern aufgerufen -> HTTP 500).

### Hintergrund: Geräteserien mit v2-Dokumentation
- Low-voltage battery (DL5, PowerDepot G2, PowerBox G2, Junior Box)
- High-voltage battery (Tower, Stack100, PowerBox Pro)
- Cygni HA/HS (Hybrid-Wechselrichter)
- HT-A / LS Series (All-in-One-Systeme)
- AquaVolt / AquaVolt_LV / SolarCube

---

## dyness_control_probe.py

### v1.1 — Sicherheit + Robustheit
- **Sicherheitsabfrage** vor dem ersten Schreib-Endpunkt: Das Skript zeigt Geräte und Endpunkte und verlangt `JA`. `--yes` überspringt die Abfrage; ohne Terminal bricht es ab. Die Bestätigung wird im Log vermerkt.
- **Zwei Phasen:** erst nur lesend (Erreichbarkeit der SN-Kandidaten), dann Abfrage, dann erst die Schreib-Endpunkte (weiterhin nur `{"deviceSn": ...}`, nie Steuerwerte).
- **Rate-Limit-Schutz** wie in `dyness_test.py` v4.3 (Mindestabstand, Retry mit Backoff bei HTTP 429, Aufruf-Statistik am Ende).
- **Neue Ergebnisklassen**, die bewusst KEIN Urteil über die Steuerbarkeit erlauben: `RATE_LIMIT`, `LEERE_ANTWORT`, `VORUEBERGEHEND` ("Network Anomaly"), `NICHT_GEBUNDEN` ("not bound").
- Fix: Eine unklare Antwort (`UNKLAR`) führte zum Urteil "KEINE STEUERUNGSMOEGLICHKEIT GEFUNDEN". Jetzt: "UNVOLLSTAENDIG — kein Urteil".
- Fix: Die Wahl des SN-Kandidaten bei `GetDeviceInfBySN` wird nicht mehr durch 429 oder leere Antworten verfälscht.
- Fix: Eine JSON-Liste statt eines Objekts als Antwort führte zu einem Absturz.
- **`ANONYMIZE`:** Seriennummern maskiert, Stationsdaten entfernt (wie `dyness_test.py`).
- Entfernt: der wirkungslose Schalter `ALLOW_REQUIRED_FIELD_PROBE`.
- Layout: Zugangsdaten am Dateianfang; das Sicherheitskonzept steht als Kommentarblock direkt darunter (bewusst nicht ausgelagert, damit es vor dem Start gelesen wird).

### v1.0 — Erstversion
- Testet alle 10 bekannten v2-Set-Endpunkte gegen jede gefundene Seriennummer, ohne Steuerwerte zu senden (nur `{"deviceSn": ...}`), um zu erkennen, welche Endpunkte für welches Gerät existieren.
