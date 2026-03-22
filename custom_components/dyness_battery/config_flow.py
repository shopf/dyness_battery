"""Config Flow für Dyness Battery Integration."""
import uuid
import voluptuous as vol
from homeassistant import config_entries
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
        if user_input is not None:
            user_input["api_base"] = "https://open-api.dyness.com"
            # Unique ID = zufällige UUID → mehrere Einträge mit derselben API ID möglich
            await self.async_set_unique_id(str(uuid.uuid4()))
            return self.async_create_entry(
                title="Dyness Battery",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
        )
