import voluptuous as vol
from homeassistant import config_entries


class ElegooConfigFlow(config_entries.ConfigFlow, domain="elegoo"):
    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required("ip_address"): str}),
            )

        self.ip_address = user_input["ip_address"]
        return self.async_create_entry(
            title=f"Elegoo Mars at {self.ip_address}",  # Customize the title
            data={"ip_address": self.ip_address},
        )
