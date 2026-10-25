"""Tests for the CC2 file listing (method 1044)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.elegoo_printer.cc2.client import (
    CC2UnusableFileListError,
    ElegooCC2Client,
)
from custom_components.elegoo_printer.cc2.const import (
    CC2_CMD_GET_FILE_LIST,
    CC2_ERROR_FILE_DELETE_FAILED,
)
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
    assert file.tools == [SKULLBOWL["color_map"][0]["t"]]
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


def test_corrupt_timestamp_keeps_the_file() -> None:  # noqa: D103
    # datetime.fromtimestamp raises OverflowError/OSError far outside its range.
    bad = dict(SKULLBOWL, create_time=10**20)
    client = _client()
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=_response(bad)
    ):
        files = asyncio.run(client.get_file_list())
    file = files[SKULLBOWL["filename"]]
    assert file.created is None
    assert file.size == SKULLBOWL["size"]


def test_one_unparseable_entry_does_not_drop_the_listing() -> None:  # noqa: D103
    broken = dict(SKULLBOWL, filename="broken.gcode", size="not a number")
    client = _client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_response(broken, SKULLBOWL),
    ):
        files = asyncio.run(client.get_file_list())
    assert list(files) == [SKULLBOWL["filename"]]


# --- get_verified_file_list: what makes a 1044 reply usable ----------------
#
# The lenient tests above are about DISPLAY: a reply it cannot read keeps the
# old listing and nobody is harmed. These are about DESTRUCTION. `cleanup_files`
# deletes files from the printer and then re-lists to prove they are gone; if it
# accepts a reply that is not a trustworthy inventory, it reports "deleted N of
# N" for files that are all still on the printer, and a nightly automation that
# trusts that report never retries. So the strict method has to fail closed, and
# "fail closed" here means raising, because an empty dict is ALSO what an empty
# printer answers and the two must not be conflated.


def _strict_client() -> ElegooCC2Client:
    """
    Return a client whose cache holds one readable file.

    So that a refusal has something to destroy, and a cache assertion can prove
    an unusable reply left the listing alone.
    """
    client = _client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_response({"filename": "keep.gcode", "size": 1}),
    ):
        asyncio.run(client.get_verified_file_list())
    assert list(client.printer_data.file_list) == ["keep.gcode"]
    return client


def _raw(result: object) -> dict:
    """Wrap `result` in a 1044 envelope exactly as given, error_code included or not."""
    return {"id": 1, "method": CC2_CMD_GET_FILE_LIST, "result": result}


def test_strict_method_refuses_a_non_zero_error_code() -> None:  # noqa: D103
    client = _strict_client()
    with (
        patch.object(
            client,
            "_send_command",
            new_callable=AsyncMock,
            return_value=_raw({"error_code": 1002}),
        ),
        pytest.raises(CC2UnusableFileListError),
    ):
        asyncio.run(client.get_verified_file_list())
    assert list(client.printer_data.file_list) == ["keep.gcode"]


def test_strict_method_refuses_a_refusal_that_also_carries_a_file_list() -> None:  # noqa: D103
    # The hole this guards, measured before the fix: the shared envelope
    # documents `result` as "Response data including error_code"
    # (docs/CC2_PROTOCOL.md:636), so a refusal is reported INSIDE result, and
    # nothing documents that a refusal omits `file_list`. Judging the reply by
    # shape alone accepted `{"error_code": 1002, "file_list": []}` as an
    # authoritative EMPTY inventory — and, worse, replaced the cache with it,
    # emptying the Print File select from a reply that said "I refused".
    # The listing in a refusal is the printer's own words for what it did NOT
    # do; it is not evidence that anything is gone.
    for refusal in (
        {"error_code": 1002, "file_list": []},
        {"error_code": CC2_ERROR_FILE_DELETE_FAILED, "file_list": []},
        # A refusal naming the very file the caller hopes is gone is the most
        # dangerous shape of all: non-empty, so nothing about it looks empty.
        {"error_code": 1002, "file_list": [{"filename": "keep.gcode", "size": 1}]},
    ):
        client = _strict_client()
        with (
            patch.object(
                client,
                "_send_command",
                new_callable=AsyncMock,
                return_value=_raw(refusal),
            ),
            pytest.raises(CC2UnusableFileListError),
        ):
            asyncio.run(client.get_verified_file_list())
        assert list(client.printer_data.file_list) == ["keep.gcode"], refusal


def test_strict_method_refuses_a_non_numeric_error_code() -> None:  # noqa: D103
    # `_error_code` already refuses these for the delete reply, for the same
    # reason: `int("abc")` would raise, `int(0.5)` truncates to 0, and on a
    # destructive path 0 means "the printer did not refuse". A code that cannot
    # be read is not consent, so it is a refusal rather than a default of 0.
    for unreadable in ("abc", None, [], {}, "  ", 0.5, 1.5, True, float("nan")):
        client = _strict_client()
        with (
            patch.object(
                client,
                "_send_command",
                new_callable=AsyncMock,
                return_value=_raw({"error_code": unreadable, "file_list": []}),
            ),
            pytest.raises(CC2UnusableFileListError),
        ):
            asyncio.run(client.get_verified_file_list())
        assert list(client.printer_data.file_list) == ["keep.gcode"], unreadable


def test_strict_method_accepts_a_missing_error_code_as_no_refusal() -> None:  # noqa: D103
    # ASSUMPTION, stated because the protocol does not settle it: every reply
    # this repo has actually captured (1002 status, 1044 file list, 1046 file
    # detail, docs/CC2_PROTOCOL.md) includes `error_code: 0`, but the envelope
    # table only says result "includ[es]" it — no method documents it as
    # REQUIRED, and 1047 documents no response body at all. So a reply that
    # omits it is treated as "said nothing", NOT as a refusal: a firmware that
    # answers 1044 with a bare {"file_list": [...]} is a listing we can read,
    # and refusing one would make the service unable to clean up a printer it
    # can otherwise read, forever. The direction of the guess is bounded by the
    # shape rule below it — absence is only ever accepted when a real
    # `file_list` is present, so this can never turn a shapeless reply into an
    # empty inventory.
    client = _strict_client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_raw({"file_list": [{"filename": "new.gcode", "size": 2}]}),
    ):
        files = asyncio.run(client.get_verified_file_list())
    assert list(files) == ["new.gcode"]


def test_strict_method_accepts_error_code_zero_with_an_empty_listing() -> None:  # noqa: D103
    # The distinction the whole contract exists for, unchanged by the refusal
    # check: an EMPTY PRINTER answers `error_code: 0, file_list: []` and that IS
    # usable — it is what confirms a delete. Refusing it would make the service
    # report a failure forever on a printer with nothing left to delete.
    client = _client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_raw({"error_code": 0, "file_list": []}),
    ):
        assert asyncio.run(client.get_verified_file_list()) == {}


def test_strict_method_accepts_the_documented_reply_shape() -> None:  # noqa: D103
    # The guard against a fix that refuses everything: exactly the reply
    # docs/CC2_PROTOCOL.md records from firmware 02.01.00.00 still parses,
    # folders skipped as before.
    client = _strict_client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_response(SKULLBOWL, FOLDER),
    ):
        files = asyncio.run(client.get_verified_file_list())
    assert list(files) == [SKULLBOWL["filename"]]


def test_strict_method_still_refuses_a_non_dict_result() -> None:  # noqa: D103
    # The pre-existing shape rule must survive the reordering (the refusal is
    # now checked first, so a list/str/int must not reach `result["error_code"]`
    # or escape a TypeError instead of the documented exception).
    client = _strict_client()
    for shape in (["old.gcode"], "file_list", 7, None):
        with (
            patch.object(
                client,
                "_send_command",
                new_callable=AsyncMock,
                return_value=_raw(shape),
            ),
            pytest.raises(CC2UnusableFileListError),
        ):
            asyncio.run(client.get_verified_file_list())
        assert list(client.printer_data.file_list) == ["keep.gcode"], shape


def test_strict_method_still_refuses_a_dict_with_no_file_list() -> None:  # noqa: D103
    # A reply that is not a refusal but carries no listing either: error_code 0
    # with no `file_list` says the request succeeded and names no files, which
    # is not something a delete confirmation can act on.
    client = _strict_client()
    with (
        patch.object(
            client,
            "_send_command",
            new_callable=AsyncMock,
            return_value=_raw({"error_code": 0}),
        ),
        pytest.raises(CC2UnusableFileListError),
    ):
        asyncio.run(client.get_verified_file_list())
    assert list(client.printer_data.file_list) == ["keep.gcode"]


def test_lenient_method_still_ignores_error_code_by_design() -> None:  # noqa: D103
    # Pinned so the strict fix is not "helpfully" mirrored here: the lenient
    # method's callers are the coordinator's refresh and the Refresh File List
    # button, which catch only transport errors. Raising on a refusal would take
    # the file list down at the coordinator; keeping the previous listing costs
    # a stale select until the next refresh. This asymmetry is deliberate.
    client = _strict_client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_raw({"error_code": 1002, "file_list": []}),
    ):
        assert asyncio.run(client.get_file_list()) == {}
