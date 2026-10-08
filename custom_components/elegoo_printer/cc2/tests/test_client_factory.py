"""
Characterization tests for the Elegoo CC2 client.

The shared-SDCP plumbing is made testable via the ``client_factory`` seam.

The CC2-only sides (auth fallback, connection generation, delayed disconnect,
light control, print-status queue, gcode proxy) are covered by the other
``cc2/tests`` modules; these tests pin the connect/disconnect/listener/
request-response contract using the shared ``FakeAiomqttClient`` double
(ONE implementation, imported from ``tests/fakes.py``).

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
from custom_components.elegoo_printer.tests.fakes import (
    FailingFakeAiomqttClient,
    FakeAiomqttClient,
)

# Named constants for PLR2004 suspects.
EXPECTED_SUBSCRIPTIONS = 3
NOZZLE_TEMP = 21.5
PRINTER_IP = "10.0.0.9"
PROXY_IP = "10.0.0.5"
PRINTER_STREAM_URL = "http://10.0.0.9:8080/?action=stream"
PROXY_STREAM_URL = "http://10.0.0.5:8080/?action=stream"
PASSWORD_FALLBACK_COUNT = 2
SHORT_TIMEOUT = 0.2


def _make_printer(*, proxy_host: str | None = None) -> Printer:
    """Build the printer object the client is connected with."""
    printer = Printer()
    printer.name = "CC2TestPrinter"
    printer.id = "test_serial"
    printer.ip_address = PRINTER_IP
    printer.proxy_host = proxy_host
    return printer


def _make_client(
    printer: Printer | None = None,
) -> tuple[ElegooCC2Client, FakeAiomqttClient]:
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
        printer=printer,
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


# --- connection_host routing (#414) -------------------------------------------------


async def test_connect_printer_targets_proxy_when_proxy_host_set() -> None:
    """A printer with proxy_host routes the MQTT connection at the proxy."""
    client, fake = _make_client()
    fake.queue_message(
        f"elegoo/{client.serial_number}/register_response",
        {"error": CC2_REG_OK},
    )
    echo = asyncio.create_task(_echo_cc2_responses(client, fake))
    try:
        assert (
            await client.connect_printer(
                _make_printer(proxy_host=PROXY_IP),
            )
            is True
        )
        assert client.printer_ip == PROXY_IP
        assert fake.kwargs["hostname"] == PROXY_IP
    finally:
        await _teardown(client, echo)


async def test_connect_printer_targets_printer_ip_without_proxy_host() -> None:
    """Without proxy_host the MQTT connection targets the printer IP itself."""
    client, fake = _make_client()
    fake.queue_message(
        f"elegoo/{client.serial_number}/register_response",
        {"error": CC2_REG_OK},
    )
    echo = asyncio.create_task(_echo_cc2_responses(client, fake))
    try:
        assert await client.connect_printer(_make_printer()) is True
        assert client.printer_ip == PRINTER_IP
        assert fake.kwargs["hostname"] == PRINTER_IP
    finally:
        await _teardown(client, echo)


async def _video_url_for(
    monkeypatch: pytest.MonkeyPatch,
    *,
    proxy_host: str | None,
    response_result: dict[str, Any],
) -> str:
    """Drive a video-stream response through the listener, return the URL used."""
    _speed_up_wait_for(monkeypatch)
    printer = _make_printer(proxy_host=proxy_host)
    # _handle_video_response builds from self.printer_ip, which in production
    # connect_printer derives from printer.connection_host.
    client, fake = _make_client(printer)
    client.printer_ip = printer.connection_host or client.printer_ip
    await _make_connected(client, fake)
    # PrinterData pre-seeds an empty ElegooVideo; clear it so the wait below
    # observes the response under test rather than the default.
    client.printer_data.video = None

    fake.queue_message(
        f"elegoo/{client.serial_number}/api_response",
        {"id": 997, "method": CC2_CMD_SET_VIDEO_STREAM, "result": response_result},
    )
    await _wait_until(lambda: client.printer_data.video is not None)
    video_url = client.printer_data.video.video_url
    await _teardown(client, None)
    return video_url


async def test_video_response_ignores_printer_url_when_proxied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With proxy_host set, the stream URL is built from the proxy host."""
    video_url = await _video_url_for(
        monkeypatch,
        proxy_host=PROXY_IP,
        response_result={"error_code": 0, "video_url": PRINTER_STREAM_URL},
    )

    assert video_url == PROXY_STREAM_URL


async def test_video_response_prefers_printer_url_without_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without proxy_host the printer-supplied URL stays preferred."""
    video_url = await _video_url_for(
        monkeypatch,
        proxy_host=None,
        response_result={"error_code": 0, "video_url": PRINTER_STREAM_URL},
    )

    assert video_url == PRINTER_STREAM_URL


async def test_video_response_builds_fallback_url_without_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without proxy_host and without a supplied URL, the default is built."""
    video_url = await _video_url_for(
        monkeypatch,
        proxy_host=None,
        response_result={"error_code": 0},
    )

    assert video_url == PRINTER_STREAM_URL


async def test_video_response_does_not_override_on_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proxy URL override is gated on success — an error yields no URL."""
    video_url = await _video_url_for(
        monkeypatch,
        proxy_host=PROXY_IP,
        response_result={"error_code": 1},
    )

    assert video_url == ""


# --- bounded serial auto-learn (#414) -----------------------------------------------


def _make_serial_probe_client(
    *,
    access_code: str | None = "x",
    fake: FakeAiomqttClient | None = None,
) -> tuple[ElegooCC2Client, FakeAiomqttClient, list[dict[str, Any]]]:
    """
    Build a client for ``async_discover_serial``.

    Returns the client, the fake, and the per-attempt constructor kwargs, so a
    test can see how many passwords were tried and with what.
    """
    fake = fake or FakeAiomqttClient()
    attempts: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> FakeAiomqttClient:
        attempts.append(dict(kwargs))
        fake.kwargs.update(kwargs)
        return fake

    client = ElegooCC2Client(
        printer_ip=PROXY_IP,
        # The whole point of the auto-learn: the serial is not known yet.
        serial_number="",
        access_code=access_code,
        logger=MagicMock(),
        client_factory=factory,
        printer=_make_printer(proxy_host=PROXY_IP),
    )
    return client, fake, attempts


async def test_discover_serial_reads_sn_from_wildcard_status_topic() -> None:
    """The serial comes from the ``elegoo/{sn}/api_status`` topic name."""
    client, fake, attempts = _make_serial_probe_client()
    fake.queue_message("elegoo/SN9X7/status-ignored-payload", {})
    fake.queue_message("elegoo/SN9X7/api_status", {"seq": 1})

    serial = await client.async_discover_serial(wait_timeout=1.0)

    assert serial == "SN9X7"
    # Subscribed to the wildcard, not a serial-specific topic.
    assert "elegoo/+/api_status" in fake.subscribed
    assert f"elegoo/{client.serial_number}/api_status" not in fake.subscribed
    # Connected to the proxy host with the CC2 username.
    assert attempts[0]["hostname"] == PROXY_IP
    assert attempts[0]["username"] == "elegoo"
    assert client.mqtt_client is None


async def test_discover_serial_uses_only_the_provided_access_code() -> None:
    """An explicit access code is the only password tried (mirrors connect)."""
    client, fake, attempts = _make_serial_probe_client(access_code="secret")
    fake.queue_message("elegoo/SN1/api_status", {})

    assert await client.async_discover_serial(wait_timeout=1.0) == "SN1"
    assert len(attempts) == 1
    assert attempts[0]["password"] == "secret"  # noqa: S105


async def test_discover_serial_tries_fallback_passwords_without_access_code() -> None:
    """Without an access code, the empty/``123456`` fallbacks are tried in order."""
    client, _unused_fake, attempts = _make_serial_probe_client(access_code=None)

    assert await client.async_discover_serial(wait_timeout=0.05) is None
    assert [a["password"] for a in attempts] == ["", "123456"]


async def test_discover_serial_returns_none_on_timeout() -> None:
    """A printer that pushes nothing within the budget yields None, not a raise."""
    client, _unused_fake, _attempts = _make_serial_probe_client()

    assert await client.async_discover_serial(wait_timeout=0.05) is None


async def test_discover_serial_never_publishes_a_registration() -> None:
    """The probe must not register: an empty serial means ``elegoo//api_register``."""
    client, fake, _attempts = _make_serial_probe_client()
    fake.queue_message("elegoo/SN2/api_status", {})

    assert await client.async_discover_serial(wait_timeout=1.0) == "SN2"

    assert not [topic for topic, _payload in fake.published if "api_register" in topic]
    assert fake.published == []


async def test_discover_serial_disconnects_after_success() -> None:
    """The transient connection is closed when a serial was found."""
    client, fake, _attempts = _make_serial_probe_client()
    fake.queue_message("elegoo/SN3/api_status", {})

    await client.async_discover_serial(wait_timeout=1.0)

    assert fake.connected is False
    assert client.mqtt_client is None


async def test_discover_serial_disconnects_after_timeout() -> None:
    """The transient connection is closed even when nothing arrived."""
    client, fake, _attempts = _make_serial_probe_client()

    await client.async_discover_serial(wait_timeout=0.05)

    assert fake.connected is False
    assert client.mqtt_client is None


async def test_discover_serial_ignores_non_matching_topics() -> None:
    """Only ``elegoo/{sn}/api_status`` counts; other shapes are skipped."""
    client, fake, _attempts = _make_serial_probe_client()
    fake.queue_message("elegoo/status", {})
    fake.queue_message("elegoo/SN4/api_other", {})
    fake.queue_message("elegoo/a/b/c", {})

    assert await client.async_discover_serial(wait_timeout=SHORT_TIMEOUT) is None


async def test_discover_serial_survives_an_unreachable_proxy() -> None:
    """A refused/unreachable proxy returns None instead of crashing the flow."""
    client, _fake, attempts = _make_serial_probe_client(
        access_code=None,
        fake=FailingFakeAiomqttClient(),
    )

    assert await client.async_discover_serial(wait_timeout=0.05) is None
    assert client.mqtt_client is None
    assert len(attempts) == PASSWORD_FALLBACK_COUNT
