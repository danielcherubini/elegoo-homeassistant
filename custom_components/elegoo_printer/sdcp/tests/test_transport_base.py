"""
Behavior pins for the shared SDCP transport base (sdcp.transport.base).

The status-extract equivalence tests pin the 4-case union chain against
the pre-extraction ws (whole-frame) and mqtt (Data/Status extraction)
shapes, plus the explicit SKIP case. The ``_send_printer_cmd`` tests pin
event registration, frame shape, and the timeout path.

Transport construction goes through a small fake subclass — the base is
a plain class, so no real wire is needed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.elegoo_printer.sdcp.const import (
    CMD_SET_VIDEO_STREAM,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterTimeoutError,
)
from custom_components.elegoo_printer.sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer
from custom_components.elegoo_printer.sdcp.transport import base as base_module
from custom_components.elegoo_printer.sdcp.transport.base import (
    SdcpPrinterClient,
)


class _FakeTransport(SdcpPrinterClient):
    """Base transport with a publish seam; no real wire."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize and record published frames."""
        super().__init__(**kwargs)
        self.published: list[dict[str, Any]] = []

    def _transport_open(self) -> bool:
        """Test transport is always open."""
        return True

    async def _publish_frame(self, frame: str) -> None:
        """Record the frame instead of publishing it."""
        self.published.append(json.loads(frame))


def _make_client(**kwargs: Any) -> _FakeTransport:
    """Build a fake transport around the shared base state."""
    printer = Printer()
    printer.connection = "fake_connection"
    printer.id = "fake_mainboard_id"
    return _FakeTransport(printer=printer, **kwargs)


async def _wait_until(predicate: Any, max_wait: float = 5.0) -> None:
    """Wait (bounded) until the predicate holds."""
    deadline = asyncio.get_running_loop().time() + max_wait
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            msg = "predicate not met in test"
            raise TimeoutError(msg)
        await asyncio.sleep(0.01)


async def test_send_printer_cmd_registers_event_and_resolves() -> None:
    """A matching response completes the command; the frame shape is pinned."""
    client = _make_client(logger=MagicMock())
    client._is_connected = True
    task = asyncio.create_task(client._send_printer_cmd(7, {"a": 1}))
    await _wait_until(lambda: len(client._response_events) == 1)
    request_id = next(iter(client._response_events))

    # Unblock the command with a matching RequestID (no real transport).
    client._response_events[request_id].set()
    await task  # Must complete without raising (no timeout).

    frame = client.published[0]
    assert frame["Id"] == "fake_connection"
    assert frame["Data"]["Cmd"] == 7
    assert frame["Data"]["Data"] == {"a": 1}
    assert frame["Data"]["RequestID"] == request_id
    assert frame["Data"]["MainboardID"] == "fake_mainboard_id"
    assert frame["Data"]["From"] == 0
    # Base (ws) shape: no leading slash pin.
    assert frame["Topic"] == "sdcp/request/fake_mainboard_id"
    assert client._response_events == {}


async def test_send_printer_cmd_timeout_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No matching response must raise ElegooPrinterTimeoutError."""
    monkeypatch.setattr(base_module, "SDCP_COMMAND_TIMEOUT", 0.01)
    client = _make_client(logger=MagicMock())
    client._is_connected = True
    with pytest.raises(ElegooPrinterTimeoutError):
        await client._send_printer_cmd(7, {"a": 1})


# ----------------------------------------------------------------------
# _status_payload_extract — the 4-case union chain (a/b/c/d + corner e)
# Equivalence vs the pre-extraction handlers: case (a)+(c) are the old
# mqtt shape, (b)+(d) the old ws shape, (d) the mqtt-skip case, (e) the
# intentional corner judgment.
# ----------------------------------------------------------------------


async def test_status_extract_case_a_mqtt_nested() -> None:
    """{'Data': {'Status': {...}}} -> inner Status (old mqtt shape)."""
    client = _make_client(logger=None)
    payload = {"Data": {"Status": {"TempOfUVLED": 30.5}}}
    assert client._status_payload_extract(payload) == {"TempOfUVLED": 30.5}


async def test_status_extract_case_b_no_data_no_status_is_itself() -> None:
    """A frame with no 'Data' and no 'Status' -> itself (old ws shape)."""
    client = _make_client(logger=None)
    payload = {"Foo": 1}
    assert client._status_payload_extract(payload) is payload


async def test_status_extract_case_c_top_level_status() -> None:
    """{'Status': {...}} (no Data) -> the top-level Status."""
    client = _make_client(logger=None)
    payload = {"Status": {"MachineStatus": 0}}
    assert client._status_payload_extract(payload) == {"MachineStatus": 0}


async def test_status_extract_case_d_data_without_status_is_skip() -> None:
    """{'Data': <dict without Status>} -> None: handler must skip."""
    client = _make_client(logger=None)
    payload = {"Data": {"Other": 1}}
    assert client._status_payload_extract(payload) is None


async def test_status_extract_case_e_neither_key_is_itself() -> None:
    """
    Corner: a frame with NEITHER 'Data' NOR 'Status' -> itself.

    Old mqtt warn+skipped this shape; the chain subsumes it into the
    ws whole-frame shape (at most a debug log now).
    """
    client = _make_client(logger=None)
    payload = {"A": 1, "B": 2}
    assert client._status_payload_extract(payload) is payload


# ----------------------------------------------------------------------
# Structural pins
# ----------------------------------------------------------------------


async def test_publish_frame_not_implemented_in_base() -> None:
    """The base _publish_frame must raise NotImplementedError."""

    class _Bare(SdcpPrinterClient):
        """A bare base instance (no transport overrides)."""

    bare = _Bare(None, Printer())
    with pytest.raises(NotImplementedError):
        await bare._publish_frame("{}")


async def test_listen_not_implemented_in_base() -> None:
    """The base _listen must raise NotImplementedError."""

    class _Bare(SdcpPrinterClient):
        """A bare base instance (no transport overrides)."""

    bare = _Bare(None, Printer())
    with pytest.raises(NotImplementedError):
        await bare._listen()


async def test_request_topic_base_starts_without_leading_slash() -> None:
    """The base request topic keeps the wire prefix as-is (ws shape)."""
    client = _make_client(logger=None)
    assert client._request_topic("sdcp/request") == "sdcp/request"


async def test_handle_push_frame_routes_shared_bodies() -> None:
    """The router dispatches the 3 shared push handler bodies."""
    client = _make_client(logger=None)

    client._handle_push_frame("print_history", {"HistoryData": ["t1", "t2"]})
    assert set(client.printer_data.print_history) == {"t1", "t2"}

    client._handle_push_frame(
        "print_history_detail",
        {"HistoryDetailList": [{"TaskId": "t1", "BeginTime": 1.0, "EndTime": 2.0}]},
    )
    detail = client.printer_data.print_history["t1"]
    assert isinstance(detail, PrintHistoryDetail)
    assert detail.task_id == "t1"

    client._handle_push_frame(
        "elegoo_video",
        {"Ack": 0, "VideoUrl": "http://printer:8080/?action=stream"},
    )
    assert client.printer_data.video.video_url == ("http://printer:8080/?action=stream")


async def test_shared_video_accessor_uses_base_framing() -> None:
    """set_printer_video_stream / get_printer_video are shared on the base."""
    client = _make_client(logger=MagicMock())
    client._is_connected = True

    async def _publish_and_ack(frame: str) -> None:
        """Confirm and resolve the command inside the publish seam."""
        client.published.append(json.loads(frame))
        for ev in client._response_events.values():
            ev.set()

    client._publish_frame = _publish_and_ack  # type: ignore[method-assign]
    await client.set_printer_video_stream(enable=True)

    frame = client.published[0]
    assert frame["Data"]["Cmd"] == CMD_SET_VIDEO_STREAM
    assert frame["Data"]["Data"] == {"Enable": 1}
    assert (await client.get_printer_video()) is client.printer_data.video
