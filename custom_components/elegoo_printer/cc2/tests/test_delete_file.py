"""
Tests for the CC2 delete-file command (method 1047).

1047 is marked "Untested" in the protocol doc and documents no response at
all, so these run against a reply shape this repo inferred from the shared
envelope. That is exactly why the assertions here are about what the client
refuses to promise: a reply it never got is never reported as a delete.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.const import (
    CC2_CMD_DELETE_FILE,
    CC2_ERROR_FILE_DELETE_FAILED,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
)
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer


def _client() -> ElegooCC2Client:
    printer = Printer()
    printer.printer_type = PrinterType.FDM
    return ElegooCC2Client("192.0.2.1", "TESTSN", printer=printer)


def _response(result: dict | None) -> dict | None:
    body: dict = {"error_code": 0} if result is None else result
    return {"id": 1, "method": CC2_CMD_DELETE_FILE, "result": body}


def test_delete_sends_the_documented_request_shape() -> None:  # noqa: D103
    # The request is what the protocol documents; the reply is not.
    client = _client()
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=_response(None)
    ) as mock_cmd:
        code = asyncio.run(client.delete_file("old_model.gcode"))

        mock_cmd.assert_called_once_with(
            CC2_CMD_DELETE_FILE,
            {"storage_media": "local", "filename": "old_model.gcode"},
        )
    assert code == 0


def test_delete_returns_a_refused_error_code_instead_of_raising() -> None:  # noqa: D103
    # One locked or busy file must not abort a forty-file cleanup: the code is
    # data the service reports per file, so it is returned, not raised.
    client = _client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_response({"error_code": CC2_ERROR_FILE_DELETE_FAILED}),
    ):
        assert asyncio.run(client.delete_file("locked.gcode")) == (
            CC2_ERROR_FILE_DELETE_FAILED
        )


def test_delete_without_answer_raises() -> None:  # noqa: D103
    # No reply at all - a disconnect mid-command, or firmware that does not
    # implement 1047 and stays silent - is never a delete.
    client = _client()
    with (
        patch.object(
            client, "_send_command", new_callable=AsyncMock, return_value=None
        ),
        pytest.raises(ElegooPrinterConnectionError),
    ):
        asyncio.run(client.delete_file("ghost.gcode"))


def test_delete_without_error_code_raises() -> None:  # noqa: D103
    # A reply that carries no error code says nothing about the file, so it is
    # treated as unanswered rather than as the error_code 0 a .get() default
    # would invent.
    client = _client()
    with (
        patch.object(
            client,
            "_send_command",
            new_callable=AsyncMock,
            return_value=_response({"filename": "maybe.gcode"}),
        ),
        pytest.raises(ElegooPrinterConnectionError),
    ):
        asyncio.run(client.delete_file("maybe.gcode"))
