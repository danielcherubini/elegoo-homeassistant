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
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.elegoo_printer import async_setup_entry, async_unload_entry
from custom_components.elegoo_printer.api import ElegooPrinterApiClient
from custom_components.elegoo_printer.const import (
    CONF_ALLOW_PRINTER_OFFLINE,
    CONF_IP,
    DOMAIN,
)
from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.websocket.server.proxy import ElegooPrinterServer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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


def _client(*, offline: bool, server: Any | None = None) -> MagicMock:
    """
    Build an api-client double.

    ``server`` is the proxy-ownership token and defaults to ``None`` — the state
    of a client that never acquired a reference. Tests that exercise a release
    path must pass a non-None sentinel, because that is the only state in which
    the code under test has anything to give back.
    """
    client = MagicMock()
    client.is_connected = False
    client.offline_setup = offline
    client.server = server
    # Real async methods: awaited by both the failed-setup cleanup and the
    # unload, where a plain MagicMock child would raise in ``await``.
    client.elegoo_disconnect = AsyncMock()
    client.elegoo_stop_mqtt_broker = AsyncMock()
    return client


def _hass() -> MagicMock:
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
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

    async def test_entry_options_reach_the_factory_and_override_entry_data(
        self,
    ) -> None:
        """
        The factory is handed ``entry.data`` overlaid with ``entry.options``.

        The options flow writes to ``entry.options`` while ``entry.data`` keeps
        the values captured at setup, so the merge has a direction: options win
        on a conflicting key, otherwise a printer whose address was edited after
        setup would be polled at its stale IP. Every other data key must still
        ride along.
        """
        entry = _entry(
            {CONF_ALLOW_PRINTER_OFFLINE: True, CONF_IP: "10.0.0.50"},
        )
        create = AsyncMock(return_value=None)
        with (
            patch.object(ElegooPrinterApiClient, "async_create", new=create),
            patch(
                "custom_components.elegoo_printer.ElegooDataUpdateCoordinator",
                return_value=_coordinator_double(),
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(_hass(), entry)

        config = create.await_args.kwargs["config"]
        assert isinstance(config, MappingProxyType | dict)
        assert dict(config) == {**entry.data, **entry.options}
        # The direction of the merge, spelled out: options beat data, and the
        # keys only entry.data carries are not dropped.
        assert config[CONF_IP] == "10.0.0.50"
        assert config["id"] == "SERIAL123"
        assert config[CONF_ALLOW_PRINTER_OFFLINE] is True


@contextlib.asynccontextmanager
async def _failed_first_refresh() -> AsyncIterator[Any]:
    """
    Drive ``async_setup_entry`` down the failing-first-refresh path.

    The proxy classmethods stay patched while the caller runs, so a follow-up
    ``async_unload_entry`` for the SAME entry is observable in the same mock.
    The ``ConfigEntryNotReady`` that production re-raises is consumed here.
    """
    hass = _hass()
    entry = _entry()
    # A proxy-enabled client that DOES hold a reference: ``server`` is the
    # ownership token the cleanup pairs with the release.
    client = _client(offline=False, server=MagicMock(name="proxy-server"))
    coordinator = _coordinator_double()  # first refresh raises ConfigEntryNotReady
    with (
        _patch_setup(client, coordinator),
        patch.object(
            ElegooPrinterServer, "release_reference", new=AsyncMock()
        ) as release,
        patch.object(
            ElegooPrinterServer,
            "remove_printer_from_server",
            new=AsyncMock(return_value=False),
        ) as remove,
    ):
        # Consume the ConfigEntryNotReady production re-raises, but stay inside
        # the patches: the caller's ``async_unload_entry`` must be observed by
        # the very same mocks that recorded the setup cleanup.
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)
        yield SimpleNamespace(
            hass=hass, entry=entry, client=client, release=release, remove=remove
        )


class TestFailedFirstRefreshCleanup:
    """
    A failed first refresh must hand back the proxy reference it holds.

    Before re-raising ``ConfigEntryNotReady``, the online setup path releases
    its reference on the shared ``ElegooPrinterServer`` AND nulls
    ``client.server``. The pairing is the whole point: ``server`` is the record
    of ownership that ``async_unload_entry`` gates on, and every release path in
    the api client pairs its release with ``None`` for exactly that reason (see
    ``ElegooPrinterApiClient.reconnect``). A cleanup that released but left the
    token behind would leave that client looking like it still owns a reference
    it has given back — the double-decrement that stops the shared proxy under a
    live printer. Proxy ownership is this branch's recurring bug class, hence
    the explicit pin.

    Every other client double in this file presets ``server = None`` (nothing to
    release), which is why this behaviour was unpinned; here the double carries
    a non-None server sentinel.
    """

    async def test_failed_first_refresh_releases_once_and_drops_the_token(
        self,
    ) -> None:
        """Exactly one release, and no ownership token left behind."""
        async with _failed_first_refresh() as ctx:
            ctx.release.assert_awaited_once()
            assert ctx.client.server is None
            # The cleanup ran to completion instead of being swallowed by its
            # best-effort try/except: without this, a cleanup that died on the
            # first await would make "released once" fail for the wrong reason.
            ctx.client.elegoo_disconnect.assert_awaited_once()
            ctx.client.elegoo_stop_mqtt_broker.assert_awaited_once()

    async def test_unload_after_failed_setup_does_not_release_again(self) -> None:
        """
        A client whose cleanup already released gives nothing back on unload.

        Reachability, stated straight: HA core short-circuits
        ``ConfigEntry.async_unload`` for any entry that is not LOADED, so a
        ``SETUP_RETRY`` entry does not reach our ``async_unload_entry`` today.
        What this pins is the contract between the two functions — the cleanup
        must leave the client in exactly the state the unload gate reads as
        "holds nothing" — so the reference cannot be decremented twice once a
        gate is dropped or another path drives that client through the unload.
        """
        async with _failed_first_refresh() as ctx:
            assert ctx.release.await_count == 1, "setup cleanup did not run"
            assert ctx.client.server is None

            assert await async_unload_entry(ctx.hass, ctx.entry) is True

            assert ctx.release.await_count == 1, (
                "async_unload_entry released a reference the failed setup had "
                "already given back: the shared proxy loses another entry's "
                "reference"
            )
            # Deregistering is gated on the same ownership token.
            ctx.remove.assert_not_awaited()
            # What the entry does own is still cleaned up: disconnect ran again
            # (once in the cleanup, once here) and the broker was stopped.
            assert ctx.client.elegoo_disconnect.await_count == 2
            ctx.client.elegoo_stop_mqtt_broker.assert_awaited()
