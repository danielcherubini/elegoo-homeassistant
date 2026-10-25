"""
Which files on a printer's local storage a cleanup may delete (#432).

Two halves, one question — "may this file be deleted, and how do we know?".

The first is pure selection: no Home Assistant, no transport, no mocks. The
assertions that matter most there are the fail-safe ones - a file with no usable
date, a file whose date is a 1970 epoch stamp from a printer whose clock was
never set, and the file being printed must never land on the delete side.

The second is the precondition selection assumes: that the thing it was handed
IS an inventory. Both destructive verdicts ("this file is old enough to go" and
"this file is gone now") are only as good as the listing they were read from,
and the CC2 client used to answer a reply it could not read with an empty
listing and no exception. So the reply-trust contract of the client methods the
cleanup path calls lives here too, at the bottom of the file, in the mock-free
style the rest of it uses: a 1044 reply that is not a listing raises, and a 1047
reply whose error code cannot be read is a refusal rather than an exception.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.elegoo_printer.cc2.cleanup import (
    SKIP_ACTIVE,
    SKIP_EXCLUDED,
    SKIP_NO_TIMESTAMP,
    SKIP_NOT_INCLUDED,
    SKIP_TOO_NEW,
    select_deletions,
)
from custom_components.elegoo_printer.cc2.client import (
    CC2_ERROR_REPLY_UNPARSEABLE,
    CC2UnusableFileListError,
    ElegooCC2Client,
)
from custom_components.elegoo_printer.cc2.const import (
    CC2_CMD_DELETE_FILE,
    CC2_CMD_GET_FILE_LIST,
    CC2_ERROR_FILE_DELETE_FAILED,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
)
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.file_info import PrinterFile
from custom_components.elegoo_printer.sdcp.models.printer import Printer

# A fixed clock: the tests must not drift with the machine's date.
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)

# One day, in the unit the printer reports (unix seconds).
SECONDS_PER_DAY = 86_400

# An epoch-near create_time, from #426's report of a Centauri Carbon that had
# never had its clock set. Deliberately NOT zero: zero is falsy and
# PrinterFile already reads that as unset. This one parses, to
# 1970-01-01 23:32:00+00:00, so a truthiness-only age filter deletes the file.
NEAR_EPOCH_CREATE_TIME = 84_720

# A create_time below the 2000 floor (1999-06-15) and one above it
# (2001-01-01), so the floor is pinned as a boundary and not as "1970 only".
# 946684800 is the floor exactly and counts as usable, so the below value has
# to sit clearly under it.
BELOW_FLOOR_CREATE_TIME = 929_448_000  # 1999-06-15 12:00 UTC (before the floor)
ABOVE_FLOOR_CREATE_TIME = 978_307_200  # 2001-01-01 00:00 UTC (after the floor)

# The age limit the reporter asked for in #432.
DAYS = 60

# How far in the past to date a file so it counts as clearly old / clearly new.
OLD_DAYS = DAYS + 30
NEW_DAYS = 1


def _file(
    name: str, *, created_days_ago: int | None = None, **extra: Any
) -> PrinterFile:
    """Build one listing entry; no create_time when created_days_ago is None."""
    data: dict[str, Any] = {"filename": name, "size": 1024, "type": "file"}
    if created_days_ago is not None:
        data["create_time"] = int(NOW.timestamp()) - created_days_ago * SECONDS_PER_DAY
    data.update(extra)
    return PrinterFile(data)


def _listing(**entries: PrinterFile) -> dict[str, PrinterFile]:
    """Key a listing the way the client does: by the printer's own filename."""
    return {file.name: file for file in entries.values()}


def test_default_patterns_delete_every_old_file() -> None:  # noqa: D103
    # The reporter's starting point: include = all, exclude = nothing.
    files = _listing(
        old=_file("old.gcode", created_days_ago=OLD_DAYS),
        fresh=_file("fresh.gcode", created_days_ago=NEW_DAYS),
    )

    to_delete, skipped = select_deletions(files, older_than_days=DAYS, now=NOW)

    assert to_delete == ["old.gcode"]
    assert skipped == [("fresh.gcode", SKIP_TOO_NEW)]


def test_exclude_wins_over_include() -> None:  # noqa: D103
    # "include=ECC* and exclude=Keep*" (#432): a kept file is kept even though
    # it matches include and is old enough.
    files = _listing(
        keep=_file("Keep_this.gcode", created_days_ago=OLD_DAYS),
        gone=_file("ECC2_skull.gcode", created_days_ago=OLD_DAYS),
    )

    to_delete, skipped = select_deletions(
        files,
        include=("ECC*", "Keep*"),
        exclude=("Keep*",),
        older_than_days=DAYS,
        now=NOW,
    )

    assert to_delete == ["ECC2_skull.gcode"]
    assert skipped == [("Keep_this.gcode", SKIP_EXCLUDED)]


def test_include_limits_the_selection_and_an_empty_include_selects_nothing() -> None:  # noqa: D103
    # Fail-safe direction: a pattern that matches nothing, and a pattern list
    # with no patterns at all, both delete nothing.
    files = _listing(
        ecc=_file("ECC2_skull.gcode", created_days_ago=OLD_DAYS),
        other=_file("other.gcode", created_days_ago=OLD_DAYS),
    )

    to_delete, skipped = select_deletions(
        files, include=("ECC*",), older_than_days=DAYS, now=NOW
    )
    assert to_delete == ["ECC2_skull.gcode"]
    assert skipped == [("other.gcode", SKIP_NOT_INCLUDED)]

    assert select_deletions(files, include=(), older_than_days=DAYS, now=NOW) == (
        [],
        [("ECC2_skull.gcode", SKIP_NOT_INCLUDED), ("other.gcode", SKIP_NOT_INCLUDED)],
    )

    assert (
        select_deletions(files, include=("nomatch*",), older_than_days=DAYS, now=NOW)[0]
        == []
    )


def test_file_without_a_creation_date_is_never_deleted() -> None:  # noqa: D103
    # Unknown age is not "old enough": an age filter that treats missing as
    # old deletes the newest file on the printer the moment it stops reporting
    # dates for it.
    files = _listing(nodate=_file("brand_new.gcode"))

    to_delete, skipped = select_deletions(files, older_than_days=DAYS, now=NOW)

    assert files["brand_new.gcode"].created is None
    assert to_delete == []
    assert skipped == [("brand_new.gcode", SKIP_NO_TIMESTAMP)]


def test_near_epoch_create_time_is_never_deleted() -> None:  # noqa: D103
    # The #426 bug on a destructive path. This create_time PARSES - assert
    # that first, or the test quietly becomes a test of the None path, which is
    # the mistake that let a 798-test-green suite ship a file-deleting filter.
    files = _listing(
        epoch=_file("clock_never_set.gcode", create_time=NEAR_EPOCH_CREATE_TIME)
    )

    to_delete, skipped = select_deletions(files, older_than_days=DAYS, now=NOW)

    assert files["clock_never_set.gcode"].created is not None
    assert to_delete == []
    assert skipped == [("clock_never_set.gcode", SKIP_NO_TIMESTAMP)]


def test_creation_dates_below_the_floor_are_treated_as_unset() -> None:  # noqa: D103
    # The floor is a boundary, not "reject 1970 specifically": 1999 is below it
    # and unreadable as an age, 2001 is above it and still judgeable - so a
    # file with a correct clock that really is old still gets deleted.
    files = _listing(
        below=_file("below_floor.gcode", create_time=BELOW_FLOOR_CREATE_TIME),
        above=_file("above_floor.gcode", create_time=ABOVE_FLOOR_CREATE_TIME),
    )
    assert files["below_floor.gcode"].created is not None
    assert files["above_floor.gcode"].created is not None

    to_delete, skipped = select_deletions(files, older_than_days=DAYS, now=NOW)

    assert to_delete == ["above_floor.gcode"]
    assert skipped == [("below_floor.gcode", SKIP_NO_TIMESTAMP)]


def test_file_exactly_at_the_limit_is_not_old_enough_yet() -> None:  # noqa: D103
    # "older than 60 days" means strictly past the cutoff: a file created
    # exactly at it is kept, one second of age more deletes it.
    at_limit = _file("at_limit.gcode", created_days_ago=DAYS)
    one_more = _file("one_more.gcode", created_days_ago=DAYS + 1)
    files = _listing(at=at_limit, more=one_more)

    to_delete, _ = select_deletions(files, older_than_days=DAYS, now=NOW)

    assert to_delete == ["one_more.gcode"]


def test_active_file_survives_every_filter() -> None:  # noqa: D103
    # The guard runs before the patterns, so include=* cannot reach the file
    # the printer is reading right now.
    files = _listing(active=_file("printing.gcode", created_days_ago=OLD_DAYS))

    to_delete, skipped = select_deletions(
        files,
        include=("*",),
        older_than_days=DAYS,
        now=NOW,
        active_filename="printing.gcode",
    )

    assert to_delete == []
    assert skipped == [("printing.gcode", SKIP_ACTIVE)]


def test_active_guard_compares_the_printers_own_filename_exactly() -> None:  # noqa: D103
    # Both directions, because exact equality FAILS OPEN: if the printer ever
    # spells the same file differently in its status push than in the 1044
    # listing, the file being printed becomes deletable. Pinning that here
    # means changing the basis later is a deliberate edit, not a silent one.
    files = _listing(
        exact=_file("printing.gcode", created_days_ago=OLD_DAYS),
        padded=_file("printing.gcode ", created_days_ago=OLD_DAYS),
    )

    guarded = select_deletions(
        files,
        older_than_days=DAYS,
        now=NOW,
        active_filename="printing.gcode",
    )
    # The exact name is skipped; the name differing by one trailing space is
    # NOT recognised as the active file, so it stays a candidate.
    assert guarded == (
        ["printing.gcode "],
        [("printing.gcode", SKIP_ACTIVE)],
    )

    # Case is likewise not folded: "PRINTING.gcode" is not "printing.gcode".
    assert select_deletions(
        {"printing.gcode": files["printing.gcode"]},
        older_than_days=DAYS,
        now=NOW,
        active_filename="PRINTING.gcode",
    )[0] == ["printing.gcode"]


def test_patterns_ignore_the_host_path_case_rules() -> None:  # noqa: D103
    # fnmatch.fnmatch normalises both sides through os.path.normcase, which is
    # the IDENTITY on Linux - so the naive version of this test passes on every
    # machine here and fails only for a user running HA on Windows. Patch
    # normcase to do what Windows does (lowercase) so the difference is
    # observable: fnmatch would then match `ecc*` against `ECC2_x.gcode`,
    # fnmatchcase never calls it and cannot.
    files = _listing(ecc=_file("ECC2_skull.gcode", created_days_ago=OLD_DAYS))

    with patch.object(os.path, "normcase", lambda name: name.lower()):
        # Prove the patch is live, so the test cannot be vacuous.
        assert os.path.normcase("ECC") == "ecc"

        lower = select_deletions(
            files, include=("ecc*",), older_than_days=DAYS, now=NOW
        )
        assert lower == ([], [("ECC2_skull.gcode", SKIP_NOT_INCLUDED)])

        upper = select_deletions(
            files, include=("ECC*",), older_than_days=DAYS, now=NOW
        )
        assert upper[0] == ["ECC2_skull.gcode"]


def test_keeps_young_excluded_and_undated_files_with_reasons() -> None:  # noqa: D103
    # One run over a mixed listing: everything that survives is reported with
    # the reason it survived, and both halves are sorted so a dry run reads the
    # same twice.
    files = _listing(
        z_old=_file("zebra.gcode", created_days_ago=OLD_DAYS),
        a_old=_file("alpha.gcode", created_days_ago=OLD_DAYS),
        m_old=_file("middle.gcode", created_days_ago=OLD_DAYS),
        fresh=_file("fresh.gcode", created_days_ago=NEW_DAYS),
        keep=_file("keep.gcode", created_days_ago=OLD_DAYS),
        nodate=_file("nodate.gcode"),
    )

    to_delete, skipped = select_deletions(
        files,
        include=("*.gcode",),
        exclude=("keep*",),
        older_than_days=DAYS,
        now=NOW,
    )

    assert to_delete == ["alpha.gcode", "middle.gcode", "zebra.gcode"]
    assert skipped == [
        ("fresh.gcode", SKIP_TOO_NEW),
        ("keep.gcode", SKIP_EXCLUDED),
        ("nodate.gcode", SKIP_NO_TIMESTAMP),
    ]


# ---------------------------------------------------------------------------
# The listing the selection is read from has to be a listing.
#
# Everything above decides "may this go?" from a dict it was handed. These tests
# decide whether the dict the cleanup path gets from the CC2 client is safe to
# base a delete — or a delete *confirmation* — on. They are in this file rather
# than a transport-test file because the question is a selection question: an
# empty listing and an unreadable reply looked identical, and both destructive
# verdicts are read out of whichever one the client returned.
# ---------------------------------------------------------------------------


def _cc2_client() -> ElegooCC2Client:
    printer = Printer()
    printer.printer_type = PrinterType.FDM
    return ElegooCC2Client("192.0.2.1", "TESTSN", printer=printer)


def _reply(method: int, result: Any) -> dict[str, Any]:
    """Wrap `result` in a 1044/1047 envelope, exactly as the printer sent it."""
    return {"id": 1, "method": method, "result": result}


def _cached_listing(client: ElegooCC2Client) -> dict[str, PrinterFile]:
    """Cache a readable listing, so a later failure has something to keep."""
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_reply(
            CC2_CMD_GET_FILE_LIST,
            {"error_code": 0, "file_list": [{"filename": "old.gcode", "size": 10}]},
        ),
    ):
        return asyncio.run(client.get_verified_file_list())


def test_a_1044_reply_with_no_file_list_raises_not_an_empty_listing() -> None:  # noqa: D103
    # The blocking case, at the layer that creates it. `result.get("file_list")
    # or []` turns an error envelope — or a rejected-parameter reply, or any
    # other shape nobody has captured, since 1047's reply is documented nowhere
    # and the only one ever measured was a bare 1003 refusal — into an EMPTY
    # listing with no exception. A caller confirming a delete
    # reads that as "nothing remains" and reports every accepted delete as a
    # success for files that are all still on the printer.
    client = _cc2_client()
    with (
        patch.object(
            client,
            "_send_command",
            new_callable=AsyncMock,
            return_value=_reply(CC2_CMD_GET_FILE_LIST, {"error_code": 1003}),
        ),
        pytest.raises(CC2UnusableFileListError),
    ):
        asyncio.run(client.get_verified_file_list())


def test_a_1044_reply_that_is_not_a_dict_raises_rather_than_breaking() -> None:  # noqa: D103
    # `result.get(...)` on a list/str/int raises AttributeError, which is not a
    # transport error: it used to escape the MQTT listener, and on the cleanup
    # path it escaped the handler AFTER the deletes had been sent, so the caller
    # got no response and could not tell what had been deleted.
    client = _cc2_client()
    for shape in (["old.gcode"], "file_list", 7, None):
        with (
            patch.object(
                client,
                "_send_command",
                new_callable=AsyncMock,
                return_value=_reply(CC2_CMD_GET_FILE_LIST, shape),
            ),
            pytest.raises(CC2UnusableFileListError),
        ):
            asyncio.run(client.get_verified_file_list())


def test_an_envelope_with_no_result_key_is_not_a_listing_either() -> None:  # noqa: D103
    # The lenient path falls back to the whole envelope, whose own keys
    # (id/method) are proof it is not a listing. The strict path has no such
    # fallback.
    client = _cc2_client()
    with (
        patch.object(
            client,
            "_send_command",
            new_callable=AsyncMock,
            return_value={"id": 1, "method": CC2_CMD_GET_FILE_LIST},
        ),
        pytest.raises(CC2UnusableFileListError),
    ):
        asyncio.run(client.get_verified_file_list())


def test_an_empty_file_list_is_a_listing_and_not_an_error() -> None:  # noqa: D103
    # The distinction the whole contract exists for: a printer with no files
    # answers `file_list: []`, and that IS usable — it confirms deletions. Only
    # a reply with no `file_list` key at all is refused. Without this test the
    # fix could easily be written as "empty means untrusted", which would make
    # the service refuse to clean up an empty printer forever.
    client = _cc2_client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_reply(CC2_CMD_GET_FILE_LIST, {"error_code": 0, "file_list": []}),
    ):
        assert asyncio.run(client.get_verified_file_list()) == {}


def test_an_unusable_reply_keeps_the_previous_listing_untouched() -> None:  # noqa: D103
    # The cache the Print File select reads must not be wiped by a reply nobody
    # can read: an "empty inventory" that came from a confused firmware is the
    # same failure mode as the one the confirmation refuses.
    client = _cc2_client()
    before = _cached_listing(client)
    assert list(before) == ["old.gcode"]

    with (
        patch.object(
            client,
            "_send_command",
            new_callable=AsyncMock,
            return_value=_reply(CC2_CMD_GET_FILE_LIST, {"error_code": 1003}),
        ),
        pytest.raises(CC2UnusableFileListError),
    ):
        asyncio.run(client.get_verified_file_list())

    assert list(client.printer_data.file_list) == ["old.gcode"]


def test_the_lenient_listing_method_still_survives_an_unreadable_reply() -> None:  # noqa: D103
    # The lenient method's callers only display or enumerate (the coordinator's
    # ten-minute refresh, the Refresh File List button), and none of them decide
    # whether a file still exists. For them a stale listing beats a wiped one and
    # beats an exception, so its contract is deliberately unchanged — it must not
    # start raising at the coordinator, which catches only transport errors.
    client = _cc2_client()
    before = _cached_listing(client)

    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_reply(CC2_CMD_GET_FILE_LIST, ["not", "a", "listing"]),
    ):
        # No AttributeError out of `result.get(...)`, and the listing survives.
        assert asyncio.run(client.get_file_list()) is before

    assert list(client.printer_data.file_list) == ["old.gcode"]


def test_a_readable_reply_still_parses_through_the_strict_method() -> None:  # noqa: D103
    # The guard against a fix that refuses everything: the one reply shape this
    # repo has actually captured (fw 02.01.00.00, all 54 files at once) still
    # parses, entries and folders included.
    client = _cc2_client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_reply(
            CC2_CMD_GET_FILE_LIST,
            {
                "error_code": 0,
                "file_list": [
                    {"filename": "old.gcode", "size": 10, "create_time": 1_700_000_000},
                    {"filename": "models", "type": "dir"},
                ],
            },
        ),
    ):
        files = asyncio.run(client.get_verified_file_list())

    assert list(files) == ["old.gcode"]
    assert files["old.gcode"].created is not None


def test_an_unreadable_delete_error_code_is_a_refusal_not_an_exception() -> None:  # noqa: D103
    # `int(result["error_code"])` raised ValueError/TypeError for a code that is
    # not a number — escaping a cleanup loop AFTER earlier deletes had landed and
    # discarding their results. The contract is "a non-zero code is returned
    # rather than raised", so an unreadable code is the most non-zero answer
    # there is, and it must be a code rather than an exception.
    for unreadable in ("abc", None, [], {}, "  ", 0.5, 1.5, True, float("nan")):
        client = _cc2_client()
        with patch.object(
            client,
            "_send_command",
            new_callable=AsyncMock,
            return_value=_reply(CC2_CMD_DELETE_FILE, {"error_code": unreadable}),
        ):
            code = asyncio.run(client.delete_file("locked.gcode"))
        # Non-zero, so a caller asking only "did the printer refuse?" is right,
        # and negative, so it cannot collide with a real CC2 code (1000-9004).
        assert code == CC2_ERROR_REPLY_UNPARSEABLE, unreadable
        assert code != 0

    # `int(0.5)` truncates to 0, and 0 means "the printer did not refuse": a
    # fractional code rounded down would be reported as a confirmed delete. It is
    # in the list above for that reason, not as decoration.
    client = _cc2_client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_reply(CC2_CMD_DELETE_FILE, {"error_code": 0.5}),
    ):
        assert asyncio.run(client.delete_file("locked.gcode")) != 0

    # A numeric string still parses, because that is what a JSON envelope full
    # of strings would carry and it is not a refusal.
    client = _cc2_client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_reply(CC2_CMD_DELETE_FILE, {"error_code": "0"}),
    ):
        assert asyncio.run(client.delete_file("fine.gcode")) == 0

    # And a real code is untouched by the coercion.
    client = _cc2_client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_reply(
            CC2_CMD_DELETE_FILE, {"error_code": CC2_ERROR_FILE_DELETE_FAILED}
        ),
    ):
        assert asyncio.run(client.delete_file("locked.gcode")) == (
            CC2_ERROR_FILE_DELETE_FAILED
        )


def test_a_delete_with_no_reply_at_all_still_raises() -> None:  # noqa: D103
    # The coercion must not swallow the case that IS a failure to answer: no
    # reply, or a reply with no error code, says nothing about the file and is
    # never reported as "refused" — it is reported as never answered.
    for reply in (
        None,
        _reply(CC2_CMD_DELETE_FILE, None),
        _reply(CC2_CMD_DELETE_FILE, {"filename": "maybe.gcode"}),
    ):
        client = _cc2_client()
        with (
            patch.object(
                client, "_send_command", new_callable=AsyncMock, return_value=reply
            ),
            pytest.raises(ElegooPrinterConnectionError),
        ):
            asyncio.run(client.delete_file("ghost.gcode"))
