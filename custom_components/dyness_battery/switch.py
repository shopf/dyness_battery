"""Switch-Entities für Dyness Battery (SCHEMA_JUNIOR Steuerung)."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, SCHEMA_JUNIOR
from ._device_info_mixin import DynessDeviceInfoMixin

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.data.get("_schema") != SCHEMA_JUNIOR:
        return

    entities: list[SwitchEntity] = [DynessWriteEnabledSwitch(coordinator)]
    for group in range(1, 5):
        entities.append(DynessGroupEnabledSwitch(coordinator, group))

    async_add_entities(entities)


class DynessWriteEnabledSwitch(DynessDeviceInfoMixin, CoordinatorEntity, SwitchEntity):
    """Master-Schalter: muss AN sein, damit irgendein Schreibvorgang ausgeführt wird.

    Startet nach jedem Neustart bewusst AUS (kein RestoreEntity) - Sicherheitsnetz
    gegen versehentliches Schreiben direkt nach einem HA-Neustart.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "write_enabled"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_sn}_write_enabled"

    @property
    def is_on(self) -> bool:
        return self.coordinator.write_enabled

    @property
    def icon(self) -> str:
        return "mdi:lock-open-variant" if self.is_on else "mdi:lock"

    @property
    def extra_state_attributes(self) -> dict:
        blocked_until = self.coordinator._write_blocked_until
        remaining = None
        if blocked_until:
            import time as _time
            remaining = max(0, int(blocked_until - _time.monotonic()))
        return {
            "letzte_schreibvorgaenge_im_fenster": len(self.coordinator._write_timestamps),
            "gesperrt_fuer_sekunden": remaining,
        }

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.write_enabled = True
        self.coordinator._write_blocked_until = None
        self.coordinator._write_timestamps = []
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.write_enabled = False
        self.async_write_ha_state()


class DynessGroupEnabledSwitch(DynessDeviceInfoMixin, CoordinatorEntity, SwitchEntity):
    """Aktiv/Inaktiv-Schalter einer der 4 Zeitfenster-Gruppen (state)."""

    _attr_has_entity_name = True
    _attr_translation_key = "group_enabled"

    def __init__(self, coordinator, group: int) -> None:
        super().__init__(coordinator)
        self._group = group
        self._attr_unique_id = f"{coordinator.device_sn}_group{group}_enabled"
        self._attr_translation_placeholders = {"group": str(group)}

    @property
    def is_on(self) -> bool:
        return str(self.coordinator.tou_groups.get(self._group, {}).get("state")) == "1"

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.snapshot_tou_state_before_edit(self._group)
        self.coordinator.tou_groups.setdefault(self._group, {})["state"] = "1"
        self.coordinator.schedule_tou_write(self._group)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.snapshot_tou_state_before_edit(self._group)
        self.coordinator.tou_groups.setdefault(self._group, {})["state"] = "0"
        self.coordinator.schedule_tou_write(self._group)
        self.async_write_ha_state()
