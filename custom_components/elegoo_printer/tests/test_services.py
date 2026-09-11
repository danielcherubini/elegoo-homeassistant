"""
Tests for the update_ip config-entry service.

Under mocks only the handler's explicit reload fires; the production double-cycle
(the entry's add_update_listener reload plus the explicit reload, serialized by HA
on the entry's setup lock, see source comment and README) is not observable here
and is documented there instead.

"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryError

from custom_components.elegoo_printer import (
    SERVICE_START_PRINT,
    SERVICE_START_PRINT_SCHEMA,
    SERVICE_UPDATE_IP,
    SERVICE_UPDATE_IP_SCHEMA,
    _async_start_print,
    _async_update_ip,
    async_setup,
)
from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.const import DOMAIN
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterNotConnectedError,
)


def _make_hass_with_entry(*, entry: MagicMock) -> MagicMock:
    """Build a hass mock whose config_entries resolves the given entry."""
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = entry
    # async_reload MUST be an AsyncMock (a plain MagicMock is not awaitable).
    hass.config_entries.async_reload = AsyncMock()
    # async_update_entry is a sync MagicMock in HA — assert updates via call kwargs.
    hass.config_entries.async_update_entry = MagicMock()
    return hass


def _make_config_entry(
    *, entry_id: str, domain: str, data: dict, state: ConfigEntryState
) -> MagicMock:
    """
    Build a fake config entry with real string and dict attributes.

    ``entry.data`` MUST be a real dict (the handler does ``{**entry.data}`` —
    a bare MagicMock attribute raises) and ``entry.entry_id`` / ``entry.domain``
    MUST be real strings (the handler string-compares and does
    ``hass.config_entries.async_get_entry(...) is entry``).
    """
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.domain = domain
    entry.data = data
    entry.state = state
    return entry


def _error_message(result: dict) -> str:
    """Return the error text attached to a failed service call result."""
    return str(result.get("error", ""))


class TestUpdateIpService:
    """The update_ip service updates a LOADED entry's ip_address and reloads it."""

    def test_async_setup_registers_update_ip_service(self) -> None:
        async def _run() -> None:
            hass = MagicMock()
            result = await async_setup(hass, {})

            assert result is True
            # update_ip and start_print; this test pins the update_ip call.
            assert hass.services.async_register.call_count == 2
            args, kwargs = hass.services.async_register.call_args_list[0]
            assert args[0] == DOMAIN
            assert args[1] in (SERVICE_UPDATE_IP, "update_ip")
            # args[2] is the handler — must be a real callable.
            assert callable(args[2])
            # IDENTITY assert on the schema constant: catches a future wrong
            # inline re-creation (schema equals but `is` it not).
            schema = kwargs.get("schema", args[3] if len(args) > 3 else None)
            assert schema is SERVICE_UPDATE_IP_SCHEMA
            supports_response = kwargs.get(
                "supports_response", args[4] if len(args) > 4 else None
            )
            assert supports_response is SupportsResponse.OPTIONAL

        asyncio.run(_run())

    def test_registered_update_ip_handler_takes_the_call_alone(self) -> None:
        # HA calls a service handler with the ServiceCall only. A bare
        # (hass, call) function registered directly raises TypeError on every
        # call; this exercises the callable HA would actually invoke.
        async def _run() -> None:
            hass = MagicMock()
            hass.config_entries.async_get_entry.return_value = None
            await async_setup(hass, {})
            handler = hass.services.async_register.call_args_list[0].args[2]

            call = MagicMock()
            call.data = {"entry_id": "ghost-404", "ip_address": "198.51.100.7"}
            result = await handler(call)

            assert result["success"] is False
            assert "ghost-404" in _error_message(result)

        asyncio.run(_run())

    def test_update_ip_updates_entry_data_and_reloads(self) -> None:
        async def _run() -> None:
            entry = _make_config_entry(
                entry_id="abc-123",
                domain=DOMAIN,
                data={"ip_address": "192.0.2.1", "id": "p1"},
                state=ConfigEntryState.LOADED,
            )
            hass = _make_hass_with_entry(entry=entry)

            call = MagicMock()
            call.data = {"entry_id": "abc-123", "ip_address": "198.51.100.7"}

            result = await _async_update_ip(hass, call)

            assert result["success"] is True
            hass.config_entries.async_update_entry.assert_called_once_with(
                entry, data={"ip_address": "198.51.100.7", "id": "p1"}
            )
            hass.config_entries.async_reload.assert_awaited_once_with("abc-123")

        asyncio.run(_run())

    def test_update_ip_unknown_entry_returns_error(self) -> None:
        async def _run() -> None:
            hass = MagicMock()
            hass.config_entries.async_get_entry.return_value = None
            hass.config_entries.async_reload = AsyncMock()
            hass.config_entries.async_update_entry = MagicMock()

            call = MagicMock()
            call.data = {"entry_id": "ghost-404", "ip_address": "198.51.100.7"}

            result = await _async_update_ip(hass, call)

            assert result["success"] is False
            hass.config_entries.async_update_entry.assert_not_called()
            hass.config_entries.async_reload.assert_not_awaited()
            # The error message must reference the entry id.
            assert "ghost-404" in _error_message(result)

        asyncio.run(_run())

    def test_update_ip_wrong_domain_returns_error(self) -> None:
        async def _run() -> None:
            entry = _make_config_entry(
                entry_id="abc-123",
                domain="some_other_integration",
                data={"ip_address": "192.0.2.1"},
                state=ConfigEntryState.LOADED,
            )
            hass = _make_hass_with_entry(entry=entry)

            call = MagicMock()
            call.data = {"entry_id": "abc-123", "ip_address": "198.51.100.7"}

            result = await _async_update_ip(hass, call)

            assert result["success"] is False
            hass.config_entries.async_update_entry.assert_not_called()
            hass.config_entries.async_reload.assert_not_awaited()

        asyncio.run(_run())

    def test_update_ip_reload_failure_returns_error(self) -> None:
        # defensive path — on HA 2025.4 a failed reload surfaces via entry state instead (see test 6); this guards versions/paths where ConfigEntryError propagates  # noqa: E501
        async def _run() -> None:
            entry = _make_config_entry(
                entry_id="abc-123",
                domain=DOMAIN,
                data={"ip_address": "192.0.2.1", "id": "p1"},
                state=ConfigEntryState.LOADED,
            )
            hass = _make_hass_with_entry(entry=entry)
            hass.config_entries.async_reload.side_effect = ConfigEntryError(
                "cannot reach printer"
            )

            call = MagicMock()
            call.data = {"entry_id": "abc-123", "ip_address": "198.51.100.7"}

            result = await _async_update_ip(hass, call)

            assert result["success"] is False
            assert "cannot reach printer" in _error_message(result)

        asyncio.run(_run())

    def test_update_ip_unreachable_printer_state_check(self) -> None:
        async def _run() -> None:
            entry = _make_config_entry(
                entry_id="abc-123",
                domain=DOMAIN,
                data={"ip_address": "192.0.2.1", "id": "p1"},
                state=ConfigEntryState.LOADED,
            )
            hass = _make_hass_with_entry(entry=entry)

            # Simulate HA 2025.4's behaviour: the reload doesn't raise, it
            # leaves the entry not-loaded (SETUP_ERROR).
            hass.config_entries.async_reload = AsyncMock(
                side_effect=lambda *_: setattr(
                    entry, "state", ConfigEntryState.SETUP_ERROR
                )
            )
            call = MagicMock()
            call.data = {"entry_id": "abc-123", "ip_address": "198.51.100.7"}

            result = await _async_update_ip(hass, call)

            assert result["success"] is False
            error = _error_message(result)
            # The message must say the printer is unreachable at the new
            # address and include the entry state.
            assert "198.51.100.7" in error
            assert "unreachable" in error.lower()
            assert "SETUP_ERROR" in error.replace(" ", "_").upper()
            # The data update legitimately happened before the failed reload.
            hass.config_entries.async_update_entry.assert_called_once_with(
                entry, data={"ip_address": "198.51.100.7", "id": "p1"}
            )

        asyncio.run(_run())


def _make_cc2_entry(*, client: MagicMock, state: ConfigEntryState) -> MagicMock:
    """Build a CC2 entry whose runtime_data.api.client is ``client``."""
    entry = _make_config_entry(
        entry_id="cc2-1", domain=DOMAIN, data={"ip_address": "192.0.2.5"}, state=state
    )
    entry.runtime_data.api.client = client
    return entry


def _cc2_client(*, error_code: int = 0) -> MagicMock:
    """Build a client that passes the isinstance check and answers print_start."""
    client = MagicMock(spec=ElegooCC2Client)
    client.print_start = AsyncMock(return_value=error_code)
    return client


def _start_call(**extra: object) -> MagicMock:
    call = MagicMock()
    call.data = {"entry_id": "cc2-1", "filename": "benchy.gcode", **extra}
    return call


class TestStartPrintService:
    """The start_print service starts a stored file on a CC2 entry only."""

    def test_async_setup_registers_start_print_service(self) -> None:
        async def _run() -> None:
            hass = MagicMock()
            await async_setup(hass, {})

            args, kwargs = hass.services.async_register.call_args_list[1]
            assert args[0] == DOMAIN
            assert args[1] == SERVICE_START_PRINT
            assert callable(args[2])
            schema = kwargs.get("schema", args[3] if len(args) > 3 else None)
            assert schema is SERVICE_START_PRINT_SCHEMA
            supports_response = kwargs.get(
                "supports_response", args[4] if len(args) > 4 else None
            )
            assert supports_response is SupportsResponse.OPTIONAL

        asyncio.run(_run())

    def test_registered_start_print_handler_takes_the_call_alone(self) -> None:
        async def _run() -> None:
            hass = MagicMock()
            hass.config_entries.async_get_entry.return_value = None
            await async_setup(hass, {})
            handler = hass.services.async_register.call_args_list[1].args[2]

            call = MagicMock()
            call.data = {"entry_id": "ghost-404", "filename": "benchy.gcode"}
            result = await handler(call)

            assert result["success"] is False
            assert "ghost-404" in _error_message(result)

        asyncio.run(_run())

    def test_schema_rejects_tray_outside_canvas(self) -> None:
        # The printer accepts tray_id 4 with error_code 0 and prints from tray 0;
        # the schema is the only place that catches it.
        ok = SERVICE_START_PRINT_SCHEMA(
            {"entry_id": "x", "filename": "a.gcode", "tray": 3}
        )
        assert ok["bed_leveling"] is True
        with pytest.raises(vol.Invalid):
            SERVICE_START_PRINT_SCHEMA(
                {"entry_id": "x", "filename": "a.gcode", "tray": 4}
            )
        with pytest.raises(vol.Invalid):
            SERVICE_START_PRINT_SCHEMA({"entry_id": "x", "filename": ""})

    def test_start_print_without_tray(self) -> None:
        async def _run() -> None:
            client = _cc2_client()
            entry = _make_cc2_entry(client=client, state=ConfigEntryState.LOADED)
            hass = _make_hass_with_entry(entry=entry)

            result = await _async_start_print(hass, _start_call())

            assert result["success"] is True
            # bed_leveling defaults to True in the schema; the handler falls
            # back to the same when a programmatic call omits it.
            client.print_start.assert_awaited_once_with(
                "benchy.gcode", tray_id=None, bed_leveling=True
            )

        asyncio.run(_run())

    def test_start_print_with_tray_without_leveling(self) -> None:
        async def _run() -> None:
            client = _cc2_client()
            entry = _make_cc2_entry(client=client, state=ConfigEntryState.LOADED)
            hass = _make_hass_with_entry(entry=entry)

            result = await _async_start_print(
                hass, _start_call(tray=2, bed_leveling=False)
            )

            assert result["success"] is True
            client.print_start.assert_awaited_once_with(
                "benchy.gcode", tray_id=2, bed_leveling=False
            )

        asyncio.run(_run())

    def test_start_print_busy_printer_reports_failure(self) -> None:
        async def _run() -> None:
            client = _cc2_client(error_code=1009)
            entry = _make_cc2_entry(client=client, state=ConfigEntryState.LOADED)
            hass = _make_hass_with_entry(entry=entry)

            result = await _async_start_print(hass, _start_call())

            assert result["success"] is False
            assert "1009" in _error_message(result)

        asyncio.run(_run())

    def test_start_print_not_connected_reports_failure(self) -> None:
        async def _run() -> None:
            client = _cc2_client()
            client.print_start = AsyncMock(side_effect=ElegooPrinterNotConnectedError)
            entry = _make_cc2_entry(client=client, state=ConfigEntryState.LOADED)
            hass = _make_hass_with_entry(entry=entry)

            result = await _async_start_print(hass, _start_call())

            assert result["success"] is False
            assert "not reachable" in _error_message(result)

        asyncio.run(_run())

    def test_start_print_refuses_non_cc2_client(self) -> None:
        async def _run() -> None:
            client = MagicMock()  # not an ElegooCC2Client
            client.print_start = AsyncMock(return_value=0)
            entry = _make_cc2_entry(client=client, state=ConfigEntryState.LOADED)
            hass = _make_hass_with_entry(entry=entry)

            result = await _async_start_print(hass, _start_call())

            assert result["success"] is False
            assert "Centauri Carbon 2" in _error_message(result)
            client.print_start.assert_not_awaited()

        asyncio.run(_run())

    def test_start_print_unknown_entry_returns_error(self) -> None:
        async def _run() -> None:
            hass = MagicMock()
            hass.config_entries.async_get_entry.return_value = None
            call = MagicMock()
            call.data = {"entry_id": "ghost-404", "filename": "benchy.gcode"}

            result = await _async_start_print(hass, call)

            assert result["success"] is False
            assert "ghost-404" in _error_message(result)

        asyncio.run(_run())

    def test_start_print_not_loaded_entry_returns_error(self) -> None:
        async def _run() -> None:
            client = _cc2_client()
            entry = _make_cc2_entry(client=client, state=ConfigEntryState.SETUP_RETRY)
            hass = _make_hass_with_entry(entry=entry)

            result = await _async_start_print(hass, _start_call())

            assert result["success"] is False
            client.print_start.assert_not_awaited()

        asyncio.run(_run())
