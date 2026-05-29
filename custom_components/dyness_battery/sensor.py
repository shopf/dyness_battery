"""Sensoren für Dyness Battery Integration."""
import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE, UnitOfPower, UnitOfElectricCurrent, UnitOfEnergy,
    UnitOfTemperature, UnitOfElectricPotential, UnitOfFrequency,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

# (key, translation_key, unit, device_class, state_class, icon, precision, entity_category)
_D = EntityCategory.DIAGNOSTIC

SENSORS = [
    # ── Haupt-Sensoren ────────────────────────────────────────────────────────
    ("soc",                    "battery_soc",            PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-high",           None, None),
    ("realTimePower",          "battery_power",          UnitOfPower.WATT,             SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:lightning-bolt",         None, None),
    ("realTimeCurrent",        "battery_current",        UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-dc",             None, None),
    ("batteryStatus",          "battery_status",         None,                         None,                          None,                              "mdi:battery-charging",       None, None),
    ("packVoltage",            "pack_voltage",           UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              3,    None),
    ("soh",                    "battery_soh",            PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-heart",          None, None),
    ("temp",                   "temperature",            UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, None),
    ("tempMax",                "temp_max",               UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer-high",       None, None),
    ("tempMin",                "temp_min",               UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer-low",        None, None),
    ("cellVoltageMax",         "cell_voltage_max",       UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              3,    None),
    ("cellVoltageMin",         "cell_voltage_min",       UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              3,    None),
    ("cellVoltageDiffMv",      "cell_voltage_diff_mv",   "mV",                         None,                          SensorStateClass.MEASUREMENT,      "mdi:arrow-expand-horizontal", 1,   None),
    ("energyChargeDay",        "energy_charge_day",      UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-charging",       None, None),
    ("energyDischargeDay",     "energy_discharge_day",   UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-minus",          None, None),
    ("energyChargeTotal",      "energy_charge_total",    UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-charging-100",   None, None),
    ("energyDischargeTotal",   "energy_discharge_total", UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-minus-outline",  None, None),
    ("cycleCount",             "cycle_count",            None,                         None,                          SensorStateClass.TOTAL_INCREASING, "mdi:battery-sync",           None, None),
    ("usableKwh",              "usable_kwh",             UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery-heart",          None, None),
    ("remainingKwh",           "remaining_kwh",          UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery-charging",       None, None),
    # Max Lade-/Entladestrom (Diagnostic)
    ("chargeCurrentLimit",     "charge_current_limit",   UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-ac",             None, _D),
    ("dischargeCurrentLimit",  "discharge_current_limit",UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-ac",             None, _D),
    # Alarm Text + neue Sensoren
    ("alarmText",              "alarm_text",             None,                         None,                          None,                              "mdi:alert-circle-outline",   None, _D),
    ("chargeVoltageLimit",     "charge_voltage_limit",   UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:battery-arrow-up",       1,    _D),
    ("dischargeVoltageLimit",  "discharge_voltage_limit",UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:battery-arrow-down",     1,    _D),
    ("cellVoltageMaxModule",   "cell_v_max_module",      None,                         None,                          None,                              "mdi:numeric",                None, _D),
    ("cellVoltageMaxCell",     "cell_v_max_cell",        None,                         None,                          None,                              "mdi:numeric",                None, _D),
    ("cellVoltageMinModule",   "cell_v_min_module",      None,                         None,                          None,                              "mdi:numeric",                None, _D),
    ("cellVoltageMinCell",     "cell_v_min_cell",        None,                         None,                          None,                              "mdi:numeric",                None, _D),
    ("balancingStatus",        "balancing_status",       None,                         None,                          None,                              "mdi:scale-balance",          None, _D),
    # Inverter / Hybrid Sensoren (aus getLastRunningDataBySn — nur wenn verfügbar)
    ("pvPower",            "pv_power",            UnitOfPower.WATT,               SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:solar-power",            None, None),
    ("loadPower",          "load_power",           UnitOfPower.WATT,               SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:home-lightning-bolt",    None, None),
    ("gridPower",          "grid_power",           UnitOfPower.WATT,               SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:transmission-tower",     None, None),
    ("pv1Power",           "pv1_power",            UnitOfPower.WATT,               SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            None, None),
    ("pv2Power",           "pv2_power",            UnitOfPower.WATT,               SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            None, None),
    ("pv3Power",           "pv3_power",            UnitOfPower.WATT,               SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            None, None),
    ("pvEnergyToday",      "pv_energy_today",      UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:solar-power",            None, None),
    ("loadEnergyToday",    "load_energy_today",    UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:home-lightning-bolt",    None, None),
    ("gridImportToday",    "grid_import_today",    UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower",     None, None),
    ("gridExportToday",    "grid_export_today",    UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower",     None, None),
    ("pvEnergyTotal",      "pv_energy_total",      UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:solar-power",            None, None),
    ("loadEnergyTotal",    "load_energy_total",    UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:home-lightning-bolt",    None, None),
    ("gridImportTotal",    "grid_import_total",    UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower",     None, None),
    ("gridExportTotal",    "grid_export_total",    UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower",     None, None),
    ("tempInternal",       "temp_internal",        UnitOfTemperature.CELSIUS,      SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, _D),
    ("tempModule",         "temp_module",          UnitOfTemperature.CELSIUS,      SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, _D),
    ("tempHeatSink",       "temp_heat_sink",       UnitOfTemperature.CELSIUS,      SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, _D),
    ("gridStatus",         "grid_status",          None,                           None,                          None,                              "mdi:transmission-tower",     None, None),
    ("runModel",           "run_model",            None,                           None,                          None,                              "mdi:cog",                    None, None),
    ("inverterWorkStatus", "inverter_work_status", None,                           None,                          None,                              "mdi:home-battery",           None, _D),
    ("gridVoltage",        "grid_voltage",         UnitOfElectricPotential.VOLT,   SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              1,    _D),
    ("gridCurrent",        "grid_current",         UnitOfElectricCurrent.AMPERE,   SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-ac",             1,    _D),
    ("gridFrequency",      "grid_frequency",       UnitOfFrequency.HERTZ,          SensorDeviceClass.FREQUENCY,   SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              2,    _D),
    ("busVoltage",         "bus_voltage",          UnitOfElectricPotential.VOLT,   SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              1,    _D),
    ("pv1Voltage",         "pv1_voltage",          UnitOfElectricPotential.VOLT,   SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            1,    _D),
    ("pv2Voltage",         "pv2_voltage",          UnitOfElectricPotential.VOLT,   SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            1,    _D),
    ("pv3Voltage",         "pv3_voltage",          UnitOfElectricPotential.VOLT,   SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            1,    _D),
    ("pv1Current",         "pv1_current",          UnitOfElectricCurrent.AMPERE,   SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            1,    _D),
    ("pv2Current",         "pv2_current",          UnitOfElectricCurrent.AMPERE,   SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            1,    _D),
    ("pv3Current",         "pv3_current",          UnitOfElectricCurrent.AMPERE,   SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            1,    _D),
    # Tower Alarm-Bits (Boolean, Diagnostic) — nur einmal registriert
    ("alarmSpreadV",           "alarm_spread_v",         None,                         None,                          None,                              "mdi:alert-circle-outline",   None, _D),
    ("alarmSpreadT",           "alarm_spread_t",         None,                         None,                          None,                              "mdi:alert-circle-outline",   None, _D),
    ("alarmInsul",             "alarm_insul",            None,                         None,                          None,                              "mdi:shield-alert",           None, _D),
    ("alarmAfe",               "alarm_afe",              None,                         None,                          None,                              "mdi:lan-disconnect",         None, _D),
    ("alarmBms",               "alarm_bms",              None,                         None,                          None,                              "mdi:lan-disconnect",         None, _D),
    ("alarmSys",               "alarm_sys",              None,                         None,                          None,                              "mdi:alert",                  None, _D),
    ("alarmTotal",             "alarm_total",            None,                         None,                          None,                              "mdi:alert",                  None, _D),
    # JuniorBox / DL5.0C Temperaturen (nur wenn vorhanden)
    ("tempMosfet",             "temp_mosfet",            UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, None),
    ("tempBmsMax",             "temp_bms_max",           UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, None),
    ("tempBmsMin",             "temp_bms_min",           UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, None),
    # ── Diagnose ─────────────────────────────────────────────────────────────
    ("createTime",             "last_update",            None,                         None,                          None,                              "mdi:clock-outline",          None, _D),
    ("batteryCapacity",        "battery_capacity",       UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery",                None, _D),
    ("deviceCommunicationStatus", "communication_status", None,                        None,                          None,                              "mdi:wifi",                   None, _D),
    ("firmwareVersion",        "firmware_version",       None,                         None,                          None,                              "mdi:chip",                   None, _D),
    ("workStatus",             "work_status",            None,                         None,                          None,                              "mdi:home-battery",           None, _D),
]

ALWAYS_REGISTER = {
    "soc", "realTimePower", "realTimeCurrent", "createTime",
    "batteryCapacity", "deviceCommunicationStatus", "firmwareVersion",
    "workStatus", "batteryStatus",
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    available_data = coordinator.data or {}

    # Pack-Level Sensoren
    async_add_entities([
        DynessSensor(coordinator, entry, key, translation_key,
                     unit, device_class, state_class, icon, precision, entity_category)
        for key, translation_key, unit, device_class, state_class, icon, precision, entity_category in SENSORS
        if key in ALWAYS_REGISTER or available_data.get(key) is not None
    ])

    # Modul-Sensoren — dynamisch bei jedem neuen Modul registrieren
    from homeassistant.helpers import entity_registry as er

    known_module_ids: set = set()
    _registry_scanned: bool = False

    # Sensoren die beim Tower Pro TP7 auf Modul-Ebene nicht verfügbar sind
    _TP7_MODULE_SKIP     = {'soc', 'soh', 'voltage', 'current', 'cycle_count', 'bms_temp', 'has_alarm'}
    # Stack100 Sub-Module liefern SOC, SOH, Spannung, Strom, Zyklen nur auf BMS-Master-Ebene.
    # Auf Sub-Modul-Ebene sind diese Werte nie vorhanden → Sensoren gar nicht erst anlegen.
    _STACK100_MODULE_SKIP = {'soc', 'soh', 'voltage', 'current', 'cycle_count', 'bms_temp', 'has_alarm'}

    def _add_new_modules() -> None:
        nonlocal _registry_scanned
        module_data = (coordinator.data or {}).get("module_data", {})
        if not module_data:
            return

        # Beim ersten Aufruf: Registry-Scan.
        #
        # WICHTIG: known_module_ids verhindert doppelte Registrierung innerhalb
        # einer Session. Nach einem HA-Restart muss async_add_entities jedoch
        # erneut aufgerufen werden — auch für bereits bekannte Module — damit HA
        # die Entities mit dem Coordinator verknüpft. HA verhindert echte Duplikate
        # intern über die unique_id (bestehender Registry-Eintrag wird wiederverwendet).
        #
        # Deshalb: beim ersten Scan known_module_ids NICHT mit Registry-IDs befüllen.
        # Stattdessen alle Module in module_data als "neu" behandeln → instanziieren.
        # Ab dem zweiten Aufruf verhindert known_module_ids echte Duplikate.
        if not _registry_scanned:
            _registry_scanned = True
            _er = er.async_get(hass)
            registry_mids = {
                parts[1]
                for entity in er.async_entries_for_config_entry(_er, entry.entry_id)
                if len(parts := entity.unique_id.split("_")) >= 3
                and len(parts[1]) >= 8 and parts[1].isalnum()
            }
            _LOGGER.debug(
                "Dyness: Registry-Scan: %d Modul(e) bereits bekannt: %s",
                len(registry_mids), registry_mids or "leer (Neuinstallation)"
            )
            # known_module_ids leer lassen → alle Module werden instanziiert
            # HA verknüpft bestehende Registry-Einträge über unique_id automatisch

        new_mids = [mid for mid in module_data if mid not in known_module_ids]
        if not new_mids:
            return
        new_entities = []
        for mid in new_mids:
            known_module_ids.add(mid)
            mod = module_data[mid]
            is_tp7_mod = bool(mod.get("is_tp7"))
            is_stack100_mod = mod.get("module_number") is not None and not is_tp7_mod
            for data_key, trans_key, unit, dev_cls, state_cls, icon, precision in MODULE_SENSORS:
                if is_tp7_mod and data_key in _TP7_MODULE_SKIP:
                    continue  # TP7: nur Master liefert diese Werte
                if is_stack100_mod and data_key in _STACK100_MODULE_SKIP:
                    continue  # Stack100: BMS-only Werte nur auf Master-Ebene verfügbar
                new_entities.append(
                    DynessModuleSensor(
                        coordinator, entry, mid, data_key, trans_key,
                        unit, dev_cls, state_cls, icon, precision,
                    )
                )
            # Individuelle Zellspannungen — nur für vorhandene Zellen registrieren
            # Stack100 liefert Zellen 17–30 mit pointValue="0.0" (nicht null).
            # Daher reicht `is not None` nicht — explizit 0.0 ausschließen.
            # Für Stack100: Point 11100 = physische Zellanzahl → nur bis dort registrieren.
            # Für alle anderen Schemas: Wert muss vorhanden und > 0 sein.
            is_stack100_mod = mod.get("module_number") is not None and not mod.get("is_tp7")
            phys_cells = int(mod.get("cell_count", 30)) if not is_stack100_mod else int(mod.get("cell_count", 16))
            for data_key, trans_key, unit, dev_cls, state_cls, icon, precision in _CELL_SENSORS:
                # Zellnummer aus data_key extrahieren (z.B. "cell_17" → 17)
                try:
                    cell_num = int(data_key.split("_")[1])
                except (IndexError, ValueError):
                    cell_num = 0
                if is_stack100_mod and cell_num > phys_cells:
                    continue  # Stack100: Zellen über physische Anzahl überspringen
                val = mod.get(data_key)
                if val is None or (isinstance(val, float) and val == 0.0) or val == "0.0":
                    continue  # Nicht vorhanden oder explizit 0.0 (API-Platzhalter)
                new_entities.append(
                    DynessModuleSensor(
                        coordinator, entry, mid, data_key, trans_key,
                        unit, dev_cls, state_cls, icon, precision,
                        enabled_default=False,
                    )
                )
        if new_entities:
            async_add_entities(new_entities)

    # Beim ersten Refresh bereits vorhandene Module registrieren
    _add_new_modules()

    # Listener für spätere Updates
    entry.async_on_unload(coordinator.async_add_listener(_add_new_modules))


class DynessSensor(CoordinatorEntity, SensorEntity):

    def __init__(self, coordinator, entry, key, translation_key,
                 unit, device_class, state_class, icon, precision=None, entity_category=None):
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key            = translation_key
        self._attr_unique_id                  = f"{entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class               = device_class
        self._attr_state_class                = state_class
        self._attr_has_entity_name            = True
        self._attr_icon                       = icon
        if precision is not None:
            self._attr_suggested_display_precision = precision
        if entity_category is not None:
            self._attr_entity_category = entity_category

    @property
    def device_info(self):
        di = self.coordinator.device_info
        return {
            "identifiers": {(DOMAIN, self.coordinator.device_sn)},
            "name": di.get("stationName", "Dyness Battery"),
            "manufacturer": "Dyness",
            "model": di.get("deviceModelName", "Dyness Battery"),
            "sw_version": di.get("firmwareVersion"),
        }

    @property
    def native_value(self):
        val = (self.coordinator.data or {}).get(self._key)
        if val is None:
            return None
        # API liefert Zahlenwerte als Strings — für numerische Sensoren konvertieren
        if self._attr_native_unit_of_measurement is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
        return val

    @property
    def available(self):
        return self.coordinator.last_update_success and self.native_value is not None


# ── Modul-Sensoren (pro Sub-Modul dynamisch registriert) ─────────────────────
# (data_key, translation_key, unit, device_class, state_class, icon, precision)

# Individuelle Zellspannungs-Sensoren (standardmäßig deaktiviert — in HA UI aktivierbar)
_CELL_SENSORS = [
    (f"cell_{i:02d}", f"module_cell_{i:02d}",
     UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,
     SensorStateClass.MEASUREMENT, "mdi:battery-outline", 3)
    for i in range(1, 31)
]

# MODULE_SENSORS: aggregierte Sensordaten pro Modul (SOC, SOH, Spannung, Temperatur etc.)
MODULE_SENSORS = [
    ("soc",                   "module_soc",           PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-high",           None),
    ("soh",                   "module_soh",           PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-heart",          None),
    ("cycle_count",           "module_cycle_count",   None,                         None,                          SensorStateClass.TOTAL_INCREASING, "mdi:battery-sync",           None),
    ("cell_voltage_max",      "module_cell_v_max",    UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              3),
    ("cell_voltage_min",      "module_cell_v_min",    UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              3),
    ("cell_voltage_spread_mv","module_cell_spread",   "mV",                         None,                          SensorStateClass.MEASUREMENT,      "mdi:arrow-expand-horizontal", 1),
    ("bms_temp",              "module_temp_bms",      UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None),
    ("cell_temp_1",           "module_temp_1",        UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None),
    ("cell_temp_2",           "module_temp_2",        UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None),
    ("voltage",               "module_voltage",       UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              3),
    ("current",               "module_current",       UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-dc",             None),
    ("has_alarm",             "module_alarm",         None,                         None,                          None,                              "mdi:alert-circle",           None),
]


class DynessModuleSensor(CoordinatorEntity, SensorEntity):
    """Sensor für ein einzelnes Sub-Modul."""

    def __init__(self, coordinator, entry, module_id, data_key,
                 translation_key, unit, device_class, state_class, icon,
                 precision=None, enabled_default=True):
        super().__init__(coordinator)
        self._module_id   = module_id
        self._data_key    = data_key
        self._attr_translation_key                 = translation_key
        self._attr_unique_id                       = f"{entry.entry_id}_{module_id}_{data_key}"
        self._attr_native_unit_of_measurement      = unit
        self._attr_device_class                    = device_class
        self._attr_state_class                     = state_class
        self._attr_has_entity_name                 = True
        self._attr_icon                            = icon
        self._attr_entity_registry_enabled_default = enabled_default
        if precision is not None:
            self._attr_suggested_display_precision = precision

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"{self.coordinator.device_sn}_{self._module_id}")},
            "name": f"Dyness Module {self._module_id}",
            "manufacturer": "Dyness",
            "model": "Battery Module",
            "via_device": (DOMAIN, self.coordinator.device_sn),
        }

    @property
    def native_value(self):
        val = (self.coordinator.data or {}).get("module_data", {}).get(self._module_id, {}).get(self._data_key)
        if val is None:
            return None
        if self._attr_native_unit_of_measurement is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
        return val

    @property
    def available(self):
        return (
            self.coordinator.last_update_success
            and self._module_id in (self.coordinator.data or {}).get("module_data", {})
        )
