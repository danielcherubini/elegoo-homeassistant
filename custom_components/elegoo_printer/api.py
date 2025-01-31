"""Sample API Client."""

from __future__ import annotations

import socket
from typing import Any

import aiohttp
import async_timeout

from .elegoo.printer import ElegooPrinterClient


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

    async def async_get_data(self) -> Any:
        """Get data from the API."""
        status = self._elegoo_printer.get_printer_status()
        return status.status

    async def async_set_title(self, value: str) -> Any:
        """Get data from the API."""
        return await self._api_wrapper(
            method="patch",
            url="https://jsonplaceholder.typicode.com/posts/1",
            data={"title": value},
            headers={"Content-type": "application/json; charset=UTF-8"},
        )

    async def _api_wrapper(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        headers: dict | None = None,
    ) -> Any:
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
                return await response.json()

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
