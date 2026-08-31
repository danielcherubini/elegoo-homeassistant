"""
Characterization tests for the websocket client lifecycle.

Covers connect, double-connect, disconnect, and connect-failure — all
wired to the shared conftest ``FakeClientSession``/``FakeWebSocket``
doubles.

Any failure while writing a pin means a previously-unknown bug, not a test
error.
"""

from __future__ import annotations

import asyncio
import json
from types import MappingProxyType
from typing import Any
from unittest.mock import MagicMock

import aiohttp

from custom_components.elegoo_printer.conftest import FakeClientSession
from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient

_SAMPLE_PRINTER_JSON = json.dumps(
    {
        "Id": "test_connection",
        "Data": {
            "Name": "Test Printer",
            "MachineName": "Saturn 4 Ultra",
            "BrandName": "Elegoo",
            "MainboardIP": "192.168.1.100",
            "ProtocolVersion": "V3.0.0",
            "FirmwareVersion": "V1.0.0",
            "MainboardID": "test_mainboard_id_12345",
        },
    }
)


def _make_client(session: Any) -> ElegooPrinterClient:
    """Build a ws client on top of the given (fake) session."""
    return ElegooPrinterClient(
        ip_address="192.168.1.100",
        session=session,  # type: ignore[arg-type]
        logger=MagicMock(),
        config=MappingProxyType(json.loads(_SAMPLE_PRINTER_JSON)),
    )


async def test_connect_resolves_fake_ws_and_starts_listener(
    ws_client: ElegooPrinterClient,
) -> None:
    """A successful ws_connect flips is_connected and creates the listener."""
    session = ws_client._session
    assert session is not None

    result = await ws_client.connect_printer(ws_client.printer, proxy_enabled=False)

    assert result is True
    assert ws_client.is_connected is True
    assert ws_client._is_connected is True
    assert ws_client._listener_task is not None
    assert session.websocket is not None
    assert session.websocket.closed is False

    await ws_client.disconnect()


async def test_double_connect_is_a_noop_single_ws_connect(
    ws_client: ElegooPrinterClient,
) -> None:
    """A second connect while connected returns True without a new ws_connect."""
    session = ws_client._session
    assert session is not None

    first = await ws_client.connect_printer(ws_client.printer, proxy_enabled=False)
    first_ws = session.websocket
    assert first is True
    assert first_ws is not None

    second = await ws_client.connect_printer(ws_client.printer, proxy_enabled=False)

    assert second is True
    # Pinned: the early-return path never calls ws_connect again.
    assert session.websocket is first_ws
    assert ws_client._is_connected is True

    await ws_client.disconnect()


async def test_disconnect_unblocks_waiters_and_closes_ws(
    ws_client: ElegooPrinterClient,
) -> None:
    """disconnect() sets pending response events and closes the websocket."""
    session = ws_client._session
    assert session is not None
    assert await ws_client.connect_printer(ws_client.printer, proxy_enabled=False)
    pending = asyncio.Event()
    ws_client._response_events["pending-request"] = pending

    await ws_client.disconnect()

    assert pending.is_set()
    assert ws_client.is_connected is False
    assert ws_client._response_events == {}
    assert session.websocket is not None
    assert session.websocket.closed is True

    # No rebuild: a re-connect must go through a fresh ws_connect.
    assert await ws_client.connect_printer(ws_client.printer, proxy_enabled=False)
    assert session.websocket is not None


async def test_connect_failure_returns_false_and_stays_disconnected() -> None:
    """A ws_connect that raises ClientError must report False."""

    class FailingSession(FakeClientSession):
        """Session whose ws_connect raises ClientConnectionError."""

        async def ws_connect(self, *_args: object, **_kwargs: object) -> object:
            msg = "connection refused"
            raise aiohttp.ClientConnectionError(msg)

    session = FailingSession()
    client = _make_client(session)

    result = await client.connect_printer(client.printer, proxy_enabled=False)

    assert result is False
    assert client.is_connected is False
    assert client._is_connected is False
    assert client.printer_websocket is None
    assert client._listener_task is None
