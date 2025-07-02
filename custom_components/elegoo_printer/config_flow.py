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

from .const import CONF_PROXY_ENABLED, DOMAIN, LOGGER
from .elegoo_sdcp.models.printer import Printer

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
    Asynchronously attempts to connect to an Elegoo printer using the given Printer object and user input configuration.

    Parameters:
        printer_object (Printer): The printer to connect to.
        user_input (dict): Configuration options for the connection.

    Returns:
        Printer: The validated Printer object if the connection is successful.

    Raises:
        ElegooPrinterClientGeneralError: If the connection to the printer fails.
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
    Asynchronously validates user input for Elegoo printer configuration, attempting to match a discovered printer or locate one by IP address, and verifies connectivity.

    Parameters:
        user_input (dict): User-provided configuration data, which may include a printer ID or IP address.
        discovered_printers (list[Printer] | None): Optional list of previously discovered Printer objects.

    Returns:
        dict: A dictionary containing the validated printer object under the "printer" key (or None if validation fails), and any error details under the "errors" key.
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
        """
        Initialize the configuration flow handler, setting up storage for discovered and selected printers.
        """
        self.discovered_printers: list[Printer] = []
        self.selected_printer: Printer | None = None

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Initiates the configuration flow by attempting to discover available Elegoo printers.

        If printers are discovered, proceeds to the printer selection step; otherwise, prompts the user to manually enter a printer IP address.
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
        Handle the configuration flow step for selecting a discovered Elegoo printer or opting for manual IP entry.

        If user input is provided, processes the selection:
        - If "manual_ip" is chosen, advances to manual IP entry.
        - If a discovered printer is selected, proceeds to options configuration for that printer.
        - If the selection is invalid, displays an error.

        If no input is provided, displays a form listing discovered printers and an option to enter an IP address manually.

        Returns:
            The result of the configuration flow step, either advancing to the next step or displaying the selection form with any errors.
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
        Handles the configuration flow step for manually entering a printer's IP address.

        If user input is provided, validates the IP and attempts to connect to the printer. On successful validation, creates a new configuration entry for the printer. If validation fails or no input is provided, displays the manual IP entry form with any relevant errors.
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
        Handle the configuration of additional options, such as proxy enabling, for a discovered Elegoo printer.

        If user input is provided and a printer is selected, validates the combined configuration and creates a config entry upon success. Otherwise, displays a form to set the proxy enabled option, defaulting to the current printer setting.
        """
        _errors = {}
        if user_input is not None and self.selected_printer:
            self.selected_printer.proxy_enabled = user_input[CONF_PROXY_ENABLED]
            try:
                # Pass the full user_input to _async_test_connection for centauri_carbon and proxy_enabled
                await _async_test_connection(self.selected_printer, user_input)
                await self.async_set_unique_id(unique_id=self.selected_printer.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self.selected_printer.name or "Elegoo Printer",
                    data=self.selected_printer.to_dict(),
                )
            except ElegooPrinterClientGeneralError as exception:
                LOGGER.error("No printer found: %s", exception)
                _errors["base"] = "no_printer_found"
            except PlatformNotReady as exception:
                LOGGER.error(exception)
                _errors["base"] = "connection"
            except (OSError, Exception) as exception:
                LOGGER.exception(exception)
                _errors["base"] = "unknown"

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
        """
        Return an options flow handler for managing configuration options of an Elegoo printer integration.

        Parameters:
            config_entry (ConfigEntry): The configuration entry for which to create the options flow.

        Returns:
            ElegooOptionsFlowHandler: The handler managing the options flow for the given configuration entry.
        """
        return ElegooOptionsFlowHandler(config_entry)

    @classmethod
    @callback
    def async_supports_options_flow(
        cls, config_entry: config_entries.ConfigEntry
    ) -> bool:
        """Return options flow support for this handler."""
        return False


class ElegooOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow handler for Elegoo Printer."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """
        Initialize the options flow handler.

        Parameters:
            config_entry (ConfigEntry): The configuration entry for which the options are being managed.
        """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """
        Manage the options for the Elegoo printer.

        Handles displaying and saving updated configuration options.
        """
        _errors = {}
        # Create a dictionary of the current settings by merging data and options.
        # This ensures the form is always populated with the current effective values.
        current_settings = {
            **(self.config_entry.options or {}),
        }
        LOGGER.debug("data: %s", self.config_entry.data)
        LOGGER.debug("options: %s", self.config_entry.options)
        if user_input is not None:
            printer = Printer.from_dict(current_settings)
            printer.proxy_enabled = user_input[CONF_PROXY_ENABLED]
            LOGGER.debug(printer.to_dict())
            try:
                tested_printer = await _async_test_connection(printer, user_input)
                LOGGER.debug("Tested printer: %s", tested_printer.to_dict())
                return self.async_create_entry(
                    title=tested_printer.name,
                    data=tested_printer.to_dict(),
                )
            except ElegooPrinterClientGeneralError as exception:
                LOGGER.error("No printer found: %s", exception)
                _errors["base"] = "no_printer_found"
            except PlatformNotReady as exception:
                LOGGER.error(exception)
                _errors["base"] = "connection"
            except (OSError, Exception) as exception:
                LOGGER.exception(exception)
                _errors["base"] = "unknown"

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA,
                suggested_values=current_settings,
            ),
            errors=_errors,
        )
