"""
Which files on a printer's local storage a cleanup may delete (#432).

Selection is the only part of a delete feature that can destroy the wrong
file, so it lives here: a pure module with no Home Assistant and no transport
imports, testable without a single mock. The vocabulary is the reporter's own
from #432 — ``include`` defaults to everything (``all``), ``exclude`` to
nothing (``nothing``), and both hold ``fnmatch`` glob patterns such as
``ECC*`` or ``Keep*``.

Nothing in here deletes anything; it only sorts a listing into candidates and
reasons. The caller (the ``cleanup_files`` service) decides whether to act.
"""

from __future__ import annotations

import fnmatch
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.elegoo_printer.sdcp.models.file_info import PrinterFile

# The reporter's defaults, quoted from #432: "include = all, exclude = nothing".
DEFAULT_INCLUDE = ("*",)
DEFAULT_EXCLUDE: tuple[str, ...] = ()

# The reporter's own number: "delete files older than 60 days" (#432).
DEFAULT_OLDER_THAN_DAYS = 60

# The reasons a listed file survives, so a dry run can say WHY nothing went.
SKIP_NO_TIMESTAMP = "no_creation_date"
SKIP_TOO_NEW = "younger_than_limit"
SKIP_EXCLUDED = "excluded"
SKIP_NOT_INCLUDED = "not_included"
SKIP_ACTIVE = "currently_printing"

# The #426 lesson (``definitions.py`` ``_MIN_VALID_JOB_TIME``, fixed in
# bb572ec) applied to a DESTRUCTIVE path. A printer whose clock has never been
# set reports a SMALL ``create_time`` - not zero, which ``PrinterFile`` already
# reads as unset, but e.g. 84720, which parses to 1970-01-01 23:32 UTC. Any age
# filter then calls that file older than every cutoff, so a nightly
# ``older_than_days: 60`` job deletes a file the user never named. In #426 the
# same bug cost a wrong sensor reading; here it costs data, so the floor is
# mandatory, not cosmetic. A stamp below it is treated as unset, which the age
# filter turns into SKIP, never into a delete: a printer that stops reporting
# dates must degrade to "deletes nothing", not "deletes everything".
_MIN_VALID_CREATE_TIME = datetime(2000, 1, 1, tzinfo=UTC)


def _usable_created(file: PrinterFile) -> datetime | None:
    """Return a file's creation date, or None when the printer clock was unset."""
    created: datetime | None = getattr(file, "created", None)
    if created is None or created < _MIN_VALID_CREATE_TIME:
        return None
    return created


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    """Whether a file name matches any of the glob patterns."""
    # fnmatchcase, not fnmatch: fnmatch normalises through os.path.normcase,
    # which is the identity on Linux and lowercases on Windows, so a user's
    # `exclude=Keep*` would keep `KEEP*` files on one host and not another.
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def select_deletions(  # noqa: PLR0913
    files: Mapping[str, PrinterFile],
    *,
    include: tuple[str, ...] = DEFAULT_INCLUDE,
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE,
    older_than_days: int,
    now: datetime | None = None,
    active_filename: str | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Split a file listing into delete candidates and survivors.

    Returns ``(to_delete, skipped)``: the names that may go, and
    ``(name, reason)`` pairs for everything that stays. Both are sorted, so a
    dry run reads the same twice and the response is assertable.

    Five rules, all of them fail-safe:

    * the file being printed is skipped first, before any pattern is consulted,
      so it cannot be talked past by ``include=*``;
    * ``exclude`` wins over ``include``;
    * a file with no usable creation date is never deleted - including a
      1970-epoch stamp from a printer whose clock was never set, which parses
      fine and would otherwise count as older than every cutoff;
    * ``created >= cutoff`` is too new, so exactly-at-the-limit is not yet
      "older than";
    * an empty ``include`` matches no name at all, which selects nothing.

    ``older_than_days`` is deliberately required, with no default: a caller
    that forgot to think about age should not compile.
    """
    if now is None:
        now = datetime.now(UTC)
    cutoff = now - timedelta(days=older_than_days)

    to_delete: list[str] = []
    skipped: list[tuple[str, str]] = []

    for name in sorted(files):
        # The active file is checked before the patterns: an `include=*` must
        # not be able to reach a file the printer is reading right now. The
        # comparison is EXACT equality with the name the printer itself
        # reported in its status push - no case folding, no trimming, no path
        # stripping - because the repo already treats the status filename and
        # the 1044 listing key as one namespace. Pinned in both directions by
        # test_active_guard_compares_the_printers_own_filename_exactly; change
        # that test deliberately, since exact equality fails OPEN on a name
        # the printer spells differently.
        if active_filename and name == active_filename:
            skipped.append((name, SKIP_ACTIVE))
            continue
        if not _matches(name, include):
            skipped.append((name, SKIP_NOT_INCLUDED))
            continue
        if _matches(name, exclude):
            skipped.append((name, SKIP_EXCLUDED))
            continue
        created = _usable_created(files[name])
        if created is None:
            skipped.append((name, SKIP_NO_TIMESTAMP))
            continue
        if created >= cutoff:
            skipped.append((name, SKIP_TOO_NEW))
            continue
        to_delete.append(name)

    return to_delete, skipped
