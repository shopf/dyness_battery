from homeassistant import config_entries
import voluptuous as vol
from . import DOMAIN

class DynessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title=f"Dyness {user_input['device_sn']}", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("api_id"): str,
                vol.Required("api_secret"): str,
                vol.Required("device_sn"): str,
                vol.Optional("api_base", default="https://open-api.dyness.com"): str,
            }),
        )