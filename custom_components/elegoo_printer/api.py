"""Sample API Client."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

import aiohttp
import async_timeout

from custom_components.elegoo_printer.elegoo_sdcp.elegoo_printer import (
    ElegooPrinterClient,
    ElegooPrinterClientWebsocketConnectionError,
    ElegooPrinterClientWebsocketError,
)

if TYPE_CHECKING:
    from logging import Logger

    from custom_components.elegoo_printer.elegoo_sdcp.models.printer import PrinterData


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


def _verify_response_or_raise(response: aiohttp.ClientResponse) -> None:
    """Verify that the response is valid."""
    if response.status in (401, 403):
        msg = "Invalid credentials"
        raise ElegooPrinterApiClientAuthenticationError(
            msg,
        )
    response.raise_for_status()


class ElegooPrinterApiClient:
    """Sample API Client."""

    _ip_address: str
    _elegoo_printer: ElegooPrinterClient
    _logger: Logger

    def __init__(
        self, ip_address: str, logger: Logger, session: aiohttp.ClientSession
    ) -> None:
        """Initialize."""
        self._ip_address = ip_address
        self._logger = logger
        self._session = session

    @classmethod
    async def async_create(
        cls, ip_address: str, logger: Logger, session: aiohttp.ClientSession
    ) -> ElegooPrinterApiClient | None:
        """Sample API Client."""
        self = ElegooPrinterApiClient(ip_address, logger, session)

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
            connected = await self._retry()
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

    async def async_get_current_print_thumbnail(self) -> str | None:
        """Get current print thumbnail."""
        try:
            return await self._elegoo_printer.get_current_print_thumbnail()
        except ElegooPrinterClientWebsocketConnectionError:
            # Retry
            connected = await self._retry()
            if connected is False:
                raise ElegooPrinterClientWebsocketError from Exception(
                    "Failed to reconnect"
                )
            return await self._elegoo_printer.get_current_print_thumbnail()
        except (ElegooPrinterClientWebsocketError, OSError):
            raise

    async def async_get_image(self, image_url: str) -> bytes | None:
        """Get the image from the printer and return it as bytes."""
        self._logger.debug("Fetching image from URL: %s", image_url)
        response = await self._api_wrapper(method="get", url=image_url)
        return await response.content.read()

    async def _retry(self) -> bool:
        """Retry connecting to the printer and getting data."""
        return await self._elegoo_printer.connect_printer()

    async def _api_wrapper(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        headers: dict | None = None,
    ) -> aiohttp.ClientResponse:
        """Get information from the API."""
        try:
            async with async_timeout.timeout(10):
                response = await self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data,
                )
                _verify_response_or_raise(response)
                return response

        except TimeoutError as exception:
            msg = f"Timeout error fetching information - {exception}"
            raise ElegooPrinterApiClientCommunicationError(
                msg,
            ) from exception
        except (aiohttp.ClientError, socket.gaierror) as exception:
            msg = f"Error fetching information - {exception}"
            raise ElegooPrinterApiClientCommunicationError(
                msg,
            ) from exception
        except Exception as exception:  # pylint: disable=broad-except
            msg = f"Something really wrong happened! - {exception}"
            raise ElegooPrinterApiClientError(
                msg,
            ) from exception
