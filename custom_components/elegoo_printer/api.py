"""Sample API Client."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_IP_ADDRESS

from custom_components.elegoo_printer.elegoo_sdcp.client import (
    ElegooPrinterClient,
    ElegooPrinterClientWebsocketConnectionError,
    ElegooPrinterClientWebsocketError,
)
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

    def __init__(
        self, ip_address: str, config: MappingProxyType[str, Any], logger: Logger
    ) -> None:
        """Initialize."""
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
        cls._proxy_server_enabled: bool = config.get(CONF_PROXY_ENABLED, False)

        if ip_address is None:
            return None

        self = ElegooPrinterApiClient(ip_address, config=config, logger=logger)

        elegoo_printer = ElegooPrinterClient(ip_address, config=config, logger=logger)
        printer = elegoo_printer.discover_printer(ip_address)

        if printer is None:
            return None

        if cls._proxy_server_enabled:
            server = ElegooPrinterServer(printer, logger=logger)
            printer = server.get_printer()

        connected = await elegoo_printer.connect_printer(printer)
        if connected:
            logger.info("Polling Started")
            self._elegoo_printer = elegoo_printer
        return self

    async def async_get_status(self) -> PrinterData:
        """Get data from the API."""
        try:
            return self._elegoo_printer.get_printer_status()
        except ElegooPrinterClientWebsocketConnectionError:
            # Retry
            connected = await self.retry()
            if connected is False:
                raise ElegooPrinterClientWebsocketError from Exception(
                    "Failed to recononect"
                )
            return self._elegoo_printer.get_printer_status()
        except ElegooPrinterClientWebsocketError:
            raise
        except OSError:
            raise

    async def async_get_attributes(self) -> PrinterData:
        """Get data from the API."""
        return self._elegoo_printer.get_printer_attributes()

    async def async_get_current_thumbnail(self) -> str | None:
        """
        Asynchronously retrieves the current print job's thumbnail image from the printer.

        Returns:
            The thumbnail image as a string, or None if unavailable.
        """
        return await self._elegoo_printer.get_current_print_thumbnail()

    async def async_get_current_task(self) -> list[PrintHistoryDetail] | None:
        """
        Asynchronously retrieve details of the current print task from the printer.

        Returns:
            A list of PrintHistoryDetail objects representing the current print task, or None if no task is active.
        """
        return await self._elegoo_printer.get_printer_current_task()

    async def retry(self) -> bool:
        """
        Attempt to reconnect to the printer asynchronously.

        Returns:
            bool: True if the reconnection is successful, False otherwise.
        """
        printer = self._elegoo_printer.printer
        if self._proxy_server_enabled:
            server = ElegooPrinterServer(printer, logger=self._logger)
            printer = server.get_printer()

        return await self._elegoo_printer.connect_printer(printer)
