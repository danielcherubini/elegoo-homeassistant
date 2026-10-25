"""Tests for the CC2 start-print command (method 1020)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.const import (
    CC2_CMD_START_PRINT,
    CC2_ERROR_PRINTER_BUSY,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
)
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.file_info import PrinterFile
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
                "config": {
                    "slot_map": [{"t": 0, "canvas_id": 0, "tray_id": 3}],
                    "printer_check": False,
                },
            },
        )


def test_start_without_bed_leveling_sends_printer_check_false() -> None:  # noqa: D103
    # False, not omitted. Measured on fw 02.01.00.00, bed at 60 C throughout:
    # the firmware remembers the last value it was given, so a job that omits
    # the key after one that sent true levels again. Omitting it would mean
    # "carry on as before" - possibly someone else's true - where the service
    # promises off. ElegooSlicer sends false the same way when its own
    # bed-leveling box is unchecked.
    client = _client()
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=_response(0)
    ) as mock_cmd:
        asyncio.run(client.print_start("benchy.gcode", bed_leveling=False))
        params = mock_cmd.call_args.args[1]
        assert params["config"] == {"slot_map": [], "printer_check": False}


def test_start_maps_every_tool_the_file_uses() -> None:  # noqa: D103
    # config.slot_map maps EACH tool index of the G-code to a physical tray
    # (docs/CC2_PROTOCOL.md, "Printing with Canvas"). Mapping only tool 0 left
    # a multi-tool file's other tools unmapped - the one defect in this repo's
    # evidence that fits #435's "all usage in A1".
    client = _client()
    client.printer_data.file_list = {
        "multi.gcode": PrinterFile(
            {
                "filename": "multi.gcode",
                "color_map": [
                    {"t": 0, "color": "#111111", "name": "PETG"},
                    {"t": 2, "color": "#222222", "name": "PLA"},
                ],
            }
        )
    }
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=_response(0)
    ) as mock_cmd:
        asyncio.run(client.print_start("multi.gcode", tray_id=1))
        mock_cmd.assert_called_once_with(
            CC2_CMD_START_PRINT,
            {
                "storage_media": "local",
                "filename": "multi.gcode",
                "config": {
                    "slot_map": [
                        {"t": 0, "canvas_id": 0, "tray_id": 1},
                        {"t": 2, "canvas_id": 0, "tray_id": 1},
                    ],
                    "printer_check": True,
                },
            },
        )


def test_start_without_file_detail_falls_back_to_tool_0() -> None:  # noqa: D103
    # Regression pin, do not weaken: when no file detail is available - nothing
    # in file_list, or a listing entry that carried no usable color_map - the
    # payload must stay byte-identical to what a single-tool print sent before
    # multi-tool mapping existed, so those prints cannot regress.
    client = _client()
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=_response(0)
    ) as mock_cmd:
        asyncio.run(client.print_start("benchy.gcode", tray_id=1))
        mock_cmd.assert_called_once_with(
            CC2_CMD_START_PRINT,
            {
                "storage_media": "local",
                "filename": "benchy.gcode",
                "config": {
                    "slot_map": [{"t": 0, "canvas_id": 0, "tray_id": 1}],
                    "printer_check": True,
                },
            },
        )

    client.printer_data.file_list = {
        "plain.gcode": PrinterFile({"filename": "plain.gcode"})
    }
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=_response(0)
    ) as mock_cmd:
        asyncio.run(client.print_start("plain.gcode", tray_id=1))
        params = mock_cmd.call_args.args[1]
        assert params["config"]["slot_map"] == [{"t": 0, "canvas_id": 0, "tray_id": 1}]


def test_start_tool_order_and_garbage() -> None:  # noqa: D103
    # Tool indices are NOT contiguous from 0: the measured 1046 response
    # (CC2_PROTOCOL.md) is a single entry with t: 3, so the map must carry the
    # file's own indices. A non-int, negative, bool or duplicate t is dropped
    # rather than sent - an out-of-range tray_id entry is a whole-map reject,
    # so one bad entry must not poison every good one.
    client = _client()
    client.printer_data.file_list = {
        "one.gcode": PrinterFile({"filename": "one.gcode", "color_map": [{"t": 3}]}),
        "messy.gcode": PrinterFile(
            {
                "filename": "messy.gcode",
                "color_map": [
                    {"t": 2},
                    {"t": 0},
                    {"t": "red"},
                    {"t": -1},
                    {"t": True},
                    {"t": 2},
                    {"color": "#000000"},
                ],
            }
        ),
    }
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=_response(0)
    ) as mock_cmd:
        asyncio.run(client.print_start("one.gcode", tray_id=1))
        assert mock_cmd.call_args.args[1]["config"]["slot_map"] == [
            {"t": 3, "canvas_id": 0, "tray_id": 1}
        ]
        asyncio.run(client.print_start("messy.gcode", tray_id=2))
        assert mock_cmd.call_args.args[1]["config"]["slot_map"] == [
            {"t": 0, "canvas_id": 0, "tray_id": 2},
            {"t": 2, "canvas_id": 0, "tray_id": 2},
        ]


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


@pytest.mark.parametrize(
    "response",
    [
        None,  # client disconnected while waiting: _send_command returns None
        {"id": 1, "method": CC2_CMD_START_PRINT},  # no result at all
        {"id": 1, "method": CC2_CMD_START_PRINT, "result": {}},  # no error_code
    ],
)
def test_start_without_acknowledgement_raises(response: dict | None) -> None:  # noqa: D103
    # A missing acknowledgement must not be reported as error_code 0 ("started").
    client = _client()
    with (
        patch.object(
            client, "_send_command", new_callable=AsyncMock, return_value=response
        ),
        pytest.raises(ElegooPrinterConnectionError),
    ):
        asyncio.run(client.print_start("benchy.gcode"))


@pytest.mark.parametrize(
    ("access_code", "token"), [("KP", "KP"), (None, "123456"), ("", "123456")]
)
def test_upload_gcode_uses_access_code_as_token(  # noqa: D103
    access_code: str | None, token: str
) -> None:
    client = _client()
    client.access_code = access_code
    with patch(
        "custom_components.elegoo_printer.cc2.upload.upload_gcode",
        new_callable=AsyncMock,
        return_value=3,
    ) as mock_upload:
        sent = asyncio.run(client.upload_gcode(object(), "a.gcode", b"abc"))  # type: ignore[arg-type]
    assert sent == len(b"abc")
    args = mock_upload.call_args.args
    assert args[1].host == "192.168.1.1"
    assert args[1].token == token
    assert args[2] == "a.gcode"
    assert args[3] == b"abc"
