"""Sample API Client."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from custom_components.elegoo_printer.elegoo_sdcp.elegoo_printer import (
    ElegooPrinterClient,
    ElegooPrinterClientWebsocketConnectionError,
    ElegooPrinterClientWebsocketError,
)
from custom_components.elegoo_printer.elegoo_sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)

if TYPE_CHECKING:
    from logging import Logger

    from custom_components.elegoo_printer.elegoo_sdcp.models.printer import PrinterData


class ElegooPrinterApiClient:
    """Sample API Client."""

    _ip_address: str
    _centauri_carbon: bool
    _elegoo_printer: ElegooPrinterClient
    _logger: Logger

    def __init__(self, ip_address: str, centauri_carbon: bool, logger: Logger) -> None:
        """Initialize."""
        self._ip_address = ip_address
        self._centauri_carbon = centauri_carbon
        self._logger = logger

    @classmethod
    async def async_create(
        cls, config: MappingProxyType[str, Any], logger: Logger
    ) -> ElegooPrinterApiClient | None:
        """Sample API Client."""
        ip_address = config.get("ip_address")
        centauri_carbon: bool = config.get("centauri_carbon", False)
        if ip_address is None:
            return None

        self = ElegooPrinterApiClient(ip_address, centauri_carbon, logger)

        elegoo_printer = ElegooPrinterClient(ip_address, centauri_carbon, logger)
        printer = elegoo_printer.discover_printer()
        if printer is None:
            return None
        connected = await elegoo_printer.connect_printer()
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
        return await self._elegoo_printer.connect_printer()
