# Dyness Battery – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/shopf/dyness_battery.svg)](https://github.com/shopf/dyness_battery/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Eine Community-Integration für Home Assistant für **Dyness Batteriespeicher** über die Dyness Cloud API.

> **Hinweis / Note:** Diese Integration nutzt die Dyness Open API (Cloud). Eine Internetverbindung ist erforderlich. Daten werden alle 5 Minuten aktualisiert (API-Limit).
> This integration uses the Dyness Open API (Cloud). An internet connection is required. Data is updated every 5 minutes (API limit).

---

## 🇩🇪 Deutsch

### Unterstützte Geräte

| Gerät | Status |
|-------|--------|
| Dyness Junior Box | ✅ Getestet |
| Dyness Tower (non-pro) | ✅ Sollte funktionieren (Community-getestet) |
| Dyness DL5.0C | ✅ Sollte funktionieren (Community-getestet) |
| Andere Dyness-Modelle mit WiFi-Dongle | ⚠️ Nicht getestet – Feedback willkommen |

> Die Integration erkennt das Gerät automatisch über die API und registriert nur die Sensoren, die für das jeweilige Gerät verfügbar sind.

### Verfügbare Sensoren

Die folgenden Sensoren sind für alle Geräte verfügbar:

| Sensor | Beschreibung | Einheit |
|--------|-------------|---------|
| Ladestand (SOC) | Aktueller Ladestand | % |
| Leistung | Lade-/Entladeleistung (+ = laden, − = entladen) | W |
| Strom | Lade-/Entladestrom | A |
| Batteriestatus | Charging / Discharging / Standby | – |

Zusätzliche Sensoren werden automatisch aktiviert, sofern das Gerät die Daten liefert:

| Sensor | Beschreibung | Einheit | Junior Box | Tower | DL5.0C |
|--------|-------------|---------|:---:|:---:|:---:|
| Pack-Spannung | Gesamtspannung des Akkupacks | V | ✅ | – | ✅ |
| Batteriezustand (SOH) | State of Health | % | ✅ | ✅ | ✅ |
| Temperatur Max | Höchste Zellentemperatur | °C | ✅ | ✅ | ✅ |
| Temperatur Min | Niedrigste Zellentemperatur | °C | ✅ | ✅ | ✅ |
| Zellspannung Max | Höchste Einzelzellspannung | V | ✅ | ✅ | ✅ |
| Zellspannung Min | Niedrigste Einzelzellspannung | V | ✅ | ✅ | ✅ |
| Zellspannungsdifferenz | Max − Min Zellspannung (Gesundheitsindikator) | mV | ✅ | ✅ | ✅ |
| Heute geladen | Geladene Energie heute | kWh | ✅ | – | ✅ |
| Heute entladen | Entladene Energie heute | kWh | ✅ | – | ✅ |
| Gesamt geladen | Kumuliert geladene Energie | kWh | ✅ | ✅ | ✅ |
| Gesamt entladen | Kumuliert entladene Energie | kWh | ✅ | – | ✅ |
| Ladezyklen | Anzahl Batteriezyklen | – | – | ✅ | – |
| Nutzbare Kapazität | Kapazität × SOH | kWh | ✅ | ✅ | ✅ |
| Verbleibende Energie | Nutzbare Kapazität × SOC | kWh | ✅ | ✅ | ✅ |

Folgende Sensoren sind unter **Diagnose** auf der Geräteseite verfügbar:

| Sensor | Beschreibung |
|--------|-------------|
| Letzte Aktualisierung | Zeitstempel der letzten Datenübertragung |
| Batteriekapazität | Installierte Kapazität laut API |
| Verbindungsstatus | Online / Offline |
| Betriebsstatus | z.B. RunMode, StandBy, Charging |
| Firmware-Version | Aktuelle Firmware |

### Voraussetzungen

1. Dyness Batterie ist bereits in der **Dyness App** eingerichtet und online

### Schritt 1: API-Zugangsdaten im Dyness Portal erstellen

1. Öffne **Dyness Benutzer Smart Monitoring** [https://ems.dyness.com/login](https://ems.dyness.com/login) in deinem Browser
2. Melde dich mit deinem Dyness-Konto an (dasselbe wie in der App)
3. Wähle im Menü links **Entwicklerzentrum** und dann **API-Verwaltung**
4. Klicke auf **API Key erstellen**
5. Notiere **App ID** und **App Secret** – das Secret wird nur einmal angezeigt!

### Installation

#### Via HACS (empfohlen)

1. Öffne HACS in Home Assistant
2. Klicke auf **Integrationen** → **⋮** → **Benutzerdefinierte Repositories**
3. Repository-URL: `https://github.com/shopf/dyness_battery` — Kategorie: **Integration**
4. Suche nach **Dyness Battery** und installiere
5. Home Assistant neu starten

#### Manuelle Installation

1. Lade die ZIP von [Releases](https://github.com/shopf/dyness_battery/releases) herunter
2. Entpacke und kopiere `custom_components/dyness_battery/` nach `config/custom_components/`
3. Home Assistant neu starten

### Konfiguration

1. **Einstellungen** → **Geräte & Dienste** → **Integration hinzufügen**
2. Nach **Dyness Battery** suchen
3. Nur **API ID** und **API Secret** eintragen — das Gerät wird automatisch erkannt

| Feld | Beschreibung | Beispiel |
|------|-------------|---------|
| API ID | Dyness API ID | `abc123xyz` |
| API Secret | Dyness API Secret | `secretkey456` |

> **Mehrere Batterien:** Bei mehreren Batterien auf einem Account wird automatisch die erste erkannte BMS verwendet. Für weitere Batterien einfach die Integration erneut hinzufügen — dieselben API-Zugangsdaten, das Gerät wird separat erkannt.

### Bekannte Einschränkungen

- **Nur Monitoring** – Steuerung (Ladezeiten, SOC-Grenzen) wird von der API nicht unterstützt
- **5-Minuten-Intervall** – Die API liefert Daten in 5-Minuten-Schritten
- **Internetabhängig** – Keine lokale Verbindung (WiFi-Dongle ist intern verbaut)

### Neues Modell hinzufügen

Du hast ein anderes Dyness-Modell mit WiFi-Dongle und möchtest es testen? Erstelle ein [Issue](https://github.com/shopf/dyness_battery/issues) mit folgenden Informationen:

- Modellbezeichnung (z.B. `Tower T14`)
- Ausgabe des API-Testscripts (siehe `tools/dyness_test.py`)

---

## 🇬🇧 English

### Supported Devices

| Device | Status |
|--------|--------|
| Dyness Junior Box | ✅ Tested |
| Dyness Tower (non-pro) | ✅ Should work (community-tested) |
| Dyness DL5.0C | ✅ Should work (community-tested) |
| Other Dyness models with WiFi dongle | ⚠️ Not tested – feedback welcome |

> The integration automatically detects the device via the API and only registers sensors available for that specific device.

### Available Sensors

The following sensors are available for all devices:

| Sensor | Description | Unit |
|--------|-------------|------|
| State of Charge (SOC) | Current battery level | % |
| Power | Charge/discharge power (+ = charging, − = discharging) | W |
| Current | Charge/discharge current | A |
| Battery Status | Charging / Discharging / Standby | – |

Additional sensors are automatically enabled if the device provides the data:

| Sensor | Description | Unit | Junior Box | Tower | DL5.0C |
|--------|-------------|------|:---:|:---:|:---:|
| Pack Voltage | Total battery pack voltage | V | ✅ | – | ✅ |
| State of Health (SOH) | Battery health | % | ✅ | ✅ | ✅ |
| Temperature Max | Highest cell temperature | °C | ✅ | ✅ | ✅ |
| Temperature Min | Lowest cell temperature | °C | ✅ | ✅ | ✅ |
| Cell Voltage Max | Highest individual cell voltage | V | ✅ | ✅ | ✅ |
| Cell Voltage Min | Lowest individual cell voltage | V | ✅ | ✅ | ✅ |
| Cell Voltage Spread | Max − Min cell voltage (health indicator) | mV | ✅ | ✅ | ✅ |
| Energy Charged Today | Energy charged today | kWh | ✅ | – | ✅ |
| Energy Discharged Today | Energy discharged today | kWh | ✅ | – | ✅ |
| Energy Charged Total | Cumulative energy charged | kWh | ✅ | ✅ | ✅ |
| Energy Discharged Total | Cumulative energy discharged | kWh | ✅ | – | ✅ |
| Cycle Count | Number of charge cycles | – | – | ✅ | – |
| Usable Capacity | Capacity × SOH | kWh | ✅ | ✅ | ✅ |
| Energy Remaining | Usable capacity × SOC | kWh | ✅ | ✅ | ✅ |

The following sensors are available under **Diagnostics** on the device page:

| Sensor | Description |
|--------|-------------|
| Last Update | Timestamp of last data transmission |
| Battery Capacity | Installed capacity per API |
| Communication Status | Online / Offline |
| Work Status | e.g. RunMode, StandBy, Charging |
| Firmware Version | Current firmware version |

### Step 1: Create API credentials in the Dyness Portal

1. Open **Dyness User Smart Monitoring** [https://ems.dyness.com/login](https://ems.dyness.com/login) in your browser
2. Log in with your Dyness account (same as the app)
3. Select **Developer Center** and then **API Management** from the left menu
4. Click **Create API Key**
5. Note down **App ID** and **App Secret** – the secret is only shown once!

### Installation

#### Via HACS (recommended)

1. Open HACS in Home Assistant
2. Click **Integrations** → **⋮** → **Custom repositories**
3. Add URL: `https://github.com/shopf/dyness_battery` — Category: **Integration**
4. Search for **Dyness Battery** and install
5. Restart Home Assistant

#### Manual Installation

1. Download the ZIP from [Releases](https://github.com/shopf/dyness_battery/releases)
2. Extract and copy `custom_components/dyness_battery/` to `config/custom_components/`
3. Restart Home Assistant

### Configuration

1. **Settings** → **Devices & Services** → **Add Integration**
2. Search for **Dyness Battery**
3. Enter only your **API ID** and **API Secret** — the device is discovered automatically

| Field | Description | Example |
|-------|-------------|---------|
| API ID | Your Dyness API ID | `abc123xyz` |
| API Secret | Your Dyness API Secret | `secretkey456` |

> **Multiple batteries:** If you have multiple batteries on one account, the first detected BMS is used automatically. To add further batteries, simply add the integration again with the same credentials.

### Known Limitations

- **Monitoring only** – Control (charge schedules, SOC limits) is not supported via the API
- **5-minute interval** – API provides data in 5-minute increments
- **Cloud dependent** – No local connection (WiFi dongle is built-in)

### Adding a New Model

Do you have a different Dyness model with a WiFi dongle and want to test it? Open an [Issue](https://github.com/shopf/dyness_battery/issues) with the following information:

- Model name (e.g. `Tower T14`)
- Output of the API test script (see `tools/dyness_test.py`)

---

## Technical Details

Uses the **Dyness Open API v1.1** with HmacSHA1 authentication.

Endpoints used:
- `POST /v1/device/storage/list` – Auto-discover device SN
- `POST /v1/device/bindSn` – Bind device to API key
- `POST /v1/device/getLastPowerDataBySn` – Current power data (every 5 min)
- `POST /v1/device/realTime/data` – Real-time BMS data: pack voltage, SOH, temperatures, cell voltages, energy totals, voltage spread (every 5 min)
- `POST /v1/station/info` – Station info (battery capacity)
- `POST /v1/device/household/storage/detail` – Device details (firmware, status)
- `POST /v1/device/storage/list` – Work status (every 5 min)

---

## Contributing

Pull requests and issues are welcome! Especially needed:
- Testing with other Dyness models
- Improvements to sensor data

---

## License

MIT License – see [LICENSE](LICENSE)
