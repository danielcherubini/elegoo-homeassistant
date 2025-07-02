"""Sample API Client."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_IP_ADDRESS

from custom_components.elegoo_printer.elegoo_sdcp.client import ElegooPrinterClient
from custom_components.elegoo_printer.elegoo_sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.elegoo_sdcp.models.printer import Printer
from custom_components.elegoo_printer.elegoo_sdcp.server import ElegooPrinterServer

from .const import CONF_CENTAURI_CARBON, CONF_PROXY_ENABLED

if TYPE_CHECKING:
    from logging import Logger

    from custom_components.elegoo_printer.elegoo_sdcp.models.printer import PrinterData


class ElegooPrinterApiClient:
    """Sample API Client."""

    _ip_address: str
    _centauri_carbon: bool
    _elegoo_printer: ElegooPrinterClient
    _logger: Logger
    _printer: Printer
    printer_data: PrinterData

    def __init__(
        self, ip_address: str, config: MappingProxyType[str, Any], logger: Logger
    ) -> None:
        """
        Initialize the ElegooPrinterApiClient with the printer's IP address, configuration, and logger.

        Sets internal flags for printer model type and proxy server usage based on the provided configuration.
        """
        self._ip_address = ip_address
        self._centauri_carbon = config.get(CONF_CENTAURI_CARBON, False)
        self._proxy_server_enabled: bool = config.get(CONF_PROXY_ENABLED, False)
        self._logger = logger

    @classmethod
    async def async_create(
        cls, config: MappingProxyType[str, Any], logger: Logger
    ) -> ElegooPrinterApiClient | None:
        """
        Asynchronously creates and initializes an ElegooPrinterApiClient instance using the provided configuration.

        Attempts to discover and connect to the printer at the specified IP address. Returns the initialized API client if successful, or None if the printer cannot be found or connected.

        Parameters:
            config (MappingProxyType[str, Any]): Configuration containing the printer's IP address and optional model flag.

        Returns:
            ElegooPrinterApiClient | None: The initialized API client instance, or None if initialization fails.
        """
        ip_address = config.get(CONF_IP_ADDRESS)
        proxy_server_enabled: bool = config.get(CONF_PROXY_ENABLED, False)

        if ip_address is None:
            return None

        self = ElegooPrinterApiClient(ip_address, config=config, logger=logger)

        elegoo_printer = ElegooPrinterClient(ip_address, config=config, logger=logger)
        printer = elegoo_printer.discover_printer(ip_address)

        if printer is None:
            return None

        if proxy_server_enabled:
            try:
                server = ElegooPrinterServer(printer, logger=logger)
                printer = server.get_printer()
            except Exception as e:
                logger.error("Failed to create proxy server: %s", e)
                # Continue with direct printer connection

        connected = await elegoo_printer.connect_printer(printer)
        if connected:
            logger.info("Polling Started")
            self._elegoo_printer = elegoo_printer
        return self

    async def async_get_status(self) -> PrinterData:
        """
        Asynchronously retrieves and updates the current status information from the connected printer.

        Returns:
            PrinterData: The latest status data of the printer.
        """

        self.printer_data = self._elegoo_printer.get_printer_status()
        return self.printer_data

    async def async_get_attributes(self) -> PrinterData:
        """
        Asynchronously retrieves and updates the printer's attribute data.

        Returns:
            PrinterData: The latest attribute information for the printer.
        """
        self.printer_data = self._elegoo_printer.get_printer_attributes()
        return self.printer_data

    async def async_get_current_thumbnail(self) -> str | None:
        """
        Asynchronously retrieves the current print job's thumbnail image as a string.

        Returns:
            str | None: The thumbnail image if available, or None if there is no active print job or thumbnail.
        """
        return await self._elegoo_printer.async_get_current_print_thumbnail()

    async def async_get_current_task(self) -> list[PrintHistoryDetail] | None:
        """
        Asynchronously retrieves details of the current print task from the printer.

        Returns:
            A list of PrintHistoryDetail objects representing the current print task, or None if no task is active.
        """
        return await self._elegoo_printer.async_get_printer_current_task()

    async def async_get_print_history(self) -> list[PrintHistoryDetail] | None:
        """
        Asynchronously retrieves the print history from the printer.

        Returns:
            A list of PrintHistoryDetail objects representing the print history, or None if no history is available.
        """
        return self._elegoo_printer.get_printer_historical_tasks()

    async def reconnect(self) -> bool:
        """
        Asynchronously attempts to reconnect to the printer, using a proxy server if enabled.

        Returns:
            bool: True if reconnection is successful, False otherwise.
        """
        printer = self._elegoo_printer.printer
        if self._proxy_server_enabled:
            server = ElegooPrinterServer(printer, logger=self._logger)
            printer = server.get_printer()

        return await self._elegoo_printer.connect_printer(printer)
