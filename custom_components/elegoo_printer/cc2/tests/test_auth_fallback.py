"""Tests for CC2 auth fallback behavior during password cycling."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.const import CC2_MQTT_DEFAULT_PASSWORD
from custom_components.elegoo_printer.sdcp.models.printer import Printer

_EXPECTED_FALLBACK_COUNT = 2


def _make_client(access_code: str | None = None) -> ElegooCC2Client:
    return ElegooCC2Client(
        printer_ip="192.168.1.100",
        serial_number="TEST123",
        access_code=access_code,
    )


def _make_printer() -> Printer:
    p = Printer()
    p.name = "TestPrinter"
    p.ip_address = "192.168.1.100"
    p.id = "TEST123"
    return p


def test_fallback_continues_after_auth_failure() -> None:
    """Empty password rejected must NOT prevent trying default '123456'."""
    client = _make_client(access_code=None)
    printer = _make_printer()
    call_count = 0

    async def side_effect(password: str) -> bool:
        nonlocal call_count
        call_count += 1
        if password == "":
            client._last_auth_failure = True
            return False
        return password == CC2_MQTT_DEFAULT_PASSWORD

    async def run() -> bool:
        with (
            patch.object(client, "_try_connect_with_password", side_effect=side_effect),
            patch.object(client, "disconnect", new_callable=AsyncMock),
        ):
            return await client.connect_printer(printer)

    result = asyncio.run(run())
    assert result is True, "Should succeed on the '123456' fallback"
    assert call_count == _EXPECTED_FALLBACK_COUNT


def test_user_code_stops_on_auth_failure() -> None:
    """User-provided access code that fails auth should stop immediately."""
    client = _make_client(access_code="mycode")
    printer = _make_printer()
    call_count = 0

    async def side_effect(_password: str) -> bool:
        nonlocal call_count
        call_count += 1
        client._last_auth_failure = True
        return False

    async def run() -> bool:
        with (
            patch.object(client, "_try_connect_with_password", side_effect=side_effect),
            patch.object(client, "disconnect", new_callable=AsyncMock),
        ):
            return await client.connect_printer(printer)

    result = asyncio.run(run())
    assert result is False
    assert call_count == 1


def test_all_fail_reports_correct_attempt_count() -> None:
    """Error log should report actual attempt count, not list length."""
    client = _make_client(access_code=None)
    printer = _make_printer()

    async def side_effect(_password: str) -> bool:
        return False

    async def run() -> tuple[bool, int]:
        with (
            patch.object(client, "_try_connect_with_password", side_effect=side_effect),
            patch.object(client, "disconnect", new_callable=AsyncMock),
            patch.object(client.logger, "error") as mock_log,
        ):
            result = await client.connect_printer(printer)
            logged_count = mock_log.call_args[0][2]
            return result, logged_count

    result, logged_count = asyncio.run(run())
    assert result is False
    assert logged_count == _EXPECTED_FALLBACK_COUNT


def test_auth_failure_flag_reset_between_attempts() -> None:
    """_last_auth_failure must be cleared between fallback attempts."""
    client = _make_client(access_code=None)
    printer = _make_printer()
    flags_seen: list[bool] = []

    async def side_effect(password: str) -> bool:
        flags_seen.append(client._last_auth_failure)
        if password == "":
            client._last_auth_failure = True
        return False

    async def run() -> None:
        with (
            patch.object(client, "_try_connect_with_password", side_effect=side_effect),
            patch.object(client, "disconnect", new_callable=AsyncMock),
        ):
            await client.connect_printer(printer)

    asyncio.run(run())
    assert flags_seen == [False, False], (
        "Flag should be False at the start of each attempt"
    )
