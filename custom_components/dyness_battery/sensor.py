"""Sensors for Dyness Tower T14."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfElectricCurrent, UnitOfEnergy, UnitOfTemperature, UnitOfElectricPotential
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from . import DOMAIN

_D = EntityCategory.DIAGNOSTIC

# Key, Translation Name, Unit, Class, State, Icon, Prec, Category
BDU_SENSORS = [
    ("soc", "battery_soc", PERCENTAGE, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "mdi:battery", 0, None),
    ("realTimePower", "power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:flash", 0, None),
    ("realTimeCurrent", "current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "mdi:current-dc", 1, None),
    ("packVoltage", "pack_voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "mdi:sine-wave", 1, None),
    ("soh", "battery_health", PERCENTAGE, None, SensorStateClass.MEASUREMENT, "mdi:heart-pulse", 0, _D),
    ("cycleCount", "cycle_count", None, None, SensorStateClass.TOTAL_INCREASING, "mdi:battery-sync", 0, _D),
    ("insulation_pos", "insulation_positive", "kΩ", None, SensorStateClass.MEASUREMENT, "mdi:shield-check", 0, _D),
    ("insulation_neg", "insulation_negative", "kΩ", None, SensorStateClass.MEASUREMENT, "mdi:shield-check", 0, _D),
    ("balancing", "balancing_status", None, None, None, "mdi:scale-balance", None, _D),
    ("master_alarm", "system_alarm", None, None, None, "mdi:alert", None, _D),
    ("workStatus", "operation_mode", None, None, None, "mdi:cog", None, _D),
]

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DynessBDUSensor(coordinator, entry, *s) for s in BDU_SENSORS])
    
    # Auto-add modules 01-04 as they are found
    known_mids = set()
    def _check_modules():
        m_data = coordinator.data.get("module_data", {})
        for mid in [m for m in m_data if m not in known_mids]:
            known_mids.add(mid)
            async_add_entities([DynessCellSensor(coordinator, entry, mid, i) for i in range(1, 31)])
    _check_modules()
    coordinator.async_add_listener(_check_modules)

class DynessBDUSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coord, entry, key, name, unit, dev, state, icon, prec, cat):
        super().__init__(coord)
        self._key, self._attr_name = key, name.replace("_", " ").title()
        self._attr_native_unit_of_measurement, self._attr_device_class = unit, dev
        self._attr_state_class, self._attr_icon = state, icon
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_entity_category, self._attr_has_entity_name = cat, True
        if prec is not None: self._attr_suggested_display_precision = prec

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.device_sn)}, "name": "Dyness T14", "manufacturer": "Dyness", "model": "Tower BDU"}

    @property
    def native_value(self): return self.coordinator.data.get(self._key)

class DynessCellSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coord, entry, mid, index):
        super().__init__(coord)
        self._mid, self._idx = mid, index
        self._attr_name = f"Cell {index:02d}"
        self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
        self._attr_device_class = SensorDeviceClass.VOLTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_unique_id = f"{entry.entry_id}_m{mid}_c{index}"
        self._attr_has_entity_name = True
        self._attr_suggested_display_precision = 3

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, f"{self.coordinator.device_sn}_{self._mid}")}, "name": f"Dyness Module {self._mid}", "via_device": (DOMAIN, self.coordinator.device_sn)}

    @property
    def native_value(self):
        return self.coordinator.data.get("module_data", {}).get(self._mid, {}).get(f"cell_{self._idx:02d}")