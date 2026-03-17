"""Sensoren für Dyness Battery Integration."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE, UnitOfPower, UnitOfElectricCurrent, UnitOfEnergy,
    UnitOfTemperature, UnitOfElectricPotential,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN

# ── Pack-level sensors (all existing + new additions) ─────────────────────────
# (key, translation_key, unit, device_class, state_class, icon, precision)
PACK_SENSORS = [
    # ── Existing sensors (unchanged) ──────────────────────────────────────────
    ("soc",                    "battery_soc",           PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-high",          None),
    ("realTimePower",          "battery_power",         UnitOfPower.WATT,             SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:lightning-bolt",        None),
    ("realTimeCurrent",        "battery_current",       UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-dc",            None),
    ("createTime",             "last_update",           None,                         None,                          None,                              "mdi:clock-outline",         None),
    ("batteryCapacity",        "battery_capacity",      UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery",               None),
    ("deviceCommunicationStatus", "communication_status", None,                       None,                          None,                              "mdi:wifi",                  None),
    ("firmwareVersion",        "firmware_version",      None,                         None,                          None,                              "mdi:chip",                  None),
    ("workStatus",             "work_status",           None,                         None,                          None,                              "mdi:home-battery",          None),
    ("packVoltage",            "pack_voltage",          UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",             3),
    ("soh",                    "battery_soh",           PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-heart",         None),
    ("tempMax",                "temp_max",              UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer-high",      None),
    ("tempMin",                "temp_min",              UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer-low",       None),
    ("cellVoltageMax",         "cell_voltage_max",      UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",             3),
    ("cellVoltageMin",         "cell_voltage_min",      UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",             3),
    ("cellVoltageDiff",        "cell_voltage_diff",     UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",             3),
    ("energyChargeDay",        "energy_charge_day",     UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-charging",      None),
    ("energyDischargeDay",     "energy_discharge_day",  UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-minus",         None),
    ("energyChargeTotal",      "energy_charge_total",   UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-charging-100",  None),
    ("energyDischargeTotal",   "energy_discharge_total",UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-minus-outline", None),
    ("cycleCount",             "cycle_count",           None,                         None,                          SensorStateClass.TOTAL_INCREASING, "mdi:battery-sync",          None),
    # ── New pack-level sensors ─────────────────────────────────────────────────
    ("totalBatteryCapacity",   "total_battery_capacity",UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery-high",          None),
    ("batteryStatus",          "battery_status",        None,                         None,                          None,                              "mdi:home-battery",          None),
    ("cellVoltageDiffMv",      "cell_voltage_diff_mv",  "mV",                         None,                          SensorStateClass.MEASUREMENT,      "mdi:arrow-expand-horizontal",1),
    ("usableKwh",              "usable_kwh",            UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery-heart",         None),
    ("remainingKwh",           "remaining_kwh",         UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery-charging",      None),
    ("avgSoh",                 "avg_soh",               PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-heart-outline", None),
    ("packCurrentA",           "pack_current",          UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-dc",            None),
    ("moduleCount",            "module_count",          None,                         None,                          None,                              "mdi:counter",               None),
    ("apiStatus",              "api_status",            None,                         None,                          None,                              "mdi:api",                   None),
    ("apiCallCount",           "api_call_count",        None,                         None,                          SensorStateClass.TOTAL_INCREASING, "mdi:counter",               None),
]

# Always register these keys even if value is None at startup
_ALWAYS_REGISTER = {
    "soc", "realTimePower", "realTimeCurrent", "createTime",
    "batteryCapacity", "deviceCommunicationStatus", "firmwareVersion", "workStatus",
    "totalBatteryCapacity", "batteryStatus", "moduleCount",
    "apiStatus", "apiCallCount",
}

# ── Module-level sensors (created per discovered DYNESS sub-module) ───────────
# (data_key, translation_key, unit, device_class, state_class, icon, enabled_default, precision)
MODULE_SENSORS = [
    ("soh",                       "module_soh",               PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-heart",          True,  None),
    ("cycle_count",               "module_cycle_count",        None,                         None,                          SensorStateClass.TOTAL_INCREASING, "mdi:battery-sync",           True,  None),
    ("cell_voltage_max",          "module_cell_voltage_max",   UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:arrow-up-circle",        True,  3),
    ("cell_voltage_min",          "module_cell_voltage_min",   UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:arrow-down-circle",      True,  3),
    ("cell_voltage_spread_mv",    "module_cell_spread_mv",     "mV",                         None,                          SensorStateClass.MEASUREMENT,      "mdi:arrow-expand-horizontal", True,  1),
    ("bms_board_temp",            "module_temp_bms",           UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            True,  None),
    ("cell_temp_1",               "module_temp_1",             UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            True,  None),
    ("cell_temp_2",               "module_temp_2",             UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            True,  None),
    ("module_voltage",            "module_voltage",            UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              True,  3),
    ("module_current",            "module_current_a",          UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-dc",             True,  None),
    ("internal_resistance_mohm",  "module_internal_resistance","mΩ",                         None,                          SensorStateClass.MEASUREMENT,      "mdi:resistor",               True,  3),
    ("rated_capacity_kwh",        "module_rated_capacity",     UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery",                True,  None),
    ("usable_kwh",                "module_usable_capacity",    UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery-heart",          True,  None),
    ("has_any_alarm",             "module_alarm_status",       None,                         None,                          None,                              "mdi:alert-circle",           True,  None),
]


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    available_data = coordinator.data or {}

    # ── Pack-level sensors ────────────────────────────────────────────────────
    pack_entities = []
    for key, translation_key, unit, device_class, state_class, icon, precision in PACK_SENSORS:
        if key in _ALWAYS_REGISTER or available_data.get(key) is not None:
            pack_entities.append(
                DynessSensor(coordinator, entry, key, translation_key,
                             unit, device_class, state_class, icon, precision)
            )
    async_add_entities(pack_entities)

    # ── Module sensors — added dynamically as modules are discovered ──────────
    # Modules may not be known on the first refresh (SUB point absent until
    # after initial binding). A listener watches every coordinator update and
    # registers entities for any newly appearing module IDs.
    known_module_ids: set = set()

    def _add_new_module_entities() -> None:
        module_data = (coordinator.data or {}).get("module_data", {})
        new_mids = [mid for mid in module_data if mid not in known_module_ids]
        if not new_mids:
            return
        new_entities = []
        for mid in new_mids:
            known_module_ids.add(mid)
            for (data_key, trans_key, unit, dev_cls, state_cls,
                 icon, enabled, precision) in MODULE_SENSORS:
                new_entities.append(
                    DynessModuleSensor(
                        coordinator, entry, mid, data_key, trans_key,
                        unit, dev_cls, state_cls, icon, enabled, precision,
                    )
                )
            for cell_num in range(1, 17):
                new_entities.append(
                    DynessModuleCellSensor(coordinator, entry, mid, cell_num)
                )
        if new_entities:
            async_add_entities(new_entities)

    # Fire once immediately (covers modules known from first refresh)
    _add_new_module_entities()
    # Register listener for modules discovered in subsequent refreshes
    entry.async_on_unload(coordinator.async_add_listener(_add_new_module_entities))


# ── Pack-level sensor entity ──────────────────────────────────────────────────

class DynessSensor(CoordinatorEntity, SensorEntity):
    """Sensor for pack-level data (BMS SN)."""

    def __init__(self, coordinator, entry, key, translation_key,
                 unit, device_class, state_class, icon, precision=None):
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key          = translation_key
        self._attr_unique_id                = f"{entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class             = device_class
        self._attr_state_class              = state_class
        self._attr_has_entity_name          = True
        self._attr_icon                     = icon
        if precision is not None:
            self._attr_suggested_display_precision = precision

    @property
    def device_info(self):
        di = self.coordinator.device_info
        return {
            "identifiers": {(DOMAIN, self.coordinator.device_sn)},
            "name": di.get("stationName", "Dyness Battery"),
            "manufacturer": "Dyness",
            "model": di.get("deviceModelName", "Junior Box"),
            "sw_version": di.get("firmwareVersion"),
        }

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._key)

    @property
    def available(self):
        return self.coordinator.last_update_success and self.native_value is not None


# ── Module-level sensor entity ────────────────────────────────────────────────

class DynessModuleSensor(CoordinatorEntity, SensorEntity):
    """Sensor for a single DYNESS sub-module (DYNESS01–DYNESS0N)."""

    def __init__(self, coordinator, entry, module_id, data_key, translation_key,
                 unit, device_class, state_class, icon,
                 enabled_default=True, precision=None):
        super().__init__(coordinator)
        self._module_id  = module_id
        self._data_key   = data_key
        self._attr_translation_key             = translation_key
        self._attr_unique_id                   = f"{entry.entry_id}_{module_id}_{data_key}"
        self._attr_native_unit_of_measurement  = unit
        self._attr_device_class                = device_class
        self._attr_state_class                 = state_class
        self._attr_has_entity_name             = True
        self._attr_icon                        = icon
        self._attr_entity_registry_enabled_default = enabled_default
        if precision is not None:
            self._attr_suggested_display_precision = precision

    @property
    def device_info(self):
        """Each module appears as a sub-device of the main BMS device."""
        return {
            "identifiers": {(DOMAIN, f"{self.coordinator.device_sn}_{self._module_id}")},
            "name": f"Dyness {self._module_id}",
            "manufacturer": "Dyness",
            "model": "DYNESS Battery Module",
            "via_device": (DOMAIN, self.coordinator.device_sn),
        }

    def _module_data(self) -> dict:
        return (self.coordinator.data or {}).get("module_data", {}).get(self._module_id, {})

    @property
    def native_value(self):
        return self._module_data().get(self._data_key)

    @property
    def available(self):
        return (
            self.coordinator.last_update_success
            and self._module_id in (self.coordinator.data or {}).get("module_data", {})
        )


class DynessModuleCellSensor(DynessModuleSensor):
    """Individual cell voltage sensor — disabled by default."""

    def __init__(self, coordinator, entry, module_id, cell_num: int):
        key = f"cell_{cell_num}_v"
        super().__init__(
            coordinator, entry, module_id, key,
            translation_key=None,          # overridden by _attr_name below
            unit=UnitOfElectricPotential.VOLT,
            device_class=SensorDeviceClass.VOLTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:battery-outline",
            enabled_default=False,         # hidden by default; enable in HA UI
            precision=3,
        )
        self._cell_num = cell_num
        self._attr_unique_id = f"{entry.entry_id}_{module_id}_cell_{cell_num}_v"
        # Explicit name so HA shows "Cell N Voltage" under the module device
        self._attr_name = f"Cell {cell_num} Voltage"
