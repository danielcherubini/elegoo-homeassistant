"""Tests for the CC2 start-print command (method 1020)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.const import (
    CC2_CMD_START_PRINT,
    CC2_ERROR_PRINTER_BUSY,
)
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer


def _client() -> ElegooCC2Client:
    printer = Printer()
    printer.printer_type = PrinterType.FDM
    return ElegooCC2Client("192.168.1.1", "TESTSN", printer=printer)


def _response(error_code: int) -> dict:
    return {
        "id": 1,
        "method": CC2_CMD_START_PRINT,
        "result": {"error_code": error_code},
    }


def test_start_default_sends_empty_slot_map_and_printer_check() -> None:  # noqa: D103
    # Defaults mirror what ElegooSlicer sends: no tray mapping, leveling on.
    client = _client()
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=_response(0)
    ) as mock_cmd:
        code = asyncio.run(client.print_start("benchy.gcode"))
        mock_cmd.assert_called_once_with(
            CC2_CMD_START_PRINT,
            {
                "storage_media": "local",
                "filename": "benchy.gcode",
                "config": {"slot_map": [], "printer_check": True},
            },
        )
    assert code == 0


def test_start_with_tray_sends_full_slot_map_entry() -> None:  # noqa: D103
    # The entry must carry t, canvas_id and tray_id together: a partial entry
    # is acknowledged but not honoured by the printer (fw 02.01.00.00).
    client = _client()
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=_response(0)
    ) as mock_cmd:
        asyncio.run(client.print_start("benchy.gcode", tray_id=3, bed_leveling=False))
        mock_cmd.assert_called_once_with(
            CC2_CMD_START_PRINT,
            {
                "storage_media": "local",
                "filename": "benchy.gcode",
                "config": {"slot_map": [{"t": 0, "canvas_id": 0, "tray_id": 3}]},
            },
        )


def test_start_without_bed_leveling_omits_printer_check() -> None:  # noqa: D103
    # Omitted, not false: omitted is the shape that was measured on the printer.
    client = _client()
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=_response(0)
    ) as mock_cmd:
        asyncio.run(client.print_start("benchy.gcode", bed_leveling=False))
        params = mock_cmd.call_args.args[1]
        assert params["config"] == {"slot_map": []}
        assert "printer_check" not in params["config"]


def test_start_returns_printer_error_code() -> None:  # noqa: D103
    client = _client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_response(CC2_ERROR_PRINTER_BUSY),
    ):
        code = asyncio.run(client.print_start("benchy.gcode"))
    assert code == CC2_ERROR_PRINTER_BUSY
