"""
Polling a printer that is switched off must report one thing, consistently (#171).

The option makes setup succeed while the printer is off, so the coordinator
becomes the only thing between "printer off" and what the dashboard shows.
Entities read availability from ``coordinator.last_update_success``, and the
MQTT/CC2 transports answer ``get_printer_status``/``get_printer_attributes``
from a cache without raising — so an offline poll *succeeds* and every entity
renders fabricated values (0 C, "idle", fans off) as live truth.

Worse, a CC2 with a Canvas only fails on the polls that happen to reach the
30-second Canvas check: the entry flickers between healthy and unavailable
instead of settling on unavailable. Gating the poll on the connection makes all
three transports agree, which is what the option promises.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.const import CONF_HAS_CANVAS
from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterNotConnectedError,
)
from custom_components.elegoo_printer.sdcp.models.printer import PrinterData

if TYPE_CHECKING:
    from types import SimpleNamespace


def _coordinator(
    hass: MagicMock,
    entry: SimpleNamespace,
    *,
    client: MagicMock,
    has_canvas: bool = False,
) -> ElegooDataUpdateCoordinator:
    """Build a coordinator over a cached-data client that never raises."""
    entry.options = {CONF_HAS_CANVAS: has_canvas}
    entry.async_on_unload = MagicMock()
    entry.pref_disable_polling = False
    api = entry.runtime_data.api
    api.client = client
    api.is_connected = False
    # The MQTT/CC2 transports do exactly this while the printer is off.
    api.async_get_printer_data = AsyncMock(return_value=PrinterData())
    api.async_get_firmware_update_info = AsyncMock(return_value=None)
    api.async_get_canvas_status = AsyncMock(return_value=None)
    api.async_get_file_list = AsyncMock(return_value={})
    api.reconnect = AsyncMock()
    return ElegooDataUpdateCoordinator(hass, entry=entry)


def _cc2_client() -> MagicMock:
    return MagicMock(spec=ElegooCC2Client)


def _mqtt_like_client() -> MagicMock:
    """Return a client that is not a CC2, so the CC2-only side-checks stand down."""
    return MagicMock(spec=object)


@pytest.mark.parametrize(
    ("client_factory", "has_canvas"),
    [
        pytest.param(_cc2_client, False, id="cc2"),
        pytest.param(_mqtt_like_client, False, id="mqtt-v1"),
        pytest.param(_cc2_client, True, id="cc2-with-canvas"),
    ],
)
async def test_a_disconnected_client_reports_no_data(
    hass: MagicMock,
    entry: SimpleNamespace,
    client_factory: MagicMock,
    *,
    has_canvas: bool,
) -> None:
    """Every transport must fail the poll rather than serve its cache."""
    coordinator = _coordinator(
        hass, entry, client=client_factory(), has_canvas=has_canvas
    )

    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.online is False


async def test_offline_polls_agree_with_each_other(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """
    Two polls of the same offline printer must not disagree.

    The Canvas check runs every 30 s and the file list every 10 minutes, so an
    ungated cache makes only some polls fail — a CC2 with a Canvas flickers
    available/unavailable forever instead of settling on unavailable.
    """
    coordinator = _coordinator(hass, entry, client=_cc2_client(), has_canvas=True)

    results = []
    for _ in range(4):
        await coordinator.async_refresh()
        results.append(coordinator.last_update_success)

    assert len(set(results)) == 1, f"availability flickered: {results}"
    assert results[0] is False


async def test_polling_a_connected_client_is_unchanged(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """The gate must not fire for a live printer."""
    coordinator = _coordinator(hass, entry, client=_cc2_client())
    coordinator.config_entry.runtime_data.api.is_connected = True

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.online is True
    assert coordinator.update_interval == timedelta(seconds=2)


async def test_a_failing_side_check_does_not_mark_the_device_unavailable(
    hass: MagicMock, entry: SimpleNamespace, caplog
) -> None:
    """
    A dropped Canvas/file-list request is not a lost link.

    ``ElegooPrinterNotConnectedError`` is a sibling of
    ``ElegooPrinterConnectionError``, not a subclass, so the side-checks'
    ``except`` clauses miss it and it fails the whole update.
    """
    coordinator = _coordinator(hass, entry, client=_cc2_client(), has_canvas=True)
    coordinator.config_entry.runtime_data.api.is_connected = True
    coordinator.config_entry.runtime_data.api.async_get_canvas_status = AsyncMock(
        side_effect=ElegooPrinterNotConnectedError
    )

    with caplog.at_level(logging.DEBUG):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
