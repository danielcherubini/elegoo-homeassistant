"""
Offline-tolerant runtime setup (#171).

By default the config entry fails with ``ConfigEntryNotReady`` when the printer
cannot be reached, which Home Assistant renders as "Failed setup, will retry".
For a printer that is switched off most of the time that is a permanent error
state, so ``CONF_ALLOW_PRINTER_OFFLINE`` lets setup finish anyway with a
disconnected client; the entities then come up ``unavailable``.

The offline exit path keeps the embedded broker reference it already acquired:
the entry stays loaded until the user turns the option off, so releasing it
here would leak it (``async_unload_entry`` only releases what
``runtime_data.api`` still holds) and would deny the printer its own reconnect
path back to the broker.
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import custom_components.elegoo_printer.api as api_module
from custom_components.elegoo_printer.api import ElegooPrinterApiClient
from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.const import CONF_ALLOW_PRINTER_OFFLINE
from custom_components.elegoo_printer.mqtt.client import ElegooMQTTClient
from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient

PRINTER_IP = "10.0.0.9"
EMBEDDED_BROKER_PORT = 1884


class _Writer:
    """The socket writer half of a successful reachability probe."""

    def close(self) -> None:
        """No-op."""

    async def wait_closed(self) -> None:
        """No-op."""


def _writer() -> _Writer:
    return _Writer()


_BASE_CONFIG: dict[str, Any] = {
    "name": "Printer",
    "id": "SERIAL123",
    "connection": "CONNECTION123",
    "model": "Centauri Carbon",
    "ip_address": PRINTER_IP,
    "protocol": "V3.0.0",
    "protocol_version": "V3",
    "transport_type": "websocket",
}


def _config(**overrides: Any) -> dict[str, Any]:
    return {**_BASE_CONFIG, **overrides}


def _hass() -> MagicMock:
    """Build a hass double whose executor jobs run inline on the loop."""
    hass = MagicMock()

    async def _run_in_executor(func: Any, *args: Any) -> Any:
        return func(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=_run_in_executor)
    return hass


@contextlib.contextmanager
def _offline_websocket() -> Any:
    """Patch out every network path so the printer answers nothing."""
    with (
        patch.object(api_module, "async_get_clientsession", return_value=MagicMock()),
        patch.object(api_module, "get_async_client", return_value=MagicMock()),
        # Neither the unicast nor the broadcast discovery answers: printer off.
        patch.object(ElegooPrinterClient, "discover_printer", return_value=[]),
        patch.object(
            ElegooPrinterApiClient, "_update_config_entry_if_needed", new=AsyncMock()
        ),
    ):
        yield


async def _create_unreachable(config: dict[str, Any]) -> ElegooPrinterApiClient | None:
    """Run ``async_create`` against a printer that answers nothing."""
    with _offline_websocket():
        return await ElegooPrinterApiClient.async_create(
            config, MagicMock(), _hass(), None
        )


class TestUnreachablePrinter:
    """The WebSocket/SDCP reachability test decides, unless the option is on."""

    async def test_without_option_setup_fails(self) -> None:
        """Default behaviour is unchanged: no client, ConfigEntryNotReady."""
        assert await _create_unreachable(_config()) is None

    async def test_with_option_returns_disconnected_client(self) -> None:
        """With the option the client is returned and reports disconnected."""
        api = await _create_unreachable(_config(**{CONF_ALLOW_PRINTER_OFFLINE: True}))

        assert api is not None
        assert api.offline_setup is True
        assert api.is_connected is False
        assert api.client.is_connected is False

    async def test_with_option_starts_no_proxy_server(self) -> None:
        """A dead printer must not occupy the shared proxy ports."""
        with patch.object(
            api_module.ElegooPrinterServer, "async_create", new=AsyncMock()
        ) as create_server:
            api = await _create_unreachable(
                _config(proxy_enabled=True, **{CONF_ALLOW_PRINTER_OFFLINE: True})
            )

        assert api is not None
        assert api.server is None
        create_server.assert_not_awaited()


class TestUnreachableCc2:
    """CC2 is gated by its MQTT-broker reachability test instead."""

    @staticmethod
    def _config(**overrides: Any) -> dict[str, Any]:
        return _config(
            model="Centauri Carbon 2",
            protocol_version="CC2",
            transport_type="cc2_mqtt",
            **overrides,
        )

    async def test_without_option_setup_fails(self) -> None:
        """Default behaviour is unchanged for CC2."""
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError):
            api = await _create_unreachable(self._config())

        assert api is None

    async def test_with_option_returns_disconnected_client(self) -> None:
        """The refused broker probe is tolerated with the option."""
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError):
            api = await _create_unreachable(
                self._config(**{CONF_ALLOW_PRINTER_OFFLINE: True})
            )

        assert api is not None
        assert isinstance(api.client, ElegooCC2Client)
        assert api.offline_setup is True
        assert api.is_connected is False


class TestMqttPrinterNeverDialsTheBroker:
    """
    MQTT is the case where the *printer* is off, not the broker.

    The embedded broker is ours and is listening, so the reachability test
    passes; it is ``connect_printer`` (the M66666 redirect plus the handshake
    wait) that fails. That exit path must keep the broker reference.
    """

    @staticmethod
    def _config(**overrides: Any) -> dict[str, Any]:
        return _config(
            model="Saturn 4",
            protocol_version="V1",
            transport_type="mqtt",
            mqtt_broker_enabled=True,
            **overrides,
        )

    @staticmethod
    @contextlib.contextmanager
    def _patches(broker_release: AsyncMock) -> Any:
        broker = MagicMock(port=EMBEDDED_BROKER_PORT, host="localhost")
        with (
            patch.object(
                api_module, "async_get_clientsession", return_value=MagicMock()
            ),
            patch.object(api_module, "get_async_client", return_value=MagicMock()),
            patch.object(
                api_module.ElegooMQTTBroker,
                "get_instance",
                new=AsyncMock(return_value=broker),
            ),
            patch.object(
                api_module.ElegooMQTTBroker, "release_instance", broker_release
            ),
            # the broker is ours: the probe to it succeeds
            patch(
                "asyncio.open_connection",
                new=AsyncMock(return_value=(MagicMock(), _writer())),
            ),
            patch.object(
                ElegooPrinterApiClient,
                "_update_config_entry_if_needed",
                new=AsyncMock(),
            ),
        ):
            yield

    async def test_without_option_setup_fails_and_broker_is_released(self) -> None:
        """Default path unchanged: reference released so the broker stops."""
        release = AsyncMock()
        with (
            self._patches(release),
            patch.object(
                ElegooMQTTClient, "connect_printer", new=AsyncMock(return_value=False)
            ),
        ):
            api = await ElegooPrinterApiClient.async_create(
                self._config(), MagicMock(), _hass(), None
            )

        assert api is None
        release.assert_awaited_once()

    async def test_with_option_keeps_the_broker_reference(self) -> None:
        """The loaded entry still owns the broker; nothing may be released."""
        release = AsyncMock()
        with (
            self._patches(release),
            patch.object(
                ElegooMQTTClient, "connect_printer", new=AsyncMock(return_value=False)
            ),
        ):
            api = await ElegooPrinterApiClient.async_create(
                self._config(**{CONF_ALLOW_PRINTER_OFFLINE: True}),
                MagicMock(),
                _hass(),
                None,
            )

        assert api is not None
        assert isinstance(api.client, ElegooMQTTClient)
        assert api.offline_setup is True
        assert api.is_connected is False
        assert api.mqtt_broker is not None
        release.assert_not_awaited()

    async def test_with_option_survives_a_failing_handshake(self) -> None:
        """An exception from the handshake is tolerated the same way."""
        release = AsyncMock()
        with (
            self._patches(release),
            patch.object(
                ElegooMQTTClient,
                "connect_printer",
                new=AsyncMock(side_effect=ConnectionRefusedError("no printer")),
            ),
        ):
            api = await ElegooPrinterApiClient.async_create(
                self._config(**{CONF_ALLOW_PRINTER_OFFLINE: True}),
                MagicMock(),
                _hass(),
                None,
            )

        assert api is not None
        assert api.offline_setup is True
        release.assert_not_awaited()
