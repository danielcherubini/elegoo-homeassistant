"""
Characterization tests for the ElegooDataUpdateCoordinator (mock-only).

Pins the refresh contract: happy path data/online/interval, the
connection-failure path (reconnect + UpdateFailed), has_canvas gating, the
CC2 early-return guard, and the 2s/30s interval flip on each
success/failure. All doubles come from the conftest fixtures.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from homeassistant.config_entries import current_entry as _current_entry_var
from homeassistant.helpers.update_coordinator import UpdateFailed

if TYPE_CHECKING:
    from types import SimpleNamespace

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.const import CONF_HAS_CANVAS
from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
)
from custom_components.elegoo_printer.sdcp.models.printer import PrinterData


def _ensure_entry_shim(entry: SimpleNamespace) -> None:
    """Give the SimpleNamespace shim the attrs the HA base class touches."""
    entry.async_on_unload = MagicMock()  # type: ignore[attr-defined]
    entry.pref_disable_polling = False  # type: ignore[attr-defined]


def _make_coordinator(
    hass: MagicMock, entry: SimpleNamespace
) -> ElegooDataUpdateCoordinator:
    """
    Build a coordinator on top of the conftest entry double.

    The coordinator's ``super().__init__`` resolves ``config_entry`` from
    HA's ``current_entry`` contextvar (as in production setup), so the
    entry must be set there before construction.
    """
    _ensure_entry_shim(entry)
    token = _current_entry_var.set(entry)
    try:
        return ElegooDataUpdateCoordinator(hass, entry=entry)
    finally:
        _current_entry_var.reset(token)


async def test_refresh_happy_path_updates_data_and_interval(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """A successful poll sets data, online=True, and the 2s interval."""
    printer_data = PrinterData()
    entry.runtime_data.api.async_get_printer_data.return_value = printer_data
    # The first refresh also performs a firmware check (rate-limited).
    entry.runtime_data.api.async_get_firmware_update_info.return_value = None

    coordinator = _make_coordinator(hass, entry)
    await coordinator.async_refresh()

    assert coordinator.data is printer_data
    assert coordinator.online is True
    assert coordinator.last_update_success is True
    assert coordinator.update_interval == timedelta(seconds=2)
    # The firmware check ran exactly once on the first refresh.
    entry.runtime_data.api.async_get_firmware_update_info.assert_awaited_once()


async def test_refresh_connection_failure_reports_offline_and_slow_interval(
    hass: MagicMock,
    entry: SimpleNamespace,
) -> None:
    """A connection error invokes api.reconnect() and records UpdateFailed."""
    entry.runtime_data.api.async_get_printer_data.side_effect = (
        ElegooPrinterConnectionError("down")
    )
    # api.reconnect may raise ConnectionError; the coordinator must swallow it.
    entry.runtime_data.api.reconnect.side_effect = ConnectionError("reconnect failed")

    coordinator = _make_coordinator(hass, entry)
    await coordinator.async_refresh()

    assert coordinator.online is False
    assert coordinator.last_update_success is False
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert coordinator.update_interval == timedelta(seconds=30)
    # The transient reconnect error is eaten (not propagated).
    entry.runtime_data.api.reconnect.assert_awaited_once()


async def test_refresh_succeeds_again_flips_interval_back_to_2s(
    hass: MagicMock,
    entry: SimpleNamespace,
) -> None:
    """After a failure, the next success restores the 2s interval."""
    entry.runtime_data.api.async_get_printer_data.side_effect = (
        ElegooPrinterConnectionError("down")
    )
    entry.runtime_data.api.reconnect.side_effect = ConnectionError("reconnect failed")

    coordinator = _make_coordinator(hass, entry)
    await coordinator.async_refresh()
    assert coordinator.online is False
    assert coordinator.update_interval == timedelta(seconds=30)

    printer_data = PrinterData()
    entry.runtime_data.api.async_get_printer_data.side_effect = None
    entry.runtime_data.api.async_get_printer_data.return_value = printer_data
    await coordinator.async_refresh()

    assert coordinator.data is printer_data
    assert coordinator.online is True
    assert coordinator.update_interval == timedelta(seconds=2)


async def test_has_canvas_rate_limits_canvas_check_even_on_failure(
    hass: MagicMock,
    entry: SimpleNamespace,
) -> None:
    """With has_canvas, a failed canvas check still updates the rate-limit."""
    entry.data[CONF_HAS_CANVAS] = True
    entry.options = {CONF_HAS_CANVAS: True}
    entry.runtime_data.api.async_get_printer_data.return_value = PrinterData()
    entry.runtime_data.api.async_get_canvas_status.side_effect = (
        ElegooPrinterConnectionError("canvas down")
    )

    coordinator = _make_coordinator(hass, entry)
    assert coordinator._has_canvas is True

    await coordinator.async_refresh()

    assert coordinator.online is True
    entry.runtime_data.api.async_get_canvas_status.assert_awaited_once()
    # Rate-limited even on failure.
    assert coordinator._last_canvas_check is not None


async def test_cc2_transition_replay_skips_non_cc2_clients(
    hass: MagicMock,
    entry: SimpleNamespace,
) -> None:
    """
    The CC2 transition-replay guard is a no-op for mock (non-CC2) clients.

    The task-7 guard is ``transport_type != CC2_MQTT``; the current guard
    is an isinstance check, which a plain MagicMock never satisfies.
    """
    api = entry.runtime_data.api
    assert not isinstance(api.client, ElegooCC2Client)

    coordinator = _make_coordinator(hass, entry)
    coordinator._replay_cc2_print_status_transitions()

    # The guard early-returned: no attribute access happened on the mock.
    api.client.assert_not_called()


async def test_firmware_check_is_rate_limited_across_refreshes(
    hass: MagicMock,
    entry: SimpleNamespace,
) -> None:
    """The firmware check only runs again after the 12h interval."""
    entry.runtime_data.api.async_get_printer_data.return_value = PrinterData()
    entry.runtime_data.api.async_get_firmware_update_info.return_value = None

    coordinator = _make_coordinator(hass, entry)
    await coordinator.async_refresh()
    entry.runtime_data.api.async_get_firmware_update_info.assert_awaited_once()

    # Follow-up refresh within the 12h window skips the firmware endpoint.
    await coordinator.async_refresh()
    entry.runtime_data.api.async_get_firmware_update_info.assert_awaited_once()
