"""Config Flow for Dyness Battery Integration."""
import uuid
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from . import DOMAIN, _BMS_SUFFIXES, _is_success, _MIN_CALL_INTERVAL
import asyncio
import hashlib
import hmac
import base64
import json
import time
import aiohttp
from email.utils import formatdate


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


async def _discover_device_sn(api_id: str, api_secret: str) -> str | None:
    """Attempts to automatically discover the BMS SN."""
    url = "https://open-api.dyness.com/openapi/ems-device/v1/device/storage/list"
    body = "{}"
    headers = _build_headers_cf(api_id, api_secret, body, "/v1/device/storage/list")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=body, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
})


class DynessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config Flow for Dyness Battery."""

    VERSION = 1

    def __init__(self):
        self._api_id = None
        self._api_secret = None
        self._discovered_sn = None

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Step 1: API Credentials + Auto-Discovery."""
        errors = {}

        if user_input is not None:
            self._api_id = user_input["api_id"]
            self._api_secret = user_input["api_secret"]

            # Attempt Auto-Discovery
            sn = await _discover_device_sn(self._api_id, self._api_secret)
            if sn:
                self._discovered_sn = sn
                # SN-based unique_id — prevents duplicate entries for the same device
                await self.async_set_unique_id(f"{self._api_id}_{sn}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Dyness Battery",
                    data={
                        "api_id": self._api_id,
                        "api_secret": self._api_secret,
                        "api_base": "https://open-api.dyness.com",
                    }
                )
            else:
                # Discovery failed → manual fallback
                errors["base"] = "discovery_failed"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_manual(self, user_input=None) -> FlowResult:
        """Step 2 (Fallback): Manual entry of serial numbers."""
        errors = {}

        if user_input is not None:
            sn = user_input["device_sn"]
            await self.async_set_unique_id(f"{user_input['api_id']}_{sn}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Dyness Battery",
                data={
                    "api_id": user_input["api_id"],
                    "api_secret": user_input["api_secret"],
                    "api_base": "https://open-api.dyness.com",
                    "device_sn": sn,
                    "dongle_sn": user_input.get("dongle_sn") or None,
                }
            )

        schema = vol.Schema({
            vol.Required("api_id", default=self._api_id or ""): str,
            vol.Required("api_secret", default=self._api_secret or ""): str,
            vol.Required("device_sn"): str,
            vol.Optional("dongle_sn", default=""): str,
        })

        return self.async_show_form(
            step_id="manual",
            data_schema=schema,
            errors=errors,
        )