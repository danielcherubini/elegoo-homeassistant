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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.const import CONF_HAS_CANVAS, LOGGER
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


def _info_logs(caplog) -> list[logging.LogRecord]:
    """
    Return this integration's records at exactly ``INFO``.

    Home Assistant's own coordinator logger reports the same outage at ERROR,
    so the level *and* the logger are filtered: this is about the lines the
    integration itself prints at the default log level.
    """
    return [
        record
        for record in caplog.records
        if record.levelno == logging.INFO and record.name == LOGGER.name
    ]


def _cc2_client() -> MagicMock:
    return MagicMock(spec=ElegooCC2Client)


def _non_cc2_client() -> MagicMock:
    """
    Return a client that is not a CC2, so the CC2-only side-checks stand down.

    It stands in for the MQTT v1 transport, whose real double is not needed
    here: what matters to the coordinator is that ``get_printer_status``
    answers from ``printer_data`` without raising even when the link is down
    (``mqtt/client.py:337-360`` — the method logs and ``return
    self.printer_data``), which is why the connection gate exists at all.
    """
    return MagicMock(spec=object)


def _rewind_canvas_check(coordinator: ElegooDataUpdateCoordinator) -> None:
    """
    Age the Canvas rate-limit stamp so the next poll re-runs that side-check.

    ``_async_update_data`` only calls ``async_get_canvas_status`` once per
    ``_canvas_check_interval`` (30 s), so back-to-back refreshes would all take
    the same branch and could not disagree with each other.
    """
    coordinator._last_canvas_check = datetime.now(UTC) - (
        coordinator._canvas_check_interval + timedelta(seconds=1)
    )


@pytest.mark.parametrize(
    ("client_factory", "has_canvas"),
    [
        pytest.param(_cc2_client, False, id="cc2"),
        pytest.param(_non_cc2_client, False, id="non-cc2"),
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


async def test_offline_polls_agree_across_the_canvas_rate_limit(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """
    Every poll of an offline printer must reach the same verdict.

    The side-checks are rate-limited (Canvas every 30 s, file list every 10
    minutes), so an ungated cache lets a check that blows up fail *some*
    polls: the entry flickers available/unavailable instead of settling on
    unavailable. Rewinding the Canvas stamp below makes the check due on polls
    1 and 3 and not due on 2 and 4 — the two branches that would disagree —
    while the gate keeps both of them failing the same way, because a
    disconnected client never reaches a side-check at all.
    """
    coordinator = _coordinator(hass, entry, client=_cc2_client(), has_canvas=True)
    api = coordinator.config_entry.runtime_data.api
    # Anything the side-check's ``except PRINT_TRANSPORT_ERRORS`` does not
    # cover — a malformed Canvas payload, say — so only the polls that reach it
    # could fail. A transport error would be swallowed and prove nothing here.
    api.async_get_canvas_status = AsyncMock(
        side_effect=ValueError("malformed Canvas payload")
    )

    results = []
    for poll in range(4):
        if poll % 2 == 0:
            _rewind_canvas_check(coordinator)
        await coordinator.async_refresh()
        results.append(coordinator.last_update_success)

    assert len(set(results)) == 1, f"availability flickered: {results}"
    assert results[0] is False
    # The reason they cannot disagree: the gate fails the poll before the
    # Canvas check is ever reached.
    api.async_get_canvas_status.assert_not_awaited()


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

    The side-checks catch ``PRINT_TRANSPORT_ERRORS`` — which covers both
    ``ElegooPrinterConnectionError`` and the ``ElegooPrinterNotConnectedError``
    a CC2 raises when its link is down (``cc2/client.py`` ``_send_command``) —
    so the request is logged at DEBUG and the poll keeps its data.
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


async def test_losing_a_live_printer_is_reported_once_at_info(
    hass: MagicMock, entry: SimpleNamespace, caplog
) -> None:
    """
    The live-to-offline transition is worth one INFO line.

    Issue #171 is titled "logs are spammed", and an offline printer is polled
    every 30 s, so the failure branch may only speak on the transition — the
    moment a user wants to know about — and afterwards must fall back to DEBUG.
    """
    coordinator = _coordinator(hass, entry, client=_cc2_client())
    api = coordinator.config_entry.runtime_data.api
    api.is_connected = True
    await coordinator.async_refresh()
    assert coordinator.online is True

    api.is_connected = False
    with caplog.at_level(logging.DEBUG):
        await coordinator.async_refresh()

    assert [record.getMessage() for record in _info_logs(caplog)] == [
        "Connection to printer Test Printer lost, attempting to reconnect: "
        "Printer not connected, cannot fetch data."
    ]


async def test_a_still_offline_printer_does_not_repeat_the_info_line(
    hass: MagicMock, entry: SimpleNamespace, caplog
) -> None:
    """
    One outage is one INFO line, no matter how many polls it spans.

    A switched-off printer is polled 2,880 times a day, and every one of those
    polls used to print the same line — the spam #171 removes.
    """
    coordinator = _coordinator(hass, entry, client=_cc2_client())
    api = coordinator.config_entry.runtime_data.api
    api.is_connected = True
    await coordinator.async_refresh()
    api.is_connected = False

    with caplog.at_level(logging.DEBUG):
        await coordinator.async_refresh()
        logged_on_the_transition = len(_info_logs(caplog))
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert logged_on_the_transition == 1
    assert len(_info_logs(caplog)) == 1, [
        f"{r.levelname}: {r.getMessage()}" for r in _info_logs(caplog)
    ]
    # The failure is still recorded, only quietly: the DEBUG line keeps saying
    # the same thing every poll.
    assert (
        len(
            [
                r
                for r in caplog.records
                if r.levelno == logging.DEBUG
                and r.name == LOGGER.name
                and "not reachable yet" in r.getMessage()
            ]
        )
        == 2
    )


async def test_a_printer_that_came_up_offline_logs_no_info_on_its_first_poll(
    hass: MagicMock, entry: SimpleNamespace, caplog
) -> None:
    """
    Setup chose the offline path, so the first failed poll is not news.

    ``async_start_offline`` already leaves ``online`` false, so there is no
    live-to-offline transition to report — and "connection lost" would be a
    lie for a printer that was never up.
    """
    coordinator = _coordinator(hass, entry, client=_cc2_client())
    coordinator.async_start_offline()

    with caplog.at_level(logging.DEBUG):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert not _info_logs(caplog), [
        f"{r.levelname}: {r.getMessage()}" for r in _info_logs(caplog)
    ]
    assert [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.DEBUG
        and r.name == LOGGER.name
        and "not reachable yet" in r.getMessage()
    ] == [
        "Printer Test Printer is not reachable yet, attempting to reconnect: "
        "Printer not connected, cannot fetch data."
    ]
