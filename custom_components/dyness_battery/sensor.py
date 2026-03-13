"""Sensoren für Dyness Battery Integration."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE, UnitOfPower, UnitOfElectricCurrent, UnitOfEnergy, UnitOfTemperature,
    UnitOfElectricPotential
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN

SENSORS = [
    # key, translation_key, unit, device_class, state_class, icon
    ("soc",                   "battery_soc",            PERCENTAGE,                        SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT, "mdi:battery-high"),
    ("realTimePower",         "battery_power",          UnitOfPower.WATT,                  SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT, "mdi:lightning-bolt"),
    ("realTimeCurrent",       "battery_current",        UnitOfElectricCurrent.AMPERE,      SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT, "mdi:current-dc"),
    ("createTime",            "last_update",            None,                               None,                          None,                          "mdi:clock-outline"),
    ("batteryCapacity",       "battery_capacity",       UnitOfEnergy.KILO_WATT_HOUR,       SensorDeviceClass.ENERGY,      None,                          "mdi:battery"),
    ("installedPower",        "installed_power",        UnitOfPower.KILO_WATT,             SensorDeviceClass.POWER,       None,                          "mdi:solar-power"),
    ("deviceCommunicationStatus", "communication_status", None,                            None,                          None,                          "mdi:wifi"),
    ("firmwareVersion",       "firmware_version",       None,                               None,                          None,                          "mdi:chip"),
    ("workStatus",            "work_status",            None,                               None,                          None,                          "mdi:home-battery"),
    # Neue Sensoren aus realTime/data
    ("soh",                   "battery_soh",            PERCENTAGE,                        SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT, "mdi:battery-heart"),
    ("tempMax",               "temp_max",               UnitOfTemperature.CELSIUS,         SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "mdi:thermometer-high"),
    ("tempMin",               "temp_min",               UnitOfTemperature.CELSIUS,         SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "mdi:thermometer-low"),
    ("cellVoltageMax",        "cell_voltage_max",       UnitOfElectricPotential.VOLT,      SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT, "mdi:sine-wave"),
    ("cellVoltageMin",        "cell_voltage_min",       UnitOfElectricPotential.VOLT,      SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT, "mdi:sine-wave"),
    ("energyChargeDay",       "energy_charge_day",      UnitOfEnergy.KILO_WATT_HOUR,       SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-charging"),
    ("energyDischargeDay",    "energy_discharge_day",   UnitOfEnergy.KILO_WATT_HOUR,       SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-minus"),
    ("energyChargeTotal",     "energy_charge_total",    UnitOfEnergy.KILO_WATT_HOUR,       SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-charging-100"),
    ("energyDischargeTotal",  "energy_discharge_total", UnitOfEnergy.KILO_WATT_HOUR,       SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-minus-outline"),
    ("cycleCount",            "cycle_count",            None,                               None,                          SensorStateClass.TOTAL_INCREASING, "mdi:battery-sync"),
]


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    # Basis-Sensoren immer registrieren, optionale nur wenn Daten vorhanden
    ALWAYS_REGISTER = {
        "soc", "realTimePower", "realTimeCurrent", "createTime",
        "batteryCapacity", "installedPower", "deviceCommunicationStatus",
        "firmwareVersion", "workStatus",
    }
    available_data = coordinator.data or {}
    async_add_entities([
        DynessSensor(coordinator, entry, key, translation_key, unit, device_class, state_class, icon)
        for key, translation_key, unit, device_class, state_class, icon in SENSORS
        if key in ALWAYS_REGISTER or available_data.get(key) is not None
    ])


class DynessSensor(CoordinatorEntity, SensorEntity):

    def __init__(self, coordinator, entry, key, translation_key, unit, device_class, state_class, icon):
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_has_entity_name = True
        self._attr_icon = icon

    @property
    def device_info(self):
        device_info = self.coordinator.device_info
        return {
            "identifiers": {(DOMAIN, self.coordinator.device_sn)},
            "name": device_info.get("stationName", "Dyness Battery"),
            "manufacturer": "Dyness",
            "model": device_info.get("deviceModelName", "Junior Box"),
            "sw_version": device_info.get("firmwareVersion"),
        }

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get(self._key)
        return None

    @property
    def available(self):
        return self.coordinator.last_update_success and self.native_value is not None
