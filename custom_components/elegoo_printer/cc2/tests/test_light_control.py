"""Tests for CC2 light control dual-param."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.const import CC2_CMD_SET_LIGHT
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer
from custom_components.elegoo_printer.sdcp.models.status import LightStatus


def _client() -> ElegooCC2Client:
    printer = Printer()
    printer.printer_type = PrinterType.FDM
    return ElegooCC2Client("192.168.1.1", "TESTSN", printer=printer)


def test_light_on_sends_brightness_and_power() -> None:  # noqa: D103
    client = _client()
    with patch.object(client, "_send_command", new_callable=AsyncMock) as mock_cmd:
        asyncio.run(client.set_light_status(LightStatus({"SecondLight": 1})))
        mock_cmd.assert_called_once_with(
            CC2_CMD_SET_LIGHT,
            {"brightness": 255, "power": 1},
        )


def test_light_off_sends_brightness_and_power() -> None:  # noqa: D103
    client = _client()
    with patch.object(client, "_send_command", new_callable=AsyncMock) as mock_cmd:
        asyncio.run(client.set_light_status(LightStatus({"SecondLight": 0})))
        mock_cmd.assert_called_once_with(
            CC2_CMD_SET_LIGHT,
            {"brightness": 0, "power": 0},
        )
