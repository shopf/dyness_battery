"""Config Flow für Dyness Battery Integration."""
import uuid
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from . import DOMAIN, _BMS_SUFFIXES, _is_success
import hashlib
import hmac
import base64
import json
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


async def _discover_device_sn(api_id: str, api_secret: str, api_base: str) -> str | None:
    """Versucht BMS SN automatisch zu ermitteln."""
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
                    device_list = (result.get("data", {}) or {}).get("list", [])
                    bms = (
                        next((d for d in device_list
                              if str(d.get("deviceSn", "")).endswith(_BMS_SUFFIXES)), None)
                        or (device_list[0] if device_list else None)
                    )
                    if bms:
                        return bms.get("deviceSn", "")
    except Exception:
        pass
    return None


STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required("api_id"): str,
    vol.Required("api_secret"): str,
    vol.Required("region", default="europe"): vol.In(["europe", "apac"]),
})


class DynessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config Flow für Dyness Battery."""

    VERSION = 1

    def __init__(self):
        self._api_id = None
        self._api_secret = None
        self._api_base = None

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Schritt 1: API-Zugangsdaten + Region + Auto-Discovery."""
        errors = {}

        if user_input is not None:
            self._api_id = user_input["api_id"]
            self._api_secret = user_input["api_secret"]
            self._api_base = API_REGIONS[user_input["region"]]

            sn = await _discover_device_sn(self._api_id, self._api_secret, self._api_base)
            if sn:
                await self.async_set_unique_id(f"{self._api_id}_{sn}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Dyness Battery",
                    data={
                        "api_id":     self._api_id,
                        "api_secret": self._api_secret,
                        "api_base":   self._api_base,
                    }
                )
            else:
                errors["base"] = "discovery_failed"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_manual(self, user_input=None) -> FlowResult:
        """Schritt 2 (Fallback): Manuelle SN-Eingabe."""
        errors = {}

        if user_input is not None:
            sn = user_input["device_sn"]
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
                }
            )

        schema = vol.Schema({
            vol.Required("api_id", default=self._api_id or ""): str,
            vol.Required("api_secret", default=self._api_secret or ""): str,
            vol.Required("region", default="europe"): vol.In(["europe", "apac"]),
            vol.Required("device_sn"): str,
            vol.Optional("dongle_sn", default=""): str,
        })

        return self.async_show_form(
            step_id="manual",
            data_schema=schema,
            errors=errors,
        )
