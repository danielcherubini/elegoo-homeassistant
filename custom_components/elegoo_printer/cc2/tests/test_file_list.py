"""Tests for the CC2 file listing (method 1044)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.const import CC2_CMD_GET_FILE_LIST
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
)
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer

# One entry as the printer sends it (fw 02.01.00.00), plus a directory to skip.
SKULLBOWL = {
    "filename": "ECC2_0.4_skullbowl_Elegoo Rapid PLA+ _0.2_1h26m.gcode",
    "size": 12484158,
    "create_time": 1789680958,
    "layer": 251,
    "print_time": 5137,
    "total_filament_used": 34.92,
    "last_print_time": 0,
    "total_print_times": 0,
    "type": "file",
    "color_map": [{"t": 0, "color": "#7F7E83", "name": "PLA"}],
}
FOLDER = {"filename": "models", "type": "dir"}


def _client() -> ElegooCC2Client:
    printer = Printer()
    printer.printer_type = PrinterType.FDM
    return ElegooCC2Client("192.168.1.1", "TESTSN", printer=printer)


def _response(*entries: dict) -> dict:
    return {
        "id": 1,
        "method": CC2_CMD_GET_FILE_LIST,
        "result": {"error_code": 0, "file_list": list(entries)},
    }


def test_get_file_list_sends_local_root() -> None:  # noqa: D103
    client = _client()
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=_response()
    ) as mock_cmd:
        asyncio.run(client.get_file_list())
        mock_cmd.assert_called_once_with(
            CC2_CMD_GET_FILE_LIST, {"storage_media": "local", "path": "/"}
        )


def test_get_file_list_parses_entries_and_skips_folders() -> None:  # noqa: D103
    client = _client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_response(SKULLBOWL, FOLDER),
    ):
        files = asyncio.run(client.get_file_list())

    assert list(files) == [SKULLBOWL["filename"]]
    file = files[SKULLBOWL["filename"]]
    assert file.size == SKULLBOWL["size"]
    assert file.layers == SKULLBOWL["layer"]
    assert file.print_time == SKULLBOWL["print_time"]
    assert file.filament_used == SKULLBOWL["total_filament_used"]
    assert file.colors == [SKULLBOWL["color_map"][0]["color"]]
    assert file.created == datetime.fromtimestamp(SKULLBOWL["create_time"], tz=UTC)
    assert client.printer_data.file_list is files


def test_get_file_list_replaces_previous_listing() -> None:  # noqa: D103
    client = _client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        side_effect=[_response(SKULLBOWL), _response()],
    ):
        assert len(asyncio.run(client.get_file_list())) == 1
        assert asyncio.run(client.get_file_list()) == {}


def test_get_file_list_without_answer_raises() -> None:  # noqa: D103
    client = _client()
    with (
        patch.object(
            client, "_send_command", new_callable=AsyncMock, return_value=None
        ),
        pytest.raises(ElegooPrinterConnectionError),
    ):
        asyncio.run(client.get_file_list())


def test_unsolicited_1044_response_is_stored() -> None:  # noqa: D103
    # A listing requested by another client on the same broker still lands in
    # printer_data, like Canvas status does.
    client = _client()
    asyncio.run(client._handle_response(_response(SKULLBOWL)))
    assert SKULLBOWL["filename"] in client.printer_data.file_list
