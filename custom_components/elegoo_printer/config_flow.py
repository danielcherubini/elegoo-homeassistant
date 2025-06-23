"""Adds config flow for Elegoo."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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

from .const import CONF_CENTAURI_CARBON, DOMAIN, LOGGER

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
    },
)


def _test_credentials(ip_address: str, centauri_carbon: bool) -> Printer:
    """
    Attempts to discover an Elegoo printer at the specified IP address.

    Args:
        ip_address: The IP address of the printer to discover.
        centauri_carbon: Whether to use seconds instead of milliseconds for time values.

    Returns:
        The discovered Printer object.

    Raises:
        ElegooPrinterClientGeneralError: If no printer is found at the given IP address.
    """
    elegoo_printer = ElegooPrinterClient(ip_address, centauri_carbon, LOGGER)
    printer = elegoo_printer.discover_printer()
    if printer:
        return printer
    raise ElegooPrinterClientGeneralError(
        f"No printer found at IP address {ip_address}"
    )


async def _async_validate_input(user_input: dict[str, Any]) -> dict:
    _errors = {}
    try:
        printer = _test_credentials(
            ip_address=user_input[CONF_IP_ADDRESS],
            centauri_carbon=user_input[CONF_CENTAURI_CARBON],
        )
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
    MINOR_VERSION = 2

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Handles the initial step of the Elegoo printer configuration flow.

        Prompts the user for printer connection details, validates the provided IP address by attempting to discover the printer, and creates a new configuration entry if successful. Displays relevant error messages if connection or validation fails.
        """
        _errors = {}
        if user_input is not None:
            validation_result = await _async_validate_input(user_input)
            _errors = validation_result["errors"]
            printer_object: Printer = validation_result["printer"]

            printer_object.ip_address = user_input[CONF_IP_ADDRESS]
            printer_object.centauri_carbon = user_input[CONF_CENTAURI_CARBON]

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
        Handles the options flow for updating Elegoo printer configuration.

        Presents a form for the user to update printer settings. Validates the provided IP address by attempting to discover the printer. On successful validation, creates a new options entry with the printer's details; otherwise, displays relevant error messages in the form.
        """
        _errors = {}
        if user_input is not None:
            validation_result = await _async_validate_input(user_input)
            _errors = validation_result["errors"]
            printer_object: Printer = validation_result["printer"]

            printer_object.ip_address = user_input[CONF_IP_ADDRESS]
            printer_object.centauri_carbon = user_input[CONF_CENTAURI_CARBON]

            if not _errors:
                return self.async_create_entry(
                    title=printer_object.name,
                    description=printer_object.name,
                    data=printer_object.to_dict(),
                )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
            errors=_errors,
        )
