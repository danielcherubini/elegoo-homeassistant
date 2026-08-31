"""
Tests for ws/cc2/mqtt transport client hardening (plan-001 Task 1).

Covers:
- disconnect() must suppress a terminal listener exception (ws/cc2/mqtt)
- the ws listener must survive a model-raising (malformed) frame
- the ws _parse_response must ignore a 1-segment Topic instead of an IndexError
- the ws async_get_printer_last_task sort key must be comparable (float)

Note: the ``_ws_listener`` / ``_mqtt_listener`` aliases on the clients are
pinned-name aliases (one-line ``def``s returning ``self._listen()``) kept
so pre-existing tests can call them — do not delete them as "unused".
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import aiohttp

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.mqtt.client import ElegooMQTTClient
from custom_components.elegoo_printer.sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient


class FakeWebSocket:
    """
    Async-iterable fake websocket with a pre-queued frame list.

    Arguments:
        frames: The frame list to yield during async iteration.

    """

    def __init__(self, frames: list) -> None:
        """Initialize the fake with the pre-queued frame list."""
        self._frames = list(frames)
        self.closed = False

    def __aiter__(self) -> FakeWebSocket:
        """Return self as its own async iterator."""
        return self

    async def __anext__(self) -> object:
        """Yield the next queued frame or raise StopAsyncIteration."""
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)

    async def close(self) -> None:
        self.closed = True

    def exception(self) -> object:
        return None


class FakeClientSession:
    """Stand-in for aiohttp.ClientSession whose ws_connect returns a fake."""

    def __init__(self, ws: object) -> None:
        """Initialize with the fake websocket to return on ws_connect."""
        self._ws = ws

    async def ws_connect(self, *args: object, **kwargs: object) -> object:
        """Return the fake websocket (and remember it for inspection)."""
        del args, kwargs
        return self._ws


def _text_msg(payload: object) -> SimpleNamespace:
    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(payload))


async def _terminal() -> None:
    """Simulate a listener that dies with a stored RuntimeError."""
    msg = "terminal"
    raise RuntimeError(msg)


def test_ws_disconnect_suppresses_terminal_listener_exception() -> None:
    """ws disconnect() must contain a terminal listener exception."""

    async def _run() -> None:
        client = ElegooPrinterClient("192.0.2.1", FakeClientSession(MagicMock()))
        client.logger = MagicMock()
        client._is_connected = True
        client._listener_task = asyncio.create_task(_terminal())
        await asyncio.sleep(0)  # let the task actually raise and store the error
        await client.disconnect()
        assert client._is_connected is False
        assert client.logger.exception.called

    asyncio.run(_run())


def test_cc2_disconnect_suppresses_terminal_listener_exception() -> None:
    """cc2 disconnect() must contain a terminal listener exception."""

    async def _run() -> None:
        client = ElegooCC2Client(
            printer_ip="192.168.1.100", serial_number="serial1", access_code=None
        )
        client.logger = MagicMock()
        client._is_connected = True
        client._listener_task = asyncio.create_task(_terminal())
        await asyncio.sleep(0)  # let the task actually raise and store the error
        await client.disconnect()
        assert client._is_connected is False

    asyncio.run(_run())


def test_mqtt_disconnect_suppresses_terminal_listener_exception() -> None:
    """mqtt disconnect() must contain a terminal listener exception."""

    async def _run() -> None:
        client = ElegooMQTTClient()
        client.logger = MagicMock()
        client._is_connected = True
        client._listener_task = asyncio.create_task(_terminal())
        await asyncio.sleep(0)  # let the task actually raise and store the error
        await client.disconnect()
        assert client._is_connected is False

    asyncio.run(_run())


def test_ws_listener_survives_malformed_status_payload() -> None:
    """A model-raising frame must not kill the ws listener."""

    async def _run() -> None:
        # Frame 1: 2-segment "x/status" whose payload makes
        # PrinterStatus.__init__ raise (round("abc", 2) -> TypeError).
        bad_frame = {
            "Id": 1,
            "Topic": "x/status",
            "Status": {"TempOfUVLED": "abc"},
        }
        # Frame 2: valid "x/status" frame that updates printer_data.status.
        good_frame = {
            "Id": 2,
            "Topic": "x/status",
            "Status": {"TempOfUVLED": 30.5},
        }
        ws = FakeWebSocket([_text_msg(bad_frame), _text_msg(good_frame)])
        client = ElegooPrinterClient("192.0.2.1", FakeClientSession(MagicMock()))
        client.logger = MagicMock()
        client.printer_websocket = ws
        task = asyncio.create_task(client._ws_listener())
        await task  # drive the listener to completion

        # The listener must have survived the malformed frame.
        assert task.done()
        assert task.exception() is None
        # The malformed frame must have been logged with a traceback.
        assert client.logger.exception.called
        # The subsequent valid frame must still have been processed.
        assert client.printer_data.status.temp_of_uvled == 30.5

    asyncio.run(_run())


def test_ws_parse_response_ignores_missing_topic_segment() -> None:
    """A 1-segment Topic must be ignored with a warning, not an IndexError."""

    async def _run() -> None:
        client = ElegooPrinterClient("192.0.2.1", FakeClientSession(MagicMock()))
        client.logger = MagicMock()
        client._parse_response(json.dumps({"Id": 1, "Topic": "status"}))
        assert client.logger.warning.called

    asyncio.run(_run())


def _make_ws_client_with_history(
    history: dict[str, PrintHistoryDetail],
) -> ElegooPrinterClient:
    client = ElegooPrinterClient("192.0.2.1", FakeClientSession(MagicMock()))
    client.printer_data.print_history = history
    return client


def test_ws_last_task_sort_mixed_finished_unfinished() -> None:
    """Regression catcher: one finished + one unfinished task must not raise."""

    async def _run() -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        history = {
            "a": PrintHistoryDetail({}),
            "b": PrintHistoryDetail({"EndTime": base.timestamp()}),
        }
        client = _make_ws_client_with_history(history)
        task = await client.async_get_printer_last_task()
        assert task is history["b"]

    asyncio.run(_run())


def test_ws_last_task_sort_all_unfinished() -> None:
    """All-None end times must not raise; winner is the first in iteration order."""

    async def _run() -> None:
        history = {
            "a": PrintHistoryDetail({}),
            "b": PrintHistoryDetail({}),
        }
        client = _make_ws_client_with_history(history)
        task = await client.async_get_printer_last_task()
        # Observed winner (max over equal keys -> first in iteration order):
        assert task is history["a"]

    asyncio.run(_run())


def test_ws_last_task_sort_tied_end_times() -> None:
    """Identical end times: winner is the first in iteration order (max semantics)."""

    async def _run() -> None:
        end_ts = (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=1)).timestamp()
        history = {
            "x": PrintHistoryDetail({"EndTime": end_ts}),
            "y": PrintHistoryDetail({"EndTime": end_ts}),
        }
        client = _make_ws_client_with_history(history)
        task = await client.async_get_printer_last_task()
        # Observed winner (max over equal keys -> first in iteration order):
        assert task is history["x"]

    asyncio.run(_run())
