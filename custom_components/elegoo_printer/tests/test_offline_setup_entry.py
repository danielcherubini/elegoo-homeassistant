"""
Config-entry setup while the printer is switched off (#171).

With ``CONF_ALLOW_PRINTER_OFFLINE`` the entry must finish setting up with a
disconnected client, so the printer's entities show as ``unavailable`` instead
of the entry sitting in "Failed setup, will retry" until it is powered on.

``async_config_entry_first_refresh`` cannot be used on that path: it raises
``ConfigEntryNotReady`` on a failed refresh, putting the entry straight back
into the state the option exists to avoid. The coordinator is started with
``async_start_offline`` instead, which leaves it in exactly the state a lost
connection already produces — listeners attached, 30 s retry cadence,
``last_update_success`` false — and so recovers on its own once the printer is
switched on, the same as the after-setup power-off path.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.elegoo_printer import async_setup_entry
from custom_components.elegoo_printer.api import ElegooPrinterApiClient
from custom_components.elegoo_printer.const import CONF_ALLOW_PRINTER_OFFLINE, DOMAIN
from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator


def _entry(options: dict[str, Any] | None = None) -> SimpleNamespace:
    """Build a config-entry double carrying the given options."""
    return SimpleNamespace(
        entry_id="entry-1",
        domain=DOMAIN,
        title="Printer",
        data={"ip_address": "10.0.0.9", "id": "SERIAL123"},
        options=options or {},
        runtime_data=None,
        add_update_listener=lambda _listener: (lambda: None),
        async_on_unload=lambda action: action,
    )


def _client(*, offline: bool) -> MagicMock:
    client = MagicMock()
    client.is_connected = False
    client.offline_setup = offline
    client.server = None
    return client


def _hass() -> MagicMock:
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    return hass


def _coordinator_double() -> MagicMock:
    """Build a coordinator double whose two start paths are separately observable."""
    coordinator = MagicMock()
    coordinator.async_start_offline = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock(
        side_effect=ConfigEntryNotReady("printer is off")
    )
    return coordinator


@contextlib.contextmanager
def _patch_setup(client: MagicMock, coordinator: MagicMock) -> Any:
    """Patch everything ``async_setup_entry`` touches besides hass/entry."""
    with (
        patch.object(
            ElegooPrinterApiClient, "async_create", new=AsyncMock(return_value=client)
        ),
        patch(
            "custom_components.elegoo_printer.ElegooDataUpdateCoordinator",
            return_value=coordinator,
        ),
        patch(
            "custom_components.elegoo_printer.async_get_loaded_integration",
            return_value=MagicMock(),
        ),
    ):
        yield


class TestOfflineStartOfTheCoordinator:
    """The offline start must reproduce the state a lost connection produces."""

    @staticmethod
    def _coordinator(hass: MagicMock) -> ElegooDataUpdateCoordinator:
        entry = _entry()
        entry.runtime_data = MagicMock()
        entry.runtime_data.api.async_get_firmware_update_info = AsyncMock(
            return_value=None
        )
        # reconnect must be an AsyncMock for assert_not_awaited below to be a
        # real assertion — a plain MagicMock child has no await history.
        entry.runtime_data.api.reconnect = AsyncMock()
        entry.async_on_unload = MagicMock()
        entry.pref_disable_polling = False
        return ElegooDataUpdateCoordinator(hass, entry=entry)

    def test_start_offline_marks_data_unavailable_and_slows_polling(
        self, hass: MagicMock
    ) -> None:
        """Entities read availability off ``last_update_success``."""
        coordinator = self._coordinator(hass)

        coordinator.async_start_offline()

        assert coordinator.last_update_success is False
        assert coordinator.online is False
        assert coordinator.update_interval == timedelta(seconds=30)

    def test_start_offline_keeps_the_stale_state_against_a_new_listener(
        self, hass: MagicMock
    ) -> None:
        """Registering the first entity must not flip availability back to true."""
        coordinator = self._coordinator(hass)
        coordinator.async_start_offline()

        coordinator.async_add_listener(lambda: None)

        assert coordinator.last_update_success is False

    def test_start_offline_does_not_touch_the_printer(self, hass: MagicMock) -> None:
        """It only marks state: the coordinator polls and reconnects as usual."""
        coordinator = self._coordinator(hass)

        coordinator.async_start_offline()

        coordinator.config_entry.runtime_data.api.reconnect.assert_not_awaited()

    def test_start_offline_logs_nothing_above_info(
        self, hass: MagicMock, caplog
    ) -> None:
        """
        The entry is loaded and working — there is nothing to report as an error.

        ``async_set_update_error`` logs at ERROR on the success-to-failure
        transition, and ``last_update_success`` is still true at setup, so using
        it here would print "Error requesting <printer> data" for a printer the
        user has simply switched off.
        """
        coordinator = self._coordinator(hass)

        with caplog.at_level(logging.INFO):
            coordinator.async_start_offline()

        loud = [r for r in caplog.records if r.levelno > logging.INFO]
        assert not loud, [f"{r.levelname}: {r.getMessage()}" for r in loud]


class TestSetupEntryOfflinePath:
    """The option turns a failed connection into a loaded, disconnected entry."""

    async def test_offline_client_without_option_still_fails_setup(self) -> None:
        """Default behaviour unchanged: ConfigEntryNotReady, nothing forwarded."""
        coordinator = _coordinator_double()
        with (
            _patch_setup(_client(offline=False), coordinator),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(_hass(), _entry())

    async def test_offline_client_with_option_completes_setup(self) -> None:
        """The entry loads, publishes runtime data and forwards its platforms."""
        entry = _entry({CONF_ALLOW_PRINTER_OFFLINE: True})
        client = _client(offline=True)
        coordinator = _coordinator_double()
        hass = _hass()

        with _patch_setup(client, coordinator):
            assert await async_setup_entry(hass, entry) is True

        assert entry.runtime_data.api is client
        assert entry.runtime_data.coordinator is coordinator
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    async def test_offline_path_skips_the_first_refresh(self) -> None:
        """A first refresh would raise ConfigEntryNotReady and undo the option."""
        entry = _entry({CONF_ALLOW_PRINTER_OFFLINE: True})
        coordinator = _coordinator_double()

        with _patch_setup(_client(offline=True), coordinator):
            await async_setup_entry(_hass(), entry)

        coordinator.async_config_entry_first_refresh.assert_not_awaited()
        coordinator.async_start_offline.assert_called_once_with()

    async def test_connected_entry_still_requires_a_first_refresh(self) -> None:
        """The online path is untouched: first refresh, no offline start."""
        coordinator = _coordinator_double()
        coordinator.async_config_entry_first_refresh = AsyncMock()

        with _patch_setup(_client(offline=False), coordinator):
            assert await async_setup_entry(_hass(), _entry()) is True

        coordinator.async_config_entry_first_refresh.assert_awaited_once()
        coordinator.async_start_offline.assert_not_called()

    async def test_connected_client_with_offline_option_uses_first_refresh(
        self,
    ) -> None:
        """The option tolerates a failure; it does not skip polling a live printer."""
        entry = _entry({CONF_ALLOW_PRINTER_OFFLINE: True})
        coordinator = _coordinator_double()
        coordinator.async_config_entry_first_refresh = AsyncMock()

        with _patch_setup(_client(offline=False), coordinator):
            assert await async_setup_entry(_hass(), entry) is True

        coordinator.async_config_entry_first_refresh.assert_awaited_once()
        coordinator.async_start_offline.assert_not_called()

    async def test_option_in_entry_options_reaches_the_api_factory(self) -> None:
        """The options flow writes ``entry.options``; the factory must see them."""
        create = AsyncMock(return_value=None)
        with (
            patch.object(ElegooPrinterApiClient, "async_create", new=create),
            patch(
                "custom_components.elegoo_printer.ElegooDataUpdateCoordinator",
                return_value=_coordinator_double(),
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(_hass(), _entry({CONF_ALLOW_PRINTER_OFFLINE: True}))

        passed = dict(create.await_args.kwargs["config"])
        assert isinstance(create.await_args.kwargs["config"], MappingProxyType | dict)
        assert passed[CONF_ALLOW_PRINTER_OFFLINE] is True
