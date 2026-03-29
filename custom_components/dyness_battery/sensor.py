"""Sensors for Dyness Battery Integration."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfElectricCurrent, UnitOfEnergy, UnitOfTemperature, UnitOfElectricPotential
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from . import DOMAIN

_D = EntityCategory.DIAGNOSTIC

SENSORS = [
    ("soc", "battery_soc", PERCENTAGE, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "mdi:battery-high", None, None),
    ("realTimePower", "battery_power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:lightning-bolt", None, None),
    ("realTimeCurrent", "battery_current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "mdi:current-dc", None, None),
    ("packVoltage", "pack_voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "mdi:sine-wave", 3, None),
    ("soh", "battery_soh", PERCENTAGE, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "mdi:battery-heart", None, None),
    ("cellVoltageMax", "cell_voltage_max", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "mdi:sine-wave", 3, None),
    ("cellVoltageMin", "cell_voltage_min", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "mdi:sine-wave", 3, None),
    ("cellVoltageDiffMv", "cell_voltage_diff_mv", "mV", None, SensorStateClass.MEASUREMENT, "mdi:arrow-expand-horizontal", 1, None),
    ("chargeLimit", "charge_limit", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "mdi:battery-arrow-up", None, None),
    ("dischargeLimit", "discharge_limit", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "mdi:battery-arrow-down", None, None),
    ("fanStatus", "fan_status", None, None, None, "mdi:fan", None, _D),
    ("heatingStatus", "heating_status", None, None, None, "mdi:heating-coil", None, _D),
    ("batteryCapacity", "battery_capacity", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, None, "mdi:battery", None, _D),
    ("firmwareVersion", "firmware_version", None, None, None, "mdi:chip", None, _D),
    ("workStatus", "work_status", None, None, None, "mdi:home-battery", None, _D),
]

ALWAYS_REGISTER = {"soc", "realTimePower", "realTimeCurrent", "batteryCapacity", "firmwareVersion", "workStatus"}

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DynessSensor(coordinator, entry, *s) for s in SENSORS if s[0] in ALWAYS_REGISTER or coordinator.data.get(s[0]) is not None])
    known_module_ids = set()
    def _add_new_modules():
        m_data = (coordinator.data or {}).get("module_data", {})
        new_mids = [mid for mid in m_data if mid not in known_module_ids]
        for mid in new_mids:
            known_module_ids.add(mid)
            async_add_entities([DynessModuleSensor(coordinator, entry, mid, *s) for s in MODULE_SENSORS])
    _add_new_modules()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_modules))

class DynessSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coord, entry, key, trans, unit, dev, state, icon, prec, cat):
        super().__init__(coord)
        self._key, self._attr_translation_key, self._attr_native_unit_of_measurement = key, trans, unit
        self._attr_device_class, self._attr_state_class, self._attr_icon = dev, state, icon
        self._attr_unique_id, self._attr_has_entity_name = f"{entry.entry_id}_{key}", True
        if prec: self._attr_suggested_display_precision = prec
        if cat: self._attr_entity_category = cat
    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.device_sn)}, "name": "Dyness Main Unit", "manufacturer": "Dyness", "model": "Tower BDU"}
    @property
    def native_value(self): return self.coordinator.data.get(self._key)

MODULE_SENSORS = [
    ("cell_temp_1", "module_temp_1", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "mdi:thermometer", None),
    ("cell_temp_2", "module_temp_2", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "mdi:thermometer", None),
    ("cell_voltage_spread_mv", "module_spread", "mV", None, SensorStateClass.MEASUREMENT, "mdi:arrow-expand-horizontal", 1),
    *[(f"cell_{i:02d}", f"cell_{i:02d}", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "mdi:battery-outline", 3) for i in range(1, 31)],
]

class DynessModuleSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coord, entry, mid, key, trans, unit, dev, state, icon, prec=None):
        super().__init__(coord)
        self._mid, self._key, self._attr_translation_key = mid, key, trans
        self._attr_native_unit_of_measurement, self._attr_device_class = unit, dev
        self._attr_state_class, self._attr_icon, self._attr_has_entity_name = state, icon, True
        self._attr_unique_id = f"{entry.entry_id}_{mid}_{key}"
        if prec: self._attr_suggested_display_precision = prec
    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, f"{self.coordinator.device_sn}_{self._mid}")}, "name": f"Dyness Module {self._mid}", "manufacturer": "Dyness", "model": "Battery Pack", "via_device": (DOMAIN, self.coordinator.device_sn)}
    @property
    def native_value(self): return self.coordinator.data.get("module_data", {}).get(self._mid, {}).get(self._key)