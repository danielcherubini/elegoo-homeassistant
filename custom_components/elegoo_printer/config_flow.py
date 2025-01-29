"""Adds config flow for Elegoo."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from slugify import slugify

from .api import (
    ElegooPrinterApiClientAuthenticationError,
    ElegooPrinterApiClientCommunicationError,
    ElegooPrinterApiClientError,
)
from .const import DOMAIN, LOGGER
from .printer import ElegooPrinterClient


class ElegooFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Elegoo."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        _errors = {}
        if user_input is not None:
            try:
                await self._test_credentials(
                    ip_address=user_input[CONF_IP_ADDRESS],
                )
            except ElegooPrinterApiClientAuthenticationError as exception:
                LOGGER.warning(exception)
                _errors["base"] = "auth"
            except ElegooPrinterApiClientCommunicationError as exception:
                LOGGER.error(exception)
                _errors["base"] = "connection"
            except ElegooPrinterApiClientError as exception:
                LOGGER.exception(exception)
                _errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(
                    ## Do NOT use this in production code
                    ## The unique_id should never be something that can change
                    ## https://developers.home-assistant.io/docs/config_entries_config_flow_handler#unique-ids
                    unique_id=slugify(user_input[CONF_IP_ADDRESS])
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_IP_ADDRESS],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_IP_ADDRESS,
                        default=(user_input or {}).get(CONF_IP_ADDRESS, vol.UNDEFINED),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT,
                        ),
                    ),
                },
            ),
            errors=_errors,
        )

    async def _test_credentials(self, ip_address: str) -> None:
        """Validate credentials."""
        elegoo_printer = ElegooPrinterClient(ip_address)
        printer = elegoo_printer.discover_printer()
        if printer:
            return True
        return None
        # client = ElegooPrinterApiClient(
        #     ip_address=ip_address,
        #     elegoo_printer=elegoo_printer,
        #     session=async_create_clientsession(self.hass),
        # )
        # await client.async_get_data()
