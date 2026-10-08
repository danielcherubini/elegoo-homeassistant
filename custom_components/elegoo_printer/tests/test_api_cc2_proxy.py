"""
Runtime setup routing for CC2 printers behind a forward proxy (#414).

``ElegooPrinterApiClient.async_create`` performs a raw TCP reachability test
before the config entry finishes setting up. If that test targets the printer's
own IP while the user configured a proxy, a proxy-only printer passes the
config flow (which uses the client) but fails on every Home Assistant start
with ``ConfigEntryNotReady`` — so the reachability host is pinned here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import custom_components.elegoo_printer.api as api_module
from custom_components.elegoo_printer.api import ElegooPrinterApiClient
from custom_components.elegoo_printer.cc2.client import ElegooCC2Client

PRINTER_IP = "10.0.0.9"
PROXY_IP = "10.0.0.5"
CC2_MQTT_PORT = 1883


def _cc2_config(*, proxy_host: str | None) -> dict[str, Any]:
    """Build the stored config a CC2 entry carries, with/without proxy_host."""
    config: dict[str, Any] = {
        "name": "Centauri Carbon 2",
        "id": "SERIAL123",
        "connection": "SERIAL123",
        "model": "Centauri Carbon 2",
        "ip_address": PRINTER_IP,
        "protocol_version": "CC2",
        "transport_type": "cc2_mqtt",
    }
    if proxy_host is not None:
        config["proxy_host"] = proxy_host
    return config


async def _create_through_proxy(config: dict[str, Any]) -> tuple[Any, list[Any]]:
    """
    Run ``async_create`` with the network mocked, returning (api, sockets).

    ``sockets`` records every ``(host, port)`` the runtime reachability test
    tried to open, which is what these tests assert on.
    """
    sockets: list[tuple[str, int]] = []

    class _Writer:
        def close(self) -> None:
            """No-op."""

        async def wait_closed(self) -> None:
            """No-op."""

    async def fake_open_connection(
        host: str, port: int, *_args: Any, **_kwargs: Any
    ) -> tuple[MagicMock, _Writer]:
        sockets.append((host, port))
        return MagicMock(), _Writer()

    with (
        patch.object(api_module, "async_get_clientsession", return_value=MagicMock()),
        patch.object(api_module, "get_async_client", return_value=MagicMock()),
        patch("asyncio.open_connection", side_effect=fake_open_connection),
        patch.object(
            ElegooCC2Client, "connect_printer", new=AsyncMock(return_value=True)
        ),
        patch.object(
            ElegooPrinterApiClient,
            "_update_config_entry_if_needed",
            new=AsyncMock(),
        ),
    ):
        api = await ElegooPrinterApiClient.async_create(
            config, MagicMock(), MagicMock(), None
        )
    return api, sockets


async def test_runtime_setup_reaches_cc2_through_proxy_host() -> None:
    """With proxy_host set, the reachability test and client use the proxy."""
    api, sockets = await _create_through_proxy(_cc2_config(proxy_host=PROXY_IP))

    assert api is not None
    assert sockets == [(PROXY_IP, CC2_MQTT_PORT)]
    assert api._mqtt_host == PROXY_IP
    assert api.client.printer_ip == PROXY_IP


async def test_runtime_setup_reaches_cc2_directly_without_proxy_host() -> None:
    """Without proxy_host, the reachability test still targets the printer."""
    api, sockets = await _create_through_proxy(_cc2_config(proxy_host=None))

    assert api is not None
    assert sockets == [(PRINTER_IP, CC2_MQTT_PORT)]
    assert api._mqtt_host == PRINTER_IP
    assert api.client.printer_ip == PRINTER_IP


async def test_runtime_setup_empty_proxy_host_falls_back_to_printer() -> None:
    """An empty proxy_host (options cleared it) connects directly."""
    api, sockets = await _create_through_proxy(_cc2_config(proxy_host=""))

    assert api is not None
    assert sockets == [(PRINTER_IP, CC2_MQTT_PORT)]
    assert api._mqtt_host == PRINTER_IP
