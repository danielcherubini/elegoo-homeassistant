"""Tests for CC2 delayed disconnect."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.const import CC2_DISCONNECT_DELAY
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer
from custom_components.elegoo_printer.sdcp.models.status import PrinterStatus


def _client() -> ElegooCC2Client:
    printer = Printer()
    printer.printer_type = PrinterType.FDM
    return ElegooCC2Client("192.168.1.1", "TESTSN", printer=printer)


def test_delayed_disconnect_clears_queue() -> None:  # noqa: D103
    client = _client()
    client._print_status_transition_queue.append(PrinterStatus())
    assert len(client._print_status_transition_queue) == 1

    async def run() -> None:
        async def instant_sleep(_: float) -> None:
            pass

        with patch("asyncio.sleep", instant_sleep):
            await client._delayed_disconnect()

    asyncio.run(run())
    assert len(client._print_status_transition_queue) == 0


def test_delayed_disconnect_skips_if_reconnected() -> None:  # noqa: D103
    client = _client()
    client._print_status_transition_queue.append(PrinterStatus())
    client._is_connected = True
    client._is_registered = True

    async def run() -> None:
        async def instant_sleep(_: float) -> None:
            pass

        with patch("asyncio.sleep", instant_sleep):
            await client._delayed_disconnect()

    asyncio.run(run())
    # Queue should NOT be cleared (reconnect succeeded)
    assert len(client._print_status_transition_queue) == 1


def test_delayed_disconnect_cancelled() -> None:  # noqa: D103
    client = _client()
    client._print_status_transition_queue.append(PrinterStatus())

    async def run() -> None:
        async def raise_cancelled(_: float) -> None:
            raise asyncio.CancelledError

        with patch("asyncio.sleep", raise_cancelled):
            await client._delayed_disconnect()

    asyncio.run(run())
    # Queue should NOT be cleared (task was cancelled)
    assert len(client._print_status_transition_queue) == 1


def test_disconnect_delay_constant() -> None:  # noqa: D103
    assert CC2_DISCONNECT_DELAY == 5  # noqa: PLR2004
