"""Gemeinsame Basis für alle neuen Control-Entities (number/select/switch/time/button).

Kapselt die Umwandlung von coordinator.device_info (rohes API-Dict) in das von
Home Assistant erwartete Format - exakt wie in sensor.py, nur einmal zentral
statt in jeder Entity-Klasse dupliziert.
"""
from __future__ import annotations

from . import DOMAIN


class DynessDeviceInfoMixin:
    """Mixin: erwartet, dass self.coordinator existiert (via CoordinatorEntity)."""

    @property
    def device_info(self):
        di = self.coordinator.device_info
        return {
            "identifiers": {(DOMAIN, self.coordinator.device_sn)},
            "name": di.get("stationName", "Dyness Battery"),
            "manufacturer": "Dyness",
            "model": di.get("deviceModelName", "Dyness Battery"),
            "sw_version": di.get("firmwareVersion"),
        }
