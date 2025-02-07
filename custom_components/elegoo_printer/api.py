"""Sample API Client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .elegoo.elegoo_printer import ElegooPrinterClient
    from .elegoo.models.printer import PrinterData


class ElegooPrinterApiClientError(Exception):
    """Exception to indicate a general API error."""


class ElegooPrinterApiClientCommunicationError(
    ElegooPrinterApiClientError,
):
    """Exception to indicate a communication error."""


class ElegooPrinterApiClientAuthenticationError(
    ElegooPrinterApiClientError,
):
    """Exception to indicate an authentication error."""


class ElegooPrinterApiClient:
    """Sample API Client."""

    def __init__(
        self,
        ip_address: str,
        elegoo_printer: ElegooPrinterClient,
    ) -> None:
        """Sample API Client."""
        self._ip_address: str = ip_address
        self._elegoo_printer: ElegooPrinterClient = elegoo_printer

    async def async_get_data(self) -> PrinterData:
        """Get data from the API."""
        self._elegoo_printer.get_printer_attributes()
        return self._elegoo_printer.get_printer_status()
