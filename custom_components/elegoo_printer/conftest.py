"""
Shared test fixtures for the Elegoo Printer integration.

These fixtures centralise the small doubles that several test modules
hand-rolled (see ``websocket/tests/``) so that tests across the package
share one definition.
"""

from __future__ import annotations

import json
from types import MappingProxyType, SimpleNamespace
from typing import Self
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from custom_components.elegoo_printer.sdcp.models.printer import (
    Printer,
    PrinterData,
)
from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient
from custom_components.elegoo_printer.websocket.server.registry import PrinterRegistry

# Verbatim sample printer payload shared by the websocket test modules.
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


class FakeWebSocket:
    """Async-iterable mock websocket that yields pre-built messages."""

    def __init__(self, messages: list[object] | None = None) -> None:
        """
        Create a fake websocket.

        ``messages`` are pre-built messages yielded by ``__anext__``/``recv``.
        """
        self.messages = list(messages or [])
        self.sent: list[str] = []
        self.closed = False
        self._index = 0

    async def __aenter__(self) -> Self:
        """Enter the websocket context."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit the websocket context, marking it closed."""
        self.closed = True

    def __aiter__(self) -> Self:
        """Return the asynchronous iterator."""
        return self

    async def __anext__(self) -> object:
        """Yield the next queued message."""
        if self._index >= len(self.messages):
            raise StopAsyncIteration
        message = self.messages[self._index]
        self._index += 1
        return message

    async def recv(self) -> object:
        """Yield the next queued message, mirroring async iteration."""
        return await self.__anext__()

    async def send_str(self, data: object, *, encoding: str | None = None) -> None:
        """Record a sent message instead of writing to a socket."""
        del encoding
        self.sent.append(str(data))

    async def close(self) -> None:
        """Mark the websocket as closed."""
        self.closed = True


class FakeClientSession:
    """
    Minimal aiohttp client-session double for the websocket client.

    ``ws_connect`` is awaited just like the real one and returns an
    async-iterable ``FakeWebSocket``; tests that need the raw fake
    access ``session.websocket`` after a client connected.
    """

    def __init__(self) -> None:
        """Create a fake session with no queued websocket messages."""
        self.websocket: FakeWebSocket | None = None
        self._queued: list[object] = []

    def queue_messages(self, messages: list[object]) -> None:
        """
        Queue websocket messages for the next ``ws_connect`` call.

        ``messages`` are the messages the next ``ws_connect`` call yields.
        """
        self._queued = list(messages)

    async def ws_connect(self, *args: object, **kwargs: object) -> FakeWebSocket:
        """Return the fake websocket (and remember it for inspection)."""
        del args, kwargs
        websocket = FakeWebSocket(self._queued)
        self.websocket = websocket
        return websocket


@pytest.fixture
def mock_logger() -> MagicMock:
    """Create a mock logger for testing."""
    return MagicMock()


@pytest.fixture
def mock_printer_registry() -> Mock:
    """Create a mock printer registry for testing."""
    return Mock(spec=PrinterRegistry)


@pytest.fixture
def sample_printer() -> Printer:
    """Create a sample printer for testing."""
    return Printer(_SAMPLE_PRINTER_JSON)


@pytest.fixture
def hass() -> MagicMock:
    """
    Create a mock Home Assistant instance.

    Minimal HA double — replace with the pytest-homeassistant ``hass``
    fixture when that plugin is adopted.
    """
    hass = MagicMock()
    hass.bus = MagicMock()
    return hass


@pytest.fixture
def ws_client(mock_logger: MagicMock) -> ElegooPrinterClient:
    """Create a real ``ElegooPrinterClient`` wired to a fake session."""
    return ElegooPrinterClient(
        ip_address="192.168.1.100",
        session=FakeClientSession(),
        logger=mock_logger,
        config=MappingProxyType(json.loads(_SAMPLE_PRINTER_JSON)),
    )


def _build_api_mock() -> MagicMock:
    """Build an API mock with async methods back-patched to AsyncMock."""
    api = MagicMock()
    for name in (
        "async_get_printer_data",
        "async_get_firmware_update_info",
        "async_get_canvas_status",
        "reconnect",
        "is_thumbnail_available",
    ):
        setattr(api, name, AsyncMock())
    return api


@pytest.fixture
def entry(sample_printer: Printer) -> SimpleNamespace:
    """
    Create a minimal config-entry double for coordinator tests.

    ``data`` mirrors what the config flow stores (``Printer.to_dict()``,
    which includes the keys the coordinator reads, e.g. ``id``);
    ``CONF_HAS_CANVAS`` rides along in ``data`` and can be overridden
    in ``options``. Consumers read everything through
    ``entry.runtime_data.*``.
    """
    config_entry = SimpleNamespace(
        data=sample_printer.to_dict(),
        options={},
        title="Test Printer",
        runtime_data=None,
    )
    coordinator = MagicMock()
    coordinator.config_entry = config_entry
    config_entry.runtime_data = SimpleNamespace(
        api=_build_api_mock(),
        client=MagicMock(),
        printer_data=PrinterData(printer=sample_printer),
        coordinator=coordinator,
        integration=MagicMock(),
    )
    return config_entry


@pytest.fixture
def runtime_data(entry: SimpleNamespace) -> SimpleNamespace:
    """Expose the runtime data attached to the ``entry`` fixture."""
    return entry.runtime_data
