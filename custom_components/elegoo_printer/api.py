"""Sample API Client."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

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

    _ip_address: str
    _elegoo_printer: ElegooPrinterClient
    _logger: Logger
    _printer: Printer
    printer_data: PrinterData

    def __init__(
        self,
        printer: Printer,
        config: MappingProxyType[str, Any],
        logger: Logger,
    ) -> None:
        """
        Initialize the ElegooPrinterApiClient with a Printer object, configuration, and logger.

        Creates an internal ElegooPrinterClient for communication with the specified printer and sets up proxy server usage based on the configuration.
        """
        self._ip_address = printer.ip_address
        self._proxy_server_enabled: bool = config.get(CONF_PROXY_ENABLED, False)
        self._logger = logger
        self._printer = printer
        self._elegoo_printer = ElegooPrinterClient(
            printer.ip_address, config=config, logger=logger
        )
        self.server: ElegooPrinterServer | None = None

    @classmethod
    async def async_create(
        cls,
        config: MappingProxyType[str, Any],
        logger: Logger,
    ) -> ElegooPrinterApiClient:
        """
        Asynchronously creates and initializes an ElegooPrinterApiClient instance from the provided configuration.

        This method parses the configuration to construct a Printer object, optionally sets up a proxy server, and attempts to connect to the printer. Returns the initialized API client instance regardless of connection success.
        """
        printer = Printer.from_dict(dict(config))
        proxy_server_enabled: bool = config.get(CONF_PROXY_ENABLED, False)
        logger.debug("CONFIGURATION %s", config)
        self = ElegooPrinterApiClient(printer, config=config, logger=logger)

        elegoo_printer = ElegooPrinterClient(
            printer.ip_address, config=config, logger=logger
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

        connected = await elegoo_printer.connect_printer(printer, proxy_server_enabled)
        if connected:
            logger.info("Polling Started")
            self._elegoo_printer = elegoo_printer
        return self

    def stop_proxy(self):
        """Stops the proxy server if it is running."""
        if self.server:
            self.server.stop()

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
        return await self._elegoo_printer.get_current_print_thumbnail()

    async def async_get_current_task(self) -> list[PrintHistoryDetail] | None:
        """
        Asynchronously retrieves details of the current print task from the printer.

        Returns:
            A list of PrintHistoryDetail objects representing the current print task, or None if no task is active.
        """
        return await self._elegoo_printer.get_printer_current_task()

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

        self._logger.debug(
            "Reconnecting to printer: %s proxy_enabled %s",
            printer.ip_address,
            self._proxy_server_enabled,
        )
        return await self._elegoo_printer.connect_printer(
            printer, self._proxy_server_enabled
        )
