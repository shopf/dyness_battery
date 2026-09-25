"""Time-Entities für Dyness Battery (SCHEMA_JUNIOR Steuerung).

Hinweis: Der Dateiname 'time.py' entspricht der HA-Konvention für die
'time'-Plattform (homeassistant.components.time). Um jede Verwechslung mit
dem Standard-Python-Modul 'time' zu vermeiden , wird hier bewusst NICHT `import time`
verwendet - stattdessen ausschließlich `datetime.time`.
"""
from __future__ import annotations

import logging
from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
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

    entities: list[TimeEntity] = []
    for group in range(1, 5):
        entities.append(DynessGroupTimeEntity(coordinator, group, "startTime", "group_start"))
        entities.append(DynessGroupTimeEntity(coordinator, group, "endTime", "group_end"))

    async_add_entities(entities)


def _parse_hhmm(value: str | None) -> dt_time | None:
    if not value:
        return None
    try:
        hh, mm = value.split(":")
        return dt_time(hour=int(hh), minute=int(mm))
    except (ValueError, AttributeError):
        return None


class DynessGroupTimeEntity(DynessDeviceInfoMixin, CoordinatorEntity, TimeEntity):
    """Start- oder Endzeit einer der 4 Zeitfenster-Gruppen.

    Setzt beim Ändern automatisch "aktiv", da die Junior Box Zeitfenster-Werte
    sonst verwirft.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator, group: int, field: str, translation_key: str) -> None:
        super().__init__(coordinator)
        self._group = group
        self._field = field
        self._attr_unique_id = f"{coordinator.device_sn}_group{group}_{field}"
        self._attr_translation_key = translation_key
        self._attr_translation_placeholders = {"group": str(group)}

    @property
    def native_value(self) -> dt_time | None:
        raw = self.coordinator.tou_groups.get(self._group, {}).get(self._field)
        return _parse_hhmm(raw)

    async def async_set_value(self, value: dt_time) -> None:
        await self.coordinator.snapshot_tou_state_before_edit(self._group)
        hhmm = f"{value.hour:02d}:{value.minute:02d}"
        g = self.coordinator.tou_groups.setdefault(self._group, {})
        g[self._field] = hhmm
        g["state"] = "1"
        self.coordinator.schedule_tou_write(self._group)
        self.async_write_ha_state()
