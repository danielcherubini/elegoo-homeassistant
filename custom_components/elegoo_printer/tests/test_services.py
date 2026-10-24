"""
Tests for the update_ip config-entry service.

Under mocks only the handler's explicit reload fires; the production double-cycle
(the entry's add_update_listener reload plus the explicit reload, serialized by HA
on the entry's setup lock, see source comment and README) is not observable here
and is documented there instead.

"""

from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryError

from custom_components.elegoo_printer import (
    SERVICE_CLEANUP_FILES,
    SERVICE_CLEANUP_FILES_SCHEMA,
    SERVICE_START_PRINT,
    SERVICE_START_PRINT_SCHEMA,
    SERVICE_UPDATE_IP,
    SERVICE_UPDATE_IP_SCHEMA,
    SERVICE_UPLOAD_GCODE,
    SERVICE_UPLOAD_GCODE_SCHEMA,
    _async_cleanup_files,
    _async_start_print,
    _async_update_ip,
    _async_upload_gcode,
    _remove_quietly,
    _stage_upload,
    async_setup,
)
from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.const import CC2_ERROR_FILE_DELETE_FAILED
from custom_components.elegoo_printer.cc2.upload import UploadFile
from custom_components.elegoo_printer.const import DOMAIN
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
    ElegooPrinterNotConnectedError,
    ElegooPrinterTimeoutError,
)
from custom_components.elegoo_printer.sdcp.models.file_info import PrinterFile
from custom_components.elegoo_printer.sdcp.models.printer import Printer


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
    *,
    entry_id: str,
    domain: str,
    data: dict,
    state: ConfigEntryState,
    options: dict | None = None,
) -> MagicMock:
    """
    Build a fake config entry with real string and dict attributes.

    ``entry.data`` MUST be a real dict (the handler does ``{**entry.data}`` —
    a bare MagicMock attribute raises) and ``entry.entry_id`` / ``entry.domain``
    MUST be real strings (the handler string-compares and does
    ``hass.config_entries.async_get_entry(...) is entry``).

    ``options`` defaults to a REAL empty dict for the same reason: left as a
    bare MagicMock attribute it splats to ``{}``, so a test silently exercises
    the no-options shape instead of the merge it means to pin.
    """
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.domain = domain
    entry.data = data
    entry.options = {} if options is None else options
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
            # update_ip, start_print, upload_gcode, cleanup_files; this test
            # pins the update_ip call. The count is load-bearing: a fifth
            # service that forgot to register, or one that registered twice,
            # fails here. TestCleanupFilesService pins the fourth by NAME and
            # index (test_async_setup_registers_cleanup_files_service), so the
            # count and the identity are both covered.
            assert hass.services.async_register.call_count == 4
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
                entry,
                data={"ip_address": "198.51.100.7", "id": "p1"},
                options={"ip_address": "198.51.100.7"},
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
                entry,
                data={"ip_address": "198.51.100.7", "id": "p1"},
                options={"ip_address": "198.51.100.7"},
            )

        asyncio.run(_run())

    def test_update_ip_overwrites_a_stale_ip_in_entry_options(self) -> None:
        """
        A stale ``ip_address`` in ``entry.options`` must not win.

        The options flow saves the whole ``Printer.to_dict()`` payload —
        ``ip_address`` included — into ``entry.options``, and runtime setup
        reads ``{**entry.data, **entry.options}``, so options beat data on a
        conflicting key. An entry whose options were saved before the printer
        moved therefore reconnects at the OLD address unless the service
        writes both stores. (PR #430, reported by @caohuongls.)
        """

        async def _run() -> None:
            entry = _make_config_entry(
                entry_id="abc-123",
                domain=DOMAIN,
                data={"ip_address": "192.0.2.1", "id": "p1"},
                options={
                    "ip_address": "192.0.2.99",  # stale, saved by the options flow
                    "allow_printer_offline": True,
                },
                state=ConfigEntryState.LOADED,
            )
            hass = _make_hass_with_entry(entry=entry)

            call = MagicMock()
            call.data = {"entry_id": "abc-123", "ip_address": "198.51.100.7"}

            result = await _async_update_ip(hass, call)

            assert result["success"] is True
            _, kwargs = hass.config_entries.async_update_entry.call_args
            # Both stores carry the new address...
            assert kwargs["data"]["ip_address"] == "198.51.100.7"
            assert kwargs["options"]["ip_address"] == "198.51.100.7"
            # ...and the merge the runtime actually reads resolves to the new
            # address rather than the stale one.
            assert {**kwargs["data"], **kwargs["options"]}[
                "ip_address"
            ] == "198.51.100.7"
            # Options keys that are not the address survive the rewrite.
            assert kwargs["options"]["allow_printer_offline"] is True
            assert kwargs["data"]["id"] == "p1"
            # The merge outcome, at the layer that picks the address we connect
            # to: async_setup_entry hands Printer.from_dict the merged config,
            # and printer.ip_address is the address every transport targets.
            # Without the options write this reads the stale 192.0.2.99.
            merged = {**kwargs["data"], **kwargs["options"]}
            assert Printer.from_dict(merged).ip_address == "198.51.100.7"

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


def _upload_hass(
    entry: MagicMock, *, filename: str = "a.gcode", data: bytes = b"G28"
) -> MagicMock:
    """Build a hass mock whose executor job returns the staged upload."""
    hass = _make_hass_with_entry(entry=entry)
    # upload_gcode is mocked in these tests, so the staged path is never opened
    staged = Path("elegoo-upload-test.gcode")
    file = UploadFile(
        filename, len(data), hashlib.md5(data, usedforsecurity=False).hexdigest()
    )
    hass.async_add_executor_job = AsyncMock(return_value=(filename, staged, file))
    return hass


def _upload_call(**extra: object) -> MagicMock:
    call = MagicMock()
    call.data = {"entry_id": "cc2-1", "file": "fid", **extra}
    return call


class TestUploadGcodeService:
    """The upload_gcode service streams an uploaded file to a CC2 and may start it."""

    def test_async_setup_registers_upload_gcode_service(self) -> None:
        async def _run() -> None:
            hass = MagicMock()
            await async_setup(hass, {})
            args, kwargs = hass.services.async_register.call_args_list[2]
            assert args[0] == DOMAIN
            assert args[1] == SERVICE_UPLOAD_GCODE
            schema = kwargs.get("schema", args[3] if len(args) > 3 else None)
            assert schema is SERVICE_UPLOAD_GCODE_SCHEMA

        asyncio.run(_run())

    def test_schema_defaults(self) -> None:
        ok = SERVICE_UPLOAD_GCODE_SCHEMA({"entry_id": "x", "file": "fid"})
        assert ok["start"] is False
        assert ok["bed_leveling"] is True
        with pytest.raises(vol.Invalid):
            SERVICE_UPLOAD_GCODE_SCHEMA({"entry_id": "x", "file": "fid", "tray": 4})

    def test_upload_without_start(self) -> None:
        async def _run() -> None:
            client = _cc2_client()
            client.upload_gcode = AsyncMock(return_value=3)
            entry = _make_cc2_entry(client=client, state=ConfigEntryState.LOADED)
            hass = _upload_hass(entry)

            with patch(
                "custom_components.elegoo_printer.async_get_clientsession"
            ) as gcs:
                result = await _async_upload_gcode(hass, _upload_call(start=False))

            assert result["success"] is True
            assert result["filename"] == "a.gcode"
            client.upload_gcode.assert_awaited_once_with(
                gcs.return_value,
                UploadFile(
                    "a.gcode", 3, hashlib.md5(b"G28", usedforsecurity=False).hexdigest()
                ),
                ANY,
            )
            client.print_start.assert_not_awaited()

        asyncio.run(_run())

    def test_upload_then_start_with_tray(self) -> None:
        async def _run() -> None:
            client = _cc2_client()
            client.upload_gcode = AsyncMock(return_value=3)
            entry = _make_cc2_entry(client=client, state=ConfigEntryState.LOADED)
            hass = _upload_hass(entry)

            with patch("custom_components.elegoo_printer.async_get_clientsession"):
                result = await _async_upload_gcode(
                    hass, _upload_call(start=True, tray=1, bed_leveling=False)
                )

            assert result["success"] is True
            client.print_start.assert_awaited_once_with(
                "a.gcode", tray_id=1, bed_leveling=False
            )

        asyncio.run(_run())

    def test_upload_failure_is_reported_and_nothing_starts(self) -> None:
        async def _run() -> None:
            client = _cc2_client()
            client.upload_gcode = AsyncMock(
                side_effect=ElegooPrinterConnectionError("Printer answered 429")
            )
            entry = _make_cc2_entry(client=client, state=ConfigEntryState.LOADED)
            hass = _upload_hass(entry)

            with patch("custom_components.elegoo_printer.async_get_clientsession"):
                result = await _async_upload_gcode(hass, _upload_call(start=True))

            assert result["success"] is False
            assert "429" in _error_message(result)
            client.print_start.assert_not_awaited()

        asyncio.run(_run())

    def test_upload_refuses_non_cc2_client(self) -> None:
        async def _run() -> None:
            client = MagicMock()
            client.upload_gcode = AsyncMock(return_value=3)
            entry = _make_cc2_entry(client=client, state=ConfigEntryState.LOADED)
            hass = _upload_hass(entry)

            result = await _async_upload_gcode(hass, _upload_call())

            assert result["success"] is False
            client.upload_gcode.assert_not_awaited()

        asyncio.run(_run())

    def test_upload_rejects_non_gcode(self) -> None:
        async def _run() -> None:
            client = _cc2_client()
            client.upload_gcode = AsyncMock(return_value=3)
            entry = _make_cc2_entry(client=client, state=ConfigEntryState.LOADED)
            hass = _make_hass_with_entry(entry=entry)
            hass.async_add_executor_job = AsyncMock(return_value=("a.txt", None, None))

            result = await _async_upload_gcode(hass, _upload_call())

            assert result["success"] is False
            assert "a.txt" in _error_message(result)
            client.upload_gcode.assert_not_awaited()

        asyncio.run(_run())


class TestStageUpload:
    """_stage_upload copies the uploaded file aside and hashes it in one pass."""

    def test_copies_and_hashes(self, tmp_path: Path) -> None:
        payload = b"G28\n" * 1000
        src = tmp_path / "part.gcode"
        src.write_bytes(payload)
        with patch(
            "custom_components.elegoo_printer.process_uploaded_file",
            return_value=nullcontext(src),
        ):
            name, staged, file = _stage_upload(MagicMock(), "fid")
        assert staged is not None
        try:
            assert name == "part.gcode"
            assert file == UploadFile(
                "part.gcode",
                len(payload),
                hashlib.md5(payload, usedforsecurity=False).hexdigest(),
            )
            assert staged.read_bytes() == payload
        finally:
            _remove_quietly(staged)
        assert not staged.exists()

    def test_decodes_percent_encoded_name(self, tmp_path: Path) -> None:
        # HA hands the name over percent-encoded; the printer stores it decoded.
        src = tmp_path / "Rapid%20PLA%2B%20part.gcode"
        src.write_bytes(b"G28")
        with patch(
            "custom_components.elegoo_printer.process_uploaded_file",
            return_value=nullcontext(src),
        ):
            name, staged, file = _stage_upload(MagicMock(), "fid")
        assert staged is not None
        _remove_quietly(staged)
        assert name == "Rapid PLA+ part.gcode"
        assert file is not None
        assert file.name == "Rapid PLA+ part.gcode"

    def test_rejects_other_suffix_without_copying(self, tmp_path: Path) -> None:
        src = tmp_path / "notes.txt"
        src.write_bytes(b"x")
        with patch(
            "custom_components.elegoo_printer.process_uploaded_file",
            return_value=nullcontext(src),
        ):
            assert _stage_upload(MagicMock(), "fid") == ("notes.txt", None, None)


# --------------------------------------------------------------------------
# cleanup_files (#432) — deleting files from the printer's local storage.
#
# The client double here is STATEFUL on purpose: it keeps a set of the names
# that are on the printer, answers get_file_list from it, and removes names when
# they are deleted. A scripted side_effect=[listing_a, listing_b] cannot catch a
# handler that never re-lists, because it hands the "after" listing over to
# whoever asks; with state, `await_count == 2` is a real claim about the handler.
# --------------------------------------------------------------------------

# One day, in the unit the printer reports (unix seconds).
DAY_SECONDS = 86_400

# An epoch-near create_time from a printer whose clock was never set (#426).
# Not zero: zero is falsy and PrinterFile already reads that as unset. This one
# parses to 1970-01-01 23:32 UTC, so a truthiness-only age filter deletes it.
NEAR_EPOCH_CREATE_TIME = 84_720

# The response fields every outcome must carry. A key missing from a service
# response renders as nothing in a template, which is indistinguishable from a
# genuinely empty list, so these are structural rather than per-branch.
CLEANUP_CORE_FIELDS = {
    "success",
    "dry_run",
    "deleted",
    "failed",
    "skipped",
    "would_delete",
}


def _entry(
    name: str, *, days_ago: int | None = None, create_time: int | None = None
) -> PrinterFile:
    """Build one 1044 listing entry; no create_time when both args are None."""
    data: dict = {"filename": name, "size": 2048, "type": "file"}
    if create_time is not None:
        data["create_time"] = create_time
    elif days_ago is not None:
        data["create_time"] = int(time.time()) - days_ago * DAY_SECONDS
    return PrinterFile(data)


def _cleanup_listing() -> dict[str, PrinterFile]:
    """Return a small listing: one old file, one fresh file."""
    return {
        "old.gcode": _entry("old.gcode", days_ago=90),
        "fresh.gcode": _entry("fresh.gcode", days_ago=1),
    }


def _cleanup_client(  # noqa: PLR0913
    listing: dict[str, PrinterFile],
    *,
    vanishing: list[str] | None = None,
    refused: list[str] | None = None,
    silence: list[str] | None = None,
    active: str | None = None,
    re_list_fails: bool = False,
) -> MagicMock:
    """
    Build a stateful CC2 client double for cleanup tests.

    ``vanishing`` names the files that really disappear from the listing when
    deleted; the default is all of them. ``vanishing=[]`` is the 1047 failure
    mode this whole design exists for — the printer answers ``error_code 0`` and
    keeps the file — and it has to be expressible in one argument.
    """
    client = MagicMock(spec=ElegooCC2Client)
    present = set(listing)
    doomed = set(listing) if vanishing is None else set(vanishing)
    refused_names = set(refused or ())
    silent_names = set(silence or ())
    list_calls: list[None] = []

    async def _get_file_list() -> dict[str, PrinterFile]:
        list_calls.append(None)
        if re_list_fails and len(list_calls) > 1:
            msg = "printer went away before the re-list"
            raise ElegooPrinterTimeoutError(msg)
        return {name: listing[name] for name in sorted(present)}

    async def _delete_file(name: str) -> int:
        if name in silent_names:
            msg = f"the printer never answered the delete for {name}"
            raise ElegooPrinterTimeoutError(msg)
        if name in refused_names:
            return CC2_ERROR_FILE_DELETE_FAILED
        if name in doomed:
            present.discard(name)
        return 0

    client.get_file_list = AsyncMock(side_effect=_get_file_list)
    client.delete_file = AsyncMock(side_effect=_delete_file)
    # MagicMock(spec=ElegooCC2Client) has NO printer_data — it is assigned in
    # __init__, not on the class, so reading it off a spec'd mock raises
    # AttributeError. That is why this does not reuse _cc2_client(): the active
    # file guard reads printer_data.status.print_info.filename.
    client.printer_data = MagicMock()
    client.printer_data.status.print_info.filename = active
    return client


def _cleanup_call(**extra: object) -> MagicMock:
    call = MagicMock()
    call.data = {"entry_id": "cc2-1", **extra}
    return call


def _cleanup_hass(client: MagicMock) -> MagicMock:
    entry = _make_cc2_entry(client=client, state=ConfigEntryState.LOADED)
    return _make_hass_with_entry(entry=entry)


class TestCleanupFilesService:
    """
    cleanup_files deletes stored files on a CC2, dry run first, re-list confirmed.

    Every test in here drives the stateful double in _cleanup_client, so the
    claims about how many listings the handler takes are measurements.
    """

    # --- schema (7) -------------------------------------------------------

    def test_schema_defaults_to_dry_run_and_sixty_days(self) -> None:
        # The reporter's starting point from #432: 60 days, include = all,
        # exclude = nothing — and dry_run ON, because 1047 is untested. This is
        # the only thing standing between a first call and a real delete.
        ok = SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x"})
        assert ok["older_than_days"] == 60
        assert ok["include"] == ["*"]
        assert ok["exclude"] == []
        assert ok["dry_run"] is True

    def test_schema_reads_a_pattern_field_given_as_a_bare_string(self) -> None:
        # The reporter wrote `include=ECC*`, and a template can render a single
        # pattern rather than a list.
        ok = SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "include": "ECC*"})
        assert ok["include"] == ["ECC*"]
        ok = SERVICE_CLEANUP_FILES_SCHEMA(
            {"entry_id": "x", "include": ["ECC*", "benchy?"], "exclude": "Keep*"}
        )
        assert ok["include"] == ["ECC*", "benchy?"]
        assert ok["exclude"] == ["Keep*"]

    def test_schema_rejects_a_zero_age_limit_and_a_non_string_pattern(self) -> None:
        # older_than_days: 0 would mean "everything on the printer is a
        # candidate"; a pattern list holding a non-string is a caller that sent
        # the wrong JSON to a destructive field.
        with pytest.raises(vol.Invalid):
            SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "older_than_days": 0})
        with pytest.raises(vol.Invalid):
            SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "older_than_days": -5})
        with pytest.raises(vol.Invalid):
            SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "include": [42]})
        with pytest.raises(vol.Invalid):
            SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "exclude": [["Keep*"]]})
        # HA's YAML loader turns `older_than_days: yes` into True, and int(True)
        # is 1 - silently the most aggressive cutoff the field allows. Same for
        # a value too large for float(): it must fail the schema, not raise out
        # of it.
        with pytest.raises(vol.Invalid):
            SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "older_than_days": True})
        with pytest.raises(vol.Invalid):
            SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "older_than_days": 10**400})

    def test_schema_rejects_a_mapping_of_patterns(self) -> None:
        # cv.ensure_list does NOT coerce a dict into a string (it passes it
        # through and the inner str check rejects it) — but this validator is
        # hand-rolled, so the mapping case is pinned here directly.
        with pytest.raises(vol.Invalid):
            SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "include": {"0": "ECC*"}})
        with pytest.raises(vol.Invalid):
            SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "exclude": 42})

    def test_schema_reads_a_blank_field_as_no_patterns(self) -> None:
        # A cleared `text` box in the UI is submitted as "", and the reporter's
        # "exclude = nothing" has to validate — which is exactly why
        # cv.All(cv.ensure_list, [vol.All(str, vol.Length(min=1))]) is not
        # usable here (it rejects "").
        ok = SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "exclude": ""})
        assert ok["exclude"] == []
        assert ok["include"] == ["*"]
        ok = SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "exclude": ["", "Keep*"]})
        assert ok["exclude"] == ["Keep*"]

    def test_schema_reads_a_blank_include_as_no_patterns(self) -> None:
        # NOT widened to "*": a blank include is indistinguishable from a
        # template that rendered to empty, and resolving that to every file
        # would delete every old file on a call that named nothing. The inert
        # run that results is reported out loud, see
        # test_blank_include_is_reported_as_an_inert_pattern_not_silence.
        ok = SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "include": ""})
        assert ok["include"] == []

    def test_schema_rejects_a_fractional_age_limit(self) -> None:
        # vol.Coerce(int) — and HA's cv.positive_int — TRUNCATE 60.9 to 60, and
        # a lower day count is a lower cutoff: that is more deletion than asked
        # for, the wrong direction on a destructive field.
        with pytest.raises(vol.Invalid):
            SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "older_than_days": 60.9})
        # A string still parses, because automations template these.
        assert (
            SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "older_than_days": "60"})[
                "older_than_days"
            ]
            == 60
        )

    # --- registration (2) -------------------------------------------------

    def test_async_setup_registers_cleanup_files_service(self) -> None:
        async def _run() -> None:
            hass = MagicMock()
            await async_setup(hass, {})

            # The fourth registration; TestUpdateIpService pins the count and
            # this test pins which service sits at this index.
            args, kwargs = hass.services.async_register.call_args_list[3]
            assert args[0] == DOMAIN
            assert args[1] == SERVICE_CLEANUP_FILES
            assert callable(args[2])
            schema = kwargs.get("schema", args[3] if len(args) > 3 else None)
            assert schema is SERVICE_CLEANUP_FILES_SCHEMA
            supports_response = kwargs.get(
                "supports_response", args[4] if len(args) > 4 else None
            )
            assert supports_response is SupportsResponse.OPTIONAL

        asyncio.run(_run())

    def test_registered_handler_takes_the_call_alone(self) -> None:
        # HA calls a handler with the ServiceCall only; the partial binding is
        # what makes that work for this service too.
        async def _run() -> None:
            hass = MagicMock()
            hass.config_entries.async_get_entry.return_value = None
            await async_setup(hass, {})
            handler = hass.services.async_register.call_args_list[3].args[2]

            call = MagicMock()
            call.data = {"entry_id": "ghost-404"}
            result = await handler(call)

            assert result["success"] is False
            assert "ghost-404" in _error_message(result)

        asyncio.run(_run())

    # --- behaviour (16) ---------------------------------------------------

    def test_dry_run_deletes_nothing_and_never_re_lists(self) -> None:
        # The default call: report only. It must not spend the confirmation
        # re-list either — one listing, no 1047 at all. The three ways a caller
        # can fail to say "delete for real" (omit the key, send None, send
        # something that is not a bool) are asserted here too: the schema's
        # default is not a guard for a programmatic caller, and only the
        # literal boolean False may reach the destructive branch.
        async def _run() -> None:
            for sneaky in ({}, {"dry_run": None}, {"dry_run": "false"}, {"dry_run": 0}):
                client = _cleanup_client(_cleanup_listing())
                hass = _cleanup_hass(client)

                result = await _async_cleanup_files(
                    hass, _cleanup_call(older_than_days=60, **sneaky)
                )

                assert result["success"] is True, sneaky
                assert result["dry_run"] is True, sneaky
                assert result["deleted"] == [], sneaky
                assert result["would_delete"] == ["old.gcode"], sneaky
                client.delete_file.assert_not_called()
                assert client.get_file_list.await_count == 1, sneaky

        asyncio.run(_run())

    def test_dry_run_reports_would_delete_and_the_reasons(self) -> None:
        # `skipped` naming WHY every file survived is what turns "it deleted
        # nothing" into a diagnosable statement.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing())
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=True, older_than_days=60)
            )

            assert result["would_delete"] == ["old.gcode"]
            assert result["skipped"] == {"fresh.gcode": "younger_than_limit"}
            assert "dry run" in str(result["message"])

        asyncio.run(_run())

    def test_deletes_old_file_and_confirms_it_by_re_listing(self) -> None:
        # Full-dict equality on purpose: it pins that unset prose (error: None)
        # is NOT sent, because a template renders None as the string "None".
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing())
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60)
            )

            assert result == {
                "success": True,
                "dry_run": False,
                "deleted": ["old.gcode"],
                "failed": {},
                "skipped": {"fresh.gcode": "younger_than_limit"},
                "would_delete": ["old.gcode"],
                "message": "deleted 1 of 1 selected file(s)",
            }
            client.delete_file.assert_awaited_once_with("old.gcode")
            # Two listings: the one it acted on and the one it believed.
            assert client.get_file_list.await_count == 2

        asyncio.run(_run())

    def test_accepted_but_still_listed_counts_as_not_deleted(self) -> None:
        # THE done-when clause. The printer answers error_code 0 for every file
        # and keeps them all in its listing — the scenario 1047's undocumented
        # reply makes possible. The reply is never believed: the re-list wins.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing(), vanishing=[])
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60)
            )

            assert result["success"] is False
            assert result["deleted"] == []
            assert result["failed"] == {"old.gcode": "still listed after delete"}
            client.delete_file.assert_awaited_once_with("old.gcode")
            assert client.get_file_list.await_count == 2

        asyncio.run(_run())

    def test_partial_delete_reports_both_halves(self) -> None:
        # A partial success is a FAILURE: a daily automation must be able to
        # alert on `not response.success` and know the printer is not in the
        # state it asked for.
        async def _run() -> None:
            listing = {
                "gone.gcode": _entry("gone.gcode", days_ago=90),
                "stuck.gcode": _entry("stuck.gcode", days_ago=90),
            }
            client = _cleanup_client(listing, vanishing=["gone.gcode"])
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60)
            )

            assert result["success"] is False
            assert result["deleted"] == ["gone.gcode"]
            assert result["failed"] == {"stuck.gcode": "still listed after delete"}
            assert result["would_delete"] == ["gone.gcode", "stuck.gcode"]

        asyncio.run(_run())

    def test_refused_delete_is_reported_and_the_rest_continue(self) -> None:
        # One locked file must not abort a forty-file cleanup: 1007 is data
        # reported per file, not an exception that unwinds the loop.
        async def _run() -> None:
            listing = {
                "locked.gcode": _entry("locked.gcode", days_ago=90),
                "old.gcode": _entry("old.gcode", days_ago=90),
            }
            client = _cleanup_client(listing, refused=["locked.gcode"])
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60)
            )

            assert client.delete_file.await_count == 2
            assert result["deleted"] == ["old.gcode"]
            assert result["failed"] == {"locked.gcode": "printer refused the delete"}
            assert result["success"] is False

        asyncio.run(_run())

    def test_re_list_failure_reports_unconfirmed_not_success(self) -> None:
        # If the confirming listing cannot be fetched, nothing is reported as
        # deleted. "The printer seemed to accept it" is not a delete.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing(), re_list_fails=True)
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60)
            )

            assert result["success"] is False
            assert result["deleted"] == []
            assert "re-list failed" in result["failed"]["old.gcode"]

        asyncio.run(_run())

    def test_connection_lost_mid_delete_reports_what_was_handled(self) -> None:
        # A transport error stops the run (one MQTT channel, 10 s each), but
        # what already happened is still reported, and the files never
        # attempted are reported as never attempted rather than silently kept.
        async def _run() -> None:
            listing = {
                "a.gcode": _entry("a.gcode", days_ago=90),
                "b.gcode": _entry("b.gcode", days_ago=90),
                "c.gcode": _entry("c.gcode", days_ago=90),
            }
            client = _cleanup_client(listing, silence=["b.gcode"])
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60)
            )

            assert result["success"] is False
            assert result["deleted"] == ["a.gcode"]
            # The file that timed out says so and names why; the files never
            # attempted say so too, rather than looking like survivors.
            assert result["failed"]["b.gcode"] == (
                "printer did not answer (ElegooPrinterTimeoutError)"
            )
            assert result["failed"]["c.gcode"] == "printer did not answer"
            # Only two 1047s were sent — the run stopped at the timeout.
            assert client.delete_file.await_count == 2
            # Two listings even though the run stopped early: "a" was accepted,
            # so it still has to be confirmed by re-listing. The files that
            # were never attempted are NOT in `deleted`, so they cannot be
            # mistaken for confirmed deletions.
            assert client.get_file_list.await_count == 2
            assert result["would_delete"] == ["a.gcode", "b.gcode", "c.gcode"]

        asyncio.run(_run())

    def test_unreachable_printer_reports_failure_in_the_response_shape(self) -> None:
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing())
            client.get_file_list = AsyncMock(
                side_effect=ElegooPrinterNotConnectedError("offline")
            )
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(hass, _cleanup_call())

            assert result["success"] is False
            assert "ElegooPrinterNotConnectedError" in _error_message(result)
            # The caller asked for a dry run (the default) and the failure says
            # so: an unreachable printer is not reported as a failed delete run.
            assert result["dry_run"] is True
            client.delete_file.assert_not_called()

        asyncio.run(_run())

    def test_nothing_matching_is_reported_as_an_empty_success(self) -> None:
        # A nightly run that finds nothing old is a CLEAN run, not a failure —
        # keying success on bool(to_delete) would raise the automation's
        # failure path every night forever. Run as a REAL (not dry) run on
        # purpose: the dry-run branch returns success unconditionally, so only
        # the destructive branch can pin this.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing())
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=365)
            )

            assert result["success"] is True
            assert result["dry_run"] is False
            assert result["would_delete"] == []
            assert result["skipped"] == {
                "fresh.gcode": "younger_than_limit",
                "old.gcode": "younger_than_limit",
            }
            client.delete_file.assert_not_called()

        asyncio.run(_run())

    def test_near_epoch_create_time_survives_a_real_delete_run(self) -> None:
        # The #426 bug on the destructive path, end to end: a printer whose
        # clock was never set reports a create_time that PARSES to 1970, and a
        # naive `if created:` filter calls it older than every cutoff. Assert
        # the parse first, then that a real (not dry) run leaves it alone.
        async def _run() -> None:
            listing = {
                "clock_never_set.gcode": _entry(
                    "clock_never_set.gcode", create_time=NEAR_EPOCH_CREATE_TIME
                )
            }
            assert listing["clock_never_set.gcode"].created is not None
            client = _cleanup_client(listing)
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60)
            )

            assert result["deleted"] == []
            assert result["skipped"] == {"clock_never_set.gcode": "no_creation_date"}
            client.delete_file.assert_not_called()
            # One listing only: nothing was selected, so nothing was confirmed.
            assert client.get_file_list.await_count == 1

        asyncio.run(_run())

    def test_never_deletes_the_file_being_printed(self) -> None:
        async def _run() -> None:
            listing = {
                "printing.gcode": _entry("printing.gcode", days_ago=90),
                "old.gcode": _entry("old.gcode", days_ago=90),
            }
            client = _cleanup_client(listing, active="printing.gcode")
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60, include="*")
            )

            assert result["deleted"] == ["old.gcode"]
            assert result["skipped"] == {"printing.gcode": "currently_printing"}
            assert client.delete_file.await_count == 1
            client.delete_file.assert_awaited_once_with("old.gcode")

        asyncio.run(_run())

    def test_blank_include_is_reported_as_an_inert_pattern_not_silence(self) -> None:
        # include: "" selects nothing (deliberate — see the schema test). What
        # must NOT happen is the run reporting success while looking like "the
        # printer had nothing old", because then a permanently-inert nightly job
        # is invisible. The message names the pattern instead.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing())
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=True, include="")
            )

            assert result["would_delete"] == []
            # success stays True: an automation must not alert on "nothing to
            # do". The fix is visibility, not a new failure.
            assert result["success"] is True
            message = str(result["message"])
            assert "matched the include" in message
            assert "(empty)" in message
            assert "nothing was deleted" in message

        asyncio.run(_run())

    def test_everything_too_new_is_not_reported_as_a_bad_pattern(self) -> None:
        # The inverse: the ordinary "nothing is old enough yet" run must keep
        # its plain message, or every nightly cleanup becomes noise blaming the
        # pattern that is fine.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing())
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass,
                _cleanup_call(dry_run=True, older_than_days=365, include="*.gcode"),
            )

            message = str(result["message"])
            assert "matched the include" not in message
            assert "365 day(s)" in message

        asyncio.run(_run())

    def test_refuses_a_non_cc2_entry(self) -> None:
        async def _run() -> None:
            client = MagicMock()  # not an ElegooCC2Client
            # Explicit AsyncMocks: on a bare MagicMock an assert_not_awaited
            # would be an auto-created attribute that passes vacuously.
            client.get_file_list = AsyncMock(return_value={})
            client.delete_file = AsyncMock(return_value=0)
            entry = _make_cc2_entry(client=client, state=ConfigEntryState.LOADED)
            hass = _make_hass_with_entry(entry=entry)

            result = await _async_cleanup_files(hass, _cleanup_call())

            assert result["success"] is False
            assert "Centauri Carbon 2" in _error_message(result)
            client.get_file_list.assert_not_awaited()
            client.delete_file.assert_not_awaited()

        asyncio.run(_run())

    def test_response_carries_every_field_in_every_outcome(self) -> None:
        # One NamedTuple rather than seven hand-built dicts, so this holds by
        # construction: walk the outcomes and check the key set each time.
        async def _run() -> None:
            outcomes = []

            client = _cleanup_client(_cleanup_listing())
            outcomes.append(
                await _async_cleanup_files(_cleanup_hass(client), _cleanup_call())
            )
            client = _cleanup_client(_cleanup_listing())
            outcomes.append(
                await _async_cleanup_files(
                    _cleanup_hass(client), _cleanup_call(dry_run=False)
                )
            )
            client = _cleanup_client(_cleanup_listing(), refused=["old.gcode"])
            outcomes.append(
                await _async_cleanup_files(
                    _cleanup_hass(client), _cleanup_call(dry_run=False)
                )
            )
            client = _cleanup_client(_cleanup_listing(), vanishing=[])
            outcomes.append(
                await _async_cleanup_files(
                    _cleanup_hass(client), _cleanup_call(dry_run=False)
                )
            )
            client = _cleanup_client(_cleanup_listing())
            outcomes.append(
                await _async_cleanup_files(
                    _cleanup_hass(client), _cleanup_call(older_than_days=365)
                )
            )
            client = _cleanup_client({})
            outcomes.append(
                await _async_cleanup_files(_cleanup_hass(client), _cleanup_call())
            )

            assert len(outcomes) == 6
            for result in outcomes:
                assert set(result) >= CLEANUP_CORE_FIELDS
                assert isinstance(result["deleted"], list)
                assert isinstance(result["failed"], dict)
                assert isinstance(result["skipped"], dict)
                # Unset prose is dropped, not sent as None: a template renders
                # None as the string "None" in the user's notification.
                assert result.get("message") is not None or result.get("error")
                assert result.get("error") is not None or result.get("message")

        asyncio.run(_run())
