"""Tests for CC2 resume being fire-and-forget."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.const import (
    CC2_CMD_PAUSE_PRINT,
    CC2_CMD_RESUME_PRINT,
)
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer


def _client() -> ElegooCC2Client:
    printer = Printer()
    printer.printer_type = PrinterType.FDM
    return ElegooCC2Client("192.168.1.1", "TESTSN", printer=printer)


def test_resume_does_not_wait_for_response() -> None:
    """1023 is only acknowledged once the resume has completed (~122 s)."""
    client = _client()
    with patch.object(client, "_send_command", new_callable=AsyncMock) as mock_cmd:
        asyncio.run(client.print_resume())
        mock_cmd.assert_called_once_with(
            CC2_CMD_RESUME_PRINT,
            wait_for_response=False,
        )


def test_pause_still_waits_for_response() -> None:
    """Pause is acknowledged promptly, so it keeps the default behaviour."""
    client = _client()
    with patch.object(client, "_send_command", new_callable=AsyncMock) as mock_cmd:
        asyncio.run(client.print_pause())
        mock_cmd.assert_called_once_with(CC2_CMD_PAUSE_PRINT)
