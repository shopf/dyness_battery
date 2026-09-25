"""Number-Entities für Dyness Battery (SCHEMA_JUNIOR Steuerung)."""
from __future__ import annotations

import logging
import math

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, SCHEMA_JUNIOR
from ._device_info_mixin import DynessDeviceInfoMixin

_LOGGER = logging.getLogger(__name__)

POWER_STEP = 8
GROUP_POWER_MIN = 152
GROUP_POWER_MAX = 800


def _round_to_step(value: int, min_value: int, max_value: int, step: int) -> int:
    """Rundet auf das nächste Vielfache von step (halbe Schritte aufwärts,
    wie die Dyness-App: 164 -> 168, nicht 160) und begrenzt auf [min,max]."""
    if value == 0:
        return 0
    rounded = int(math.floor(value / step + 0.5)) * step
    return max(min_value, min(max_value, rounded))


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.data.get("_schema") != SCHEMA_JUNIOR:
        return  # Steuerung aktuell nur für die Junior Box implementiert

    entities: list[NumberEntity] = [
        DynessPowerLimitNumber(coordinator),
        DynessDischargeDepthNumber(coordinator),
    ]
    for group in range(1, 5):
        entities.append(DynessGroupPowerNumber(coordinator, group))

    async_add_entities(entities)


class DynessPowerLimitNumber(DynessDeviceInfoMixin, CoordinatorEntity, NumberEntity):
    """Leistungsgrenze (SetBaseSetting.powerLimit): 152-800W, Vielfache von 8."""

    _attr_has_entity_name = True
    _attr_translation_key = "base_power_limit"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = GROUP_POWER_MIN
    _attr_native_max_value = GROUP_POWER_MAX
    _attr_native_step = POWER_STEP
    _attr_native_unit_of_measurement = "W"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_sn}_base_power_limit"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.base_setting.get("power_limit")

    async def async_set_native_value(self, value: float) -> None:
        value_int = _round_to_step(int(round(value)), GROUP_POWER_MIN, GROUP_POWER_MAX, POWER_STEP)
        self.coordinator.base_setting["power_limit"] = value_int
        self.coordinator.schedule_base_setting_write()
        self.async_write_ha_state()


class DynessDischargeDepthNumber(DynessDeviceInfoMixin, CoordinatorEntity, NumberEntity):
    """Entladetiefe (DOD): 20-100%, keine Schrittweiten-Beschränkung bekannt."""

    _attr_has_entity_name = True
    _attr_translation_key = "base_discharge_depth"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 20
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_sn}_base_discharge_depth"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.base_setting.get("discharge_depth")

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.base_setting["discharge_depth"] = int(value)
        self.coordinator.schedule_base_setting_write()
        self.async_write_ha_state()


class DynessGroupPowerNumber(DynessDeviceInfoMixin, CoordinatorEntity, NumberEntity):
    """Leistung (W) einer der 4 Zeitfenster-Gruppen.

    0 = unkonfiguriert (Default-Zustand, wenn das Zeitfenster noch nie genutzt
    wurde). Jeder Wert >0 wird auf das nächste Vielfache von 8 im Bereich
    152-800W gerundet (wie die Dyness-App) und setzt automatisch "aktiv", da
    die Junior Box Zeitfenster-Werte sonst verwirft.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "group_power"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = GROUP_POWER_MAX
    _attr_native_step = POWER_STEP
    _attr_native_unit_of_measurement = "W"

    def __init__(self, coordinator, group: int) -> None:
        super().__init__(coordinator)
        self._group = group
        self._attr_unique_id = f"{coordinator.device_sn}_group{group}_power"
        self._attr_translation_placeholders = {"group": str(group)}

    @property
    def native_value(self) -> float | None:
        return self.coordinator.tou_groups.get(self._group, {}).get("power")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.snapshot_tou_state_before_edit(self._group)
        value_int = _round_to_step(int(round(value)), GROUP_POWER_MIN, GROUP_POWER_MAX, POWER_STEP)
        g = self.coordinator.tou_groups.setdefault(self._group, {})
        g["power"] = value_int
        if value_int != 0:
            # Die Junior Box verwirft Zeitfenster-Werte, wenn state="0" (inaktiv)
            # ist — also beim Setzen eines sinnvollen Werts automatisch aktivieren.
            g["state"] = "1"
        self.coordinator.schedule_tou_write(self._group)
        self.async_write_ha_state()
