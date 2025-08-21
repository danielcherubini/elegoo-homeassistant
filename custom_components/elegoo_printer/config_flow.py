"""Adds config flow for Elegoo."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_CAMERA_ENABLED, CONF_PROXY_ENABLED, DOMAIN, LOGGER
from .sdcp.exceptions import (
    ElegooConfigFlowConnectionError,
    ElegooConfigFlowGeneralError,
)
from .sdcp.models.enums import PrinterType
from .sdcp.models.printer import Printer
from .websocket.client import ElegooPrinterClient

if TYPE_CHECKING:
    from homeassistant.helpers.selector import SelectOptionDict

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
    hass: HomeAssistant, printer_object: Printer, user_input: dict[str, Any]
) -> Printer:
    """
    Attempt to connect to an Elegoo printer.

    Arguments:
        hass: The Home Assistant instance.
        printer_object: The printer object to test.
        user_input: The user input data.

    Returns:
        The validated Printer object if the connection is successful.

    Raises:
        ElegooConfigFlowGeneralError: If the printer's IP address is missing.
        ElegooConfigFlowConnectionError: If the connection to the printer fails.

    """
    if printer_object.ip_address is None:
        msg = "IP address is required to connect to the printer"
        raise ElegooConfigFlowGeneralError(msg)

    elegoo_printer = ElegooPrinterClient(
        printer_object.ip_address,
        config=MappingProxyType(user_input),
        logger=LOGGER,
        session=async_get_clientsession(hass),
    )

    printer_object.proxy_enabled = user_input.get(CONF_PROXY_ENABLED, False)
    LOGGER.debug(
        "Connecting to printer: %s at %s with proxy enabled: %s",
        printer_object.name,
        printer_object.ip_address,
        printer_object.proxy_enabled,
    )
    if await elegoo_printer.connect_printer(
        printer_object, proxy_enabled=printer_object.proxy_enabled
    ):
        return printer_object
    msg = f"Failed to connect to printer {printer_object.name} at {printer_object.ip_address}"  # noqa: E501
    raise ElegooConfigFlowConnectionError(msg)


async def _async_validate_input(  # noqa: PLR0912
    hass: HomeAssistant,
    user_input: dict[str, Any],
    discovered_printers: list[Printer] | None = None,
) -> dict:
    """
    Asynchronously validates user input for Elegoo printer configuration.

    Matches a discovered printer or locates one by IP address, and verifies
    connectivity.

    Arguments:
        hass: The Home Assistant instance.
        user_input: Configuration data that may include a printer ID or IP address.
        discovered_printers: A list of discovered printers.

    Returns:
        A dictionary containing the validated printer object under the "printer" key
        (or None if validation fails), and error details under the "errors" key.

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
            ip_address,
            config=MappingProxyType(user_input),
            logger=LOGGER,
            session=async_get_clientsession(hass),
        )
        printers = await hass.async_add_executor_job(
            elegoo_printer.discover_printer, ip_address
        )
        if printers:
            printer_object = printers[0]
        else:
            _errors["base"] = "no_printer_found"
    if printer_object:
        try:
            # Pass the full user_input to _async_test_connection for centauri_carbon and proxy_enabled  # noqa: E501
            validated_printer = await _async_test_connection(
                hass, printer_object, user_input
            )
            return {"printer": validated_printer, "errors": None}  # noqa: TRY300
        except ElegooConfigFlowConnectionError as exception:
            LOGGER.error("Config Flow: Connection error: %s", exception)
            _errors["base"] = "connection"
        except ElegooConfigFlowGeneralError as exception:
            LOGGER.error("Config Flow: No printer found: %s", exception)
            _errors["base"] = "validation_no_printer_found"
        except PlatformNotReady as exception:
            LOGGER.error(exception)
            _errors["base"] = "connection"
        except OSError as exception:
            LOGGER.exception(exception)
            _errors["base"] = "unknown"
    else:
        _errors["base"] = "no_printer_selected_or_ip_provided"

    return {"printer": None, "errors": _errors}


class ElegooFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Elegoo."""

    VERSION = 4
    MINOR_VERSION = 0

    def __init__(self) -> None:
        """Initialize the configuration flow handler."""
        self.discovered_printers: list[Printer] = []
        self.selected_printer: Printer | None = None

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> config_entries.ConfigFlowResult:
        """
        Initiate the configuration flow by attempting to discover available Elegoo printers.

        If printers are discovered, proceeds to the printer selection step; otherwise,
        prompts the user to manually enter a printer IP address.

        Arguments:
            user_input: The user input data.

        Returns:
            The result of the configuration flow step.

        """  # noqa: E501
        # Initiate discovery
        elegoo_printer_client = ElegooPrinterClient(
            "0.0.0.0",  # noqa: S104
            logger=LOGGER,
            session=async_get_clientsession(self.hass),
        )  # IP doesn't matter for discovery
        self.discovered_printers = await self.hass.async_add_executor_job(
            elegoo_printer_client.discover_printer
        )

        if self.discovered_printers:
            return await self.async_step_discover_printers()
        return await self.async_step_manual_ip()

    async def async_step_discover_printers(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Handle the step for selecting a discovered Elegoo printer.

        If user input is provided, processes the selection and either advances to
        manual IP entry or presents options for the selected printer. If no input is
        provided, displays a form listing discovered printers with IP addresses and an
        option to enter an IP manually.

        Arguments:
            user_input: The user input data.

        Returns:
            The result of the configuration flow step, advancing to the next step or
            displaying the selection form with any errors.

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
                if self.selected_printer.printer_type == PrinterType.RESIN:
                    return self.async_show_form(
                        step_id="resin_options",
                        data_schema=vol.Schema(
                            {
                                vol.Required(
                                    CONF_CAMERA_ENABLED,
                                    default=self.selected_printer.camera_enabled,
                                ): selector.BooleanSelector(
                                    selector.BooleanSelectorConfig()
                                ),
                            }
                        ),
                        errors=_errors,
                    )
                return self.async_show_form(
                    step_id="fdm_options",
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
            _errors["base"] = "invalid_printer_selection"

        # Filter out printers without an IP address
        valid_printers = [p for p in self.discovered_printers if p.ip_address]
        if not valid_printers:
            LOGGER.warning("No discovered printers with an IP address found.")
            return await self.async_step_manual_ip()

        printer_options: list[SelectOptionDict] = [
            {"value": p.id, "label": f"{p.name} ({p.ip_address})"}
            for p in valid_printers
            if p.id is not None
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
        Handle the configuration flow step for manually entering a printer's IP address.

        If user input is provided, validates the IP and attempts to connect to the
        printer. On successful validation, creates a new configuration entry for the
        printer. If validation fails or no input is provided, displays the manual IP
        entry form with any relevant errors.

        Arguments:
            user_input: The user input data.

        Returns:
            The result of the configuration flow step.

        """
        _errors = {}
        if user_input is not None:
            validation_result = await _async_validate_input(self.hass, user_input)
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

    async def async_step_resin_options(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the configuration of additional options for a discovered Elegoo printer."""  # noqa: E501
        _errors = {}
        if user_input is not None and self.selected_printer:
            printer_to_validate = Printer.from_dict(self.selected_printer.to_dict())
            printer_to_validate.camera_enabled = user_input[CONF_CAMERA_ENABLED]
            try:
                # Pass the full user_input to _async_test_connection for centauri_carbon and proxy_enabled  # noqa: E501
                validated_printer = await _async_test_connection(
                    self.hass, printer_to_validate, user_input
                )
                await self.async_set_unique_id(unique_id=validated_printer.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=validated_printer.name or "Elegoo Printer",
                    data=validated_printer.to_dict(),
                )
            except ElegooConfigFlowConnectionError as exception:
                LOGGER.error("Connection error: %s", exception)
                _errors["base"] = "connection"
            except ElegooConfigFlowGeneralError as exception:
                LOGGER.error("No printer found: %s", exception)
                _errors["base"] = "manual_options_no_printer_found"
            except PlatformNotReady as exception:
                LOGGER.error(exception)
                _errors["base"] = "connection"
            except OSError as exception:
                LOGGER.exception(exception)
                _errors["base"] = "unknown"

        return self.async_show_form(
            step_id="resin_options",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CAMERA_ENABLED,
                        default=self.selected_printer.camera_enabled,
                    ): selector.BooleanSelector(selector.BooleanSelectorConfig()),
                }
            ),
            errors=_errors,
        )

    async def async_step_fdm_options(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the configuration of additional options for a discovered Elegoo printer."""  # noqa: E501
        _errors = {}
        if user_input is not None and self.selected_printer:
            printer_to_validate = Printer.from_dict(self.selected_printer.to_dict())
            printer_to_validate.proxy_enabled = user_input[CONF_PROXY_ENABLED]
            try:
                # Pass the full user_input to _async_test_connection for centauri_carbon and proxy_enabled  # noqa: E501
                validated_printer = await _async_test_connection(
                    self.hass, printer_to_validate, user_input
                )
                await self.async_set_unique_id(unique_id=validated_printer.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=validated_printer.name or "Elegoo Printer",
                    data=validated_printer.to_dict(),
                )
            except ElegooConfigFlowConnectionError as exception:
                LOGGER.error("Connection error: %s", exception)
                _errors["base"] = "connection"
            except ElegooConfigFlowGeneralError as exception:
                LOGGER.error("No printer found: %s", exception)
                _errors["base"] = "manual_options_no_printer_found"
            except PlatformNotReady as exception:
                LOGGER.error(exception)
                _errors["base"] = "connection"
            except OSError as exception:
                LOGGER.exception(exception)
                _errors["base"] = "unknown"

        return self.async_show_form(
            step_id="fdm_options",
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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ElegooOptionsFlowHandler:
        """
        Return an options flow handler for managing configuration options.

        Arguments:
            config_entry: The configuration entry for which to create the options flow.

        Returns:
            The handler managing the options flow for the given configuration entry.

        """
        return ElegooOptionsFlowHandler(config_entry)

    @classmethod
    @callback
    def async_supports_options_flow(
        cls,
        config_entry: config_entries.ConfigEntry,  # noqa: ARG003
    ) -> bool:
        """Return options flow support for this handler."""
        return True


class ElegooOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow handler for Elegoo Printer."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """
        Initialize the options flow handler.

        Arguments:
            config_entry: The configuration entry for which the options are being managed.

        """  # noqa: E501
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """
        Display and manage the options form for an existing Elegoo printer configuration.

        Allows users to update printer settings such as IP address and proxy usage.
        Validates the updated configuration by testing connectivity to the printer
        before saving changes. If validation fails, displays relevant error messages
        on the form.

        Arguments:
            user_input: The user input data.

        Returns:
            The result of the configuration flow step.

        """  # noqa: E501
        _errors = {}
        # Create a dictionary of the current settings by merging data and options.
        # This ensures the form is always populated with the current effective values.
        current_settings = {
            **(self.config_entry.data or {}),
            **(self.config_entry.options or {}),
        }
        printer = Printer.from_dict(current_settings)
        LOGGER.debug("data: %s", self.config_entry.data)
        LOGGER.debug("options: %s", self.config_entry.options)
        if user_input is not None:
            try:
                tested_printer = await _async_test_connection(
                    self.hass, printer, user_input
                )
                tested_printer.proxy_enabled = user_input[CONF_PROXY_ENABLED]
                LOGGER.debug("Tested printer: %s", tested_printer.to_dict())
                return self.async_create_entry(
                    title=tested_printer.name,
                    data=tested_printer.to_dict(),
                )
            except ElegooConfigFlowConnectionError as exception:
                LOGGER.error("Connection error: %s", exception)
                _errors["base"] = "connection"
            except ElegooConfigFlowGeneralError as exception:
                LOGGER.error("No printer found: %s", exception)
                _errors["base"] = "init_no_printer_found"
            except PlatformNotReady as exception:
                LOGGER.error(exception)
                _errors["base"] = "connection"
            except OSError as exception:
                LOGGER.exception(exception)
                _errors["base"] = "unknown"

        data_schema = {
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
        }

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(data_schema),
                suggested_values=current_settings,
            ),
            errors=_errors,
        )
