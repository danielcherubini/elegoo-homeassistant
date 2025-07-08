"""Sample API Client."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.elegoo_printer.elegoo_sdcp.client import ElegooPrinterClient
from custom_components.elegoo_printer.elegoo_sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.elegoo_sdcp.models.printer import Printer
from custom_components.elegoo_printer.elegoo_sdcp.server import ElegooPrinterServer

from .const import CONF_PROXY_ENABLED

if TYPE_CHECKING:
    from logging import Logger

    from custom_components.elegoo_printer.elegoo_sdcp.models.printer import PrinterData


class ElegooPrinterApiClient:
    """Sample API Client."""

    _ip_address: str | None
    client: ElegooPrinterClient
    _logger: Logger
    printer: Printer
    printer_data: PrinterData

    def __init__(
        self,
        printer: Printer,
        config: MappingProxyType[str, Any],
        logger: Logger,
        hass: HomeAssistant,
    ) -> None:
        """
        Initialize the ElegooPrinterApiClient with a Printer object, configuration, and logger.

        Creates an internal ElegooPrinterClient for communication with the specified printer and sets up proxy server usage based on the configuration.
        """
        self._ip_address = printer.ip_address
        self._proxy_server_enabled: bool = config.get(CONF_PROXY_ENABLED, False)
        self._logger = logger
        self.printer = printer
        self.client = ElegooPrinterClient(
            printer.ip_address,
            config=config,
            logger=logger,
            session=async_get_clientsession(hass),
        )
        self.server: ElegooPrinterServer | None = None

    @classmethod
    async def async_create(
        cls,
        config: MappingProxyType[str, Any],
        logger: Logger,
        hass: HomeAssistant,
    ) -> ElegooPrinterApiClient:
        """
        Asynchronously creates and initializes an ElegooPrinterApiClient instance.

        This method parses the configuration to construct a Printer object, optionally
        sets up a proxy server, and attempts to connect to the printer. It returns an
        initialized client instance; the connection status should be checked separately.
        """
        printer = Printer.from_dict(dict(config))
        proxy_server_enabled: bool = config.get(CONF_PROXY_ENABLED, False)
        logger.debug("CONFIGURATION %s", config)
        self = ElegooPrinterApiClient(printer, config=config, logger=logger, hass=hass)

        elegoo_printer = ElegooPrinterClient(
            printer.ip_address,
            config=config,
            logger=logger,
            session=async_get_clientsession(hass),
        )

        if printer.proxy_enabled:
            logger.debug("Proxy server is enabled, attempting to create proxy server.")
            try:
                self.server = ElegooPrinterServer(printer, logger=logger)
                printer = self.server.get_printer()
                printer.proxy_enabled = proxy_server_enabled
            except Exception as e:
                logger.error("Failed to create proxy server: %s", e)
                # Continue with direct printer connection

        logger.debug(
            "Connecting to printer: %s at %s with proxy enabled %s and printer.proxy: %s",
            printer.name,
            printer.ip_address,
            proxy_server_enabled,
            printer.proxy_enabled,
        )

        try:
            connected = await elegoo_printer.connect_printer(
                printer, proxy_server_enabled
            )
            if connected:
                logger.info("Polling Started")
                self.client = elegoo_printer
            else:
                raise ConfigEntryNotReady(
                    f"Could not connect to printer at {printer.ip_address}, proxy_enabled: {proxy_server_enabled}"
                )
        except Exception as e:
            raise ConfigEntryNotReady from e

        return self

    async def elegoo_disconnect(self) -> None:
        """Disconnect from the printer by closing the WebSocket connection."""
        await self.client.disconnect()

    def elegoo_stop_proxy(self) -> None:
        """Stops the proxy server if it is running."""
        if self.server:
            self.server.stop()

    async def async_get_status(self) -> PrinterData:
        """
        Asynchronously retrieves and updates the current status information from the connected printer.

        Returns:
            PrinterData: The latest status data of the printer.
        """

        self.printer_data = await self.client.get_printer_status()
        return self.printer_data

    async def async_get_attributes(self) -> PrinterData:
        """
        Asynchronously retrieves and updates the printer's attribute data.

        Returns:
            PrinterData: The latest attribute information for the printer.
        """
        self.printer_data = await self.client.get_printer_attributes()
        return self.printer_data

    def is_thumbnail_available(self) -> bool:
        """
        Checks if the current print job's thumbnail image exists and returns a bool.

        Returns:
            bool: True if thumbnail image is available, or False otherwise.
        """
        return self.client.get_current_print_thumbnail() is not None

    async def async_get_current_thumbnail(self) -> str | None:
        """
        Asynchronously retrieves the current print job's thumbnail image as a string.

        Returns:
            str | None: The thumbnail image if available, or None if there is no active print job or thumbnail.
        """
        return await self.client.async_get_current_print_thumbnail()

    async def async_get_current_task(self) -> PrintHistoryDetail | None:
        """
        Asynchronously retrieves details of the current print task from the printer.

        Returns:
            A list of PrintHistoryDetail objects representing the current print task, or None if no task is active.
        """
        return await self.client.async_get_printer_current_task()

    async def async_get_print_history(
        self,
    ) -> dict[str, PrintHistoryDetail | None] | None:
        """
        Asynchronously retrieves the print history from the printer.

        Returns:
            A list of PrintHistoryDetail objects representing the print history, or None if no history is available.
        """
        return await self.client.async_get_printer_historical_tasks()

    async def reconnect(self) -> bool:
        """
        Asynchronously attempts to reconnect to the printer, using a proxy server if enabled.

        Returns:
            bool: True if reconnection is successful, False otherwise.
        """
        printer = self.client.printer
        if self._proxy_server_enabled:
            server = ElegooPrinterServer(printer, logger=self._logger)
            printer = server.get_printer()

        self._logger.debug(
            "Reconnecting to printer: %s proxy_enabled %s",
            printer.ip_address,
            self._proxy_server_enabled,
        )
        return await self.client.connect_printer(printer, self._proxy_server_enabled)
