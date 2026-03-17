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
| Dyness DL5.0C (4 × DYNESS-Module, 20,48 kWh) | ✅ Vollständig getestet |
| Dyness Tower (non-pro) | ✅ Sollte funktionieren (Community-getestet) |
| Andere Dyness-Modelle mit WiFi-Dongle | ⚠️ Nicht getestet – Feedback willkommen |

> Die Integration erkennt Gerätetyp und Modulanzahl **vollautomatisch** – keine manuelle Konfiguration der Module erforderlich.

### Neue Funktionen

- **Automatische Modulerkennung** – Die Anzahl der DYNESS-Module (2, 4, 6, …) wird bei jedem Start automatisch aus der BMS-Antwort ermittelt
- **Korrekte Gesamtkapazität** – Die API meldet nur die Kapazität eines Moduls; die Integration multipliziert automatisch mit der tatsächlichen Modulanzahl
- **Einzelzell-Spannungen** – Alle 16 Zellspannungen pro Modul abrufbar (standardmäßig deaktiviert, über HA-UI aktivierbar)
- **Pro-Modul-Sensoren** – Jedes Modul erscheint als eigenes Untergerät in Home Assistant mit eigenem Satz an Sensoren
- **Innenwiderstand** – Gemessener DC-Innenwiderstand pro Modul in mΩ
- **Alarmstatus** – Alarm- und Schutzregister werden pro Modul überwacht

### Verfügbare Sensoren

#### Pack-Ebene (BMS) – immer verfügbar

| Sensor | Beschreibung | Einheit |
|--------|-------------|---------|
| Ladestand (SOC) | Aktueller Ladestand | % |
| Leistung | Lade-/Entladeleistung (+ = laden, − = entladen) | W |
| Strom | Lade-/Entladestrom | A |
| Pack-Strom (BMS) | Batterie-Packstrom direkt vom BMS | A |
| Letzte Aktualisierung | Zeitstempel der letzten Datenübertragung | – |
| Batteriekapazität (pro Modul) | Kapazität eines einzelnen Moduls laut API | kWh |
| **Gesamtkapazität Batterie** | Korrigierte Gesamtkapazität (× Modulanzahl) | kWh |
| **Batteriestatus** | Laden / Entladen / Standby | – |
| Verbindungsstatus | Online / Offline | – |
| Betriebsstatus | z.B. RunMode, StandBy, Charging | – |
| Firmware-Version | Aktuelle Firmware | – |
| **Modulanzahl** | Automatisch erkannte Anzahl DYNESS-Module | – |

#### Pack-Ebene – geräteabhängig

| Sensor | Beschreibung | Einheit | Junior Box | Tower | DL5.0C |
|--------|-------------|---------|:---:|:---:|:---:|
| Pack-Spannung | Gesamtspannung des Akkupacks | V | ✅ | – | ✅ |
| Batteriezustand min. (SOH) | Niedrigster Modulzustand | % | ✅ | ✅ | ✅ |
| Batteriezustand Ø (SOH) | Durchschnittlicher Modulzustand | % | ✅ | – | ✅ |
| Temperatur Max | Höchste Zellentemperatur | °C | ✅ | ✅ | ✅ |
| Temperatur Min | Niedrigste Zellentemperatur | °C | ✅ | ✅ | ✅ |
| Zellspannung Max | Höchste Einzelzellspannung | V | ✅ | ✅ | ✅ |
| Zellspannung Min | Niedrigste Einzelzellspannung | V | ✅ | ✅ | ✅ |
| Zellspannungsdifferenz | Max − Min Zellspannung | V | ✅ | ✅ | ✅ |
| **Zellspannungsdifferenz (mV)** | Max − Min Zellspannung in mV | mV | ✅ | ✅ | ✅ |
| **Nutzbare Kapazität** | Gesamtkapazität × SOH | kWh | ✅ | ✅ | ✅ |
| **Verbleibende Energie** | Nutzbare Kapazität × SOC | kWh | ✅ | ✅ | ✅ |
| Heute geladen | Geladene Energie heute | kWh | ✅ | – | ✅ |
| Heute entladen | Entladene Energie heute | kWh | ✅ | – | ✅ |
| Gesamt geladen | Kumuliert geladene Energie | kWh | ✅ | ✅ | ✅ |
| Gesamt entladen | Kumuliert entladene Energie | kWh | ✅ | – | ✅ |
| Ladezyklen | Anzahl Batteriezyklen | – | – | ✅ | – |

#### Pro-Modul-Sensoren (ein Untergerät pro DYNESS-Modul)

Jedes Modul erscheint als eigenes Gerät in Home Assistant (verknüpft mit dem BMS als übergeordnetem Gerät).

| Sensor | Beschreibung | Einheit |
|--------|-------------|---------|
| Batteriezustand (SOH) | State of Health dieses Moduls | % |
| Ladezyklen | Ladezyklen dieses Moduls (variiert pro Modul) | – |
| Zellspannung Max | Höchste Zellspannung im Modul | V |
| Zellspannung Min | Niedrigste Zellspannung im Modul | V |
| Zellspannungsdifferenz | Max − Min Zellspannung (Balancing-Indikator) | mV |
| BMS-Platinentemperatur | Temperatur der BMS-Platine | °C |
| Zelltemperatur 1 | NTC-Sensor 1 | °C |
| Zelltemperatur 2 | NTC-Sensor 2 | °C |
| Spannung | Modulspannung (= Packspannung, Parallelschaltung) | V |
| Strom | Modulstrom (Summe × Modulanzahl ≈ Packstrom) | A |
| Innenwiderstand | DC-Innenwiderstand des Moduls | mΩ |
| Nennkapazität | Bewertete Kapazität (5,12 kWh / 100 Ah) | kWh |
| Nutzbare Kapazität | Nennkapazität × SOH | kWh |
| Alarmstatus | True wenn aktive Alarm- oder Schutzregister | – |
| **Zelle 1–16 Spannung** | Einzelne Zellspannungen *(standardmäßig deaktiviert)* | V |

> **Einzelzellspannungen aktivieren:** In Home Assistant unter *Einstellungen → Geräte & Dienste → Dyness Battery → [Modulname] → Entitäten* die gewünschten Zell-Sensoren aktivieren.

### Voraussetzungen

1. Dyness Batterie ist bereits in der **Dyness App** eingerichtet und online

### Schritt 1: API-Zugangsdaten im Dyness Portal erstellen

1. Öffne **Dyness Benutzer Smart Monitoring** [https://ems.dyness.com/login](https://ems.dyness.com/login) in deinem Browser
2. Melde dich mit deinem Dyness-Konto an (dasselbe wie in der App)
3. Wähle im Menü links **Entwicklerzentrum** und dann **API-Verwaltung**
4. Klicke auf **API Key erstellen**
5. Notiere **App ID** und **App Secret** – das Secret wird nur einmal angezeigt!

> **Seriennummern finden:** Wähle im Menü links **Kraftwerkszentrum** und dann **Geräteverwaltung**. Die Batterie-SN endet auf `-BMS`, die Dongle-SN ist dieselbe ohne `-BMS`.

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
3. Formular ausfüllen:

| Feld | Beschreibung | Beispiel |
|------|-------------|---------|
| API ID | Dyness API ID | `abc123xyz` |
| API Secret | Dyness API Secret | `secretkey456` |
| Batterie SN | Seriennummer mit `-BMS` | `R07ABCDEF123456-BMS` |
| Dongle SN | Seriennummer ohne `-BMS` | `R07ABCDEF123456` |

> **Hinweis zu Seriennummern:** Dies sind Beispiele. Seriennummern beginnen typischerweise mit `R07` gefolgt von 13 weiteren Zeichen. Die Dongle-SN ist 16 Zeichen lang, die Batterie-SN ist identisch mit dem Zusatz `-BMS` (insgesamt 20 Zeichen).
>
> **Module werden automatisch erkannt** – es ist keine manuelle Eingabe von Modul-Seriennummern erforderlich.

### Bekannte Einschränkungen

- **Nur Monitoring** – Steuerung (Ladezeiten, SOC-Grenzen) wird von der API nicht unterstützt
- **5-Minuten-Intervall** – Die API liefert Daten in 5-Minuten-Schritten
- **Internetabhängig** – Keine lokale Verbindung (WiFi-Dongle ist intern verbaut)
- **station/info meldet Einzelmodul-Kapazität** – Die Integration korrigiert dies automatisch durch Multiplikation mit der erkannten Modulanzahl

### Neues Modell hinzufügen

Du hast ein anderes Dyness-Modell mit WiFi-Dongle und möchtest es testen? Erstelle ein [Issue](https://github.com/shopf/dyness_battery/issues) mit folgenden Informationen:

- Modellbezeichnung (z.B. `Tower T14`)
- Seriennummer-Format (Batterie-SN und Dongle-SN)
- Ausgabe des API-Testscripts (siehe unten)

**API-Testscript** – zum Testen welche Endpunkte dein Modell unterstützt:
```bash
# Zugangsdaten eintragen und ausführen
python3 dyness_test.py
```
Das Script findest du im Repository unter `tools/dyness_test.py`.

---

## 🇬🇧 English

### Supported Devices

| Device | Status |
|--------|--------|
| Dyness Junior Box | ✅ Tested |
| Dyness DL5.0C (4 × DYNESS modules, 20.48 kWh) | ✅ Fully tested |
| Dyness Tower (non-pro) | ✅ Should work (community-tested) |
| Other Dyness models with WiFi dongle | ⚠️ Not tested – feedback welcome |

> The integration automatically detects the device type and module count — no manual module configuration required.

### What's new

- **Automatic module discovery** — the number of DYNESS modules (2, 4, 6, …) is detected automatically from the BMS response on startup
- **Corrected total capacity** — the API only reports one module's capacity; the integration multiplies by the discovered module count automatically
- **Individual cell voltages** — all 16 cell voltages per module available (disabled by default, enable per entity in HA UI)
- **Per-module sub-devices** — each module appears as its own device in Home Assistant with its own sensor set
- **Internal resistance** — measured DC internal resistance per module in mΩ
- **Alarm status** — alarm and protection registers monitored per module

### Available Sensors

#### Pack level (BMS) — always available

| Sensor | Description | Unit |
|--------|-------------|------|
| State of Charge (SOC) | Current battery level | % |
| Power | Charge/discharge power (+ = charging, − = discharging) | W |
| Current | Charge/discharge current | A |
| Pack Current (BMS) | Battery pack current direct from BMS | A |
| Last Update | Timestamp of last data transmission | – |
| Battery Capacity (per module) | Single module capacity as reported by API | kWh |
| **Total Battery Capacity** | Corrected total capacity (× module count) | kWh |
| **Battery Status** | Charging / Discharging / Standby | – |
| Communication Status | Online / Offline | – |
| Work Status | e.g. RunMode, StandBy, Charging | – |
| Firmware Version | Current firmware version | – |
| **Module Count** | Auto-discovered number of DYNESS modules | – |

#### Pack level — device-dependent

| Sensor | Description | Unit | Junior Box | Tower | DL5.0C |
|--------|-------------|------|:---:|:---:|:---:|
| Pack Voltage | Total battery pack voltage | V | ✅ | – | ✅ |
| State of Health min (SOH) | Lowest module health | % | ✅ | ✅ | ✅ |
| State of Health avg (SOH) | Average module health | % | ✅ | – | ✅ |
| Temperature Max | Highest cell temperature | °C | ✅ | ✅ | ✅ |
| Temperature Min | Lowest cell temperature | °C | ✅ | ✅ | ✅ |
| Cell Voltage Max | Highest individual cell voltage | V | ✅ | ✅ | ✅ |
| Cell Voltage Min | Lowest individual cell voltage | V | ✅ | ✅ | ✅ |
| Cell Voltage Spread | Max − Min cell voltage | V | ✅ | ✅ | ✅ |
| **Cell Voltage Spread (mV)** | Max − Min cell voltage in mV | mV | ✅ | ✅ | ✅ |
| **Usable Capacity** | Total capacity × SOH | kWh | ✅ | ✅ | ✅ |
| **Energy Remaining** | Usable capacity × SOC | kWh | ✅ | ✅ | ✅ |
| Energy Charged Today | Energy charged today | kWh | ✅ | – | ✅ |
| Energy Discharged Today | Energy discharged today | kWh | ✅ | – | ✅ |
| Energy Charged Total | Cumulative energy charged | kWh | ✅ | ✅ | ✅ |
| Energy Discharged Total | Cumulative energy discharged | kWh | ✅ | – | ✅ |
| Cycle Count | Number of charge cycles | – | – | ✅ | – |

#### Per-module sensors (one sub-device per DYNESS module)

Each module appears as its own device in Home Assistant, linked to the BMS as the parent device.

| Sensor | Description | Unit |
|--------|-------------|------|
| State of Health (SOH) | Health of this specific module | % |
| Cycle Count | Charge cycles for this module (varies per module) | – |
| Cell Voltage Max | Highest cell voltage in module | V |
| Cell Voltage Min | Lowest cell voltage in module | V |
| Cell Voltage Spread | Max − Min cell voltage (balancing indicator) | mV |
| BMS Board Temperature | BMS PCB temperature | °C |
| Cell Temperature 1 | NTC sensor 1 | °C |
| Cell Temperature 2 | NTC sensor 2 | °C |
| Voltage | Module voltage (= pack voltage, parallel topology) | V |
| Current | Module current (sum × module count ≈ pack current) | A |
| Internal Resistance | DC internal resistance of the module | mΩ |
| Rated Capacity | Nameplate capacity (5.12 kWh / 100 Ah) | kWh |
| Usable Capacity | Rated capacity × SOH | kWh |
| Alarm Status | True if any alarm or protection register is active | – |
| **Cell 1–16 Voltage** | Individual cell voltages *(disabled by default)* | V |

> **Enabling individual cell voltages:** In Home Assistant go to *Settings → Devices & Services → Dyness Battery → [module name] → Entities* and enable the desired cell sensors.

### Prerequisites

1. Dyness battery is already set up and online in the **Dyness app**

### Step 1: Create API credentials in the Dyness Portal

1. Open **Dyness User Smart Monitoring** [https://ems.dyness.com/login](https://ems.dyness.com/login) in your browser
2. Log in with your Dyness account (same as the app)
3. Select **Developer Center** and then **API Management** from the left menu
4. Click **Create API Key**
5. Note down **App ID** and **App Secret** – the secret is only shown once!

> **Finding serial numbers:** Select **Plants Center** and then **Device Management** from the left menu. The battery SN ends with `-BMS`, the dongle SN is the same without `-BMS`.

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
3. Fill in the form:

| Field | Description | Example |
|-------|-------------|---------|
| API ID | Your Dyness API ID | `abc123xyz` |
| API Secret | Your Dyness API Secret | `secretkey456` |
| Battery SN | Serial number with `-BMS` | `R07ABCDEF123456-BMS` |
| Dongle SN | Serial number without `-BMS` | `R07ABCDEF123456` |

> **Note on serial numbers:** These are examples. Serial numbers typically start with `R07` followed by 13 more characters. The dongle SN is 16 characters long, the battery SN is identical with the suffix `-BMS` added (20 characters total).
>
> **Modules are auto-discovered** — no manual entry of module serial numbers is required.

### Known Limitations

- **Monitoring only** – Control (charge schedules, SOC limits) is not supported via the API
- **5-minute interval** – API provides data in 5-minute increments
- **Cloud dependent** – No local connection (WiFi dongle is built-in)
- **station/info reports single-module capacity** – The integration corrects this automatically by multiplying by the discovered module count

### Adding a New Model

Do you have a different Dyness model with a WiFi dongle and want to test it? Open an [Issue](https://github.com/shopf/dyness_battery/issues) with the following information:

- Model name (e.g. `Tower T14`)
- Serial number format (battery SN and dongle SN)
- Output of the API test script (see `tools/dyness_test.py`)

---

## Technical Details

Uses the **Dyness Open API v1.1** with HmacSHA1 authentication.

### Endpoints used

| Endpoint | Purpose | Frequency |
|----------|---------|-----------|
| `POST /v1/device/bindSn` | Bind BMS and module SNs to API key | Once at startup |
| `POST /v1/device/realTime/data` (BMS SN) | Pack voltage, SOH, temps, cell extremes, energy totals | Every 5 min |
| `POST /v1/device/realTime/data` (module SN) | 131 points per module: all 16 cell voltages, temps, IR, health, alarms | Every 5 min |
| `POST /v1/device/getLastPowerDataBySn` | SOC, power, current, timestamp | Every 5 min |
| `POST /v1/device/storage/list` | Work status | Every 5 min |
| `POST /v1/station/info` | Installed capacity (single-module value) | Once at startup |
| `POST /v1/device/household/storage/detail` | Firmware version, communication status | Once at startup |

### Device architecture (DL5.0C example)

```
BMS  (R07E…-BMS)
├── DYNESS01  (R07E…-DYNESS01)  — 16 LFP cells in series, 5.12 kWh / 100 Ah
├── DYNESS02  (R07E…-DYNESS02)  — 16 LFP cells in series, 5.12 kWh / 100 Ah
├── DYNESS03  (R07E…-DYNESS03)  — 16 LFP cells in series, 5.12 kWh / 100 Ah
└── DYNESS04  (R07E…-DYNESS04)  — 16 LFP cells in series, 5.12 kWh / 100 Ah
                                   ─────────────────────────────────────────
                                   Total: 16S4P · 20.48 kWh · 400 Ah @ 51.2 V
```

Module SNs are discovered automatically from the BMS `SUB` data point — the integration works for any number of parallel modules.

### Module-level point IDs (all 131 decoded)

Key confirmed point IDs for each DYNESS module:

| Point ID | Field | Example value |
|----------|-------|---------------|
| 10000 | Serial number | `0106032501061890` |
| 10100 | Firmware version | `24.9-26.8.1` |
| 10300–11800 | Cell 1–16 voltage (V) | `3.350` |
| 12400 | BMS board temperature (°C) | `32.2` |
| 12500 / 12600 | Cell NTC temps 1 & 2 (°C) | `22.7` / `22.6` |
| 13400 | Module current (A) | `-0.4` |
| 13500 | Module voltage (V) | `53.58` |
| 13600 | DC internal resistance (mΩ) | `33.464` |
| 13700 | Module count in pack | `4` |
| 13900 | Cycle count | `42` |
| 14000 | State of Health (%) | `99.0` |
| 14100 | Rated capacity (Ah) | `100.0` |
| 14300 / 15200 | Alarm status bitmasks | `0` |
| 16300 | Protection status bitmask | `0` |
| 18100–19200 | Protection thresholds | `3.6 V`, `115 A`, `65 °C` |

Cell voltage point ID formula: `pointId = 10300 + (cell_num − 1) × 100`

---

## Contributing

Pull requests and issues are welcome! Especially needed:
- Testing with other Dyness models
- Improvements to sensor data

---

## License

MIT License – see [LICENSE](LICENSE)
