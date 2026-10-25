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
import pathlib
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
    _MAX_OLDER_THAN_DAYS,
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
    _listed_names_for,
    _remove_quietly,
    _stage_upload,
    async_setup,
)
from custom_components.elegoo_printer.cc2.client import (
    CC2_ERROR_REPLY_UNPARSEABLE,
    CC2UnusableFileListError,
    ElegooCC2Client,
)
from custom_components.elegoo_printer.cc2.const import (
    CC2_ERROR_FILE_DELETE_FAILED,
    CC2_ERROR_PRINTER_BUSY,
)
from custom_components.elegoo_printer.cc2.upload import UploadFile
from custom_components.elegoo_printer.const import DOMAIN
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
    ElegooPrinterNotConnectedError,
    ElegooPrinterTimeoutError,
)
from custom_components.elegoo_printer.sdcp.models.enums import ElegooMachineStatus
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
            # bed_leveling defaults to True in the schema — which core applies
            # for every caller — and the handler's own `.get(..., True)` mirrors
            # that default rather than trusting it, so the two cannot disagree
            # about what an omitted field means.
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


class _FakePrintInfo:
    """Just enough PrintInfo for the active-file guard to read."""

    def __init__(self, filename: str | None) -> None:
        self.filename = filename


class _FakeStatus:
    """
    A printer status the tests can set and the handler can re-read.

    Deliberately a real object rather than a MagicMock: the guard has to be able
    to tell a machine status it recognises from one it does not, and a MagicMock
    answers `isinstance(..., ElegooMachineStatus)` with a mock that is neither,
    which would make every test silently take the "unknown means busy" path.
    ``refreshes`` counts the full-status requests, so "the handler asked the
    printer before deleting" is assertable.
    """

    def __init__(
        self,
        *,
        print_info_filename: str | None = None,
        machine_status: ElegooMachineStatus | None = ElegooMachineStatus.IDLE,
    ) -> None:
        self.print_info = _FakePrintInfo(print_info_filename)
        self.current_status = machine_status
        self.refreshes: list[None] = []


def _cleanup_client(  # noqa: PLR0913
    listing: dict[str, PrinterFile],
    *,
    vanishing: list[str] | None = None,
    refused: list[str] | None = None,
    busy: list[str] | None = None,
    silence: list[str] | None = None,
    active: str | None = None,
    re_list_fails: bool = False,
    re_list_unusable: bool = False,
    broken_re_list: bool = False,
    break_delete: list[str] | None = None,
    unreadable_delete_code: bool = False,
    machine_status: ElegooMachineStatus = ElegooMachineStatus.IDLE,
    status_after_refresh: tuple[str | None, ElegooMachineStatus] | None = None,
) -> MagicMock:
    """
    Build a stateful CC2 client double for cleanup tests.

    ``vanishing`` names the files that really disappear from the listing when
    deleted; the default is all of them. ``vanishing=[]`` is the 1047 failure
    mode this whole design exists for — the printer answers ``error_code 0`` and
    keeps the file — and it has to be expressible in one argument.

    The listing is served by BOTH client methods, because the handler is
    supposed to ask the STRICT one:

    * ``get_file_list`` — the lenient method, which NEVER raises: for a reply it
      will not trust it returns whatever the cache already held, which may be a
      stale listing. The double makes it answer ``{}`` unconditionally, purely as
      a trap: any handler that still asked it for an inventory would see an empty
      printer and report every delete confirmed, failing its own tests. That is
      what makes "the confirmation trusts the listing" a measurement rather than
      a claim. (It is a trap, not a description: the real lenient method keeps
      the previous listing rather than answering ``{}`` — see
      ``test_file_list.py``.)
    * ``get_verified_file_list`` — the strict method, which raises for a reply
      that is not a listing. This double answers it from ``present``, and
      ``re_list_unusable`` makes the SECOND call raise
      ``CC2UnusableFileListError``, which is the 1044-answers-an-error-envelope
      case: the printer answers 1047 with 0, then answers the confirming 1044
      with something that carries no ``file_list``.

    ``status_after_refresh`` is what the printer's full-status request (1002)
    brings back — a ``(filename, machine_status)`` pair — and is how a test
    simulates a job that started AFTER the last status push: the cached status
    the handler reads first says nothing is printing, and only the fresh read
    the destructive branch performs reveals the job.
    """
    client = MagicMock(spec=ElegooCC2Client)
    present = set(listing)
    doomed = set(listing) if vanishing is None else set(vanishing)
    refused_names = set(refused or ())
    busy_names = set(busy or ())
    broken_delete_names = set(break_delete or ())
    silent_names = set(silence or ())
    list_calls: list[None] = []
    status = _FakeStatus(print_info_filename=active, machine_status=machine_status)

    async def _get_verified_file_list() -> dict[str, PrinterFile]:
        list_calls.append(None)
        if re_list_fails and len(list_calls) > 1:
            msg = "printer went away before the re-list"
            raise ElegooPrinterTimeoutError(msg)
        if re_list_unusable and len(list_calls) > 1:
            msg = "the printer's file-list reply is not a file listing"
            raise CC2UnusableFileListError(msg)
        if broken_re_list and len(list_calls) > 1:
            # Not a transport error: a reply so far off the documented envelope
            # that reading it broke, which is what `result.get(...)` on a list
            # or a string does.
            broken = "'list' object has no attribute 'get'"
            raise AttributeError(broken)
        return {name: listing[name] for name in sorted(present)}

    async def _get_file_list() -> dict[str, PrinterFile]:
        # The lenient path, as a TRAP rather than a simulation: a handler that
        # asked here instead of at the strict method would see an empty printer
        # and confirm every delete. (The real lenient method returns the
        # previous listing for a reply it will not write, not `{}`; what it must
        # never do is raise, and that is what disqualifies it as a delete
        # confirmation — an absence of an exception is not an inventory.)
        return {}

    async def _delete_file(name: str) -> int:
        if name in silent_names:
            msg = f"the printer never answered the delete for {name}"
            raise ElegooPrinterTimeoutError(msg)
        if name in broken_delete_names:
            broken = "'list' object has no attribute 'get'"
            raise AttributeError(broken)
        if name in refused_names:
            return CC2_ERROR_FILE_DELETE_FAILED
        if name in busy_names:
            return CC2_ERROR_PRINTER_BUSY
        if unreadable_delete_code:
            # The client coerces an unreadable code to its own non-zero marker;
            # the double reports what the real client would return.
            return CC2_ERROR_REPLY_UNPARSEABLE
        if name in doomed:
            present.discard(name)
        return 0

    async def _refresh_status() -> None:
        if status_after_refresh is not None:
            name, machine = status_after_refresh
            status.print_info.filename = name
            status.current_status = machine
        status.refreshes.append(None)

    client.get_file_list = AsyncMock(side_effect=_get_file_list)
    client.get_verified_file_list = AsyncMock(side_effect=_get_verified_file_list)
    client.delete_file = AsyncMock(side_effect=_delete_file)
    client.refresh_status = AsyncMock(side_effect=_refresh_status)
    # MagicMock(spec=ElegooCC2Client) has NO printer_data — it is assigned in
    # __init__, not on the class, so reading it off a spec'd mock raises
    # AttributeError. That is why this does not reuse _cc2_client(): the active
    # file guard reads printer_data.status.
    client.printer_data = MagicMock()
    client.printer_data.status = status
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
        # re-list either — one listing, no 1047 at all. The three ways a call
        # can fail to say "delete for real" (omit the key, send None, send
        # something that is not a bool) are asserted here too: the schema
        # already refuses the last two for every caller that comes through core,
        # so this pins the handler's own second gate — only the literal boolean
        # False may reach the destructive branch.
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
                # One listing, and it is the strict one: a dry run must not
                # spend the confirming re-list either.
                assert client.get_verified_file_list.await_count == 1, sneaky
                client.get_file_list.assert_not_awaited()

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
            # Two listings: the one it acted on and the one it believed, both
            # through the strict method — the lenient one answers `{}` for a
            # reply it could not read, so it can never settle a delete.
            assert client.get_verified_file_list.await_count == 2
            client.get_file_list.assert_not_awaited()

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
            assert client.get_verified_file_list.await_count == 2

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
            assert client.get_verified_file_list.await_count == 2
            assert result["would_delete"] == ["a.gcode", "b.gcode", "c.gcode"]

        asyncio.run(_run())

    def test_unreachable_printer_reports_failure_in_the_response_shape(self) -> None:
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing())
            client.get_verified_file_list = AsyncMock(
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
            assert client.get_verified_file_list.await_count == 1

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

    # --- what counts as a listing (B2) ------------------------------------

    def test_re_list_answering_an_error_envelope_reports_everything_unconfirmed(
        self,
    ) -> None:
        # THE blocking case. 1047 answers error_code 0 and the confirming 1044
        # answers something that carries no `file_list` — an error envelope, a
        # rejected-parameter reply, any shape nobody has captured, since 1047
        # documents no response body at all. Read leniently that parses to an
        # EMPTY listing, and an empty listing read as authoritative means
        # "nothing remains", i.e. every delete confirmed: a nightly automation
        # reporting "deleted 40 of 40" for files that are all still there, and
        # never retrying because success was true.
        #
        # Usability is therefore required, not inferred from the absence of an
        # exception: the strict listing method raises, and a raised failure
        # reports every accepted file as unconfirmed with its own reason.
        async def _run() -> None:
            listing = {
                "a.gcode": _entry("a.gcode", days_ago=90),
                "b.gcode": _entry("b.gcode", days_ago=90),
            }
            client = _cleanup_client(listing, re_list_unusable=True)
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60)
            )

            assert result["success"] is False
            # Nothing is reported as deleted, because nothing was proven gone.
            assert result["deleted"] == []
            # Every accepted file is reported, with a reason naming the reply
            # problem rather than the generic "still listed" one — the two mean
            # different things to whoever reads the notification.
            assert set(result["failed"]) == {"a.gcode", "b.gcode"}
            for name in ("a.gcode", "b.gcode"):
                assert "re-list failed" in result["failed"][name]
                assert "CC2UnusableFileListError" in result["failed"][name]
                assert result["failed"][name] != "still listed after delete"
            # Both 1047s really went out: the run is unconfirmed, not aborted.
            assert client.delete_file.await_count == 2

        asyncio.run(_run())

    def test_confirmation_never_asks_the_lenient_listing_method(self) -> None:
        # `get_file_list` cannot settle a delete: it never raises, so a caller
        # cannot tell a real listing from a reply it refused to write. The double
        # makes it answer `{}` as a trap — a handler that went back to it would
        # see an empty printer and report every delete confirmed, failing the
        # tests above. This pins the routing directly.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing())
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60)
            )

            assert result["deleted"] == ["old.gcode"]
            client.get_verified_file_list.assert_awaited()
            client.get_file_list.assert_not_awaited()

        asyncio.run(_run())

    def test_a_re_list_that_breaks_on_a_malformed_reply_is_still_reported(
        self,
    ) -> None:
        # A 1044 `result` that is a list/str/int makes the client's
        # `result.get(...)` raise AttributeError, which is NOT a transport error:
        # it used to escape the handler AFTER the deletes were sent, leaving the
        # caller with no response at all and no way to tell what was deleted.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing(), broken_re_list=True)
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60)
            )

            assert result["success"] is False
            assert result["deleted"] == []
            assert "re-list broke" in result["failed"]["old.gcode"]
            assert "AttributeError" in result["failed"]["old.gcode"]

        asyncio.run(_run())

    def test_a_delete_reply_that_breaks_keeps_the_results_that_landed(
        self,
    ) -> None:
        # Same shape one method earlier: an AttributeError out of `delete_file`
        # used to unwind the loop and discard the verdicts of the files that had
        # already been deleted. The run stops, but what landed is reported and
        # still goes to be confirmed by re-listing.
        async def _run() -> None:
            listing = {
                "a.gcode": _entry("a.gcode", days_ago=90),
                "b.gcode": _entry("b.gcode", days_ago=90),
                "c.gcode": _entry("c.gcode", days_ago=90),
            }
            client = _cleanup_client(listing, break_delete=["b.gcode"])
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60)
            )

            assert result["success"] is False
            # "a" was accepted and the re-list confirms it.
            assert result["deleted"] == ["a.gcode"]
            assert "AttributeError" in result["failed"]["b.gcode"]
            # The files behind it say they were never attempted, rather than
            # looking like survivors or like refusals.
            assert result["failed"]["c.gcode"] == (
                "not attempted after an unreadable reply"
            )
            assert client.delete_file.await_count == 2

        asyncio.run(_run())

    def test_an_unreadable_delete_error_code_is_reported_as_a_refusal(self) -> None:
        # The client coerces a non-numeric `error_code` to its own non-zero
        # marker rather than raising, so the service must report it as a refusal
        # and name it as unreadable instead of printing "(error_code -1)" as if
        # -1 were a firmware answer.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing(), unreadable_delete_code=True)
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60)
            )

            assert result["success"] is False
            assert result["deleted"] == []
            assert result["failed"] == {
                "old.gcode": "printer refused the delete (unreadable error_code)"
            }

        asyncio.run(_run())

    # --- the handler's own read of the parameters (C1, C2, C3) ------------

    def test_a_bare_string_pattern_is_not_splatted_into_characters(self) -> None:
        # `include: "ECC*"` is the spelling the README documents, and the schema
        # turns it into ["ECC*"]. The handler used to read the field raw and hit
        # `list(value or ())`, and `list("ECC*")` is ['E','C','C','*'] — a lone
        # `*`, which matches EVERY file. A narrower request became the widest
        # possible one, on a destructive field. Note that this was reachable
        # THROUGH the registered schema, not around it: core validates every
        # caller's data, so the bug was the handler disagreeing with the schema,
        # and these calls drive the handler directly the way core's validated
        # output would.
        async def _run() -> None:
            listing = {
                "ECC2_skull.gcode": _entry("ECC2_skull.gcode", days_ago=90),
                "unrelated.gcode": _entry("unrelated.gcode", days_ago=90),
            }

            client = _cleanup_client(dict(listing))
            result = await _async_cleanup_files(
                _cleanup_hass(client),
                _cleanup_call(dry_run=True, older_than_days=60, include="ECC*"),
            )
            assert result["would_delete"] == ["ECC2_skull.gcode"]
            assert result["skipped"] == {"unrelated.gcode": "not_included"}

            # And in a REAL run: the splat's damage is only ever measurable on
            # the destructive branch.
            client = _cleanup_client(dict(listing))
            result = await _async_cleanup_files(
                _cleanup_hass(client),
                _cleanup_call(dry_run=False, older_than_days=60, include="ECC*"),
            )
            assert result["deleted"] == ["ECC2_skull.gcode"]
            client.delete_file.assert_awaited_once_with("ECC2_skull.gcode")

            # `exclude` splats the OTHER way, into patterns matching nothing:
            # the files the user named to keep become candidates.
            client = _cleanup_client(dict(listing))
            result = await _async_cleanup_files(
                _cleanup_hass(client),
                _cleanup_call(dry_run=True, older_than_days=60, exclude="ECC*"),
            )
            assert result["would_delete"] == ["unrelated.gcode"]
            assert result["skipped"] == {"ECC2_skull.gcode": "excluded"}

            # The handler and the schema now agree on the same input, which is
            # the point: one validator, not two that can drift.
            assert SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "include": "ECC*"})[
                "include"
            ] == ["ECC*"]

        asyncio.run(_run())

    def test_a_string_day_count_is_coerced_to_the_number_it_describes(self) -> None:
        # Automations template this field, so "60" is a normal value — and the
        # schema accepts it. Read raw it reached `timedelta(days="60")` and
        # raised TypeError out of the handler, with no response for the caller.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing())
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days="60")
            )

            assert result["success"] is True
            assert result["deleted"] == ["old.gcode"]

            # And it is the same 60, not a different cutoff: the dry-run message
            # quotes the day count it selected with, so a coercion that invented
            # or truncated a value would show up here.
            client = _cleanup_client(_cleanup_listing())
            result = await _async_cleanup_files(
                _cleanup_hass(client),
                _cleanup_call(dry_run=True, older_than_days="60"),
            )
            assert "60 day(s)" in str(result["message"])
            assert result["would_delete"] == ["old.gcode"]

        asyncio.run(_run())

    def test_the_handlers_own_validators_reject_bad_parameters(self) -> None:
        # The schema's validators run in the handler too, so a call that reaches
        # this function without core having run the schema first — the tests, or
        # a direct call into the module — gets the same refusal, in the response
        # shape rather than as an exception, and nothing is listed or deleted.
        # For a real service call core has already refused these; this gate is
        # the second one on a destructive field.
        async def _run() -> None:
            for bad in (
                {"older_than_days": 0},
                {"older_than_days": -5},
                {"older_than_days": 60.9},
                {"older_than_days": True},
                # C3: `10**20` clears `vol.Range(min=1)` and the whole-number
                # check (a float cannot tell them apart) and then overflows
                # `timedelta` — an OverflowError, not a validation error, so HA
                # does not catch it. It is now refused by the ceiling.
                {"older_than_days": 10**20},
                {"older_than_days": float("inf")},
                {"older_than_days": float("nan")},
                {"include": ["ECC*", 42]},
                {"include": {"0": "ECC*"}},
                {"exclude": 42},
            ):
                client = _cleanup_client(_cleanup_listing())
                hass = _cleanup_hass(client)

                result = await _async_cleanup_files(
                    hass, _cleanup_call(dry_run=False, **bad)
                )

                assert result["success"] is False, bad
                assert result["deleted"] == [], bad
                assert "Invalid parameters" in _error_message(result), bad
                client.delete_file.assert_not_called()

        asyncio.run(_run())

    def test_the_day_count_ceiling_matches_the_one_the_ui_advertises(self) -> None:
        # services.yaml publishes `number: min: 1, max: 3650` for this field;
        # the schema has to accept the same window the UI offers and refuse
        # outside it, or the ceiling is a surprise rather than a bound.
        assert (
            SERVICE_CLEANUP_FILES_SCHEMA({"entry_id": "x", "older_than_days": 1})[
                "older_than_days"
            ]
            == 1
        )
        assert (
            SERVICE_CLEANUP_FILES_SCHEMA(
                {"entry_id": "x", "older_than_days": _MAX_OLDER_THAN_DAYS}
            )["older_than_days"]
            == _MAX_OLDER_THAN_DAYS
        )
        with pytest.raises(vol.Invalid):
            SERVICE_CLEANUP_FILES_SCHEMA(
                {"entry_id": "x", "older_than_days": _MAX_OLDER_THAN_DAYS + 1}
            )
        # And the ceiling is the number services.yaml advertises, checked
        # against the file rather than against a second copy of the constant.
        services = pathlib.Path(
            pathlib.Path(__file__).parents[1] / "services.yaml"
        ).read_text(encoding="utf-8")
        assert f"max: {_MAX_OLDER_THAN_DAYS}" in services

    # --- the active-print guard (C4) --------------------------------------

    def test_a_real_run_asks_the_printer_for_its_status_before_deleting(
        self,
    ) -> None:
        # The guard used to read `printer_data.status`, a cache holding the last
        # push — up to the coordinator interval stale. The destructive branch
        # now asks for a full status (1002) and waits, so the name it compares
        # against answers this call. A dry run does not pay for it: it deletes
        # nothing, so a stale name costs nothing.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing())
            await _async_cleanup_files(
                _cleanup_hass(client), _cleanup_call(dry_run=False, older_than_days=60)
            )
            client.refresh_status.assert_awaited_once()

            client = _cleanup_client(_cleanup_listing())
            await _async_cleanup_files(
                _cleanup_hass(client), _cleanup_call(dry_run=True, older_than_days=60)
            )
            client.refresh_status.assert_not_awaited()

        asyncio.run(_run())

    def test_a_job_started_after_the_last_status_push_is_still_protected(
        self,
    ) -> None:
        # The realistic shape of the bug: the cached status says nothing is
        # printing (it predates the job), so `active` is None and
        # `active_filename and name == active_filename` skips the guard
        # entirely. Only the fresh read taken before deleting sees the job.
        async def _run() -> None:
            listing = {
                "printing.gcode": _entry("printing.gcode", days_ago=90),
                "old.gcode": _entry("old.gcode", days_ago=90),
            }
            client = _cleanup_client(
                listing,
                active=None,
                status_after_refresh=("printing.gcode", ElegooMachineStatus.PRINTING),
            )
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60, include="*")
            )

            assert result["deleted"] == ["old.gcode"]
            assert result["skipped"] == {"printing.gcode": "currently_printing"}
            client.delete_file.assert_awaited_once_with("old.gcode")

        asyncio.run(_run())

    def test_a_busy_printer_that_names_no_file_deletes_nothing(self) -> None:
        # The other half of the same hole: with no name to compare, the guard
        # cannot protect anything, because there is nothing to match against —
        # every listed file is a candidate and one of them may be the file the
        # printer is reading. The run is refused, and refused as a failure, so
        # the automation sees "not in the state I asked for" and retries later.
        async def _run() -> None:
            listing = {
                "a.gcode": _entry("a.gcode", days_ago=90),
                "b.gcode": _entry("b.gcode", days_ago=90),
            }
            client = _cleanup_client(
                listing, active=None, machine_status=ElegooMachineStatus.PRINTING
            )
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60, include="*")
            )

            assert result["success"] is False
            assert result["deleted"] == []
            client.delete_file.assert_not_called()
            message = _error_message(result)
            assert "busy state without naming a file" in message
            assert "deleted nothing" in message
            # The selection it had worked out is still reported, so the refusal
            # is diagnosable rather than opaque.
            assert result["would_delete"] == ["a.gcode", "b.gcode"]

        asyncio.run(_run())

    def test_an_unknown_machine_status_counts_as_busy(self) -> None:
        # No status ever received (None), or a code new firmware reports that
        # this integration's mapper does not know: not "idle", because the cost
        # of refusing a cleanup is one more night of files and the cost of
        # guessing wrong is a file deleted out from under the nozzle.
        async def _run() -> None:
            for machine in (None, "something-new"):
                client = _cleanup_client(
                    _cleanup_listing(), active=None, machine_status=machine
                )
                hass = _cleanup_hass(client)

                result = await _async_cleanup_files(
                    hass, _cleanup_call(dry_run=False, older_than_days=60)
                )

                assert result["success"] is False, machine
                assert result["deleted"] == [], machine
                client.delete_file.assert_not_called()

        asyncio.run(_run())

    def test_a_busy_printer_that_names_its_file_still_deletes_the_rest(self) -> None:
        # The refusal above must not turn into "never clean up while printing":
        # a printer that says PRINTING *and* names the file it is reading has
        # that one file skipped by `select_deletions`, and everything else on
        # the printer is still fair game.
        async def _run() -> None:
            listing = {
                "printing.gcode": _entry("printing.gcode", days_ago=90),
                "old.gcode": _entry("old.gcode", days_ago=90),
            }
            client = _cleanup_client(
                listing,
                active="printing.gcode",
                machine_status=ElegooMachineStatus.PRINTING,
            )
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60, include="*")
            )

            assert result["success"] is True
            assert result["deleted"] == ["old.gcode"]
            assert result["skipped"] == {"printing.gcode": "currently_printing"}

        asyncio.run(_run())

    def test_the_active_guard_survives_a_path_prefix_or_a_different_case(
        self,
    ) -> None:
        # `select_deletions` compares the printer's name EXACTLY, which fails
        # open: a printer reporting its job as "/local/dir/x.gcode", or spelling
        # it "PRINTING.GCODE", for a file the 1044 listing keys "printing.gcode"
        # means the SAME file, and exact equality leaves the file being printed
        # on the delete side. The handler resolves the printer's name to the
        # listing key on basename+casefold first, so the guard still fires — and
        # can only ever add a skip, never a delete. (Every match, not the first:
        # pinned by test_every_listing_key_naming_the_active_file_is_skipped.)
        async def _run() -> None:
            for reported in ("/local/dir/printing.gcode", "PRINTING.GCODE"):
                listing = {
                    "printing.gcode": _entry("printing.gcode", days_ago=90),
                    "old.gcode": _entry("old.gcode", days_ago=90),
                }
                client = _cleanup_client(listing, active=reported)
                hass = _cleanup_hass(client)

                result = await _async_cleanup_files(
                    hass, _cleanup_call(dry_run=False, older_than_days=60, include="*")
                )

                assert result["deleted"] == ["old.gcode"], reported
                assert result["skipped"] == {"printing.gcode": "currently_printing"}, (
                    reported
                )
                client.delete_file.assert_awaited_once_with("old.gcode")

            # A name that denotes a DIFFERENT file is not folded into the
            # active one: the guard is a skip, not a licence to skip everything.
            listing = {
                "printing.gcode": _entry("printing.gcode", days_ago=90),
                "old.gcode": _entry("old.gcode", days_ago=90),
            }
            client = _cleanup_client(listing, active="something_else.gcode")
            result = await _async_cleanup_files(
                _cleanup_hass(client),
                _cleanup_call(dry_run=False, older_than_days=60, include="*"),
            )
            assert result["deleted"] == ["old.gcode", "printing.gcode"]

        asyncio.run(_run())

    def test_every_listing_key_naming_the_active_file_is_skipped(self) -> None:
        # The one case where "the broadening can only add a skip" is not
        # enough on its own: TWO listing keys whose basename matches the
        # printer's active name (`dirA/job.gcode` and `dirB/job.gcode` against
        # a printer reporting `job.gcode`). Basename+casefold cannot tell those
        # two apart, and the handler cannot know which one the printer is
        # reading — so it must protect BOTH. Resolving the active name to the
        # FIRST match and protecting only that was measured deleting the other:
        # `to_delete == ['dirB/job.gcode', 'other.gcode']`, i.e. the file under
        # the nozzle gone. The guard is the only thing standing between a
        # nightly automation and a live print, so every spelling stays.
        async def _run() -> None:
            listing = {
                "dirA/job.gcode": _entry("dirA/job.gcode", days_ago=90),
                "dirB/job.gcode": _entry("dirB/job.gcode", days_ago=90),
                "other.gcode": _entry("other.gcode", days_ago=90),
            }
            client = _cleanup_client(listing, active="job.gcode")
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=False, older_than_days=60, include="*")
            )

            assert result["success"] is True
            assert result["deleted"] == ["other.gcode"]
            assert result["skipped"] == {
                "dirA/job.gcode": "currently_printing",
                "dirB/job.gcode": "currently_printing",
            }
            # The stronger claim, stated as the calls rather than as the report:
            # neither spelling of the active file was ever sent to 1047.
            requested = [c.args[0] for c in client.delete_file.await_args_list]
            assert requested == ["other.gcode"]

        asyncio.run(_run())

    def test_a_colliding_active_name_is_protected_on_a_dry_run_too(self) -> None:
        # The same rule on the reporting branch, where nothing is deleted: a dry
        # run that lists `dirB/job.gcode` under would_delete is telling the user
        # their live print is scheduled for deletion, which is the misleading
        # half of the same bug.
        async def _run() -> None:
            listing = {
                "dirA/job.gcode": _entry("dirA/job.gcode", days_ago=90),
                "dirB/Job.GCODE": _entry("dirB/Job.GCODE", days_ago=90),
                "old.gcode": _entry("old.gcode", days_ago=90),
            }
            client = _cleanup_client(listing, active="/local/dirA/job.gcode")
            hass = _cleanup_hass(client)

            result = await _async_cleanup_files(
                hass, _cleanup_call(dry_run=True, older_than_days=60, include="*")
            )

            assert result["would_delete"] == ["old.gcode"]
            assert result["skipped"] == {
                "dirA/job.gcode": "currently_printing",
                "dirB/Job.GCODE": "currently_printing",
            }
            client.delete_file.assert_not_called()

        asyncio.run(_run())

    def test_the_resolver_hands_back_every_key_that_could_be_the_active_one(
        self,
    ) -> None:
        # The helper itself, not through the service: the whole fix is "return
        # every match", and the shapes worth pinning are (a) two colliding keys
        # both come back, (b) a name matching nothing comes back empty rather
        # than as a near-miss, and (c) an empty/absent active name comes back
        # empty — which the handler reads as "nothing to protect", NOT as
        # "protect everything".
        listing = {
            "dirA/job.gcode": _entry("dirA/job.gcode", days_ago=90),
            "dirB/Job.GCODE": _entry("dirB/Job.GCODE", days_ago=90),
            "job.gcode": _entry("job.gcode", days_ago=90),
            "other.gcode": _entry("other.gcode", days_ago=90),
        }
        assert _listed_names_for(listing, "/local/dirA/job.gcode") == [
            "dirA/job.gcode",
            "dirB/Job.GCODE",
            "job.gcode",
        ]
        assert _listed_names_for(listing, "JOB.gcode") == [
            "dirA/job.gcode",
            "dirB/Job.GCODE",
            "job.gcode",
        ]
        assert _listed_names_for(listing, "other.gcode") == ["other.gcode"]
        assert _listed_names_for(listing, "missing.gcode") == []
        for no_name in (None, "", "   "):
            assert _listed_names_for(listing, no_name) == [], no_name
        # A name that is only a directory ("/") resolves to nothing, rather than
        # to every entry whose basename happens to be empty.
        assert _listed_names_for({"weird/": _entry("weird/", days_ago=90)}, "/") == []

    def test_every_refused_reason_the_printer_can_give_is_reported(self) -> None:
        # Three refusal texts, and until now only the first was asserted: a
        # cleanup that told the user "printer refused the delete" for a printer
        # that said BUSY, or that dropped the code a support thread is asking
        # for, left the suite green. The codes come from the double, the texts
        # from `_cleanup_failure_text`, so each branch is pinned to its code.
        async def _run() -> None:
            cases = {
                CC2_ERROR_FILE_DELETE_FAILED: "printer refused the delete",
                CC2_ERROR_PRINTER_BUSY: "printer is busy",
                # An unmapped code keeps the number: it is the one thing a
                # report on GitHub is read for.
                1003: "printer refused the delete (error_code 1003)",
            }
            for code, expected in cases.items():
                listing = {"old.gcode": _entry("old.gcode", days_ago=90)}
                client = _cleanup_client(listing)
                client.delete_file = AsyncMock(return_value=code)
                hass = _cleanup_hass(client)

                result = await _async_cleanup_files(
                    hass, _cleanup_call(dry_run=False, older_than_days=60)
                )

                assert result["success"] is False, code
                assert result["failed"] == {"old.gcode": expected}, code
                # Nothing vanished, so nothing is claimed as deleted.
                assert result["deleted"] == [], code

            # 1009 through the double's own knob, which is how the rest of the
            # tests reach it and what keeps the knob from being dead code.
            client = _cleanup_client(_cleanup_listing(), busy=["old.gcode"])
            result = await _async_cleanup_files(
                _cleanup_hass(client), _cleanup_call(dry_run=False, older_than_days=60)
            )
            assert result["failed"] == {"old.gcode": "printer is busy"}

        asyncio.run(_run())

    def test_a_real_run_says_what_it_deleted_and_what_it_did_not(self) -> None:
        # The message is the whole user-visible output of a nightly automation:
        # a notify template reading `response.message` is all most users ever
        # see. The dry-run texts are asserted elsewhere; the destructive
        # branch's three texts were not, so a message that said "deleted 1 of
        # 1" for a run that deleted nothing, or that hid the refused half of a
        # partial run, passed.
        async def _run() -> None:
            # The clean run.
            client = _cleanup_client(_cleanup_listing())
            result = await _async_cleanup_files(
                _cleanup_hass(client), _cleanup_call(dry_run=False, older_than_days=60)
            )
            assert result["message"] == "deleted 1 of 1 selected file(s)"

            # The partial run: the refused half has to be in the text, or the
            # notification reads as a complete success.
            listing = {
                "gone.gcode": _entry("gone.gcode", days_ago=90),
                "stuck.gcode": _entry("stuck.gcode", days_ago=90),
            }
            client = _cleanup_client(listing, vanishing=["gone.gcode"])
            result = await _async_cleanup_files(
                _cleanup_hass(client), _cleanup_call(dry_run=False, older_than_days=60)
            )
            assert result["message"] == (
                "deleted 1 of 2 selected file(s); 1 refused or unconfirmed"
            )

            # The nothing-to-do run: it names the cutoff it used, and it is a
            # real run, so this is the text a nightly job actually reports.
            client = _cleanup_client(_cleanup_listing())
            result = await _async_cleanup_files(
                _cleanup_hass(client),
                _cleanup_call(dry_run=False, older_than_days=365),
            )
            assert result["success"] is True
            assert "no file was older than 365 day(s)" in str(result["message"])
            client.delete_file.assert_not_called()

        asyncio.run(_run())

    def test_an_empty_listing_says_the_printer_listed_nothing(self) -> None:
        # The other branch of the pattern note, and the one never asserted: a
        # printer with no files at all must be told apart from a printer whose
        # files were all too new and from an include pattern that matched
        # nothing. All three report success with an empty list; only the message
        # says which happened, and the empty-listing case used to fall through
        # to the pattern text — blaming a pattern on a printer with no files.
        async def _run() -> None:
            client = _cleanup_client({})
            result = await _async_cleanup_files(
                _cleanup_hass(client), _cleanup_call(dry_run=True)
            )

            assert result["success"] is True
            assert result["would_delete"] == []
            assert result["message"] == (
                "the printer listed no files, so there was nothing to clean up"
            )

        asyncio.run(_run())

    def test_the_pattern_note_survives_into_a_real_run(self) -> None:
        # The note is asserted on the dry-run branch only, where it is one of
        # two texts; on the destructive branch it competes with the
        # "deleted N of M" and "no file was older than" texts, and losing there
        # would put a nightly job back to looking like an ordinary clean run.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing())
            result = await _async_cleanup_files(
                _cleanup_hass(client), _cleanup_call(dry_run=False, include="")
            )

            assert result["success"] is True
            assert result["deleted"] == []
            message = str(result["message"])
            assert "matched the include pattern(s) (empty)" in message
            assert "nothing was deleted" in message
            client.delete_file.assert_not_called()

        asyncio.run(_run())

    def test_an_unreadable_reply_is_not_reported_as_an_unreachable_printer(
        self,
    ) -> None:
        # A printer that ANSWERED with something unreadable is a firmware
        # problem; a printer that did not answer is a network problem. Telling
        # the user "printer not reachable" for the first sends them at the
        # router, so the two diagnoses are worded apart — while both stay a
        # failure with nothing deleted.
        async def _run() -> None:
            client = _cleanup_client(_cleanup_listing(), re_list_unusable=True)
            # Break the FIRST listing too, so the run fails before selecting.
            client.get_verified_file_list = AsyncMock(
                side_effect=CC2UnusableFileListError("not a file listing")
            )
            result = await _async_cleanup_files(
                _cleanup_hass(client), _cleanup_call(dry_run=True)
            )
            assert result["success"] is False
            message = _error_message(result)
            assert "did not answer" not in message
            assert "unreadable" in message
            assert "CC2UnusableFileListError" in message

            client = _cleanup_client(_cleanup_listing())
            client.get_verified_file_list = AsyncMock(
                side_effect=ElegooPrinterConnectionError("no route to host")
            )
            result = await _async_cleanup_files(
                _cleanup_hass(client), _cleanup_call(dry_run=True)
            )
            assert "did not answer the file list" in _error_message(result)

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

    # --- the upload lock (one printer, two ways to touch its storage) ------

    @staticmethod
    def _real_lock_client(
        listing: dict[str, PrinterFile], order: list[str]
    ) -> MagicMock:
        """Build a cleanup client whose upload lock is a real lock, not a mock."""
        client = _cleanup_client(listing)
        # MagicMock(spec=...) auto-creates an attribute here that supports
        # `async with` vacuously — so a test against it could not tell a held
        # lock from no lock at all. This is the real thing the service holds.
        client.upload_lock = asyncio.Lock()
        original_delete = client.delete_file

        async def _delete(name: str) -> int:
            order.append(f"delete:{name}")
            return await original_delete(name)

        client.delete_file = AsyncMock(side_effect=_delete)
        return client

    async def _hold_upload_lock(self, client: MagicMock, order: list[str]) -> None:
        """Take the client's upload lock, as an upload in flight would."""
        await client.upload_lock.acquire()
        order.append("upload:start")

    def test_a_real_run_waits_for_an_upload_to_finish(self) -> None:
        """
        No delete runs while a ranged upload is being assembled.

        1047 travels over MQTT and an upload is a run of ranged PUTs, so the two
        do not share a transport and the client's own serialisation does not
        order them. What they share is the printer's storage, and the printer is
        the one assembling a half-written file from those ranges — so the delete
        sequence takes the same lock ``upload_gcode``'s caller holds.
        """

        async def _run() -> None:
            order: list[str] = []
            client = self._real_lock_client(_cleanup_listing(), order)
            hass = _cleanup_hass(client)
            await self._hold_upload_lock(client, order)

            task = asyncio.create_task(
                _async_cleanup_files(
                    hass, _cleanup_call(dry_run=False, older_than_days=60)
                )
            )
            # Let the handler list, select and block on the lock.
            for _ in range(10):
                await asyncio.sleep(0)

            assert not task.done(), "the run went ahead during an upload"
            client.delete_file.assert_not_awaited()
            assert order == ["upload:start"]

            client.upload_lock.release()
            result = await asyncio.wait_for(task, timeout=1)

            assert result["success"] is True
            assert result["deleted"] == ["old.gcode"]
            assert order == ["upload:start", "delete:old.gcode"]

        asyncio.run(_run())

    def test_a_dry_run_never_waits_behind_an_upload(self) -> None:
        """
        Reporting changes nothing on the printer, so it takes no lock.

        A nightly dry run blocked behind a large upload would answer "no files
        are old enough" for a question it was never asked, and a caller awaiting
        a report has no reason to wait for someone else's file transfer.
        """

        async def _run() -> None:
            order: list[str] = []
            client = self._real_lock_client(_cleanup_listing(), order)
            hass = _cleanup_hass(client)
            await self._hold_upload_lock(client, order)

            result = await asyncio.wait_for(
                _async_cleanup_files(hass, _cleanup_call(older_than_days=60)),
                timeout=1,
            )

            assert result["success"] is True
            assert result["would_delete"] == ["old.gcode"]
            # Still held, and the report finished anyway.
            assert client.upload_lock.locked()
            assert order == ["upload:start"]

        asyncio.run(_run())

    def test_an_upload_that_starts_a_print_while_cleanup_waits_is_never_deleted(
        self,
    ) -> None:
        """
        The decision to delete is made from data gathered under the lock.

        ``test_a_real_run_waits_for_an_upload_to_finish`` proves the lock is
        honoured. It does NOT prove the decision is fresh — and the gap between
        those two is where the files went. ``_upload_then_start`` holds this same
        lock across the upload AND the ``print_start`` after it, so this sequence
        was reachable:

          1. cleanup lists the printer, reads its status, and picks `old.gcode`
             ("idle, and that file is 90 days old");
          2. cleanup blocks on the held lock;
          3. the upload finishes replacing `old.gcode` and STARTS PRINTING it,
             then releases;
          4. cleanup resumes holding a snapshot of a printer that no longer
             exists and deletes the file under the nozzle.

        The guard inside the lock could not catch it, because it re-reads the
        activity object the run had already collected — it asks nothing. So this
        test drives the real interleaving against a real ``asyncio.Lock`` (a
        ``spec=`` mock supports ``async with`` vacuously and would pass whether or
        not the code ever took the lock) and asserts the outcome the user needs:
        `old.gcode` comes back ``currently_printing`` and no 1047 is ever sent for
        it.

        The re-uploaded entry keeps its OLD create_time on purpose. If it were
        re-stamped as new, the age filter would skip the file too and the test
        would pass without the active-file guard working at all; leaving it old
        means the ONLY thing that can save this file is the fresh status read.
        """

        async def _run() -> None:
            order: list[str] = []
            listing = _cleanup_listing()
            client = self._real_lock_client(listing, order)
            # The printer starts idle and naming no file, which is what makes
            # `old.gcode` a candidate at the moment cleanup first looks.
            status = client.printer_data.status
            hass = _cleanup_hass(client)

            # Record WHEN the read side runs, so "the decision was made under the
            # lock" is a measurement rather than a claim about code layout.
            original_list = client.get_verified_file_list.side_effect
            original_refresh = client.refresh_status.side_effect

            async def _listed() -> dict[str, PrinterFile]:
                order.append("list")
                return await original_list()

            async def _refreshed() -> None:
                order.append("refresh")
                await original_refresh()

            client.get_verified_file_list = AsyncMock(side_effect=_listed)
            client.refresh_status = AsyncMock(side_effect=_refreshed)

            await self._hold_upload_lock(client, order)

            cleanup = asyncio.create_task(
                _async_cleanup_files(
                    hass, _cleanup_call(dry_run=False, older_than_days=60)
                )
            )
            for _ in range(10):
                await asyncio.sleep(0)
            assert not cleanup.done(), "the run finished during an upload"

            # The upload lands while cleanup is parked: `old.gcode` is replaced
            # and is now the job the printer is reading.
            listing["old.gcode"] = _entry("old.gcode", days_ago=90)
            status.print_info.filename = "old.gcode"
            status.current_status = ElegooMachineStatus.PRINTING
            order.append("upload:started_printing")
            client.upload_lock.release()
            order.append("upload:released")

            result = await asyncio.wait_for(cleanup, timeout=1)

            assert result["deleted"] == [], result
            # `old.gcode` survived because the printer named it as its job, and
            # `fresh.gcode` because it is one day old — so the survival is the
            # active-file guard's doing and not the age filter's.
            assert result["skipped"] == {
                "old.gcode": "currently_printing",
                "fresh.gcode": "younger_than_limit",
            }, result
            client.delete_file.assert_not_awaited()
            # Both reads happened AFTER the lock was released, i.e. after the
            # upload-and-start had already landed. `refresh` is the fresh 1002
            # that the active-file guard is decided from.
            assert order.index("upload:released") < order.index("list"), order
            assert order.index("upload:released") < order.index("refresh"), order

        asyncio.run(_run())

    def test_a_dry_run_still_never_pays_for_the_lock_it_does_not_need(
        self,
    ) -> None:
        """
        Moving the read side inside the lock must not move the dry run with it.

        The fix for the stale-snapshot race relocates the listing, the status read
        and the selection. A dry run deletes nothing, so it must keep answering
        immediately from behind a held lock rather than queueing for an upload it
        cannot interfere with — and it must still not pay for the fresh 1002.
        """

        async def _run() -> None:
            order: list[str] = []
            client = self._real_lock_client(_cleanup_listing(), order)
            status = client.printer_data.status
            hass = _cleanup_hass(client)
            await self._hold_upload_lock(client, order)

            result = await asyncio.wait_for(
                _async_cleanup_files(hass, _cleanup_call(older_than_days=60)),
                timeout=1,
            )

            assert result["success"] is True
            assert result["would_delete"] == ["old.gcode"]
            assert client.upload_lock.locked()
            assert order == ["upload:start"]
            # A dry run reads the cache, as before: no 1002 for a name it cannot
            # act on.
            assert status.refreshes == []
            client.refresh_status.assert_not_awaited()

        asyncio.run(_run())
