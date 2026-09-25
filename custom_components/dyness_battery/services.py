"""Steuerungs-Services für die Dyness Battery Integration (Vorerst SCHEMA_JUNIOR).

Diese Datei ist bewusst als eigenständiges Modul gehalten, damit sie sich ohne
großen Merge-Konflikt neben der bestehenden __init__.py einfügen lässt.

Registriert zwei Services:
  - dyness_battery.set_base_setting    -> POST /v2/SetBaseSetting
  - dyness_battery.set_work_schedule   -> POST /v2/SetWorkModeSetting
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from . import DOMAIN  # DOMAIN wird bereits in __init__.py definiert

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_BASE_SETTING = "set_base_setting"
SERVICE_SET_WORK_SCHEDULE = "set_work_schedule"


def _multiple_of_8(value: int) -> int:
    """Voluptuous-Validator: wirft, wenn value kein Vielfaches von 8 ist."""
    if int(value) % 8 != 0:
        raise vol.Invalid("Muss ein Vielfaches von 8 sein (z.B. 152, 160, 168, ...)")
    return value


# ── Schema: SetBaseSetting ──────────────────────────────────────────────────
SET_BASE_SETTING_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        # 0=Eigenverbrauch, 1=Off-grid, 3=Economic, 6=Battery priority (siehe PDF)
        # Praxistest bestätigt: hat auf der Junior Box keine Wirkung.
        vol.Required("work_mode"): vol.In(["0", "1", "3", "6"]),
        vol.Required("power_limit"): vol.All(
            vol.Coerce(int), vol.Range(min=152, max=800), _multiple_of_8
        ),
        vol.Required("discharge_depth"): vol.All(vol.Coerce(int), vol.Range(min=20, max=100)),
    }
)

# ── Schema: SetWorkModeSetting (eine Zeitfenster-Gruppe) ────────────────────
_TIME_RE = r"^([01]\d|2[0-3]):[0-5]\d$"

GROUP_SCHEMA = vol.Schema(
    {
        vol.Required("batteryWorkGroup"): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
        vol.Required("state"): vol.In(["0", "1"]),
        # 16=Load Priority, 17=Battery Priority, 255=Shutdown (laut PDF)
        vol.Required("mode"): vol.In(["16", "17", "255"]),
        vol.Required("startTime"): cv.matches_regex(_TIME_RE),
        vol.Required("endTime"): cv.matches_regex(_TIME_RE),
        # 0 = unkonfiguriert, sonst Vielfaches von 8 im Bereich 152-800W
        vol.Required("power"): vol.All(
            vol.Coerce(int),
            vol.Any(
                vol.Equal(0),
                vol.All(vol.Range(min=152, max=800), _multiple_of_8),
            ),
        ),
        # Wochentage: einzelner Wert 0-6, CSV ("0,1,2,3,4") oder Bitmap-Wert als String
        vol.Required("week"): cv.string,
    }
)

SET_WORK_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        # Es müssen immer genau 4 Gruppen (Nummer 1-4) mitgeschickt werden
        vol.Required("groups"): vol.All(cv.ensure_list, [GROUP_SCHEMA], vol.Length(min=4, max=4)),
    }
)


def _get_coordinator(hass: HomeAssistant, config_entry_id: str):
    coordinator = hass.data.get(DOMAIN, {}).get(config_entry_id)
    if coordinator is None:
        raise HomeAssistantError(
            f"Dyness: Kein Coordinator für config_entry_id='{config_entry_id}' gefunden. "
            "Ist die ID korrekt? (Einstellungen > Geräte & Dienste > Dyness Battery > "
            "'..." "' > Diagnose zeigt die entry_id in der URL bzw. den Diagnose-Daten.)"
        )
    return coordinator


async def async_setup_services(hass: HomeAssistant) -> None:
    """Registriert die Dyness Control-Services (idempotent, nur einmal nötig)."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_BASE_SETTING):
        return

    async def handle_set_base_setting(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        result = await coordinator.async_set_base_setting(
            work_mode=str(call.data["work_mode"]),
            power_limit=str(call.data["power_limit"]),
            discharge_depth=str(call.data["discharge_depth"]),
        )
        _LOGGER.info("Dyness set_base_setting Ergebnis: %s", result)

    async def handle_set_work_schedule(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        # Sicherstellen dass Gruppen 1-4 jeweils genau einmal vorkommen
        groups_in = call.data["groups"]
        group_numbers = sorted(g["batteryWorkGroup"] for g in groups_in)
        if group_numbers != [1, 2, 3, 4]:
            raise HomeAssistantError(
                f"Dyness: Es müssen die Gruppen 1,2,3,4 jeweils genau einmal enthalten sein "
                f"(erhalten: {group_numbers})."
            )
        groups = [
            {
                "batteryWorkGroup": str(g["batteryWorkGroup"]),
                "state": str(g["state"]),
                "mode": str(g["mode"]),
                "startTime": g["startTime"],
                "endTime": g["endTime"],
                "power": str(g["power"]),
                "week": str(g["week"]),
            }
            for g in groups_in
        ]
        result = await coordinator.async_set_work_schedule(groups)
        _LOGGER.info("Dyness set_work_schedule Ergebnis: %s", result)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_BASE_SETTING, handle_set_base_setting, schema=SET_BASE_SETTING_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_WORK_SCHEDULE, handle_set_work_schedule, schema=SET_WORK_SCHEDULE_SCHEMA
    )
    _LOGGER.info("Dyness: Control-Services registriert (%s, %s)",
                 SERVICE_SET_BASE_SETTING, SERVICE_SET_WORK_SCHEDULE)
