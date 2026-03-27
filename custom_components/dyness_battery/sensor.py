"""Sensoren für Dyness Battery Integration."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE, UnitOfPower, UnitOfElectricCurrent, UnitOfEnergy,
    UnitOfTemperature, UnitOfElectricPotential,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN

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
    ("totalBatteryCapacity",   "total_battery_capacity", UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery-high",           None, None),
    ("usableKwh",              "usable_kwh",             UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery-heart",          None, None),
    ("remainingKwh",           "remaining_kwh",          UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery-charging",       None, None),
    ("tempMosfet",             "temp_mosfet",            UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, None),
    ("tempBmsMax",             "temp_bms_max",           UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, None),
    ("tempBmsMin",             "temp_bms_min",           UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, None),
    ("alarmStatus1",           "alarm_status_1",         None,                         None,                          None,                              "mdi:alert-circle-outline",   None, None),
    ("alarmStatus2",           "alarm_status_2",         None,                         None,                          None,                              "mdi:alert-circle-outline",   None, None),
    ("alarmTotal",             "alarm_total",            None,                         None,                          None,                              "mdi:alert",                  None, None),
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
    known_module_ids: set = set()

    def _add_new_modules() -> None:
        module_data = (coordinator.data or {}).get("module_data", {})
        new_mids = [mid for mid in module_data if mid not in known_module_ids]
        if not new_mids:
            return
        new_entities = []
        for mid in new_mids:
            known_module_ids.add(mid)
            for data_key, trans_key, unit, dev_cls, state_cls, icon, precision in MODULE_SENSORS:
                new_entities.append(
                    DynessModuleSensor(
                        coordinator, entry, mid, data_key, trans_key,
                        unit, dev_cls, state_cls, icon, precision,
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
        return (self.coordinator.data or {}).get(self._key)

    @property
    def available(self):
        return self.coordinator.last_update_success and self.native_value is not None


# ── Modul-Sensoren (pro Sub-Modul dynamisch registriert) ─────────────────────
# (data_key, translation_key, unit, device_class, state_class, icon, precision)
MODULE_SENSORS = [
    ("soh",                  "module_soh",            PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-heart",         None),
    ("cycle_count",          "module_cycle_count",    None,                         None,                          SensorStateClass.TOTAL_INCREASING, "mdi:battery-sync",          None),
    ("cell_voltage_max",     "module_cell_v_max",     UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",             3),
    ("cell_voltage_min",     "module_cell_v_min",     UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",             3),
    ("cell_voltage_spread_mv","module_cell_spread",   "mV",                         None,                          SensorStateClass.MEASUREMENT,      "mdi:arrow-expand-horizontal",1),
    ("bms_temp",             "module_temp_bms",       UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",           None),
    ("cell_temp_1",          "module_temp_1",         UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",           None),
    ("cell_temp_2",          "module_temp_2",         UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",           None),
    ("voltage",              "module_voltage",        UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",             3),
    ("current",              "module_current",        UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-dc",            None),
    ("has_alarm",            "module_alarm",          None,                         None,                          None,                              "mdi:alert-circle",          None),
]


class DynessModuleSensor(CoordinatorEntity, SensorEntity):
    """Sensor für ein einzelnes Sub-Modul."""

    def __init__(self, coordinator, entry, module_id, data_key,
                 translation_key, unit, device_class, state_class, icon, precision=None):
        super().__init__(coordinator)
        self._module_id   = module_id
        self._data_key    = data_key
        self._attr_translation_key            = translation_key
        self._attr_unique_id                  = f"{entry.entry_id}_{module_id}_{data_key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class               = device_class
        self._attr_state_class                = state_class
        self._attr_has_entity_name            = True
        self._attr_icon                       = icon
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
        return (self.coordinator.data or {}).get("module_data", {}).get(self._module_id, {}).get(self._data_key)

    @property
    def available(self):
        return (
            self.coordinator.last_update_success
            and self._module_id in (self.coordinator.data or {}).get("module_data", {})
        )
