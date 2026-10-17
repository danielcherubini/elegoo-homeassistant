"""
A failed reconnect may never revive a printer that is still switched off (#171).

An entry that came up offline (the "Printer is often switched off" option) is
polled every 30 s, and each failed poll calls ``api.reconnect()``. For the
default MQTT topology — the embedded broker, which is ours and therefore always
answers the reachability probe — ``reconnect()`` goes straight to
``ElegooMQTTClient.connect_printer``, which raises ``_is_connected`` *before* the
handshake (``mqtt/client.py:273``) and whose own ``except`` covers only
``(asyncio.TimeoutError, OSError, aiomqtt.MqttError)``. A powered-off printer
never answers the handshake, so the transport raises
``ElegooPrinterTimeoutError`` (``sdcp/transport/base.py:253``) — which none of
those cover, and which is not a builtin ``TimeoutError`` — out of
``connect_printer`` with the flag raised and the broker link still open.

``reconnect()`` used to call ``connect_printer`` unguarded, so that exception
also escaped ``reconnect()`` and then escaped the coordinator's
``except (ConnectionError, TimeoutError)`` around the reconnect call. Home
Assistant's coordinator has a catch-all, and it reports anything that reaches it
as ``Unexpected error fetching <printer> data`` with a full traceback. Worse, the
half-open client then passes the poll gate on the *next* poll, answers from its
empty cache, and is published as a live reading — and nothing calls
``reconnect()`` again, so the printer that was simply switched off is now
"healthy" at 0 °C and idle forever.

These tests drive a real ``ElegooPrinterApiClient`` over a real
``ElegooMQTTClient`` with only the sockets faked, so the flag flip is the
transport's own. ``api.reconnect`` is deliberately never mocked: the polling
suite that mocked it is why this slipped through.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiomqtt

import custom_components.elegoo_printer.api as api_module
from custom_components.elegoo_printer.api import ElegooPrinterApiClient
from custom_components.elegoo_printer.const import (
    CONF_ALLOW_PRINTER_OFFLINE,
    CONF_HAS_CANVAS,
    CONF_MQTT_BROKER_ENABLED,
)
from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.mqtt.client import ElegooMQTTClient
from custom_components.elegoo_printer.sdcp.exceptions import ElegooPrinterTimeoutError
from custom_components.elegoo_printer.sdcp.models.printer import PrinterData
from custom_components.elegoo_printer.tests.fakes import FakeAiomqttClient

if TYPE_CHECKING:
    from types import SimpleNamespace

EMBEDDED_BROKER_PORT = 1884

#: An MQTT printer on the default embedded broker, tolerant of being switched
#: off, with no address so ``connect_printer`` skips the UDP redirect preamble.
_BASE_CONFIG: dict[str, Any] = {
    "name": "Printer",
    "id": "SERIAL123",
    "connection": "CONNECTION123",
    "model": "Saturn 4",
    "ip_address": "",
    "protocol": "V1.0.0",
    "protocol_version": "V1",
    "transport_type": "mqtt",
    CONF_MQTT_BROKER_ENABLED: True,
    CONF_ALLOW_PRINTER_OFFLINE: True,
}


def _config(**overrides: Any) -> dict[str, Any]:
    return {**_BASE_CONFIG, **overrides}


class _Writer:
    """The socket writer half of a successful broker probe."""

    def close(self) -> None:
        """No-op."""

    async def wait_closed(self) -> None:
        """No-op."""


@contextlib.contextmanager
def _broker_up_printer_down(handshake_error: Exception) -> Any:
    """
    Run with the embedded broker answering and the printer answering nothing.

    The broker is ours, so both reachability probes succeed and every connect
    attempt reaches ``connect_printer``; only the SDCP handshake fails, exactly
    as it does for a printer that is powered down. Left open for the whole test:
    a reconnect that ran with these patches released would fail the probe instead
    of the handshake, and prove nothing.
    """
    broker = MagicMock(port=EMBEDDED_BROKER_PORT, host="localhost")
    with (
        patch.object(api_module, "async_get_clientsession", return_value=MagicMock()),
        patch.object(api_module, "get_async_client", return_value=MagicMock()),
        patch.object(
            api_module.ElegooMQTTBroker,
            "get_instance",
            new=AsyncMock(return_value=broker),
        ),
        patch.object(api_module.ElegooMQTTBroker, "release_instance", new=AsyncMock()),
        patch(
            "asyncio.open_connection",
            new=AsyncMock(return_value=(MagicMock(), _Writer())),
        ),
        patch.object(
            ElegooPrinterApiClient,
            "_update_config_entry_if_needed",
            new=AsyncMock(),
        ),
        patch.object(
            ElegooMQTTClient,
            "_send_printer_cmd",
            new=AsyncMock(side_effect=handshake_error),
        ),
    ):
        yield


@contextlib.asynccontextmanager
async def _offline_mqtt_api(hass: MagicMock) -> Any:
    """
    Yield a *real* api client that came up offline the real MQTT way.

    ``async_create`` opens the broker link, the handshake times out, and the
    tolerated offline exit hands back a genuinely disconnected client — the
    starting state of every entry that ticked the option. The patches stay
    installed for the body of the ``async with``.
    """
    fake = FakeAiomqttClient()
    with (
        _broker_up_printer_down(ElegooPrinterTimeoutError("no handshake")),
        patch.object(aiomqtt, "Client", return_value=fake),
    ):
        api = await ElegooPrinterApiClient.async_create(
            _config(), MagicMock(), hass, None
        )
        assert api is not None
        assert api.offline_setup is True
        assert api.is_connected is False
        yield api


def _coordinator_over(
    hass: MagicMock,
    entry: SimpleNamespace,
    api: ElegooPrinterApiClient,
    *,
    cached_data: Any = None,
) -> ElegooDataUpdateCoordinator:
    """
    Wire ``api`` into the entry as its runtime data and build a real coordinator.

    Only the *data fetch* is stubbed, and with what the transport really hands
    back: the MQTT client answers ``get_printer_status``/
    ``get_printer_attributes`` from its cache without raising
    (``mqtt/client.py:337-376``), which is precisely why the poll gate exists.
    ``api.reconnect``, ``api.is_connected`` and the transport underneath them all
    stay real — that is the whole point of this file.
    """
    entry.options = {CONF_HAS_CANVAS: False}
    entry.async_on_unload = MagicMock()
    entry.pref_disable_polling = False
    api.async_get_printer_data = AsyncMock(
        return_value=cached_data if cached_data is not None else api.client.printer_data
    )
    api.async_get_firmware_update_info = AsyncMock(return_value=None)
    entry.runtime_data.api = api
    return ElegooDataUpdateCoordinator(hass, entry=entry)


def _loud_logs(caplog: Any) -> list[logging.LogRecord]:
    """Return every record at WARNING or above, from any logger involved."""
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_a_handshake_timeout_during_reconnect_leaves_the_client_offline(
    hass: MagicMock,
) -> None:
    """
    ``reconnect()`` must absorb the handshake timeout and report failure.

    This is the reviewer's sequence: an offline MQTT client whose
    ``connect_printer`` raises ``ElegooPrinterTimeoutError`` mid-handshake. The
    flag the transport raised before the handshake has to be taken back down,
    because ``is_connected`` is the only signal the poll gate has.
    """
    async with _offline_mqtt_api(hass) as api:
        assert await api.reconnect() is False, (
            "a failed reconnect must report failure, not raise out of reconnect()"
        )
        assert api.client.is_connected is False
        assert api.is_connected is False
        assert api.client.mqtt_client is None, "half-open transport was left open"


async def test_a_connect_attempt_that_reports_failure_closes_its_transport(
    hass: MagicMock,
) -> None:
    """
    A ``connect_printer`` that returns False may not leave the link open either.

    The transports that tear down before returning False are covered by the test
    above, but the CC2 password loop breaks out of its attempts *without*
    disconnecting when a user-provided access code fails auth
    (``cc2/client.py:340-356``), so a client can come back from a returned
    ``False`` still reporting ``is_connected``. ``reconnect()`` closes
    defensively rather than trusting each transport to have tidied up.
    """
    async with _offline_mqtt_api(hass) as api:

        async def half_open_then_false(_self: Any, _printer: Any) -> bool:
            """Simulate a transport that raises its own flag and then says "no"."""
            api.client._is_connected = True
            api.client.mqtt_client = FakeAiomqttClient()
            return False

        with patch.object(
            ElegooMQTTClient, "connect_printer", new=half_open_then_false
        ):
            assert await api.reconnect() is False

        assert api.is_connected is False
        assert api.client.is_connected is False


async def test_a_failed_reconnect_does_not_make_the_next_poll_succeed(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """
    Two polls of a switched-off printer must say the same thing.

    Poll 1 fails quietly and calls ``reconnect()``; the handshake timeout used to
    escape that call, leaving the client half-open, and poll 2 then passed the
    gate, served the empty cache as live data and marked the entry healthy
    (``last_update_success is True``, ``online is True``, the 2 s interval). The
    entities came back ``available`` with fabricated defaults, and
    ``reconnect()`` never ran again — the promise #171 makes, broken.
    """
    async with _offline_mqtt_api(hass) as api:
        coordinator = _coordinator_over(hass, entry, api)
        # The real offline entry point used by ``async_setup_entry``.
        coordinator.async_start_offline()
        # Guard the premise of this file: if ``reconnect`` is ever mocked out
        # here, the bug this test exists to pin becomes invisible again.
        assert api.reconnect.__func__ is ElegooPrinterApiClient.reconnect

        await coordinator.async_refresh()
        first = (coordinator.last_update_success, coordinator.online)
        assert first == (False, False)

        await coordinator.async_refresh()

        assert (coordinator.last_update_success, coordinator.online) == first, (
            "poll 2 disagreed with poll 1: the failed reconnect revived the client"
        )
        assert coordinator.last_update_success is False
        assert coordinator.online is False
        assert coordinator.update_interval == timedelta(seconds=30)
        assert api.is_connected is False, "the half-open transport was left open"


async def test_a_reconnect_that_answers_still_brings_the_printer_back(
    hass: MagicMock,
) -> None:
    """
    The guard must not swallow the recovery it exists beside.

    The whole point of the option is that a printer powers on and comes back by
    itself, so the successful branch of ``reconnect()`` has to stay reachable:
    once the handshake is answered again the client reconnects and reports
    ``is_connected``, and the next poll serves real data.
    """
    async with _offline_mqtt_api(hass) as api:
        with patch.object(ElegooMQTTClient, "_send_printer_cmd", new=AsyncMock()):
            assert await api.reconnect() is True

        assert api.client.is_connected is True
        assert api.is_connected is True

        await api.client.disconnect()


def _reconnect_raises_api() -> MagicMock:
    """
    Build an api double whose ``reconnect`` raises the way the MQTT transport does.

    Used only by the test that pins the coordinator's own guard, where the api is
    deliberately the misbehaving one.
    """
    api = MagicMock()
    api.is_connected = False
    api.client = MagicMock()
    api.printer.transport_type = object()
    api.async_get_printer_data = AsyncMock(return_value=PrinterData())
    api.async_get_firmware_update_info = AsyncMock(return_value=None)
    api.reconnect = AsyncMock(
        side_effect=ElegooPrinterTimeoutError("no handshake")  # not a builtin
    )
    return api


async def test_a_reconnect_that_raises_is_still_contained_by_the_coordinator(
    hass: MagicMock, entry: SimpleNamespace, caplog: Any
) -> None:
    """
    Even a ``reconnect()`` that misbehaves may not reach Home Assistant's net.

    The api layer now absorbs its own transport errors, but the coordinator is the
    last place that can stop one, so its guard covers the transport errors too —
    not just the builtin ``ConnectionError``/``TimeoutError`` it used to name.
    Here the api double raises the way the MQTT transport does, and the poll must
    still end as a quiet failure: ``UpdateFailed``, one WARNING, no traceback.
    """
    coordinator = _coordinator_over(
        hass, entry, _reconnect_raises_api(), cached_data=PrinterData()
    )
    coordinator.async_start_offline()

    with caplog.at_level(logging.DEBUG):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.online is False
    loud = [r for r in caplog.records if r.levelno > logging.WARNING]
    assert not loud, [f"{r.levelname}: {r.getMessage()}" for r in loud]
    assert "Unexpected error fetching" not in caplog.text


async def test_two_failed_polls_log_nothing_above_warning(
    hass: MagicMock, entry: SimpleNamespace, caplog: Any
) -> None:
    """
    A switched-off printer may not put a traceback in the log (#171's report).

    Anything escaping ``_async_update_data`` reaches Home Assistant's catch-all,
    which prints ``Unexpected error fetching <printer> data`` at ERROR with the
    full traceback — the noise the option exists to remove, back with a vengeance
    on the 30 s retry cadence.
    """
    async with _offline_mqtt_api(hass) as api:
        coordinator = _coordinator_over(hass, entry, api)
        coordinator.async_start_offline()

        with caplog.at_level(logging.DEBUG):
            await coordinator.async_refresh()
            await coordinator.async_refresh()

        loud = _loud_logs(caplog)
        assert not loud, [f"{r.name} {r.levelname}: {r.getMessage()}" for r in loud]
        assert "Unexpected error fetching" not in caplog.text
