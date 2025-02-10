"""Adds config flow for Elegoo."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.helpers import selector

from .const import DOMAIN, LOGGER
from .elegoo.elegoo_printer import (
    ElegooPrinterClient,
    ElegooPrinterClientWebsocketConnectionError,
    ElegooPrinterClientWebsocketError,
)

if TYPE_CHECKING:
    from custom_components.elegoo_printer.elegoo.models.printer import Printer


class ElegooPrinterClientGeneralError(Exception):
    """Exception For Elegoo Printer."""


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
                printer = await self._test_credentials(
                    ip_address=user_input[CONF_IP_ADDRESS],
                )
            except ElegooPrinterClientWebsocketConnectionError as exception:
                LOGGER.error(exception)
                _errors["base"] = "connection"
            except ElegooPrinterClientWebsocketError as exception:
                LOGGER.exception(exception)
                _errors["base"] = "websocket"
            except (OSError, Exception) as exception:
                LOGGER.exception(exception)
                _errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(unique_id=printer.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=printer.name,
                    description=printer.name,
                    data=printer.__dict__,
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

    async def _test_credentials(self, ip_address: str) -> Printer:
        """Validate credentials."""
        elegoo_printer = ElegooPrinterClient(ip_address)
        printer = elegoo_printer.discover_printer()
        if printer:
            return printer
        raise ElegooPrinterClientGeneralError from Exception("No Printer")
