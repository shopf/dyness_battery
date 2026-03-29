from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfElectricPotential
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from . import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        DynessSensor(coordinator, entry, "soc", "Battery SOC", PERCENTAGE, SensorDeviceClass.BATTERY),
        DynessSensor(coordinator, entry, "power", "Power", UnitOfPower.WATT, SensorDeviceClass.POWER),
        DynessSensor(coordinator, entry, "voltage", "Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
    ]
    async_add_entities(entities)
    
    known_mids = set()
    def _add_modules():
        m_data = coordinator.data.get("module_data", {})
        for mid in [m for m in m_data if m not in known_mids]:
            known_mids.add(mid)
            async_add_entities([DynessCellSensor(coordinator, entry, mid, i) for i in range(1, 31)])
    _add_modules()
    coordinator.async_add_listener(_add_modules)

class DynessSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coord, entry, key, name, unit, dev):
        super().__init__(coord)
        self._key, self._attr_name = key, name
        self._attr_native_unit_of_measurement, self._attr_device_class = unit, dev
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_has_entity_name = True
    @property
    def device_info(self): return {"identifiers": {(DOMAIN, self.coordinator.device_sn)}, "name": "Dyness T14"}
    @property
    def native_value(self): return self.coordinator.data.get(self._key)

class DynessCellSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coord, entry, mid, idx):
        super().__init__(coord)
        self._mid, self._idx = mid, idx
        self._attr_name = f"Cell {idx:02d}"
        self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
        self._attr_unique_id = f"{entry.entry_id}_m{mid}_c{idx}"
        self._attr_has_entity_name = True
    @property
    def device_info(self): return {"identifiers": {(DOMAIN, f"{self.coordinator.device_sn}_{self._mid}")}, "name": f"Module {self._mid}", "via_device": (DOMAIN, self.coordinator.device_sn)}
    @property
    def native_value(self): return self.coordinator.data.get("module_data", {}).get(self._mid, {}).get(f"cell_{self._idx:02d}")