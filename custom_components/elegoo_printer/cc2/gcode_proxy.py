"""
HTTP client for the cc2-gcode-capture-proxy REST API.

Queries per-extruder filament data parsed from G-code files that were
captured by the proxy as they were uploaded from the slicer to the printer.
"""

from __future__ import annotations

import asyncio
import logging
from http import HTTPStatus
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 5


class GCodeProxyClient:
    """Client for the cc2-gcode-capture-proxy filament API."""

    def __init__(self, base_url: str, session: aiohttp.ClientSession) -> None:
        """
        Initialize with base URL and aiohttp session.

        Args:
            base_url: The base URL of the cc2-gcode-capture-proxy service.
            session: The aiohttp client session to use for requests.

        """
        self._base_url = base_url.rstrip("/")
        self._session = session

    async def fetch_filament_data(self, filename: str) -> dict[str, Any] | None:
        """
        Fetch filament metadata for a given filename.

        Args:
            filename: The G-code filename to query filament data for.

        Returns:
            The parsed JSON dict on success, or None on any error.

        """
        url = f"{self._base_url}/api/filament"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                resp = await self._session.get(url, params={"filename": filename})
            if resp.status != HTTPStatus.OK:
                logger.debug(
                    "Proxy returned %s for filename %s", resp.status, filename
                )
                return None
            return await resp.json()
        except TimeoutError:
            logger.debug("Proxy request timed out for filename %s", filename)
        except aiohttp.ClientError as exc:
            logger.debug("Proxy request failed for filename %s: %s", filename, exc)
        return None

    async def check_health(self) -> bool:
        """
        Check whether the proxy service is reachable and healthy.

        Returns:
            True if the proxy health endpoint responds with status "ok", False otherwise.

        """
        url = f"{self._base_url}/api/health"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                resp = await self._session.get(url)
            if resp.status == HTTPStatus.OK:
                data = await resp.json()
                return data.get("status") == "ok"
        except (TimeoutError, aiohttp.ClientError):
            logger.debug("Proxy health check failed", exc_info=True)
        return False
