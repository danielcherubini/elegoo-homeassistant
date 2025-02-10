"""Sample API Client."""

from __future__ import annotations

from logging import Logger
from typing import TYPE_CHECKING

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
        logger: Logger
    ) -> None:
        """Sample API Client."""
        self._ip_address: str = ip_address

        elegoo_printer = ElegooPrinterClient(ip_address)
        printer = elegoo_printer.discover_printer()
        if printer is None:
            return
        connected = elegoo_printer.connect_printer()
        if connected:
            logger.info("Polling Started")
            self._elegoo_printer: ElegooPrinterClient = elegoo_printer

    async def async_get_status(self) -> PrinterData:
        """Get data from the API."""
        try:
            return self._elegoo_printer.get_printer_status()
        except (websocket.WebSocketConnectionClosedException, websocket.WebSocketException) as e:
            # Probably best to do reconnection mechanic here.
            raise ElegooPrinterApiClientCommunicationError(e)
        except OSError as e:
            raise ElegooPrinterApiClientError(e)


    async def async_get_attributes(self) -> PrinterData:
        """Get data from the API."""
        try:
            return self._elegoo_printer.get_printer_attributes()
        except (websocket.WebSocketConnectionClosedException, websocket.WebSocketException) as e:
            raise ElegooPrinterApiClientCommunicationError(e)
        except OSError as e:
            raise ElegooPrinterApiClientError(e)

