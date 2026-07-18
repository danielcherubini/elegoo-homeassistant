"""Tests for WebSocket printer discovery matching."""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, call, patch

from custom_components.elegoo_printer.api import ElegooPrinterApiClient
from custom_components.elegoo_printer.sdcp.models.printer import Printer

if TYPE_CHECKING:
    from collections.abc import Callable

_DOC_IP = "192.0.2.10"
_HOSTNAME = "centauri-carbon.local"


def _printer(ip_address: str) -> Printer:
    return Printer.from_dict(
        {
            "name": "Centauri Carbon",
            "ip_address": ip_address,
            "protocol": "V3",
        }
    )


def _api_client(discovered_printers: list[Printer]) -> ElegooPrinterApiClient:
    api_client = ElegooPrinterApiClient.__new__(ElegooPrinterApiClient)
    api_client.client = MagicMock()
    api_client.client.discover_printer.return_value = discovered_printers
    api_client._logger = MagicMock()
    api_client.hass = MagicMock()

    async def _run_executor_job(func: Callable[..., Any], *args: Any) -> Any:
        return func(*args)

    api_client.hass.async_add_executor_job = AsyncMock(side_effect=_run_executor_job)
    return api_client


class TestDiscoverPrinterWithFallback:
    """Hostname-aware WebSocket discovery."""

    def test_resolved_hostname_matches_numeric_discovery_result(self) -> None:
        async def _run() -> None:
            api_client = _api_client([_printer(_DOC_IP)])
            with patch(
                "custom_components.elegoo_printer.api.socket.gethostbyname",
                return_value=_DOC_IP,
            ) as resolve_hostname:
                assert await api_client._discover_printer_with_fallback(
                    _printer(_HOSTNAME)
                )

            resolve_hostname.assert_called_once_with(_HOSTNAME)
            api_client.client.discover_printer.assert_called_once_with(_HOSTNAME)

        asyncio.run(_run())

    def test_resolved_hostname_matches_broadcast_discovery_result(self) -> None:
        async def _run() -> None:
            api_client = _api_client([])
            api_client.client.discover_printer.side_effect = [
                [],
                [_printer(_DOC_IP)],
            ]
            with patch(
                "custom_components.elegoo_printer.api.socket.gethostbyname",
                return_value=_DOC_IP,
            ):
                assert await api_client._discover_printer_with_fallback(
                    _printer(_HOSTNAME)
                )

            assert api_client.client.discover_printer.call_args_list == [
                call(_HOSTNAME),
                call(),
            ]

        asyncio.run(_run())

    def test_unresolvable_hostname_still_uses_broadcast_fallback(self) -> None:
        async def _run() -> None:
            api_client = _api_client([])
            with patch(
                "custom_components.elegoo_printer.api.socket.gethostbyname",
                side_effect=socket.gaierror,
            ):
                assert not await api_client._discover_printer_with_fallback(
                    _printer(_HOSTNAME)
                )

            assert api_client.client.discover_printer.call_args_list == [
                call(_HOSTNAME),
                call(),
            ]

        asyncio.run(_run())
