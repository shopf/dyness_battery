"""Select-Entities für Dyness Battery (SCHEMA_JUNIOR Steuerung)."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, SCHEMA_JUNIOR
from ._device_info_mixin import DynessDeviceInfoMixin

_LOGGER = logging.getLogger(__name__)

# HINWEIS zu workMode (SetBaseSetting): siehe __init__.py (_FIXED_WORK_MODE) -
# ein Praxistest hat bestätigt, dass das Feld auf der Junior Box keine Wirkung
# hat. Die zugehörige Entity wurde deshalb entfernt.

# mode je Zeitfenster-Gruppe (SetWorkModeSetting) - NUR Junior-Box-Enum
# (16/17/255), NICHT die Cygni/AquaVolt-Enum (0/1) verwenden!
GROUP_MODE_API = {
    "load_priority": "16",
    "battery_priority": "17",
    "shutdown": "255",
}
GROUP_MODE_API_REVERSE = {v: k for k, v in GROUP_MODE_API.items()}

# Wochentage-Presets für "week" - vereinfachte Auswahl statt Einzeltage.
# Stabile Options-Keys, Anzeige kommt aus translations/*.json.
WEEK_PRESET_API = {
    "all_days": "0,1,2,3,4,5,6",
    "weekdays": "0,1,2,3,4",
    "weekend": "5,6",
}
WEEK_PRESET_API_REVERSE = {v: k for k, v in WEEK_PRESET_API.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.data.get("_schema") != SCHEMA_JUNIOR:
        return

    entities: list[SelectEntity] = []
    for group in range(1, 5):
        entities.append(DynessGroupModeSelect(coordinator, group))
        entities.append(DynessGroupWeekSelect(coordinator, group))

    async_add_entities(entities)


class DynessGroupModeSelect(DynessDeviceInfoMixin, CoordinatorEntity, SelectEntity):
    """Modus (Load/Battery Priority/Shutdown) je Zeitfenster-Gruppe.

    Setzt beim Ändern automatisch "aktiv" (state="1"), da die Junior Box
    Zeitfenster-Werte sonst verwirft - außer bei "Shutdown",
    das ist inhaltlich bereits ein Deaktivieren.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "group_mode"
    _attr_options = list(GROUP_MODE_API.keys())

    def __init__(self, coordinator, group: int) -> None:
        super().__init__(coordinator)
        self._group = group
        self._attr_unique_id = f"{coordinator.device_sn}_group{group}_mode"
        self._attr_translation_placeholders = {"group": str(group)}

    @property
    def current_option(self) -> str | None:
        raw = self.coordinator.tou_groups.get(self._group, {}).get("mode")
        return GROUP_MODE_API_REVERSE.get(str(raw))

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.snapshot_tou_state_before_edit(self._group)
        g = self.coordinator.tou_groups.setdefault(self._group, {})
        g["mode"] = GROUP_MODE_API[option]
        if option != "shutdown":
            g["state"] = "1"
        self.coordinator.schedule_tou_write(self._group)
        self.async_write_ha_state()


class DynessGroupWeekSelect(DynessDeviceInfoMixin, CoordinatorEntity, SelectEntity):
    """Wochentage-Preset je Zeitfenster-Gruppe (vereinfachte Auswahl).

    Setzt beim Ändern automatisch "aktiv", siehe DynessGroupModeSelect.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "group_week"
    _attr_options = list(WEEK_PRESET_API.keys())

    def __init__(self, coordinator, group: int) -> None:
        super().__init__(coordinator)
        self._group = group
        self._attr_unique_id = f"{coordinator.device_sn}_group{group}_week"
        self._attr_translation_placeholders = {"group": str(group)}

    @property
    def current_option(self) -> str | None:
        raw = self.coordinator.tou_groups.get(self._group, {}).get("week")
        return WEEK_PRESET_API_REVERSE.get(str(raw), "all_days")

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.snapshot_tou_state_before_edit(self._group)
        g = self.coordinator.tou_groups.setdefault(self._group, {})
        g["week"] = WEEK_PRESET_API[option]
        g["state"] = "1"
        self.coordinator.schedule_tou_write(self._group)
        self.async_write_ha_state()
