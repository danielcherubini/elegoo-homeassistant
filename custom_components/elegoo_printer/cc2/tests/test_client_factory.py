"""
Characterization tests for the Elegoo CC2 client.

The shared-SDCP plumbing is made testable via the ``client_factory`` seam.

The CC2-only sides (auth fallback, connection generation, delayed disconnect,
light control, print-status queue, gcode proxy) are covered by the other
``cc2/tests`` modules; these tests pin the connect/disconnect/listener/
request-response contract using the shared ``FakeAiomqttClient`` double
(ONE implementation, imported from ``tests/test_mqtt_client.py``).

Any failure while writing a pin means a previously-unknown bug, not a test
error.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.const import (
    CC2_CMD_GET_ATTRIBUTES,
    CC2_CMD_GET_STATUS,
    CC2_CMD_SET_VIDEO_STREAM,
    CC2_EVENT_STATUS,
    CC2_REG_OK,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterTimeoutError,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer
from custom_components.elegoo_printer.sdcp.models.status import PrinterStatus
from custom_components.elegoo_printer.sdcp.models.video import ElegooVideo
from custom_components.elegoo_printer.tests.test_mqtt_client import (
    FailingFakeAiomqttClient,
    FakeAiomqttClient,
)

# Named constants for PLR2004 suspects.
EXPECTED_SUBSCRIPTIONS = 3
NOZZLE_TEMP = 21.5


def _make_printer() -> Printer:
    """Build the printer object the client is connected with."""
    printer = Printer()
    printer.name = "CC2TestPrinter"
    printer.id = "test_serial"
    return printer


def _make_client() -> tuple[ElegooCC2Client, FakeAiomqttClient]:
    """Wire a client to the fake via the ``client_factory`` seam."""
    fake = FakeAiomqttClient()

    def factory(**kwargs: Any) -> FakeAiomqttClient:
        fake.kwargs.update(kwargs)
        return fake

    client = ElegooCC2Client(
        printer_ip="127.0.0.1",
        serial_number="test_serial",
        access_code="x",  # Skip the ""/"123456" password-fallback loop.
        logger=MagicMock(),
        client_factory=factory,
    )
    return client, fake


def _make_failing_client() -> tuple[ElegooCC2Client, FakeAiomqttClient]:
    """Wire a client whose fake raises OSError on connect."""
    fake = FailingFakeAiomqttClient()

    def factory(**kwargs: Any) -> FakeAiomqttClient:
        fake.kwargs.update(kwargs)
        return fake

    client = ElegooCC2Client(
        printer_ip="127.0.0.1",
        serial_number="test_serial",
        access_code="x",
        logger=MagicMock(),
        client_factory=factory,
    )
    return client, fake


async def _echo_cc2_responses(client: ElegooCC2Client, fake: FakeAiomqttClient) -> None:
    """
    Reply to every published api_request with a matching-id response.

    CC2 request ids are predictable (``self._request_counter`` starts at 0 and
    increments per send), but echoing from the recorded publish keeps this
    helper robust regardless of which endpoint initiated the request.
    """
    seen: set[int] = set()
    while True:
        for _topic, payload in list(fake.published):
            request = json.loads(payload)
            request_id = request.get("id")
            if request_id is None or request_id in seen:
                continue
            seen.add(request_id)
            fake.queue_message(
                f"elegoo/{client.serial_number}/api_response",
                {
                    "id": request_id,
                    "method": request.get("method"),
                    "result": {},
                },
            )
        await asyncio.sleep(0.01)


async def _wait_until(predicate: Any, max_wait: float = 5.0) -> None:
    """Wait (bounded) until the predicate holds."""
    deadline = asyncio.get_running_loop().time() + max_wait
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            msg = "predicate not met"
            raise TimeoutError(msg)
        await asyncio.sleep(0.01)


async def _make_connected(client: ElegooCC2Client, fake: FakeAiomqttClient) -> None:
    """Establish a connected+registered client without connect_printer."""
    fake.connected = True
    client.mqtt_client = fake
    client._connection_generation += 1
    client._is_connected = True
    client._is_registered = True
    client._listener_task = asyncio.create_task(client._mqtt_listener())


async def _teardown(client: ElegooCC2Client, echo: asyncio.Task | None) -> None:
    """Disconnect the client (cancelling listener + heartbeat) and stop echo."""
    await client.disconnect()
    if echo is not None:
        echo.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await echo


def _speed_up_wait_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap long ``asyncio.wait_for`` timeouts so tests run quickly."""
    real_wait_for = asyncio.wait_for

    async def fast_wait_for(
        fut: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Cap long wait_for timeouts (pos or keyword) so tests are fast."""
        delay = args[0] if args else kwargs.get("timeout")
        if delay is not None and delay > 1:
            if args:
                args = (0.1,)
            else:
                kwargs["timeout"] = 0.1
        return await real_wait_for(fut, *args, **kwargs)

    monkeypatch.setattr(asyncio, "wait_for", fast_wait_for)

    monkeypatch.setattr(asyncio, "wait_for", fast_wait_for)


async def test_connect_pins_contract_topics_and_initial_data() -> None:
    """Connect pins the auth kwargs, the CC2 topic set, and initial data."""
    client, fake = _make_client()
    # Pre-seed the registration response (topic contains "register_response").
    fake.queue_message(
        f"elegoo/{client.serial_number}/register_response",
        {"error": CC2_REG_OK},
    )
    echo = asyncio.create_task(_echo_cc2_responses(client, fake))
    try:
        result = await client.connect_printer(_make_printer())
        assert result is True
        assert client._is_connected is True
        assert client.is_connected is True  # Registered + transport open.
        assert fake.connected is True
        # User-provided access code is passed straight through as password.
        assert fake.kwargs["password"] == "x"  # noqa: S105
        assert fake.kwargs["username"] == "elegoo"
        assert client._is_registered is True
        assert len(fake.subscribed) == EXPECTED_SUBSCRIPTIONS
        assert fake.subscribed == [
            f"elegoo/{client.serial_number}/{client._client_id}/api_response",
            f"elegoo/{client.serial_number}/api_status",
            f"elegoo/{client.serial_number}/{client._request_id}/register_response",
        ]
        assert client._listener_task is not None
        assert client._heartbeat_task is not None
        # _request_initial_data: predictable ids 1 and 2, attributes first.
        api_requests = [
            json.loads(payload)
            for topic, payload in fake.published
            if topic.endswith("/api_request")
        ]
        assert [(r["id"], r["method"]) for r in api_requests] == [
            (1, CC2_CMD_GET_ATTRIBUTES),
            (2, CC2_CMD_GET_STATUS),
        ]
    finally:
        await _teardown(client, echo)

    assert client.is_connected is False
    assert client.mqtt_client is None
    assert client._is_registered is False
    assert fake.connected is False


async def test_connect_failure_returns_false_and_cleans_up() -> None:
    """A broker connection failure (OSError) must report False and clean up."""
    client, fake = _make_failing_client()
    result = await client.connect_printer(_make_printer())

    assert result is False
    assert client.is_connected is False
    assert client.mqtt_client is None
    assert client._is_registered is False
    assert fake.connected is False


async def test_disconnect_unblocks_waiters_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """disconnect() sets pending response events and closes the transport."""
    _speed_up_wait_for(monkeypatch)
    client, fake = _make_client()
    await _make_connected(client, fake)
    pending = asyncio.Event()
    client._response_events[9] = pending

    await client.disconnect()

    assert pending.is_set()
    assert client.is_connected is False
    assert client.mqtt_client is None
    assert client._is_registered is False
    assert client._response_events == {}
    assert fake.connected is False


async def test_send_command_resolves_on_matching_response() -> None:
    """A matching-id api_response completes the command round-trip."""
    client, fake = _make_client()
    await _make_connected(client, fake)
    echo = asyncio.create_task(_echo_cc2_responses(client, fake))
    try:
        result = await client._send_command(CC2_CMD_GET_ATTRIBUTES, {})
        assert result is not None
        assert result["method"] == CC2_CMD_GET_ATTRIBUTES
        assert result["id"] == 1
    finally:
        echo.cancel()
        await _teardown(client, None)


async def test_send_command_times_out_without_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No matching response must raise ElegooPrinterTimeoutError."""
    # _make_connected raises nothing here, keep the CC2 command timeout short.
    monkeypatch.setattr(
        "custom_components.elegoo_printer.cc2.client.CC2_COMMAND_TIMEOUT", 0.1
    )

    client, fake = _make_client()
    await _make_connected(client, fake)
    with pytest.raises(ElegooPrinterTimeoutError):
        await client._send_command(CC2_CMD_GET_ATTRIBUTES, {})

    await _teardown(client, None)


async def test_full_status_response_updates_printer_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A method-1002 response updates printer_data.status."""
    _speed_up_wait_for(monkeypatch)
    client, fake = _make_client()
    await _make_connected(client, fake)

    fake.queue_message(
        f"elegoo/{client.serial_number}/api_response",
        {
            "id": 999,
            "method": CC2_CMD_GET_STATUS,
            "result": {
                "sequence": 1,
                "machine_status": {"status": 0},
                "print_status": {"uuid": "task-uuid", "filename": "f.3mf"},
            },
        },
    )
    await _wait_until(
        lambda: client._cached_status.get("print_status", {}).get("uuid") == "task-uuid"
    )
    assert isinstance(client.printer_data.status, PrinterStatus)
    await _teardown(client, None)


async def test_delta_status_push_updates_printer_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 6000 status event (delta update) is applied to the cached status."""
    _speed_up_wait_for(monkeypatch)
    client, fake = _make_client()
    await _make_connected(client, fake)

    fake.queue_message(
        f"elegoo/{client.serial_number}/api_status",
        {
            "method": CC2_EVENT_STATUS,
            "result": {
                "sequence": 1,
                "machine_status": {"status": 0},
                "extruder": {"temperature": NOZZLE_TEMP, "target": 210},
            },
        },
    )
    await _wait_until(lambda: client._status_sequence == 1)
    assert isinstance(client.printer_data.status, PrinterStatus)
    assert client.printer_data.status.temp_of_nozzle == NOZZLE_TEMP
    await _teardown(client, None)


async def test_video_response_updates_video_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A video-stream response updates printer_data.video."""
    _speed_up_wait_for(monkeypatch)
    client, fake = _make_client()
    await _make_connected(client, fake)

    fake.queue_message(
        f"elegoo/{client.serial_number}/api_response",
        {
            "id": 998,
            "method": CC2_CMD_SET_VIDEO_STREAM,
            "result": {
                "error_code": 0,
                "video_url": "http://192.168.1.100:8080/?action=stream",
            },
        },
    )
    await _wait_until(
        lambda: client.printer_data.video.video_url
        == "http://192.168.1.100:8080/?action=stream"
    )
    assert isinstance(client.printer_data.video, ElegooVideo)
    assert await client.get_printer_status() is client.printer_data
    await _teardown(client, None)
