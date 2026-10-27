"""Tests for the CC2 file listing (method 1044)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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

# The one readable file the strict tests fill the cache with, so that a refusal
# has something to destroy and a cache assertion can prove it did not.
KEEP = {"filename": "keep.gcode", "size": 1}


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
    with _broker_delivers(client, _response(SKULLBOWL, FOLDER)):
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
    with _broker_delivers(client, _response(broken, SKULLBOWL)):
        files = asyncio.run(client.get_file_list())
    assert list(files) == [SKULLBOWL["filename"]]


# --- get_verified_file_list: what makes a 1044 reply usable ----------------
#
# The lenient tests above are about DISPLAY: a reply it cannot read keeps the
# old listing and nobody is harmed. These are about DESTRUCTION. Anything that
# deletes files has to re-list to prove they are gone (the cleanup service that
# drove these gates was removed for #432; the probe script still re-lists
# through this method); if it accepts a reply that is not a trustworthy
# inventory, it reports "deleted N of N" for files that are all still on the
# printer, and a nightly automation that trusts that report never retries. So
# the strict method has to fail closed, and
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
        side_effect=_delivering(
            client, _response({"filename": "keep.gcode", "size": 1})
        ),
    ):
        asyncio.run(client.get_verified_file_list())
    assert list(client.printer_data.file_list) == ["keep.gcode"]
    return client


def _delivering(client: ElegooCC2Client, reply: dict) -> Any:
    """
    Return a ``_send_command`` side_effect that dispatches `reply` the way MQTT does.

    Every strict test below needs to say something about what a refused reply
    does to ``printer_data.file_list``, and for years those assertions were
    vacuous: they patched ``_send_command`` with a bare ``AsyncMock`` carrying
    only a ``return_value``, so the reply never reached ``_handle_response``, the
    listener never ran, and the cache was preserved by the TEST HARNESS rather
    than by the client. The assertions stayed green through the whole period in
    which production was wiping the cache on exactly those replies.

    This wrapper restores the production ordering. Real `_send_command` does not
    obtain a reply by returning one; the listener receives the message,
    ``_handle_response`` dispatches it to the method-specific handler (which for
    1044 WRITES THE CACHE), and only then is the waiting `_send_command` woken
    with the same envelope. So the fake hands the reply to the real
    ``_handle_response`` first and returns it second. Now a test that asserts
    the select survived a refused 1044 is measuring the client's behaviour, and
    it fails if the writer ever goes back to trusting a refusal.
    """

    async def _send(*_args: Any, **_kwargs: Any) -> dict:
        await client._handle_response(reply)
        return reply

    return _send


def _broker_delivers(client: ElegooCC2Client, reply: dict) -> Any:
    """Patch `_send_command` to deliver `reply` through the real response path."""
    return patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        side_effect=_delivering(client, reply),
    )


def _raw(result: object) -> dict:
    """Wrap `result` in a 1044 envelope exactly as given, error_code included or not."""
    return {"id": 1, "method": CC2_CMD_GET_FILE_LIST, "result": result}


def test_strict_method_refuses_a_non_zero_error_code() -> None:  # noqa: D103
    client = _strict_client()
    with (
        _broker_delivers(client, _raw({"error_code": 1002})),
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
            _broker_delivers(client, _raw(refusal)),
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
            _broker_delivers(client, _raw({"error_code": unreadable, "file_list": []})),
            pytest.raises(CC2UnusableFileListError),
        ):
            asyncio.run(client.get_verified_file_list())
        assert list(client.printer_data.file_list) == ["keep.gcode"], unreadable


def test_strict_method_accepts_a_missing_error_code_as_no_refusal() -> None:  # noqa: D103
    # ASSUMPTION, stated because the protocol does not settle it: every reply
    # this repo has actually captured (1002 status, 1044 file list, 1046 file
    # detail, docs/CC2_PROTOCOL.md) includes `error_code: 0`, but the envelope
    # table only says result "includ[es]" it — no method documents it as
    # REQUIRED, and 1047's reply is documented nowhere (its only measured
    # answer was a bare 1003). So a reply that omits it is treated as "said
    # nothing", NOT as a refusal: a firmware that
    # answers 1044 with a bare {"file_list": [...]} is a listing we can read,
    # and refusing one would make the service unable to clean up a printer it
    # can otherwise read, forever. The direction of the guess is bounded by the
    # shape rule below it — absence is only ever accepted when `file_list`
    # carries an actual list, so this can never turn a reply that names no
    # files into an empty inventory.
    client = _strict_client()
    with _broker_delivers(
        client, _raw({"file_list": [{"filename": "new.gcode", "size": 2}]})
    ):
        files = asyncio.run(client.get_verified_file_list())
    assert list(files) == ["new.gcode"]


def test_strict_method_accepts_error_code_zero_with_an_empty_listing() -> None:  # noqa: D103
    # The distinction the whole contract exists for, unchanged by the refusal
    # check: an EMPTY PRINTER answers `error_code: 0, file_list: []` and that IS
    # usable — it is what confirms a delete. Refusing it would make the service
    # report a failure forever on a printer with nothing left to delete.
    client = _client()
    with _broker_delivers(client, _raw({"error_code": 0, "file_list": []})):
        assert asyncio.run(client.get_verified_file_list()) == {}


def test_strict_method_accepts_the_documented_reply_shape() -> None:  # noqa: D103
    # The guard against a fix that refuses everything: exactly the reply
    # docs/CC2_PROTOCOL.md records from firmware 02.01.00.00 still parses,
    # folders skipped as before.
    client = _strict_client()
    with _broker_delivers(client, _response(SKULLBOWL, FOLDER)):
        files = asyncio.run(client.get_verified_file_list())
    assert list(files) == [SKULLBOWL["filename"]]


def test_strict_method_still_refuses_a_non_dict_result() -> None:  # noqa: D103
    # The pre-existing shape rule must survive the reordering (the refusal is
    # now checked first, so a list/str/int must not reach `result["error_code"]`
    # or escape a TypeError instead of the documented exception).
    client = _strict_client()
    for shape in (["old.gcode"], "file_list", 7, None):
        with (
            _broker_delivers(client, _raw(shape)),
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
        _broker_delivers(client, _raw({"error_code": 0})),
        pytest.raises(CC2UnusableFileListError),
    ):
        asyncio.run(client.get_verified_file_list())
    assert list(client.printer_data.file_list) == ["keep.gcode"]


def test_lenient_method_never_raises_on_a_refusal_but_stops_believing_it() -> None:  # noqa: D103
    # The asymmetry, pinned so the strict rule is not "helpfully" mirrored into
    # an exception here: the lenient method's callers are the coordinator's
    # refresh and the Refresh File List button, which catch only transport
    # errors. Raising on a refusal would take the file list down at the
    # coordinator; keeping the previous listing costs a stale select until the
    # next refresh. So this must NOT raise — that half of the old behaviour is
    # deliberately unchanged.
    #
    # What changed is what it RETURNS. It used to hand back `{}` for this reply,
    # because `_handle_file_list` wrote the empty `file_list` that came attached
    # to a 1002. A refusal says nothing about what the printer holds, so the
    # writer now leaves the previous listing alone and the lenient caller sees
    # the stale one — the safe direction for a select, and the direction the
    # strict method relies on for its "a failed verification cannot empty the
    # select" guarantee.
    client = _strict_client()
    kept = client.printer_data.file_list["keep.gcode"]
    with _broker_delivers(client, _raw({"error_code": 1002, "file_list": []})):
        assert asyncio.run(client.get_file_list()) == {"keep.gcode": kept}
    assert list(client.printer_data.file_list) == ["keep.gcode"]


def test_strict_method_refuses_a_file_list_that_is_not_a_list() -> None:  # noqa: D103
    # PRESENCE is not a listing. Measured before the fix, against a cache the
    # strict method itself had just filled: `{"error_code": 0, "file_list":
    # None}` (and 0 / "" / {} / "abc") passed the `"file_list" in result` gate,
    # and `_handle_file_list`'s `result.get("file_list") or []` then read the
    # non-list as zero entries — so delete-confirmation reported "deleted 3 of 3"
    # for files the printer still held, the Print File select was emptied, and
    # the automation never retried. Nothing documents that 1044 always answers
    # `file_list` as a JSON array, and 1047's reply is documented nowhere (the
    # only one ever measured was a bare 1003); a serializer emitting `null` for
    # an unset repeated field is the most
    # ordinary way a first hardware run differs from the capture. A value that
    # is not a list says nothing about the printer's contents, so it is not
    # consent to believe the printer is empty.
    #
    # Only NON-lists belong in THIS test. A list whose entries are themselves
    # unreadable is a separate rule, checked one layer down — see
    # test_strict_method_refuses_entries_it_could_not_read. A printer holding
    # only folders still legitimately lists as empty, and stays usable.
    for not_a_list in (None, 0, "", {}, "abc"):
        client = _strict_client()
        with (
            _broker_delivers(client, _raw({"error_code": 0, "file_list": not_a_list})),
            pytest.raises(CC2UnusableFileListError),
        ):
            asyncio.run(client.get_verified_file_list())
        assert list(client.printer_data.file_list) == ["keep.gcode"], not_a_list


def test_strict_method_accepts_an_empty_file_list_over_a_full_cache() -> None:  # noqa: D103
    # The constraint that keeps the rule above from becoming "refuse unless a
    # non-empty list": a real JSON array with nothing in it IS the answer an
    # empty printer gives, and it is the only thing that confirms a delete.
    # Refusing it would leave a printer with nothing left to delete reporting
    # failure forever, so `[]` must replace a non-empty cache rather than raise.
    client = _strict_client()
    with _broker_delivers(client, _raw({"error_code": 0, "file_list": []})):
        assert asyncio.run(client.get_verified_file_list()) == {}
    assert client.printer_data.file_list == {}


def test_lenient_method_ignores_a_file_list_that_is_not_a_list() -> None:  # noqa: D103
    # Same rule on the lenient path, which cannot raise (its callers catch only
    # transport errors) but must not wipe the cache either: a 1044 pushed by
    # another client on the broker, or a Refresh File List press, answering
    # `file_list: null` used to empty the Print File select. Ignoring the reply
    # keeps the listing that WAS readable, which is what a malformed reply
    # deserves. `[]` still replaces it — the empty printer stays empty here.
    for not_a_list in (None, 0, "", {}, "abc"):
        client = _strict_client()
        kept = client.printer_data.file_list["keep.gcode"]
        with _broker_delivers(client, _raw({"error_code": 0, "file_list": not_a_list})):
            files = asyncio.run(client.get_file_list())
        assert files == {"keep.gcode": kept}, not_a_list
        assert list(client.printer_data.file_list) == ["keep.gcode"], not_a_list
        # and the same holds for the listener, which has no reply to return
        client._handle_file_list({"error_code": 0, "file_list": not_a_list})
        assert list(client.printer_data.file_list) == ["keep.gcode"], not_a_list


def test_lenient_method_still_replaces_the_cache_with_an_empty_list() -> None:  # noqa: D103
    # Positive control for the rule above: `[]` is a list, so it is a listing,
    # so the lenient path keeps replacing the cache with it.
    client = _strict_client()
    with _broker_delivers(client, _raw({"error_code": 0, "file_list": []})):
        assert asyncio.run(client.get_file_list()) == {}
    assert client.printer_data.file_list == {}


def test_strict_method_refuses_entries_it_could_not_read() -> None:  # noqa: D103
    # A LIST is not yet a READABLE listing. Measured against the current client
    # before this fix, with one file cached by the strict method itself:
    #
    #   real listing        -> ACCEPTED, 1 file   (correct)
    #   []                  -> ACCEPTED, 0 files  (correct)
    #   ["a","b","c"]       -> ACCEPTED, 0 files  <- cache wiped, "deleted all"
    #   [{"x":1},{"y":2}]   -> ACCEPTED, 0 files  <- same
    #
    # `_handle_file_list` skips entries permissively — non-dicts, anything whose
    # `type` is not "file", and anything `PrinterFile` cannot build or cannot
    # take a name from — so a 1044 reply that plainly says "here are my entries"
    # in a shape this parser does not recognise parsed to zero files, replaced
    # the cache with them, and delete-confirmation reported "deleted N of N" for
    # files the printer had kept. That is the same false success as the refusal
    # envelope and the `file_list: null` reply, one layer further down: the
    # reply is a list, so every gate above it passes.
    #
    # "The printer told me it has entries and I understood none of them" is
    # precisely the state in which a delete must not be reported as confirmed:
    # the deleted file may be sitting in that list in a shape the parser cannot
    # see, and an absence-of-name check against it proves nothing.
    unreadable_lists = (
        # Firmware that sends bare names instead of entry objects.
        ["a.gcode", "b.gcode", "c.gcode"],
        # Entry objects, but none carrying anything PrinterFile can name.
        [{"x": 1}, {"y": 2}],
        [{"size": 12345, "layer": 100}],
        [{"filename": None}, {"filename": ""}],
        # A fold of everything above: still nothing readable.
        ["only-a-string.gcode", {"no": "filename"}],
    )
    for file_list in unreadable_lists:
        client = _strict_client()
        with (
            _broker_delivers(client, _raw({"error_code": 0, "file_list": file_list})),
            pytest.raises(CC2UnusableFileListError),
        ):
            asyncio.run(client.get_verified_file_list())
        assert list(client.printer_data.file_list) == ["keep.gcode"], file_list


def test_strict_method_refuses_a_listing_hiding_one_file_among_junk() -> None:  # noqa: D103
    # The guard is "any entry dropped as unreadable", not "every entry". A
    # listing of one readable file plus one unreadable one LOOKS usable, and the
    # file it does read might not be the file that was just deleted — the
    # unreadable entry could be it. Confirming against a listing that may be
    # hiding the very file in question is the false success this method exists
    # to prevent, so the safe direction costs a retry: "delete not confirmed,
    # re-list failed", and a nightly automation asks again instead of concluding
    # the printer is clean. The lenient path serves the same reply (see
    # test_lenient_method_still_serves_a_partial_listing).
    client = _strict_client()
    with (
        _broker_delivers(
            client,
            _raw(
                {
                    "error_code": 0,
                    "file_list": [
                        {"filename": "readable.gcode", "size": 1},
                        {"no_filename": "at all"},
                    ],
                }
            ),
        ),
        pytest.raises(CC2UnusableFileListError),
    ):
        asyncio.run(client.get_verified_file_list())
    # NOT `== ["keep.gcode"]`, and deliberately so. This reply is a partly
    # readable listing, which is exactly the case the WRITER keeps (a partial
    # listing beats a stale select), and the listener writes it before the
    # strict gates run at all. So the cache legitimately ends up holding the
    # readable part and losing `keep.gcode`. That is safe: safety here comes
    # from the RAISE above — a confirming caller sees the exception and reports every
    # delete unconfirmed — not from the cache keeping its old contents. The
    # guarantee the strict method documents is narrower and still true: a reply
    # that is REFUSED or wholly unreadable cannot empty the select; a partly
    # readable one can trim it, and no delete is confirmed against either.
    assert list(client.printer_data.file_list) == ["readable.gcode"]


def test_strict_method_accepts_a_folders_only_listing_as_empty() -> None:  # noqa: D103
    # The distinction the fix must not blur: a directory entry is SKIPPED ON
    # PURPOSE, not lost. A printer whose local storage holds only folders has no
    # deletable files, and that is a complete, trustworthy answer — refusing it
    # would report "inventory cannot be read" forever on a printer that is
    # genuinely, verifiably clean. So the rule is "nothing dropped as
    # unreadable", not "at least one file parsed".
    #
    # Both spellings this repo has seen: `type: "dir"` in the fixtures and
    # `type: "directory"` in docs/CC2_PROTOCOL.md's own 1044 sample.
    for folders in (
        [{"filename": "models", "type": "dir"}],
        [
            {"filename": "models", "type": "directory"},
            {"filename": "gcode", "type": "dir"},
        ],
        # A non-file entry with no type-adjacent fields at all is still a
        # deliberate non-file, not an unreadable one.
        [{"filename": "sub", "type": "folder"}],
    ):
        client = _strict_client()
        with _broker_delivers(client, _raw({"error_code": 0, "file_list": folders})):
            files = asyncio.run(client.get_verified_file_list())
        assert files == {}, folders
        assert client.printer_data.file_list == {}, folders


def test_strict_method_still_accepts_an_empty_and_a_mixed_listing() -> None:  # noqa: D103
    # Positive controls for the two rules above, so neither can be written as
    # "refuse unless something parsed":
    #   * `[]` is what an EMPTY PRINTER answers and the only thing that confirms
    #     a delete (also pinned on its own above);
    #   * files + folders is the one reply shape this repo has actually
    #     captured from firmware 02.01.00.00, folders skipped, files kept.
    client = _strict_client()
    with _broker_delivers(client, _raw({"error_code": 0, "file_list": []})):
        assert asyncio.run(client.get_verified_file_list()) == {}
    assert client.printer_data.file_list == {}

    client = _strict_client()
    with _broker_delivers(client, _response(SKULLBOWL, FOLDER)):
        files = asyncio.run(client.get_verified_file_list())
    assert list(files) == [SKULLBOWL["filename"]]
    assert client.printer_data.file_list is files


def test_lenient_method_still_serves_a_partial_listing() -> None:  # noqa: D103
    # The asymmetry, pinned from the other side. The strict path refuses a
    # listing with one unreadable entry; the lenient path MUST keep serving the
    # readable part of it. Its callers are the coordinator's refresh, the
    # Refresh File List button and the MQTT listener — all of them feed the
    # Print File select and catch only transport errors, so a partial listing
    # beats an exception (which would take the select down at the coordinator)
    # and beats a wiped cache. Failing closed belongs on the destructive path
    # only; here it would be a UI outage over one mangled entry.
    reply = _raw(
        {
            "error_code": 0,
            "file_list": [
                SKULLBOWL,
                FOLDER,
                "bare-string.gcode",
                {"no": "filename"},
            ],
        }
    )
    client = _strict_client()
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=reply
    ):
        files = asyncio.run(client.get_file_list())
    assert list(files) == [SKULLBOWL["filename"]]
    assert list(client.printer_data.file_list) == [SKULLBOWL["filename"]]

    # And the listener, which has no reply to return and no caller at all.
    client = _strict_client()
    client._handle_file_list(reply["result"])
    assert list(client.printer_data.file_list) == [SKULLBOWL["filename"]]


# --- the real wire: what the listener actually does to the cache -----------
#
# Everything above reaches the client through a patched `_send_command`. That is
# now enough to be meaningful, because `_broker_delivers` routes the reply
# through the real `_handle_response` first — but it is still a patch, and a
# patch can be wrong in a way that flatters the code. These last tests remove it:
# the client is wired to a fake MQTT transport that only publishes, and the
# printer's answer is injected through `_handle_message`, the same coroutine the
# real `_mqtt_listener` awaits per message. Topic routing, the JSON decode, the
# request-id matching, the response event, `_handle_response`, the writer and the
# strict gates are all production code here.
#
# They exist because the previous round's cache assertions were vacuous in a way
# that was invisible from inside the test file: `_handle_response` dispatches
# EVERY 1044 to the lenient writer on its way to waking `_send_command`, so the
# listener writes the cache before any strict gate runs. Measured through this
# path, before the fix, `error_code 1002 + file_list: []`, a bare-string list,
# no-filename dicts and `filename: ''`/null ALL wiped a readable listing to `{}`
# and emptied the Print File select — while every mocked test in this file saw
# the listing preserved.


def _wire_client() -> tuple[ElegooCC2Client, list[asyncio.Task]]:
    """
    Return a client connected to a publish-only fake transport, plus its tasks.

    "Connected" here means only what `_send_command` checks: registered, with an
    MQTT client object present. Nothing subscribes — the fake transport can
    publish and nothing else, so a reply can only arrive by being pushed through
    `_handle_message`, which is what `_answer_with` below arranges. The task list
    is handed back so the caller keeps the in-flight handler tasks alive and can
    cancel them at the end.
    """
    client = _client()
    client._is_connected = True
    client._is_registered = True
    client.mqtt_client = MagicMock()  # type: ignore[assignment]
    return client, []


def _answer_with(
    client: ElegooCC2Client, pending: list[asyncio.Task], reply: dict
) -> None:
    """
    Make the next command the client sends be answered with `reply`.

    The answer is delivered the way a real one is: as an inbound MQTT message on
    the api_response topic, through `_handle_message`, on its own task — the
    shape of a printer replying on the listener's task. So the reply travels the
    production route (JSON decode, topic routing, request-id matching, the
    response event, `_handle_response`, the writer) rather than being handed to
    the caller. The id is taken from the outgoing request so the waiting
    `_send_command` recognises it.
    """

    async def _publish(_topic: str, payload: str) -> None:
        envelope = {**reply, "id": json.loads(payload)["id"]}
        pending.append(
            asyncio.create_task(
                client._handle_message(
                    f"elegoo/{client.serial_number}/{client._client_id}/api_response",
                    json.dumps(envelope),
                )
            )
        )

    client.mqtt_client.publish = AsyncMock(side_effect=_publish)


def test_a_refused_reply_on_the_real_wire_cannot_empty_the_select() -> None:  # noqa: D103
    # THE pin for Finding B, driven end to end with no method mocked away. The
    # cache starts holding one readable file; the printer then answers a real
    # 1044 request with a refusal. Both things have to hold, and they are
    # different things: the strict caller must raise, AND the select must still
    # hold the file it held. The second is what the lenient listener used to
    # destroy, because it ran first.
    refusals = (
        {"error_code": 1002, "file_list": []},
        {"error_code": CC2_ERROR_FILE_DELETE_FAILED, "file_list": []},
        # A refusal that names the file the caller hopes is gone.
        {"error_code": 1002, "file_list": [{"filename": "keep.gcode", "size": 1}]},
        # An error_code that cannot be read is a refusal, not a 0.
        {"error_code": "abc", "file_list": []},
        # A reply that names no readable file at all: bare strings, and
        # entry objects carrying no usable filename.
        {"error_code": 0, "file_list": ["old-a.gcode", "old-b.gcode"]},
        {"error_code": 0, "file_list": [{"size": 12345}]},
        {"error_code": 0, "file_list": [{"filename": "", "type": "file"}]},
        {"error_code": 0, "file_list": [{"filename": None, "type": "file"}]},
        # A `filename` that is not a string at all (Finding A).
        {"error_code": 0, "file_list": [{"filename": ["old-a.gcode"], "type": "file"}]},
        # A `type` this client cannot read (Finding C).
        {"error_code": 0, "file_list": [{"filename": "keep.gcode", "type": "File"}]},
        # Not a listing in the first place.
        {"error_code": 0, "file_list": None},
        {"error_code": 1002},
    )
    for result in refusals:
        client, pending = _wire_client()
        _answer_with(client, pending, _raw({"error_code": 0, "file_list": [KEEP]}))
        asyncio.run(client.get_verified_file_list())
        assert list(client.printer_data.file_list) == ["keep.gcode"]

        _answer_with(client, pending, _raw(result))
        with pytest.raises(CC2UnusableFileListError):
            asyncio.run(client.get_verified_file_list())
        assert list(client.printer_data.file_list) == ["keep.gcode"], result

        for task in pending:
            task.cancel()


def test_an_unsolicited_refusal_on_the_real_wire_cannot_empty_the_select() -> None:  # noqa: D103
    # The same guarantee with no caller at all. Another client on the printer's
    # broker asks for the file list and gets a refusal; our listener sees the
    # reply, and there is nobody to raise at — the ONLY thing standing between
    # that reply and the Print File select is the writer's own judgement. Before
    # the fix this wiped the cache for every shape below.
    refusals = (
        {"error_code": 1002, "file_list": []},
        {"error_code": 0, "file_list": ["old-a.gcode"]},
        {"error_code": 0, "file_list": [{"no": "filename"}]},
        {"error_code": 0, "file_list": [{"filename": ["a.gcode"], "type": "file"}]},
        {"error_code": 0, "file_list": [{"filename": "a.gcode", "type": "File"}]},
    )
    for result in refusals:
        client, pending = _wire_client()
        _answer_with(client, pending, _raw({"error_code": 0, "file_list": [KEEP]}))
        asyncio.run(client.get_verified_file_list())

        asyncio.run(
            client._handle_message(
                f"elegoo/{client.serial_number}/{client._client_id}/api_response",
                json.dumps(_raw(result)),
            )
        )
        assert list(client.printer_data.file_list) == ["keep.gcode"], result
        for task in pending:
            task.cancel()


# --- Findings A and C: the two fields a bogus entry can hide behind --------


def test_strict_method_refuses_a_filename_that_is_not_a_string() -> None:  # noqa: D103
    # Finding A. `PrinterFile.name` is `str(data.get("filename") or "")`, so
    # checking "did the name come out empty" only catches the instances that
    # coerce to empty. Measured against the previous client, every one of these
    # was ACCEPTED as a real file, under a name no real file could ever have:
    #
    #   filename=['old-a.gcode']        -> name="['old-a.gcode']"
    #   filename={'name': 'old-a.gcode'}-> name="{'name': ...}"
    #   filename=12345 / True / 1.5     -> '12345' / 'True' / '1.5'
    #
    # The dangerous consequence is not the ugly key, it is what a delete
    # confirmation does with it: the printer keeps the file, the bogus key does
    # not match the deleted name, the confirmation reported
    # `success: true, "deleted 1 of 1"`, and the automation never retries. So a
    # non-string `filename` is now DROPPED AS UNREADABLE, which the strict path
    # refuses. `file_info.PrinterFile` is untouched — it is shared with the SDCP
    # path, whose callers only display, so the trust decision belongs here.
    bogus = (
        ["old-a.gcode"],
        {"name": "old-a.gcode"},
        12345,
        True,
        False,
        1.5,
        0,
        {"filename": "nested"},
    )
    for filename in bogus:
        client = _strict_client()
        with (
            _broker_delivers(
                client, _raw({"error_code": 0, "file_list": [{"filename": filename}]})
            ),
            pytest.raises(CC2UnusableFileListError),
        ):
            asyncio.run(client.get_verified_file_list())
        # Not stored under a coerced name either.
        assert list(client.printer_data.file_list) == ["keep.gcode"], filename


def test_lenient_writer_refuses_a_listing_of_only_coerced_filenames() -> None:  # noqa: D103
    # The same entries on the lenient path: nothing is raised, but the writer
    # must not store `["a.gcode"]` under the key `"['a.gcode']"`. A listing
    # whose every file-shaped entry is unreadable names no file this client can
    # use, so it is not allowed to replace one it could use.
    client = _strict_client()
    kept = client.printer_data.file_list["keep.gcode"]
    client._handle_file_list(
        {"error_code": 0, "file_list": [{"filename": ["keep.gcode"], "type": "file"}]}
    )
    assert client.printer_data.file_list == {"keep.gcode": kept}
    assert "['keep.gcode']" not in client.printer_data.file_list


def test_strict_method_refuses_a_type_it_does_not_recognise() -> None:  # noqa: D103
    # Finding C. The old rule was `entry.get("type", "file") != "file"` -> not a
    # file, so a MIS-SPELLED type made a real file invisible while the listing
    # was still accepted: `"File"`, `"file "`, `["file"]`, `0`, `None` all
    # landed in the same branch as a genuine folder, and a listing made only of
    # them was blessed as a complete, empty, trustworthy inventory — a false
    # confirmation through the one door the previous round left open.
    #
    # Only the recognised non-file types (see `CC2_NON_FILE_TYPES`) are skipped
    # as deliberate non-files. Anything else that is present is UNREADABLE, and
    # the strict path refuses. Note `0`: it is falsy, so an `or "file"`-style
    # default would have read it as a file; it is a PRESENT non-string value and
    # therefore junk, same as `None` from a serializer emitting null for an
    # unset enum.
    unrecognised = (
        "File",
        "FILE",
        "file ",
        " file",
        "files",
        ["file"],
        0,
        1,
        None,
        True,
        {},
    )
    for entry_type in unrecognised:
        client = _strict_client()
        with (
            _broker_delivers(
                client,
                _raw(
                    {
                        "error_code": 0,
                        "file_list": [{"filename": "real.gcode", "type": entry_type}],
                    }
                ),
            ),
            pytest.raises(CC2UnusableFileListError),
        ):
            asyncio.run(client.get_verified_file_list())
        # The file is not silently dropped from the select either — the whole
        # reply is refused, so the previous listing survives.
        assert list(client.printer_data.file_list) == ["keep.gcode"], entry_type


def test_strict_method_still_accepts_every_recognised_folder_spelling() -> None:  # noqa: D103
    # The constraint the fix above must not break: a folders-only printer
    # legitimately has nothing deletable, and that is a complete answer. The
    # three spellings this repo has seen stay SKIPPED ON PURPOSE rather than
    # counted as unreadable — tightening `type` must not turn a genuinely clean
    # printer into one whose inventory "cannot be read", forever.
    for folders in (
        [{"filename": "models", "type": "dir"}],
        [{"filename": "gcode", "type": "directory"}],
        [{"filename": "sub", "type": "folder"}],
        [
            {"filename": "models", "type": "dir"},
            {"filename": "gcode", "type": "directory"},
            {"filename": "sub", "type": "folder"},
        ],
    ):
        client = _strict_client()
        with _broker_delivers(client, _raw({"error_code": 0, "file_list": folders})):
            files = asyncio.run(client.get_verified_file_list())
        assert files == {}, folders
        assert client.printer_data.file_list == {}, folders


def test_a_real_file_next_to_a_folder_is_still_listed_and_deletable() -> None:  # noqa: D103
    # The positive control the tightening must keep: the captured fw
    # 02.01.00.00 shape — `type: "file"` entries plus a `type: "dir"` entry —
    # still yields exactly the files, and a filename with spaces, a plus sign
    # and unicode is still the key the caller deletes by. If the type rule were
    # narrowed too far, this is the test that goes red first.
    unicode_name = "Ünïcode_日本語_φ0.4_+pla+.gcode"
    unicode_entry = {"filename": unicode_name, "size": 7, "type": "file"}
    client = _strict_client()
    with _broker_delivers(
        client,
        _response(SKULLBOWL, FOLDER, unicode_entry),
    ):
        files = asyncio.run(client.get_verified_file_list())
    assert sorted(files) == sorted([SKULLBOWL["filename"], unicode_name])
    assert files[unicode_name].size == unicode_entry["size"]
    assert client.printer_data.file_list == files


def test_a_filename_without_a_type_key_is_still_a_file() -> None:  # noqa: D103
    # A missing `type` means "file", as it always has — that is what the
    # measured firmware sends for a G-code entry, and `_strict_client` itself is
    # built on such an entry. Pinned separately so a change to the type rule
    # cannot quietly make an absent key unreadable and break every printer this
    # client can talk to today.
    client = _strict_client()
    with _broker_delivers(
        client,
        _raw({"error_code": 0, "file_list": [{"filename": "plain.gcode", "size": 3}]}),
    ):
        files = asyncio.run(client.get_verified_file_list())
    assert list(files) == ["plain.gcode"]
