"""
Tests for CC2 print_info.status transition queue (HA replay).

These tests set _cached_status and call _update_printer_status() to mimic the
post-merge state MQTT handling would leave, without standing up a broker.
"""

from __future__ import annotations

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.const import (
    CC2_STATUS_IDLE,
    CC2_STATUS_PRINTING,
    CC2_SUBSTATUS_PRINTING,
    CC2_SUBSTATUS_PRINTING_COMPLETED,
)
from custom_components.elegoo_printer.sdcp.models.enums import (
    ElegooPrintStatus,
    PrinterType,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer


def _client() -> ElegooCC2Client:
    printer = Printer()
    printer.printer_type = PrinterType.FDM
    return ElegooCC2Client("192.168.1.1", "TESTSN", printer=printer)


def test_queue_records_printing_complete_idle_sequence() -> None:
    """Rapid MQTT-style deltas must enqueue each distinct ElegooPrintStatus."""
    client = _client()
    base = {"print_status": {}}

    def apply_delta(sub_status: int, machine_status: int) -> None:
        client._cached_status = {  # noqa: SLF001
            **base,
            "machine_status": {
                "status": machine_status,
                "sub_status": sub_status,
            },
        }
        client._update_printer_status()  # noqa: SLF001

    apply_delta(CC2_SUBSTATUS_PRINTING, CC2_STATUS_PRINTING)
    apply_delta(CC2_SUBSTATUS_PRINTING_COMPLETED, CC2_STATUS_PRINTING)
    apply_delta(0, CC2_STATUS_IDLE)

    pending = client.consume_print_status_transition_queue()
    assert [s.print_info.status for s in pending] == [
        ElegooPrintStatus.PRINTING,
        ElegooPrintStatus.COMPLETE,
        ElegooPrintStatus.IDLE,
    ]


def test_no_queue_when_print_status_unchanged() -> None:
    """Repeated mapping with the same print_info.status must not enqueue again."""
    client = _client()

    def apply_delta(sub_status: int, machine_status: int) -> None:
        client._cached_status = {  # noqa: SLF001
            "print_status": {},
            "machine_status": {
                "status": machine_status,
                "sub_status": sub_status,
            },
        }
        client._update_printer_status()  # noqa: SLF001

    apply_delta(CC2_SUBSTATUS_PRINTING, CC2_STATUS_PRINTING)
    client.consume_print_status_transition_queue()
    apply_delta(CC2_SUBSTATUS_PRINTING, CC2_STATUS_PRINTING)
    assert client.consume_print_status_transition_queue() == []


def test_consume_clears_queue() -> None:
    """consume_print_status_transition_queue must return items once then yield empty."""
    client = _client()
    client._cached_status = {  # noqa: SLF001
        "print_status": {},
        "machine_status": {
            "status": CC2_STATUS_PRINTING,
            "sub_status": CC2_SUBSTATUS_PRINTING,
        },
    }
    client._update_printer_status()  # noqa: SLF001
    assert len(client.consume_print_status_transition_queue()) == 1
    assert client.consume_print_status_transition_queue() == []
