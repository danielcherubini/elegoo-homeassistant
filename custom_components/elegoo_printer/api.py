"""Sample API Client."""

from __future__ import annotations

from typing import TYPE_CHECKING

from custom_components.elegoo_printer.elegoo_sdcp.elegoo_printer import (
    ElegooPrinterClient,
    ElegooPrinterClientWebsocketConnectionError,
    ElegooPrinterClientWebsocketError,
)

if TYPE_CHECKING:
    from logging import Logger

    from custom_components.elegoo_printer.elegoo_sdcp.models.printer import PrinterData


class ElegooPrinterApiClient:
    """Sample API Client."""

    _ip_address: str
    _elegoo_printer: ElegooPrinterClient
    _logger: Logger

    def __init__(self, ip_address: str, logger: Logger) -> None:
        """Initialize."""
        self._ip_address = ip_address
        self._logger = logger

    @classmethod
    async def async_create(
        cls, ip_address: str, logger: Logger
    ) -> ElegooPrinterApiClient | None:
        """Sample API Client."""
        self = ElegooPrinterApiClient(ip_address, logger)

        elegoo_printer = ElegooPrinterClient(ip_address, logger)
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

    async def retry(self) -> bool:
        """Retry connecting to the printer and getting data."""
        return await self._elegoo_printer.connect_printer()
