"""Config Flow für Dyness Battery Integration."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from . import DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required("api_id"): str,
    vol.Required("api_secret"): str,
})


class DynessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config Flow für Dyness Battery."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}

        if user_input is not None:
            user_input["api_base"] = "https://open-api.dyness.com"
            await self.async_set_unique_id(user_input["api_id"])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Dyness Battery",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
