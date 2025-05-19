"""Adds config flow for Elegoo."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import selector

from custom_components.elegoo_printer.elegoo_sdcp.elegoo_printer import (
    ElegooPrinterClient,
    ElegooPrinterClientWebsocketConnectionError,
    ElegooPrinterClientWebsocketError,
)

from .const import DOMAIN, LOGGER, USE_SECONDS

if TYPE_CHECKING:
    from .elegoo_sdcp.models.printer import Printer


async def _test_credentials(ip_address: str, use_seconds: bool) -> Printer:
    """Validate credentials."""
    elegoo_printer = ElegooPrinterClient(ip_address, use_seconds, LOGGER)
    printer = elegoo_printer.discover_printer()
    if printer:
        return printer
    raise ElegooPrinterClientGeneralError from Exception("No Printer")


class ElegooPrinterClientGeneralError(Exception):
    """Exception For Elegoo Printer."""


class ElegooFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Elegoo."""

    VERSION = 1
    MINOR_VERSION = 2

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        _errors = {}
        if user_input is not None:
            try:
                printer = await _test_credentials(
                    ip_address=user_input[CONF_IP_ADDRESS],
                    use_seconds=user_input[USE_SECONDS],
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
                    vol.Required(
                        USE_SECONDS,
                        default=(user_input or {}).get(USE_SECONDS, False),
                    ): selector.BooleanSelector(
                        selector.BooleanSelectorConfig(),
                    ),
                },
            ),
            errors=_errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ElegooOptionsFlowHandler:
        """Get the options flow for this handler."""
        return ElegooOptionsFlowHandler()

    @classmethod
    @callback
    def async_supports_options_flow(
        cls, config_entry: config_entries.ConfigEntry
    ) -> bool:
        """Return options flow support for this handler."""
        return True


class ElegooOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow handler for Elegoo Printer"""

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle options flow"""
        _errors = {}
        current_config = self.config_entry.data

        if user_input is not None:
            try:
                printer = await _test_credentials(
                    ip_address=user_input[CONF_IP_ADDRESS],
                    use_seconds=user_input[USE_SECONDS],
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
                # await self.async_set_unique_id(unique_id=printer.id)
                # self._abort_if_unique_id_configured()
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
                        default=(user_input or {}).get(
                            CONF_IP_ADDRESS,
                            current_config.get(CONF_IP_ADDRESS, vol.UNDEFINED),
                        ),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT,
                        ),
                    ),
                    vol.Required(
                        USE_SECONDS,
                        default=(user_input or {}).get(
                            USE_SECONDS, current_config.get(USE_SECONDS, False)
                        ),
                    ): selector.BooleanSelector(
                        selector.BooleanSelectorConfig(),
                    ),
                },
            ),
            errors=_errors,
        )
