"""Adds config flow for Elegoo."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Dict

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import selector

from custom_components.elegoo_printer.elegoo_sdcp.client import (
    ElegooPrinterClient,
    ElegooPrinterClientWebsocketConnectionError,
    ElegooPrinterClientWebsocketError,
)

from .const import CONF_CENTAURI_CARBON, CONF_PROXY_ENABLED, DOMAIN, LOGGER

if TYPE_CHECKING:
    from .elegoo_sdcp.models.printer import Printer

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_IP_ADDRESS,
        ): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT,
            ),
        ),
        vol.Required(
            CONF_CENTAURI_CARBON,
        ): selector.BooleanSelector(
            selector.BooleanSelectorConfig(),
        ),
        vol.Required(
            CONF_PROXY_ENABLED,
        ): selector.BooleanSelector(
            selector.BooleanSelectorConfig(),
        ),
    },
)


def _test_credentials(user_input: Dict[str, Any]) -> Printer:
    """
    Discover an Elegoo printer using the provided user input.
    
    Attempts to connect to the printer at the specified IP address using the configuration in `user_input`. Returns the discovered Printer object if successful.
    
    Raises:
        ElegooPrinterClientGeneralError: If no printer is found at the specified IP address.
    Returns:
        Printer: The discovered printer object.
    """
    ip_address = user_input[CONF_IP_ADDRESS]

    elegoo_printer = ElegooPrinterClient(
        ip_address, config=MappingProxyType(user_input), logger=LOGGER
    )
    printer = elegoo_printer.discover_printer(ip_address)
    if printer:
        return printer
    raise ElegooPrinterClientGeneralError(
        f"No printer found at IP address {ip_address}"
    )


async def _async_validate_input(user_input: dict[str, Any]) -> dict:
    """
    Asynchronously validates user input for Elegoo printer configuration and returns the discovered printer or error details.
    
    Parameters:
        user_input (dict[str, Any]): Dictionary containing configuration parameters for the printer.
    
    Returns:
        dict: A dictionary with keys "printer" (the discovered Printer object or None) and "errors" (None or a dictionary of error codes if validation fails).
    """
    _errors = {}
    try:
        printer = _test_credentials(user_input)
        return {"printer": printer, "errors": None}
    except ElegooPrinterClientGeneralError as exception:  # New specific catch
        LOGGER.error("No printer found: %s", exception)
        _errors["base"] = "no_printer_found"  # Or "cannot_connect"
    except ElegooPrinterClientWebsocketConnectionError as exception:
        LOGGER.error(exception)
        _errors["base"] = "connection"
    except ElegooPrinterClientWebsocketError as exception:
        LOGGER.exception(exception)
        _errors["base"] = "websocket"
    except (OSError, Exception) as exception:
        LOGGER.exception(exception)
        _errors["base"] = "unknown"
    return {"printer": None, "errors": _errors}


class ElegooPrinterClientGeneralError(Exception):
    """Exception For Elegoo Printer."""


class ElegooFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Elegoo."""

    VERSION = 1
    MINOR_VERSION = 3

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Handle the initial user step for configuring an Elegoo printer integration.
        
        Prompts the user for printer connection details, validates the input by attempting to discover the printer, and creates a new configuration entry if successful. If validation fails, displays the form again with appropriate error messages.
        
        Returns:
            A ConfigFlowResult indicating the outcome of the step, such as showing the form, creating an entry, or aborting if already configured.
        """
        _errors = {}
        if user_input is not None:
            validation_result = await _async_validate_input(user_input)
            _errors = validation_result["errors"]
            printer_object: Printer = validation_result["printer"]

            printer_object.ip_address = user_input[CONF_IP_ADDRESS]
            printer_object.centauri_carbon = user_input[CONF_CENTAURI_CARBON]
            printer_object.proxy_enabled = user_input[CONF_PROXY_ENABLED]

            if not _errors:
                await self.async_set_unique_id(unique_id=printer_object.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=printer_object.name,
                    description=printer_object.name,
                    data=printer_object.to_dict(),
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, user_input),
            errors=_errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ElegooOptionsFlowHandler:
        """Get the options flow for this handler."""
        return ElegooOptionsFlowHandler(config_entry)

    @classmethod
    @callback
    def async_supports_options_flow(
        cls, config_entry: config_entries.ConfigEntry
    ) -> bool:
        """Return options flow support for this handler."""
        return True


class ElegooOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow handler for Elegoo Printer"""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """
        Handle the initial step of the options flow for updating Elegoo printer configuration.
        
        Presents a form to update printer settings, validates the input by attempting to discover the printer, and creates an options entry with updated details if validation succeeds. Displays error messages if validation fails.
        
        Returns:
            ConfigFlowResult: The result of the options flow step, either showing the form or creating an entry.
        """
        _errors = {}
        if user_input is not None:
            validation_result = await _async_validate_input(user_input)
            _errors = validation_result["errors"]
            printer_object: Printer = validation_result["printer"]

            printer_object.ip_address = user_input[CONF_IP_ADDRESS]
            printer_object.centauri_carbon = user_input[CONF_CENTAURI_CARBON]
            printer_object.proxy_enabled = user_input[CONF_PROXY_ENABLED]

            if not _errors:
                return self.async_create_entry(
                    title=printer_object.name,
                    description=printer_object.name,
                    data=printer_object.to_dict(),
                )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.data
            ),
            errors=_errors,
        )
