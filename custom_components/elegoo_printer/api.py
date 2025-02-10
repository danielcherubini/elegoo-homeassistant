"""Sample API Client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp
import websocket

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
        session: aiohttp.ClientSession,
    ) -> None:
        """Sample API Client."""
        self._ip_address: str = ip_address
        self._elegoo_printer: ElegooPrinterClient = elegoo_printer
        self._session: aiohttp.ClientSession = session

    async def async_get_status(self) -> PrinterData:
        """Get data from the API."""
        try:
            return self._elegoo_printer.get_printer_status()
        except (websocket.WebSocketConnectionClosedException, websocket.WebSocketException, OSError) as e:
            raise ElegooPrinterApiClientCommunicationError(e)


    async def async_get_attributes(self) -> PrinterData:
        """Get data from the API."""
        try:
            return self._elegoo_printer.get_printer_attributes()
        except (websocket.WebSocketConnectionClosedException, websocket.WebSocketException, OSError) as e:
            raise ElegooPrinterApiClientCommunicationError(e)

