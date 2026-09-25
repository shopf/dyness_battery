"""Button-Entities für Dyness Battery."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, SCHEMA_JUNIOR, _notify_text
from ._device_info_mixin import DynessDeviceInfoMixin

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.data.get("_schema") != SCHEMA_JUNIOR:
        return

    async_add_entities([DynessRefreshFromServerButton(coordinator)])


class DynessRefreshFromServerButton(DynessDeviceInfoMixin, CoordinatorEntity, ButtonEntity):
    """Erzwingt einen sofortigen Lesepoll und importiert die Telemetrie neu.

    Reine Lese-Aktion — läuft NICHT über den Write-Guard, funktioniert also
    auch wenn "Schreiben aktiviert" aus ist.

    WICHTIG: Kann Start/Ende/Leistung/Modus der Zeitfenster sowie Leistungs-
    grenze/Entladetiefe zuverlässig neu von Dyness holen. Der "Zeitfenster X
    aktiv"-Schalter und die Wochentage-Auswahl können NICHT zurückgeholt
    werden — dafür gibt es keinen bekannten zuverlässigen Rohpunkt (der
    vermeintliche "State"-Punkt ist tatsächlich die Wochentage-Bitmaske,
    keine Ein/Aus-Information). Nützlich z.B. nach Änderungen direkt in der
    Dyness-App, damit HA nicht dauerhaft veraltete, selbst gesetzte Werte
    anzeigt.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "refresh_from_server"
    _attr_icon = "mdi:cloud-sync"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_sn}_refresh_from_server"

    async def async_press(self) -> None:
        # Alle lokalen "HA hat das schon geschrieben"-Sperren aufheben, damit
        # der nächste Lesepoll die Telemetrie wieder vollständig übernimmt.
        for group in range(1, 5):
            self.coordinator._tou_pending[group] = False
        self.coordinator._base_setting_pending = False
        await self.coordinator.async_request_refresh()

        # Erinnerung: der "aktiv"-Schalter kann NICHT automatisch zurückgeholt
        # werden (siehe Klassendoku) - der Nutzer muss das ggf. selbst prüfen.
        try:
            title, message = _notify_text(self.hass, "refresh_reminder")
            await self.hass.services.async_call(
                "persistent_notification", "create",
                {
                    "title": title,
                    "message": message,
                    "notification_id": "dyness_refresh_reminder",
                },
                blocking=False,
            )
        except Exception:  # noqa: BLE001
            pass
