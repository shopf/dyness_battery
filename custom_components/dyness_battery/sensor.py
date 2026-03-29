"""Sensors for Dyness Battery Integration."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfElectricCurrent, UnitOfEnergy, UnitOfTemperature, UnitOfElectricPotential
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from . import DOMAIN

_D = EntityCategory.DIAGNOSTIC

SENSORS = [
    # ── Power & Charge ──
    ("soc", "battery_soc", PERCENTAGE, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "mdi:battery-high", None, None),
    ("realTimePower", "battery_power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:lightning-bolt", None, None),
    ("realTimeCurrent", "battery_current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "mdi:current-dc", None, None),
    ("packVoltage", "pack_voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "mdi:sine-wave", 1, None),
    ("chargeLimit", "charge_limit", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "mdi:battery-arrow-up", None, None),
    ("dischargeLimit", "discharge_limit", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "mdi:battery-arrow-down", None, None),
    
    # ── Energy & Lifecycle ──
    ("energyChargeTotal", "total_energy_charged", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:counter", None, None),
    ("cycleCount", "cycle_count", None, None, SensorStateClass.TOTAL_INCREASING, "mdi:battery-sync", None, None),
    ("batteryCapacity", "rated_capacity", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, None, "mdi:battery", None, _D),
    
    # ── Thermal & Health ──
    ("soh", "battery_soh", PERCENTAGE, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "mdi:battery-heart", None, None),
    ("cellVoltageDiffMv", "cell_voltage_spread", "mV", None, SensorStateClass.MEASUREMENT, "mdi:arrow-expand-horizontal", 1, None),
    ("tempSpreadMax", "temp_spread_max", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "mdi:thermometer-alert", 1, None),
    ("insulationPos", "insulation_positive", "kΩ", None, SensorStateClass.MEASUREMENT, "mdi:shield-check", None, _D),
    ("insulationNeg", "insulation_negative", "kΩ", None, SensorStateClass.MEASUREMENT, "mdi:shield-check", None, _D),
    
    # ── Status & Hardware ──
    ("balancingStatus", "balancing_status", None, None, None, "mdi:scale-balance", None, None),
    ("fanStatus", "fan_status", None, None, None, "mdi:fan", None, _D),
    ("heatingStatus", "heating_status", None, None, None, "mdi:heating-coil", None, _D),
    ("boxCount", "box_count", None, None, None, "mdi:package-variant", None, _D),
    ("cellsPerBox", "cells_per_box", None, None, None, "mdi:battery-charging-100", None, _D),
    ("masterAlarm", "master_alarm_flag", None, None, None, "mdi:alert", None, _D),
]

# ... remaining async_setup_entry and DynessSensor / Module classes same as before ...