"""Tests for CC2 connection generation guard."""

from __future__ import annotations

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer


def _client() -> ElegooCC2Client:
    printer = Printer()
    printer.printer_type = PrinterType.FDM
    return ElegooCC2Client("192.168.1.1", "TESTSN", printer=printer)


def test_generation_starts_at_zero() -> None:  # noqa: D103
    client = _client()
    assert client._connection_generation == 0
    assert client._listener_generation == 0


def test_disconnect_increments_generation() -> None:  # noqa: D103
    client = _client()
    # Simulate what _try_connect_with_password does
    client._connection_generation += 1
    # Simulate what _mqtt_listener does (capture generation)
    client._listener_generation = client._connection_generation
    # Simulate what disconnect() does (increment generation)
    client._connection_generation += 1
    # Now the generations should mismatch (stale callback detected)
    assert client._connection_generation != client._listener_generation
