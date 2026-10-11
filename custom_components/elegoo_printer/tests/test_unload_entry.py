"""
Unloading an entry must only release the proxy reference it actually holds.

Since the offline-setup work (#171) an entry can be LOADED while never having
acquired a proxy reference: the "printer not reachable" exit of
``ElegooPrinterApiClient.async_create`` happens *before*
``_setup_proxy_if_enabled``, so that client has ``server is None`` and is not in
the shared ``ElegooPrinterServer`` registry. Before that change an unreachable
entry never reached ``async_unload_entry`` — it sat in ``SETUP_RETRY``.

Every printer shares one ``ElegooPrinterServer`` instance on fixed ports and the
reference count is class-level, so releasing a reference that was never taken
subtracts another entry's reference. Once it reaches zero the shared server is
stopped out from under a live entry, which is a user-visible disruption until
that entry reconnects on its next poll. ``client.server`` is the record of
whether this client holds a reference: every acquire sets it, every release path
pairs it with ``None`` (see ``ElegooPrinterApiClient.reconnect`` and the failure
exits of ``async_create``).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.elegoo_printer import async_unload_entry
from custom_components.elegoo_printer.sdcp.models.printer import Printer
from custom_components.elegoo_printer.websocket.server.proxy import ElegooPrinterServer


def _printer(ip_address: str, mainboard_id: str) -> Printer:
    """Build a real ``Printer`` for the given address."""
    return Printer(
        json.dumps(
            {
                "Id": mainboard_id,
                "Data": {
                    "Name": f"Printer {mainboard_id}",
                    "MachineName": "Saturn 4 Ultra",
                    "BrandName": "Elegoo",
                    "MainboardIP": ip_address,
                    "ProtocolVersion": "V3.0.0",
                    "FirmwareVersion": "V1.0.0",
                    "MainboardID": mainboard_id,
                },
            }
        )
    )


def _client(printer: Printer, server: ElegooPrinterServer | None) -> MagicMock:
    """
    Build an api-client double.

    ``server`` is the only thing that matters for these tests: it is what the
    real client sets when it acquires a proxy reference and clears when it
    releases one, so ``None`` means "this entry never took a reference".
    """
    client = MagicMock()
    client.printer = printer
    client.server = server
    client.elegoo_disconnect = AsyncMock()
    client.elegoo_stop_mqtt_broker = AsyncMock()
    return client


def _entry(client: MagicMock) -> SimpleNamespace:
    """Build a config-entry double carrying the given client as its runtime data."""
    return SimpleNamespace(entry_id="entry", runtime_data=SimpleNamespace(api=client))


def _hass() -> MagicMock:
    """Build a hass double whose platform unload succeeds, like a real unload."""
    hass = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    return hass


@pytest.fixture
def proxy() -> Any:
    """
    Isolate the proxy singleton and keep it off the network.

    The real ``async_create``/``release_reference`` bookkeeping runs — only the
    sockets are faked — so a reference-count regression is observable.
    """
    ElegooPrinterServer._instance = None
    ElegooPrinterServer._reference_count = 0
    with (
        patch.object(ElegooPrinterServer, "start", new=AsyncMock()),
        patch.object(ElegooPrinterServer, "stop", new=AsyncMock()) as stop,
        patch.object(ElegooPrinterServer, "_force_cleanup_ports", new=AsyncMock()),
    ):
        yield SimpleNamespace(stop=stop)
    ElegooPrinterServer._instance = None
    ElegooPrinterServer._reference_count = 0


async def _online_client(printer: Printer, hass: MagicMock) -> MagicMock:
    """
    Build a client for an entry that came up online through the proxy.

    Goes through the real singleton path, so it holds a genuine reference and is
    registered in the shared server exactly like a live entry.
    """
    client = _client(printer, server=None)
    client.server = await ElegooPrinterServer.async_create(
        logger=MagicMock(), hass=hass, printer=printer
    )
    return client


class TestUnloadReleasesOnlyItsOwnProxyReference:
    """An entry may only give back a proxy reference it acquired."""

    async def test_unloading_an_offline_entry_leaves_the_shared_server_running(
        self, proxy: SimpleNamespace
    ) -> None:
        """
        The regression: unloading entry B must not stop entry A's proxy server.

        A is online and holds the shared instance; B came up offline through the
        unreachable exit, so it has no server and is not registered. Unloading B
        used to decrement A's reference and stop the shared server.
        """
        hass = _hass()
        entry_a_client = await _online_client(_printer("10.0.0.1", "AAA"), hass)
        server = ElegooPrinterServer._instance
        assert server is not None
        assert ElegooPrinterServer._reference_count == 1
        # B: loaded, proxy-enabled in its options, but never reached the proxy.
        offline_client = _client(_printer("10.0.0.2", "BBB"), server=None)
        entry_b = _entry(offline_client)

        assert await async_unload_entry(hass, entry_b) is True

        assert ElegooPrinterServer._instance is server
        assert ElegooPrinterServer._reference_count == 1
        assert proxy.stop.await_count == 0
        # A is untouched: still registered, still the only printer in the registry.
        assert server.printer_registry.count() == 1
        assert server.printer_registry.get_printer_by_ip("10.0.0.1") is not None
        assert entry_a_client.server is server
        # Everything the offline entry DOES own is still cleaned up.
        offline_client.elegoo_disconnect.assert_awaited_once()
        offline_client.elegoo_stop_mqtt_broker.assert_awaited_once()

    async def test_unloading_an_offline_entry_that_kept_its_reference_releases_one(
        self, proxy: SimpleNamespace
    ) -> None:
        """
        The gate is on the reference, not on connectivity.

        The other offline exit of ``async_create`` (the printer answered the
        discovery probe but not the connect handshake) happens *after*
        ``_setup_proxy_if_enabled``, so that client keeps ``server`` and its
        registry entry — unloading it must still give the reference back.
        """
        hass = _hass()
        client_a = await _online_client(_printer("10.0.0.1", "AAA"), hass)
        client_b = await _online_client(_printer("10.0.0.2", "BBB"), hass)
        client_b.is_connected = False  # offline, but it holds the reference
        server = ElegooPrinterServer._instance
        assert ElegooPrinterServer._reference_count == 2

        assert await async_unload_entry(hass, _entry(client_b)) is True

        assert ElegooPrinterServer._instance is server
        assert ElegooPrinterServer._reference_count == 1
        assert proxy.stop.await_count == 0
        assert server is not None
        assert server.printer_registry.get_printer_by_ip("10.0.0.2") is None
        assert client_a.server is server

    async def test_unloading_one_of_two_online_entries_keeps_the_server_alive(
        self, proxy: SimpleNamespace
    ) -> None:
        """A holder's unload decrements exactly one reference."""
        hass = _hass()
        client_a = await _online_client(_printer("10.0.0.1", "AAA"), hass)
        client_b = await _online_client(_printer("10.0.0.2", "BBB"), hass)
        server = ElegooPrinterServer._instance
        assert ElegooPrinterServer._reference_count == 2

        assert await async_unload_entry(hass, _entry(client_b)) is True

        assert ElegooPrinterServer._instance is server
        assert ElegooPrinterServer._reference_count == 1
        assert proxy.stop.await_count == 0
        assert server.printer_registry.count() == 1
        assert server.printer_registry.get_printer_by_ip("10.0.0.2") is None
        assert server.printer_registry.get_printer_by_ip("10.0.0.1") is not None
        assert client_a.server is server
