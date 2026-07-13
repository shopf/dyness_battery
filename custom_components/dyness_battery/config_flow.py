"""Config Flow für Dyness Battery Integration."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from . import DOMAIN, _BMS_SUFFIXES, _is_success
import hashlib
import hmac
import base64
import aiohttp
from email.utils import formatdate

# API Domains je Region
API_REGIONS = {
    "europe": "https://open-api.dyness.com",
    "apac":   "https://apacopen-api.dyness.com",
}


def _build_headers_cf(api_id, api_secret, body, path):
    date = formatdate(timeval=None, localtime=False, usegmt=True)
    md5 = base64.b64encode(hashlib.md5(body.encode()).digest()).decode()
    sts = f"POST\n{md5}\napplication/json\n{date}\n{path}"
    sig = base64.b64encode(
        hmac.new(api_secret.encode(), sts.encode(), "sha1").digest()
    ).decode()
    return {
        "Content-Type": "application/json;charset=UTF-8",
        "Content-MD5": md5,
        "Date": date,
        "Authorization": f"API {api_id}:{sig}",
    }


async def _fetch_device_list(api_id: str, api_secret: str, api_base: str) -> list:
    """Gibt alle Geräte auf dem Account zurück."""
    path = "/v1/device/storage/list"
    url = f"{api_base}/openapi/ems-device{path}"
    body = "{}"
    headers = _build_headers_cf(api_id, api_secret, body, path)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, data=body,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                result = await resp.json(content_type=None)
                if _is_success(result):
                    return (result.get("data", {}) or {}).get("list", [])
    except Exception:
        pass
    return []


def _select_bms_devices(device_list: list) -> list:
    """Filtert BMS/BDU Geräte — ein Eintrag pro physischem Gerät."""
    bms_devices = [
        d for d in device_list
        if str(d.get("deviceSn", "")).endswith(_BMS_SUFFIXES)
    ]
    # Fallback: alle Geräte wenn kein BMS-Suffix gefunden
    return bms_devices if bms_devices else device_list


# ── Options: Alarm-Delay ─────────────────────────────────────────────────────
# Konfigurierbare Mindestdauer bevor ein Alarm als Notification gemeldet wird.
# Verhindert Benachrichtigungen bei kurzzeitigen Transient-Ereignissen
# (z.B. Pack-High-Voltage beim Balancing auf 100% SOC).
ALARM_DELAY_OPTIONS = {
    "0":   "Sofort (Standard)",
    "15":  "15 Minuten",
    "30":  "30 Minuten",
    "60":  "60 Minuten",
    "120": "2 Stunden",
}


class DynessOptionsFlow(config_entries.OptionsFlow):
    """Options-Dialog: Alarm-Delay konfigurieren."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Gespeicherter Wert immer als String — HA serialisiert Form-Werte als str
        current_delay = str(self.config_entry.options.get("alarm_delay_minutes", "0"))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(
                    "alarm_delay_minutes",
                    default=current_delay,
                ): vol.In(ALARM_DELAY_OPTIONS),
            }),
        )


STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required("api_id"): str,
    vol.Required("api_secret"): str,
    vol.Required("region", default="europe"): vol.In(["europe", "apac"]),
})


class DynessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config Flow für Dyness Battery."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        """Options-Dialog für bestehende Einträge (Zahnrad-Button auf der Integrationskachel).
        
        Hinweis: config_entry wird NICHT an den Konstruktor übergeben — HA 2024.x setzt
        self.config_entry automatisch. Übergabe als Argument würde einen 500-Fehler auslösen.
        """
        return DynessOptionsFlow()

    def __init__(self):
        self._api_id = None
        self._api_secret = None
        self._api_base = None
        self._devices = []      # alle gefundenen Geräte
        self._region = "europe"

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Schritt 1: API-Zugangsdaten + Region."""
        errors = {}

        if user_input is not None:
            self._api_id     = user_input["api_id"]
            self._api_secret = user_input["api_secret"]
            self._region     = user_input["region"]
            self._api_base   = API_REGIONS[self._region]

            device_list = await _fetch_device_list(
                self._api_id, self._api_secret, self._api_base
            )
            self._devices = _select_bms_devices(device_list)

            if not self._devices:
                errors["base"] = "discovery_failed"
            elif len(self._devices) == 1:
                # Nur ein Gerät → direkt anlegen
                return await self._create_entry_for_device(self._devices[0])
            else:
                # Mehrere Geräte → Auswahl anbieten
                return await self.async_step_select_device()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_select_device(self, user_input=None) -> FlowResult:
        """Schritt 2: Gerät auswählen (bei mehreren Geräten auf dem Account)."""
        errors = {}

        if user_input is not None:
            sn = user_input["device_sn"]
            device = next(
                (d for d in self._devices if d.get("deviceSn") == sn), None
            )
            if device:
                return await self._create_entry_for_device(device)
            errors["base"] = "device_not_found"

        # Auswahl-Liste aufbauen
        device_options = {
            d["deviceSn"]: (
                f"{d.get('deviceModelName', 'Dyness')} — "
                f"{d.get('stationName', d['deviceSn'])} "
                f"({d.get('workStatus', '?')})"
            )
            for d in self._devices
            if d.get("deviceSn")
        }

        schema = vol.Schema({
            vol.Required("device_sn"): vol.In(device_options),
        })

        return self.async_show_form(
            step_id="select_device",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "count": str(len(self._devices)),
            },
        )

    async def _create_entry_for_device(self, device: dict) -> FlowResult:
        """Legt einen Config Entry für ein Gerät an."""
        sn = device.get("deviceSn", "")
        station_name = device.get("stationName") or "Dyness Battery"

        # unique_id = api_id + deviceSN → jedes Gerät separat konfigurierbar
        await self.async_set_unique_id(f"{self._api_id}_{sn}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=station_name,
            data={
                "api_id":     self._api_id,
                "api_secret": self._api_secret,
                "api_base":   self._api_base,
                "device_sn":  sn,
                "dongle_sn":  device.get("collectorSn") or None,
            },
        )

    async def async_step_manual(self, user_input=None) -> FlowResult:
        """Fallback: Manuelle SN-Eingabe."""
        errors = {}

        if user_input is not None:
            sn     = user_input["device_sn"]
            region = user_input.get("region", "europe")
            await self.async_set_unique_id(f"{user_input['api_id']}_{sn}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Dyness Battery",
                data={
                    "api_id":     user_input["api_id"],
                    "api_secret": user_input["api_secret"],
                    "api_base":   API_REGIONS.get(region, API_REGIONS["europe"]),
                    "device_sn":  sn,
                    "dongle_sn":  user_input.get("dongle_sn") or None,
                },
            )

        schema = vol.Schema({
            vol.Required("api_id",     default=self._api_id     or ""): str,
            vol.Required("api_secret", default=self._api_secret or ""): str,
            vol.Required("region",     default="europe"): vol.In(["europe", "apac"]),
            vol.Required("device_sn"): str,
            vol.Optional("dongle_sn", default=""): str,
        })

        return self.async_show_form(
            step_id="manual",
            data_schema=schema,
            errors=errors,
        )
