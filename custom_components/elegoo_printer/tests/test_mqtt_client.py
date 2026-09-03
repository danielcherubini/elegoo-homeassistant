"""
Characterization tests for the SDCP-over-MQTT client transport.

These tests pin the CURRENT behavior of ``ElegooMQTTClient`` using an injected
``client_factory`` fake (never a real ``aiomqtt.Client``). Any failure while
writing a pin means a previously-unknown bug, not a test error.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.elegoo_printer.mqtt.client import ElegooMQTTClient
from custom_components.elegoo_printer.mqtt.const import (
    TOPIC_ATTRIBUTES,
    TOPIC_ERROR,
    TOPIC_NOTICE,
    TOPIC_PREFIX,
    TOPIC_RESPONSE,
    TOPIC_STATUS,
)
from custom_components.elegoo_printer.sdcp.const import (
    CMD_CONTROL_DEVICE,
    CMD_RETRIEVE_HISTORICAL_TASKS,
    CMD_SET_VIDEO_STREAM,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterTimeoutError,
)
from custom_components.elegoo_printer.sdcp.models.attributes import PrinterAttributes
from custom_components.elegoo_printer.sdcp.models.printer import Printer
from custom_components.elegoo_printer.sdcp.models.status import PrinterStatus
from custom_components.elegoo_printer.sdcp.models.video import ElegooVideo
from custom_components.elegoo_printer.tests.fakes import (
    FailingFakeAiomqttClient,
    FakeAiomqttClient,
)


def _make_printer() -> Printer:
    """Build a printer with no IP so the MQTT connect skips its UDP preamble."""
    printer = Printer()
    printer.name = "TestPrinter"
    printer.id = "test_mainboard_id"
    return printer


def _make_client(fake: FakeAiomqttClient) -> ElegooMQTTClient:
    """Wire a client to the fake via the ``client_factory`` seam."""

    def factory(**kwargs: Any) -> FakeAiomqttClient:
        fake.kwargs.update(kwargs)
        return fake

    return ElegooMQTTClient(
        mqtt_host="127.0.0.1",
        mqtt_port=1883,
        logger=MagicMock(),
        client_factory=factory,
    )


async def _echo_responses(client: ElegooMQTTClient, fake: FakeAiomqttClient) -> None:
    """
    Reply to every published request with a matching-RequestID response.

    The clients use unpredictable ``secrets.token_hex(8)`` request IDs, so a
    silent fake would block 10 s per handshake command and then raise an
    uncaught ``ElegooPrinterTimeoutError`` out of ``connect_printer``.
    """
    seen: set[str] = set()
    while True:
        for _topic, payload in list(fake.published):
            request = json.loads(payload)
            data = request.get("Data", {})
            request_id = data.get("RequestID")
            if request_id is None or request_id in seen:
                continue
            seen.add(request_id)
            fake.queue_message(
                f"/{TOPIC_PREFIX}/{TOPIC_RESPONSE}/{client.printer.id}",
                {
                    "Id": request.get("Id"),
                    "Data": {
                        "Cmd": data.get("Cmd", 0),
                        "Data": data.get("Data", {}),
                        "RequestID": request_id,
                    },
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


async def _make_connected(client: ElegooMQTTClient, fake: FakeAiomqttClient) -> None:
    """Establish a connected client without going through connect_printer."""
    fake.connected = True
    client.mqtt_client = fake
    client._is_connected = True
    client._listener_task = asyncio.create_task(client._mqtt_listener())


async def _teardown(client: ElegooMQTTClient, echo: asyncio.Task | None) -> None:
    """Cancel the echo helper and disconnect the client."""
    if echo is not None:
        echo.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await echo
    await client.disconnect()


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


async def test_connect_success_pins_contract_and_topic_strings() -> None:
    """Connect pins the broker kwargs, the 5 leading-slash topics, handshake."""
    fake = FakeAiomqttClient()
    client = _make_client(fake)
    echo = asyncio.create_task(_echo_responses(client, fake))
    try:
        result = await client.connect_printer(_make_printer())
        assert result is True
        assert client.is_connected is True
        assert fake.connected is True
        assert fake.kwargs == {
            "hostname": "127.0.0.1",
            "port": 1883,
            "keepalive": 60,
        }
        # Pin the per-transport topic divergence: the SDCP-over-MQTT transport
        # requires a LEADING SLASH on every subscribed topic (the printer-side
        # subscription pattern matches on it).
        assert len(fake.subscribed) == 5
        assert fake.subscribed == [
            f"/{TOPIC_PREFIX}/{TOPIC_RESPONSE}/{client.printer.id}",
            f"/{TOPIC_PREFIX}/{TOPIC_STATUS}/{client.printer.id}",
            f"/{TOPIC_PREFIX}/{TOPIC_ATTRIBUTES}/{client.printer.id}",
            f"/{TOPIC_PREFIX}/{TOPIC_NOTICE}/{client.printer.id}",
            f"/{TOPIC_PREFIX}/{TOPIC_ERROR}/{client.printer.id}",
        ]
        handshake_commands = [
            json.loads(payload)["Data"]["Cmd"]
            for topic, payload in fake.published
            if topic == f"/sdcp/request/{client.printer.id}"
        ]
        assert handshake_commands == [0, 1, 512]  # refresh, attributes, 2s period
        assert await client.get_printer_status() is client.printer_data
    finally:
        # Keep the echo alive while disconnecting so the final
        # CMD_DISCONNECT is answered instead of timing out 10 s.
        await client.disconnect()
        echo.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await echo

    assert fake.connected is False
    assert client.mqtt_client is None
    assert client.is_connected is False


async def test_connect_failure_returns_false_and_cleans_up() -> None:
    """A broker connection failure (OSError) must report False and clean up."""
    fake = FailingFakeAiomqttClient()
    client = _make_client(fake)
    result = await client.connect_printer(_make_printer())

    assert result is False
    assert client.is_connected is False
    assert client.mqtt_client is None
    assert fake.connected is False


async def test_disconnect_unblocks_waiters_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """disconnect() sets pending response events, closes the transport."""
    _speed_up_wait_for(monkeypatch)
    fake = FakeAiomqttClient()
    client = _make_client(fake)
    await _make_connected(client, fake)
    pending = asyncio.Event()
    client._response_events["pending-request"] = pending

    await client.disconnect()

    assert pending.is_set()
    assert client.is_connected is False
    assert client.mqtt_client is None
    assert client._response_events == {}
    assert fake.connected is False


async def test_send_printer_cmd_resolves_on_matching_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _speed_up_wait_for(monkeypatch)
    """A pre-seeded matching-RequestID response completes the command."""
    fake = FakeAiomqttClient()
    client = _make_client(fake)
    await _make_connected(client, fake)

    cmd_task = asyncio.create_task(
        client._send_printer_cmd(
            CMD_CONTROL_DEVICE, {"TargetFanSpeed": {"auxiliary": 50}}
        )
    )
    await _wait_until(lambda: len(client._response_events) == 1)
    request_id = next(iter(client._response_events))
    fake.queue_message(
        f"/{TOPIC_PREFIX}/{TOPIC_RESPONSE}/{client.printer.id}",
        {
            "Id": "conn",
            "Data": {
                "Cmd": CMD_CONTROL_DEVICE,
                "Data": {},
                "RequestID": request_id,
            },
        },
    )
    await cmd_task  # No timeout: response was pre-seeded.

    published = [
        (topic, payload)
        for topic, payload in fake.published
        if topic == f"/sdcp/request/{client.printer.id}"
    ]
    assert published, "The command frame must be recorded on the user topic"
    frame = json.loads(published[-1][1])
    assert frame["Data"]["Cmd"] == CMD_CONTROL_DEVICE
    assert frame["Data"]["RequestID"] == request_id
    await _teardown(client, None)


async def test_send_printer_cmd_times_out_without_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No matching response must raise ElegooPrinterTimeoutError."""
    _speed_up_wait_for(monkeypatch)
    fake = FakeAiomqttClient()
    client = _make_client(fake)
    await _make_connected(client, fake)

    with pytest.raises(ElegooPrinterTimeoutError):
        await client._send_printer_cmd(CMD_CONTROL_DEVICE, {"a": 1})

    await _teardown(client, None)


async def test_status_push_updates_printer_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _speed_up_wait_for(monkeypatch)
    """A status push frame updates printer_data.status."""
    fake = FakeAiomqttClient()
    client = _make_client(fake)
    await _make_connected(client, fake)

    fake.queue_message(
        f"/{TOPIC_PREFIX}/{TOPIC_STATUS}/{client.printer.id}",
        {
            "Data": {
                "Status": {
                    "MachineStatus": 0,
                    "TempOfNozzle": 60.5,
                    "PrintInfo": {"Status": 4, "Progress": 42},
                }
            }
        },
    )
    await _wait_until(lambda: client.printer_data.status.temp_of_nozzle == 60.5)
    assert await client.get_printer_status() is client.printer_data
    assert isinstance(client.printer_data.status, PrinterStatus)
    await _teardown(client, None)


async def test_attributes_push_updates_printer_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An attributes push frame re-assigns printer_data.attributes."""
    _speed_up_wait_for(monkeypatch)
    fake = FakeAiomqttClient()
    client = _make_client(fake)
    await _make_connected(client, fake)
    default_attributes = client.printer_data.attributes

    fake.queue_message(
        f"/{TOPIC_PREFIX}/{TOPIC_ATTRIBUTES}/{client.printer.id}",
        {
            "Data": {
                "Attributes": {
                    "MachineName": "Saturn 4",
                    "FirmwareVersion": "V1.2.3",
                    "Name": "TestPrinter",
                }
            }
        },
    )
    await _wait_until(lambda: client.printer_data.attributes is not default_attributes)
    assert await client.get_printer_attributes() is client.printer_data
    assert isinstance(client.printer_data.attributes, PrinterAttributes)
    assert client.printer_data.attributes.firmware_version == "V1.2.3"
    assert client.printer_data.attributes.machine_name == "Saturn 4"
    await _teardown(client, None)


async def test_print_history_response_populates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _speed_up_wait_for(monkeypatch)
    """A print-history response frame seeds the print_history cache."""
    fake = FakeAiomqttClient()
    client = _make_client(fake)
    await _make_connected(client, fake)

    fake.queue_message(
        f"/{TOPIC_PREFIX}/{TOPIC_RESPONSE}/{client.printer.id}",
        {
            "Id": "conn",
            "Data": {
                "Cmd": CMD_RETRIEVE_HISTORICAL_TASKS,
                "Data": {"HistoryData": ["task-1", "task-2"]},
            },
        },
    )
    await _wait_until(lambda: client.printer_data.print_history)
    assert set(client.printer_data.print_history) == {"task-1", "task-2"}
    await _teardown(client, None)


async def test_print_video_response_updates_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _speed_up_wait_for(monkeypatch)
    """A video-stream response frame updates printer_data.video."""
    fake = FakeAiomqttClient()
    client = _make_client(fake)
    await _make_connected(client, fake)

    fake.queue_message(
        f"/{TOPIC_PREFIX}/{TOPIC_RESPONSE}/{client.printer.id}",
        {
            "Id": "conn",
            "Data": {
                "Cmd": CMD_SET_VIDEO_STREAM,
                "Data": {"Ack": 0, "VideoUrl": "http://printer:8080/?action=stream"},
            },
        },
    )
    await _wait_until(
        lambda: client.printer_data.video.video_url
        == "http://printer:8080/?action=stream"
    )
    assert isinstance(client.printer_data.video, ElegooVideo)
    assert await client.get_printer_status() is client.printer_data
    await _teardown(client, None)
