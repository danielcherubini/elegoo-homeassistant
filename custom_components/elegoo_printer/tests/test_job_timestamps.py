"""
Tests for job timestamps the printer reports near 1970.

The printer sometimes reports BeginTime/EndTime near 1970 (seen on a Centauri
Carbon after a restart), which HA showed as "1 January 1970". Begin time falls back
to the elapsed print time instead, and an unset end time reads as unknown.
"""

from datetime import UTC, datetime, timedelta

from custom_components.elegoo_printer.definitions import PRINTER_STATUS_COMMON
from custom_components.elegoo_printer.sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.sdcp.models.printer import PrinterData


def _value(key: str, printer_data: PrinterData) -> datetime | None:
    description = next(d for d in PRINTER_STATUS_COMMON if d.key == key)
    return description.value_fn(printer_data)


def _printer_data(begin: int, end: int, elapsed_ms: int | None) -> PrinterData:
    printer_data = PrinterData()
    printer_data.current_job = PrintHistoryDetail({"BeginTime": begin, "EndTime": end})
    printer_data.status.print_info.current_ticks = elapsed_ms
    return printer_data


def test_unset_clock_begin_time_uses_elapsed_time() -> None:
    """A 1970 begin time is replaced by now minus the elapsed print time."""
    printer_data = _printer_data(begin=84720, end=0, elapsed_ms=641_000)

    begin = _value("begin_time", printer_data)

    expected = datetime.now(UTC) - timedelta(milliseconds=641_000)
    assert begin is not None
    assert abs(begin - expected) <= timedelta(seconds=31)
    assert begin.second == 0


def test_unset_clock_without_elapsed_time_is_unknown() -> None:
    """With no elapsed time to fall back on, begin time is unknown."""
    printer_data = _printer_data(begin=84720, end=0, elapsed_ms=None)

    assert _value("begin_time", printer_data) is None


def test_unset_clock_end_time_is_unknown() -> None:
    """A 1970 end time reads as unknown rather than 1 January 1970."""
    printer_data = _printer_data(begin=84720, end=91000, elapsed_ms=0)

    assert _value("end_time", printer_data) is None


def test_valid_clock_times_pass_through() -> None:
    """Real timestamps are reported unchanged."""
    begin_ts = int(datetime(2026, 10, 11, 9, 37, tzinfo=UTC).timestamp())
    printer_data = _printer_data(begin=begin_ts, end=begin_ts + 5100, elapsed_ms=1)

    assert _value("begin_time", printer_data) == datetime.fromtimestamp(
        begin_ts, tz=UTC
    )
    assert _value("end_time", printer_data) == datetime.fromtimestamp(
        begin_ts + 5100, tz=UTC
    )
