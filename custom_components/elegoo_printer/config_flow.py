"""Adds config flow for Elegoo."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Dict

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import callback
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers import selector

from custom_components.elegoo_printer.elegoo_sdcp.client import ElegooPrinterClient
from .elegoo_sdcp.models.printer import Printer

from .const import CONF_PROXY_ENABLED, DOMAIN, LOGGER

if TYPE_CHECKING:
    pass

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
            CONF_PROXY_ENABLED,
        ): selector.BooleanSelector(
            selector.BooleanSelectorConfig(),
        ),
    },
)


async def _async_test_connection(
    printer_object: Printer, user_input: Dict[str, Any]
) -> Printer:
    """
    Attempts to connect to an Elegoo printer using the provided Printer object.
    """
    elegoo_printer = ElegooPrinterClient(
        printer_object.ip_address, config=MappingProxyType(user_input), logger=LOGGER
    )
    if await elegoo_printer.connect_printer(printer_object):
        return printer_object
    raise ElegooPrinterClientGeneralError(
        f"Failed to connect to printer {printer_object.name} at {printer_object.ip_address}"
    )


async def _async_validate_input(
    user_input: dict[str, Any], discovered_printers: list[Printer] | None = None
) -> dict:
    """
    Validate user input for Elegoo printer configuration and return the discovered printer or error details.
    """
    _errors = {}
    printer_object: Printer | None = None

    if "printer_id" in user_input and discovered_printers:
        # User selected a discovered printer
        selected_printer_id = user_input["printer_id"]
        for p in discovered_printers:
            if p.id == selected_printer_id:
                printer_object = p
                break
        if not printer_object:
            _errors["base"] = "invalid_printer_selection"
    elif CONF_IP_ADDRESS in user_input:
        # Manual IP entry
        ip_address = user_input[CONF_IP_ADDRESS]
        elegoo_printer = ElegooPrinterClient(
            ip_address, config=MappingProxyType(user_input), logger=LOGGER
        )
        printers = elegoo_printer.discover_printer(ip_address)
        if printers:
            printer_object = printers[0]
        else:
            _errors["base"] = "no_printer_found"

    if printer_object:
        try:
            # Pass the full user_input to _async_test_connection for centauri_carbon and proxy_enabled
            validated_printer = await _async_test_connection(printer_object, user_input)
            return {"printer": validated_printer, "errors": None}
        except ElegooPrinterClientGeneralError as exception:
            LOGGER.error("No printer found: %s", exception)
            _errors["base"] = "no_printer_found"
        except PlatformNotReady as exception:
            LOGGER.error(exception)
            _errors["base"] = "connection"
        except (OSError, Exception) as exception:
            LOGGER.exception(exception)
            _errors["base"] = "unknown"
    else:
        _errors["base"] = "no_printer_selected_or_ip_provided"

    return {"printer": None, "errors": _errors}


class ElegooPrinterClientGeneralError(Exception):
    """Exception For Elegoo Printer."""


class ElegooFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Elegoo."""

    VERSION = 1
    MINOR_VERSION = 3

    def __init__(self) -> None:
        """Initialize the config flow."""
        self.discovered_printers: list[Printer] = []
        self.selected_printer: Printer | None = None

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Handle the initial step.
        """
        # Initiate discovery
        elegoo_printer_client = ElegooPrinterClient(
            "0.0.0.0", logger=LOGGER
        )  # IP doesn't matter for discovery
        self.discovered_printers = await self.hass.async_add_executor_job(
            elegoo_printer_client.discover_printer
        )

        if self.discovered_printers:
            return await self.async_step_discover_printers()
        else:
            return await self.async_step_manual_ip()

    async def async_step_discover_printers(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Handle the discovery step.
        """
        _errors = {}

        if user_input is not None:
            if user_input["selection"] == "manual_ip":
                return await self.async_step_manual_ip()

            selected_printer_id = user_input["selection"]
            self.selected_printer = next(
                (p for p in self.discovered_printers if p.id == selected_printer_id),
                None,
            )

            if self.selected_printer:
                # Proceed to the next step to get centauri_carbon and proxy_enabled
                return self.async_show_form(
                    step_id="manual_options",
                    data_schema=vol.Schema(
                        {
                            vol.Required(
                                CONF_PROXY_ENABLED,
                                default=self.selected_printer.proxy_enabled,
                            ): selector.BooleanSelector(
                                selector.BooleanSelectorConfig(),
                            ),
                        }
                    ),
                    errors=_errors,
                )
            else:
                _errors["base"] = "invalid_printer_selection"

        printer_options = [
            {"value": p.id, "label": f"{p.name} ({p.ip_address})"}
            for p in self.discovered_printers
        ]
        printer_options.append({"value": "manual_ip", "label": "Enter IP manually"})

        return self.async_show_form(
            step_id="discover_printers",
            data_schema=vol.Schema(
                {
                    vol.Required("selection"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=printer_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=_errors,
        )

    async def async_step_manual_ip(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Handle the manual IP entry step.
        """
        _errors = {}
        if user_input is not None:
            validation_result = await _async_validate_input(user_input)
            _errors = validation_result["errors"]
            printer_object: Printer = validation_result["printer"]

            if not _errors:
                await self.async_set_unique_id(unique_id=printer_object.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=printer_object.name or "Elegoo Printer",
                    data=printer_object.to_dict(),
                )

        return self.async_show_form(
            step_id="manual_ip",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, user_input),
            errors=_errors,
        )

    async def async_step_manual_options(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Handle the manual options for a discovered printer.
        """
        _errors = {}
        if user_input is not None and self.selected_printer:
            # Combine selected printer info with manual options
            combined_input = {
                CONF_IP_ADDRESS: self.selected_printer.ip_address,
                CONF_PROXY_ENABLED: user_input[CONF_PROXY_ENABLED],
            }
            validation_result = await _async_validate_input(
                combined_input, discovered_printers=self.discovered_printers
            )
            _errors = validation_result["errors"]
            printer_object: Printer = validation_result["printer"]

            if not _errors:
                await self.async_set_unique_id(unique_id=printer_object.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=printer_object.name or "Elegoo Printer",
                    data=printer_object.to_dict(),
                )

        default_proxy_enabled = (
            self.selected_printer.proxy_enabled if self.selected_printer else False
        )

        return self.async_show_form(
            step_id="manual_options",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PROXY_ENABLED,
                        default=default_proxy_enabled,
                    ): selector.BooleanSelector(
                        selector.BooleanSelectorConfig(),
                    ),
                }
            ),
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
    """Options flow handler for Elegoo Printer."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """
        Initialize the options flow handler.

        Parameters:
            config_entry (ConfigEntry): The configuration entry for which the options are being managed.
        """
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """
        Manage the options for the Elegoo printer.

        Handles displaying and saving updated configuration options.
        """
        _errors = {}
        if user_input is not None:
            # When validating, it's good practice to use the full current config
            # combined with the new user input.
            combined_input = {
                **(self.config_entry.data or {}),
                **(self.config_entry.options or {}),
                **user_input,
            }

            validation_result = await _async_validate_input(combined_input)
            _errors = validation_result.get("errors")

            if not _errors:
                # Save the user's input from the form to the .options dictionary.
                # Any existing options not in the form will be preserved if you
                # merge them first.
                updated_options = {**self.config_entry.options, **user_input}
                return self.async_create_entry(title="", data=updated_options)

        # Create a dictionary of the current settings by merging data and options.
        # This ensures the form is always populated with the current effective values.
        current_settings = {
            **(self.config_entry.data or {}),
            **(self.config_entry.options or {}),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA,
                suggested_values=current_settings,
            ),
            errors=_errors,
        )
