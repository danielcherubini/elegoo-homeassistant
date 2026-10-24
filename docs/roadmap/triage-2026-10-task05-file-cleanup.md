---
status: committed
parent: docs/roadmap/triage-2026-10-status.md
done-when: "A CC2 user can delete files from the printer's local storage from an automation, selecting by age and by name pattern, with a dry run that deletes nothing by default, and **a deletion is confirmed by a fresh 1044 file listing rather than by the printer's 1047 reply** — pinned by `test_accepted_but_still_listed_counts_as_not_deleted`, which drives a client double that answers `error_code 0` and keeps the file in its listing, and asserts `success is False`, `deleted == []` and `failed == {\"ghost.gcode\": \"still listed after delete\"}`; that test is verified to fail (with 3 others) when the confirmation re-list is disabled, so the clause is falsifiable and not a claim. **An age-based destructive filter must also survive a printer clock that has never been set: a `create_time` below a 2000 floor is treated as unset and SKIPPED, never deleted** — pinned by `test_near_epoch_create_time_is_never_deleted`, `test_creation_dates_below_the_floor_are_treated_as_unset` and `test_near_epoch_create_time_survives_a_real_delete_run`, which seed `create_time: 84720` (1970-01-01 23:32 UTC, which *parses*, so a naive `if created:` filter deletes it) and assert the file survives; removing the floor fails exactly those 3 (trap T10). Concretely: a new `elegoo_printer.cleanup_files` service (fourth registration, `SupportsResponse.OPTIONAL`, reusing `_resolve_cc2_client` at `__init__.py:260` unchanged) takes `entry_id`, `older_than_days` (default 60, whole numbers only), `include` (default `[\"*\"]`), `exclude` (default `[]`) and `dry_run` (default **true**), and every response carries `success`/`dry_run`/`deleted`/`failed`/`skipped`/`would_delete`; selection lives in a new pure module `cc2/cleanup.py` using the reporter's own include/exclude vocabulary and never deleting a file whose `create_time` is missing, epoch-near, or a file the printer is printing; `ElegooCC2Client.delete_file()` is added and its docstring states that 1047's response shape is undocumented and that its return value is not proof of deletion; the gate is green from the measured 766 baseline — `make format` no-op, `make lint` \"All checks passed\", `make test` **806 passed** (766 + 40, every addition itemised in § Count) — with `test_services.py` at 28 → 53 and its `call_count == 3` pin updated to 4; `translations/` is untouched (the parent row's \"18 locales\" claim is wrong and is corrected in § Other notes); `config_flow.py` and entry options are untouched, because the reporter's \"how would config be stored\" question is answered by the service's call data; and #432 **must not be closed on this commit** — the suite is green against a mocked 1047 whose reply shape this repo has never observed, so the issue stays open pending one hardware dry-run-and-delete run, recorded in § The issue stays open and in the CHANGELOG line."
---

# T5 — #432 Clean up old printer files (1047 delete is documented "Untested")

**Issue:** [#432](https://github.com/danielcherubini/elegoo-homeassistant/issues/432), opened by **@tvidal-net**:
> "I would like to create an automation that would run once a day and delete files older than 60 days from the
> printer, both gcode and video files."
>
> "I was thinking of a include/exclude list approach that would start with `include = all`, `exclude = nothing`
> and could be configured for something like `include=ECC*` and `exclude=Keep*` to remove or keep only some
> files."
>
> "I am not sure what kind of config is already available to HACS, so some investigation on how the config would
> be stored may be required." … "I am a software engineer … happy to give it a try with AI assistance and open a
> PR, if there's interest in this idea from the maintainers."

Read the whole issue before starting: it has **no comments** (`gh issue view 432 --json comments` returns `[]`), so
there is no follow-up from the reporter to reconcile with, and no maintainer commitment yet. The reporter offered to
PR this. Whoever implements it owes them a reply that says what shipped, what did not (videos — see
§ Videos are out of scope), and that their vocabulary was used verbatim.

**This task has a protocol unknown at its centre, and the design is built around that rather than through it.**
Method 1047 is the only delete primitive the CC2 has. `docs/CC2_PROTOCOL.md:1457` marks it `| DELETE_FILE | 1047 |
Untested | — |` and its "Delete File" section (`:1603-1614`) documents the **request** and **no response at all** —
unlike 1044, 1046 and 1048, each of which shows a response body. Nothing in this repo has ever received a 1047
reply. So "the printer's reply can be trusted to confirm a delete" is not a slow path or an optimisation to skip;
it is an **unverified assumption about a destructive operation**, and the whole feature is built so that the
assumption is never load-bearing: **deletion is confirmed by re-listing.**

Grounding was done inline against the tree at `feature/triage-2026-10` (`414be9b`). Everything below is a
measurement or a read, not an inference: the baseline was re-measured; the whole feature was **implemented and
measured in a throwaway copy at `/tmp/t5`** (real tree left byte-identical — `git status --porcelain` returns 0
lines, 766 passed, lint clean, format no-op, 124 files formatted, before and after); and **fourteen** traps were
injected into that finished copy and each one's failing tests recorded (§ The traps — read the note there about whose
measurements these are). The review round added the epoch floor, the blank-pattern message, the whole-days check and
the active-guard basis test to that copy, which took it from 798 to **806 passed**; T1–T9 were then **re-run** against
the larger suite because the new tests change which set each trap catches. Where the brief and the tree disagree, the
tree won, and each disagreement is called out in § Corrections to the brief; where the **reviewer and this plan**
disagreed, § Reviewer round 1 records it. All seven findings were checked against the tree before acting: the blocking
one was a real data-loss bug and is fixed; four non-blocking ones were accepted as written (NB-2, NB-3, NB-4, NB-6); two offered a choice of
remedy as an either/or and I picked one after measuring what the other would cost (NB-5, NB-7); and one — NB-3 — was a
false claim **this plan** had made about `cv.ensure_list`, whose real justification is different and is now written
down. No finding was rejected. Two of the plan's own earlier claims were.

## Reviewer round 1 (a data-loss bug in this plan, fixed here)

The review of this plan found **one blocking defect in the design I had written, not in the brief**. I am recording it
in full because it is the most serious thing on the page: **the age filter as first specified deletes files.**

> **BLOCKING.** The filter was `if created:` — so a *non-zero but tiny* `create_time` parses to a 1970 date, compares
> as older than any cutoff, and a nightly `older_than_days: 60` job **destroys a file the user never named.** The
> plan mentioned 1970/epoch zero times (verified: `grep -ci "1970\|epoch\|_MIN_VALID"` on the committed revision
> → `0`).

This repo has already fixed this exact class of bug, for job times, in **#426** (`bb572ec`
"fix(sensor): handle 1970 job times from the printer"), at `definitions.py:54-61`:

```python
# The printer sometimes reports job times near 1970 (seen on a Centauri Carbon
# after a restart). Job times before this are treated as unset.
_MIN_VALID_JOB_TIME = datetime(2000, 1, 1, tzinfo=UTC)

def _valid_job_time(value: datetime | None) -> datetime | None:
    """Return a job timestamp, or None when the printer clock was unset."""
    return value if value and value >= _MIN_VALID_JOB_TIME else None
```

`PrinterFile` (`sdcp/models/file_info.py:29-36`) guards only against `create_time` being **falsy** or
**unparseable**. Measured against the real model with a 60-day cutoff and `now = 2026-10-24`:

| `create_time` | `file.created` | plan's old `if created:` |
| --- | --- | --- |
| missing / `0` | `None` | skipped (correct, by luck) |
| **`84720`** | **`1970-01-01 23:32:00+00:00`** | **DELETED** |
| `1` | `1970-01-01 00:00:01+00:00` | **DELETED** |
| `10**20` | `None` (`OverflowError`) | skipped (correct) |
| `1789680958` | `2026-09-17 21:35:58+00:00` | skipped (correct) |

**State the principle plainly, because it is the whole reason the floor is mandatory:** *an age-based destructive
filter is only as safe as its treatment of a printer clock that has never been set.* A printer that stops reporting
dates must degrade to **"deletes nothing"**, never to "deletes everything". In #426 the same bug cost a wrong sensor
reading; here it costs a user's file, which is why the floor sits in the selection module rather than at the edge and
why three tests pin it instead of one.

The fix (§ The change item 2) mirrors `_MIN_VALID_JOB_TIME` — same 2000-01-01 floor, same "below the floor means
unset" semantics — with a comment in the code naming #426 and the fact that this is the destructive path. Trap **T10**
removes the floor and fails exactly the 3 new timestamp tests.

Four smaller review findings are also folded in below and marked **(NB-n)** where they land: the `# noqa: PLR0913`
placement that cannot pass lint **(NB-2)**, a false claim I had written about `cv.ensure_list` **(NB-3)**, the
per-file ruff ignore lists **(NB-4)**, the blank-pattern silent no-op **(NB-5)**, the active-file guard's comparison
basis **(NB-6)**, and `vol.Coerce(int)` truncating toward *more* deletion **(NB-7)**.

## Corrections to the brief (three of its grounding claims are wrong)
Trust the tree. These were checked by reading, and the first two change what you write.

| Brief says | Tree says | Consequence |
| --- | --- | --- |
| "`CC2_CMD_DELETE_FILE` does not exist yet — add it to `cc2/const.py`" | **It already exists**: `CC2_CMD_DELETE_FILE = 1047` at `cc2/const.py:80`, sitting between 1046 and 1048. `grep -rn "CC2_CMD_DELETE_FILE" custom_components/` finds exactly two hits: that line, and `scripts/test_cc2_printer.py:71` (a simulator constant; the simulator defines 1044/1046/1047 constants at `:69-71` but implements **none** of them — `grep -o "elif method == CC2_CMD_[A-Z_]*" scripts/test_cc2_printer.py | sort -u` lists 13 branches, all control/status, no file methods — so it answers nothing for 1047). | Do **not** add the command constant. What is genuinely missing is `CC2_ERROR_FILE_DELETE_FAILED = 1007` (the error block at `:94-105` has 1000-1005, 1009, 1010, 1012, 9000, 9004 and no 1007) and the client **method**. |
| Seam includes "`translations/*` (18 locales)" | **`translations/` must not be touched at all.** There is no `strings.json` (`ls custom_components/elegoo_printer/strings.json` → No such file). All 18 files in `translations/` have exactly the top-level keys `options`, `config`, `entity` — verified on `ar.json`, `cs.json`, `da.json` — and no `services` key anywhere. `tests/test_translations.py`'s own docstring scopes it to error-key parity on `options.error`/`config.error`. **Services have no translation surface**: `services.yaml` is plain English and that is how the other three work. | `translations/` is OUT. The parent status row's T5 seam is corrected in § Other notes. |
| Seam includes "`config_flow.py` … possibly" | OUT, and this answers the reporter's open question. `_resolve_cc2_client` reads `entry.runtime_data.api.client`; nothing here needs a config option. The reporter asked "how would config be stored" — **it isn't stored at all.** The parameters are the service's *call data*, which is exactly what makes it an automation-friendly daily job (`older_than_days` lives in the automation, where a user can see and edit it) rather than a hidden per-entry option that an options flow would have to grow. Say this in the reply to the reporter. | `config_flow.py`, `data.py`, `coordinator.py`, entry options: no change. |

Also worth knowing before you start: `_resolve_cc2_client` (`__init__.py:260`) already does every guard this service
needs — entry exists, `entry.domain == DOMAIN`, state is `LOADED`, `isinstance(client, ElegooCC2Client)` — and
returns the ready-made error dict. **Reuse it.** It was added for `upload_gcode` and `start_print` re-implements the
same guards inline (`:209-228`); do not copy that older shape.

## Videos are out of scope — establish it, then say it plainly

The reporter asked for "both gcode and video files". **This service cannot delete videos, and the honest reason is
that this integration has never seen a video file on the printer.** The evidence, all read from the tree:

- **`printer_data.file_list` is the only inventory available**, filled from 1044. `_handle_file_list`
  (`client.py:1408-1422`) keeps only entries with `entry.get("type", "file") == "file"`; `PrinterFile`
  (`sdcp/models/file_info.py`) parses `filename`, `size`, `create_time`, `layer`, `print_time`,
  `total_filament_used`, `color_map` — every one of those is a **slicer-written G-code field**. There is no
  extension, MIME or media-type field to classify a listing entry by, and no test or capture in the repo shows a
  non-`.gcode` entry: `cc2/tests/test_file_list.py`'s fixtures are one `.gcode` file plus one `type: "dir"`
  folder.
- **The camera is a live stream, not a file.** `docs/CC2_PROTOCOL.md:1644-1693`: MJPEG on port 8080
  (`http://<ip>:8080/?action=stream`), one connection max, and on stock firmware "port 8080 serves the camera MJPEG
  stream for **every request**". `camera.py` proxies that stream. Nothing writes it to disk, on the printer or in HA.
- **`storage_media` values are `local` / `u-disk` / `sd-card`** (`:1446-1448`). There is no video/album media type.
  1044's `path` parameter is documented **only ever as `"/"`** (`:664`, `:1470` — the only two places `path` appears in a 1044 example) — whether it addresses a
  subdirectory is **UNVERIFIED**, and this task does not guess at it.
- **Timelapse video is a known concept on this hardware but is not in the file list.** `docs/CC2_PROTOCOL.md:746`
  has `| 12 | VIDEO_COMPOSING | Creating timelapse video |` — so the printer *does* build videos — and the SDCP
  (first-gen/resin) transport has `CMD_EXPORT_TIME_LAPSE = 323` (`sdcp/const.py:37`) plus
  `PrintHistoryDetail.time_lapse_video_url` (`sdcp/models/print_history_detail.py:44-45`). **None of that is CC2
  MQTT, and none of it is wired up**: `grep -rn "CMD_EXPORT_TIME_LAPSE\|CMD_SET_TIME_LAPSE_PHOTOGRAPHY" custom_components/`
  finds the two definitions and zero uses. So the repo has a hint that CC2 stores timelapses somewhere, and no
  documented way to list or delete them.

**Decision: ship G-code selection, do not silently narrow the request.** Concretely:

1. **Do not filter by extension.** The selector in `cc2/cleanup.py` matches names only. If a user's firmware *does*
   list `timelapse.mp4`, the pattern machinery will match and delete it like any other file, because 1047 takes a
   filename and this integration has no reason to think a video is special. This is why there is no `.gcode` check
   anywhere in the selection code — a filter the protocol does not support would only hide files a user can see.
2. **Say so in `README.md` and `services.yaml`** — written out in § The change item 7, and already drafted in the
   measured copy. Not a euphemism like "supports G-code", which reads as a scope choice rather than an evidence gap.
3. **Say so to the reporter**, and ask the question that actually resolves it: *does your printer's file list (the
   one the **Print file** select shows, or a 1044 dump) ever contain video files?* They have the hardware and offered
   to PR. That answer decides whether a follow-up is needed; nothing in this repo can.

**UNVERIFIED, and it must stay in the docs:** whether CC2 stores deletable timelapse files at all, and if so under
what path and media type. Do not "investigate" this by sending 1044 with a guessed `path`, and do not add a video
code path on spec — an unverifiable delete path is the worst thing this task could ship.

## Grounding (verified against the tree)

Baseline, measured on this branch at `414be9b` with a clean tree: **766 passed, 12 warnings**; `make lint`
"All checks passed"; `make format` a no-op (`ruff format --check .` → "124 files already formatted").
`test_services.py`: **28 passed**. `cc2/tests/test_file_list.py` + `cc2/tests/test_start_print.py`: **20 passed**.

The three existing services, which this task joins rather than redesigns (`__init__.py`):

| Piece | Line | Note |
| --- | --- | --- |
| `SERVICE_UPDATE_IP` / `_async_update_ip` | `:83` / `:96` | schema constant at `:88` |
| `SERVICE_START_PRINT` / `_async_start_print` | `:181` / `:198` | guards inline — the older shape |
| `_resolve_cc2_client` | `:260` | **the shape to reuse** |
| `SERVICE_UPLOAD_GCODE` / `_async_upload_gcode` | `:284` / `:408` | the only current user of the resolver |
| `async_setup` registration | `:447`, three `async_register` calls at `:455`, `:462`, `:469` | each `partial(handler, hass)` + `schema=` + `supports_response=SupportsResponse.OPTIONAL` |

`async_setup` binds with `partial` because HA calls a handler with the `ServiceCall` alone — registering a
`(hass, call)` function bare raises `TypeError` on every call. Keep that.

Client side (`cc2/client.py`): `get_file_list()` at `:1425` sends
`{"storage_media": "local", "path": "/"}` and **raises `ElegooPrinterConnectionError` when the printer did not
answer**; `print_start()` at `:1654` is the wire-shape and error-code precedent (`_send_command` → `result.error_code`,
raising when there is no result or no error code). `_send_command` raises `ElegooPrinterTimeoutError` (a subclass of
`ElegooPrinterConnectionError`, `sdcp/exceptions.py:28`) after `CC2_COMMAND_TIMEOUT = 10 s` (`cc2/const.py:36`), and
returns `None` if the client disconnects while waiting.

`printer_data.file_list` is refreshed by the coordinator every ten minutes
(`coordinator.py:280-303`, `_refresh_file_list_if_due`), so the **Print file select is already the UI for "what is on
the printer"** — a user can eyeball the list before turning the dry run off. Worth mentioning in the reply.

Active-file guard: `client.printer_data.status.print_info.filename` is populated for CC2 by
`CC2StatusMapper._map_print_info` (`cc2/models.py:229`, from `print_status.filename`), and the test printer sets it
(`scripts/test_cc2_printer.py:342`, cleared to `""` at `:362`). It is `None` on a client that has never received
status. `sdcp/models/status.py:67-95` already keeps `_FDM_ACTIVE_STATUSES`, but it is private, keyed on the *status*
enum, and `compute_percent_complete` is its only consumer — so this task guards on the **filename**, which is
exact, rather than borrowing a status set that would refuse to delete anything while the printer is merely
levelling. **Exact equality is also the reason the guard needs its own test and its own stated basis** — it fails open
on a name mismatch, so see NB-6 in § The change item 2 for the sentence that states the basis and the test that pins
it. Note that the repo's own *sensor* for the same field trims it
(`definitions.py:800`, `(… .filename or "").strip() or None`) — this guard deliberately does **not** trim, so that
the comparison basis is one thing and not two.

Lint facts that will bite (all reproduced against the repo's own `pyproject.toml` with ruff 0.14.1 — and **corrected
after the review**, because an earlier revision of this paragraph said `PLR2004` and `D401` fire "in `cc2/tests/` **and
in `tests/`**", which is wrong in both halves): `select = ["ALL"]`; the two test directories have **different** ignore
lists and the wider one is the one the service tests live in — `custom_components/elegoo_printer/tests/*.py`
(`pyproject.toml:67`) ignores `S101, D102, D103, ANN001, ANN201, ANN202, PLR2004, SLF001, D403, ARG002`, while
`custom_components/elegoo_printer/cc2/tests/*.py` (`:68`) ignores **only** `S101, SLF001, PLC0415`. So: `PLR2004`
fires in `cc2/tests/` and is **silent** in `tests/`; `ANN201`/`D103` fire in `cc2/tests/` and are silent in `tests/`;
`D401` (imperative mood) is in **neither** list and fires in **both** directories for ordinary helper functions, but
**not** for functions named `test_*` in either (measured both ways) — so keep helper docstrings imperative and do not
bother annotating test signatures in `tests/`. `PLR0913` (too many args) fires above 5, is reported **on the `def`
line**, and has three `# noqa` precedents (`mqtt/client.py:89`, `cc2/client.py:115`, `config_flow.py:1386`), all of
which put the directive on the `def`; putting it on a parameter line buys you `PLR0913` **and** `RUF100` (measured —
§ The change item 2, NB-2). D200/D213 pull opposite ways (single-sentence summary inline, multi-paragraph summary on
the second line).

**The `create_time`/clock fact that the whole safety story turns on** (measured against the real
`sdcp/models/file_info.py` in the real tree, before any of this code exists): `PrinterFile` maps a *falsy* or
*unparseable* `create_time` to `created = None`, and **nothing else**. `create_time=0` → `None`; `create_time=10**20`
→ `None` (`OverflowError`); but **`create_time=84720` → `1970-01-01 23:32:00+00:00`** and `create_time=1` →
`1970-01-01 00:00:01+00:00`. Both parse, so both clear a truthiness-only age filter and land on the delete side of
every cutoff. This repo has already been bitten by exactly this on the non-destructive path — #426 / `bb572ec`,
`definitions.py:54-61`, `_MIN_VALID_JOB_TIME = datetime(2000, 1, 1, tzinfo=UTC)` with `_valid_job_time()` rejecting
anything below it, pinned by `tests/test_job_timestamps.py` — and the comment there says the printer "sometimes reports
job times near 1970 (seen on a Centauri Carbon after a restart)". **This task's file list is the same field on the same
printer, on a path that deletes.** See § Reviewer round 1 and § The change item 2.

## The change

### 1. `cc2/const.py` — one constant, not two

`CC2_CMD_DELETE_FILE = 1047` already exists (`:80`). Add only the error code, in the error block after
`CC2_ERROR_TOKEN_UPDATE_FAILED = 1005`:

```python
# 1007 FILE_DELETE_FAILED. UNVERIFIED against a real reply: the protocol lists
# the code but documents no response for 1047 at all.
CC2_ERROR_FILE_DELETE_FAILED = 1007
```

It exists so the 1007 literal does not appear bare in a test — `PLR2004` fires there otherwise (measured).

### 2. New `cc2/cleanup.py` — the dangerous decision, in a pure module

Selection is the only part of a delete feature that can destroy the wrong file, so it lives in a module with **no
Home Assistant and no transport imports**, testable without mocks. It uses the reporter's vocabulary:
`include` defaults to everything, `exclude` to nothing.

```python
DEFAULT_INCLUDE = ("*",)
DEFAULT_EXCLUDE: tuple[str, ...] = ()

# The reporter's own number: "delete files older than 60 days" (#432).
DEFAULT_OLDER_THAN_DAYS = 60

SKIP_NO_TIMESTAMP = "no_creation_date"
SKIP_TOO_NEW = "younger_than_limit"
SKIP_EXCLUDED = "excluded"
SKIP_NOT_INCLUDED = "not_included"
SKIP_ACTIVE = "currently_printing"

# The #426 lesson (definitions.py:54-61, fixed in bb572ec) applied to a
# DESTRUCTIVE path. A printer whose clock has never been set reports a SMALL
# create_time - not zero, which PrinterFile already reads as unset, but e.g.
# 84720, which parses to 1970-01-01 23:32 UTC. Any age filter then calls that
# file older than every cutoff, so a nightly older_than_days: 60 job deletes a
# file the user never named. In #426 the same bug cost a wrong sensor reading;
# here it costs data, so the floor is mandatory, not cosmetic. A stamp below it
# is treated as unset, which the age filter turns into SKIP, never into a
# delete: a printer that stops reporting dates must degrade to "deletes
# nothing", not "deletes everything".
_MIN_VALID_CREATE_TIME = datetime(2000, 1, 1, tzinfo=UTC)


def _usable_created(file: PrinterFile) -> datetime | None:
    """Return a file's creation date, or None when the printer clock was unset."""
    created: datetime | None = getattr(file, "created", None)
    if created is None or created < _MIN_VALID_CREATE_TIME:
        return None
    return created


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    """Whether a file name matches any of the glob patterns."""
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def select_deletions(  # noqa: PLR0913
    files: dict[str, PrinterFile],
    *,
    include: tuple[str, ...] = DEFAULT_INCLUDE,
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE,
    older_than_days: int,
    now: datetime | None = None,
    active_filename: str | None = None,
) -> tuple[list[str], list[tuple[str, str]]]: ...
```

**`# noqa: PLR0913` goes on the `def` line** (NB-2). The previous revision of this plan had it on the
`files: dict[str, PrinterFile]` parameter line, which does not work: ruff reports `PLR0913` on the `def` line, so that
placement yields **two** errors, not zero. Measured in a scratch copy with the repo's own `pyproject.toml` and
ruff 0.14.1:

```
PLR0913 Too many arguments in function definition (6 > 5)
 --> a.py:4:5
4 | def select_deletions(
5 |     files: dict[str, str],  # noqa: PLR0913 on the function below

RUF100 [*] Unused `noqa` directive (unused: `PLR0913`)
 --> a.py:5:29
```

Put it on `def select_deletions(  # noqa: PLR0913`, matching all three existing precedents
(`mqtt/client.py:89`, `cc2/client.py:115`, `config_flow.py:1386`), each of which sits on the `def`.

Six decisions in there, each measured:

- **`fnmatchcase`, not `fnmatch`.** `fnmatch.fnmatch` normalises case through `os.path.normcase`, which is the
  identity on Linux and lowercases on Windows. A user's `exclude=Keep*` would then keep `KEEP*` files on one host
  and not another. **Trap T7 below is the interesting one in this plan** and it is about how to *test* this, not
  about the choice.
- **A file with no *usable* timestamp is never deleted.** `PrinterFile.created` is `None` when `create_time` is
  missing *or* unparseable (`file_info.py:29-36`, pinned by
  `cc2/tests/test_file_list.py::test_corrupt_timestamp_keeps_the_file`), and it is **a 1970 date when the clock was
  never set** — which that test does *not* cover, because `10**20` raises and `84720` does not. Unknown age is not
  "old enough", and an epoch age is not "ancient". This is the one rule that can cost a user a deletion they wanted,
  and it is deliberate: an age filter that treats "unknown" as "old" deletes the newest file on the printer the
  moment the printer stops reporting dates for it. **This is the blocking review finding; see § Reviewer round 1.**
- **The active file is skipped before any pattern is consulted**, so it cannot be talked past by `include=*`. The
  comparison basis is stated in NB-6 below and pinned by its own test.
- **Both results are sorted**, so a dry run reads the same twice and the response is assertable.
- `created >= cutoff` is **too new** — exactly-at-the-limit is not yet "older than".
- `older_than_days` is a required argument (no default) on purpose: a caller that forgot to think about age should not
  compile.

**The active-file guard's comparison basis (NB-6), stated because it fails open.** `active_filename` is
`client.printer_data.status.print_info.filename`, which CC2 fills from the pushed status payload at
`cc2/models.py:229` (`print_info.filename = print_status.get("filename")`, i.e. `print_status.filename` in the status
message — **not** from 1047, which documents no response at all), and it is compared to the 1044 listing key with
**exact equality** — no case folding, no trimming, no prefix stripping. That is the right basis: the repo already
treats the two as **one namespace**, because `cc2/models.py:315-316` looks the *print* filename up in the
`_file_details` cache built from 1044 listings, and `start_print`/`upload_gcode` pass bare listing names back in. But
exact equality **fails open**: if the two ever differ by case, whitespace or a path prefix, the file currently
printing is no longer recognised and becomes deletable. `test_active_guard_compares_the_printers_own_filename_exactly` pins both directions
— the exact name is skipped, and a name differing by one trailing space is *not* — so a future change to the basis is
a deliberate edit to that test rather than a silent one. Trap **T14** (folding case and trimming instead) fails
exactly that test. Do not "harden" the guard into a fuzzy match without reading that test first.


### 3. `cc2/client.py` — `delete_file`, whose docstring is part of the deliverable

Insert before `set_fan_speed` (`:1741`), next to the other file methods. **Add `CC2_CMD_DELETE_FILE` to the
`from .const import (...)` block at `:44-60`** — it is alphabetically before `CC2_CMD_GET_ATTRIBUTES`; forgetting
this is a `NameError` at call time, not an import error, because the import block is what fails, so ruff catches it
(`F821`) but only if you lint.

```python
    async def delete_file(
        self,
        filename: str,
        *,
        storage_media: str = "local",
    ) -> int:
        """
        Delete one file from the printer's storage (method 1047).

        UNTESTED ON HARDWARE. … the doc gives no response shape for 1047 …
        reading the reply as result.error_code is an inference from the shared
        envelope, not a measurement. A 0 here means "the printer did not
        refuse", nothing more; the CALLER decides whether the file is gone, by
        re-listing.
        """
        response = await self._send_command(
            CC2_CMD_DELETE_FILE,
            {"storage_media": storage_media, "filename": filename},
        )
        result = (response or {}).get("result")
        if not isinstance(result, dict) or "error_code" not in result:
            msg = f"No acknowledgement from the printer for delete file {filename}"
            raise ElegooPrinterConnectionError(msg)
        return int(result["error_code"])
```

Deliberately **not** `-> None` and not raising on non-zero: a refused delete (1007) is data the service reports per
file, and one locked file must not abort a 40-file cleanup. The `raise` is only for *no reply at all*, mirroring
`print_start` (`:1735-1738`) — a command this repo has never seen answered is treated as unanswered, which is also
the correct handling for firmware that does not implement 1047 and stays silent (it surfaces as a
`ElegooPrinterTimeoutError` after 10 s).

Keep the "UNTESTED ON HARDWARE" paragraph in full. This method's docstring is where a future implementer looks to
find out whether they can trust the return value, and today the answer is no.

### 4. `__init__.py` — the service

New in this order, ending before `async_setup` (`:447`): `SERVICE_CLEANUP_FILES = "cleanup_files"`, `_glob_patterns`,
`_whole_days`, `SERVICE_CLEANUP_FILES_SCHEMA`, `CleanupReport`, `_pattern_note`, `_async_delete_each`,
`_async_cleanup_files`, `_cleanup_failure_text`, `_cleanup_unreachable`. Then a **fourth** `async_register` in
`async_setup` after the `upload_gcode` one.

Schema — the reporter's vocabulary, with the two defaults they proposed:

```python
def _whole_days(value: object) -> int:
    """Validate older_than_days as a WHOLE number of days, rejecting fractions."""
    days = vol.Coerce(int)(value)
    if days != float(value):
        msg = "older_than_days must be a whole number of days"
        raise vol.Invalid(msg)
    return vol.Range(min=1)(days)


SERVICE_CLEANUP_FILES_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): str,
        vol.Optional("older_than_days", default=DEFAULT_OLDER_THAN_DAYS): _whole_days,
        vol.Optional("include", default=list(DEFAULT_INCLUDE)): _glob_patterns,
        vol.Optional("exclude", default=list(DEFAULT_EXCLUDE)): _glob_patterns,
        # 1047 is documented "Untested" and its reply shape is undocumented, so
        # the safe answer is the default: report, delete nothing.
        vol.Optional("dry_run", default=True): bool,
    }
)
```

**Why `_whole_days` and not `vol.All(vol.Coerce(int), vol.Range(min=1))` (NB-7).** `vol.Coerce(int)` **truncates**:
measured against the installed voluptuous, `60.9 → 60` and `0.5 → Invalid (min)`. A lower day count is a **lower
cutoff**, so a caller asking for 60.9 days would get **slightly more deletion than they asked for** — the wrong
direction to round on a destructive field. HA's own `cv.positive_int` has the identical defect (measured: it returns
`60` for `60.9`), so the check has to be explicit rather than borrowed. A string still parses (`"60" → 60`), because
automations template these. Pinned by `test_schema_rejects_a_fractional_age_limit`; trap **T12** (drop the
fraction check) fails exactly that test.

**`_glob_patterns` is a hand-rolled validator, and that is a decision — but not for the reason an earlier revision of
this plan gave.** Two claims in that revision were **false** and must not reach a code comment or the PR description
(NB-3):

| Claim written earlier | Measured against installed voluptuous + HA 2025.4 |
| --- | --- |
| "a `text` selector with `multiple: true` hands over a *mapping*" | **False.** `TextSelector.__call__` (`homeassistant/helpers/selector.py:1352-1359`) validates `multiple: true` as a **`list[str]`** and raises `vol.Invalid("Value should be a list")` otherwise. (Core's `multiple: true` usage is widespread — `components/ecobee/services.yaml:152` is one, for a `device` selector — but no precedent is needed: the selector's own `__call__` is the answer.) |
| "`vol.All(cv.ensure_list, [vol.All(str, vol.Length(min=1))])` would **coerce a mapping** into `str({...})`" | **False.** `cv.ensure_list` does not coerce a dict: it passes it through and the inner `str` check rejects it — `expected str @ data[0]`. Measured: `{'0': 'ECC*'}` → `Invalid: expected str @ data[0]`; `42` → same. |

**The real justification, which the reviewer confirmed and which the code comment must carry:** the composition
**rejects the empty string** — `""` → `length of value must be at least 1 @ data[0]` — and **a cleared `text` field in
the UI is submitted as `""`**. The reporter's "exclude = nothing" has to *validate*, so the blank case has to be
handled explicitly, and no composition of the stdlib validators expresses it. That, plus the standing fact already
documented at `__init__.py:107-109` that **selectors shape the UI only and a programmatic or REST caller can send any
JSON at all** (the reason `_async_update_ip` re-checks the entry domain and calls the selector "not the real guard"),
is why the shape rules and the blank rule live together in one hand-rolled validator. So: `services.yaml` uses a plain
`text` selector (one pattern, exactly the reporter's `include=ECC*`); the validator takes a `str` or a `list[str]`,
rejects a mapping, and treats `""` as "no patterns" — with the consequences of that last rule pinned, see below.
Pinned by `test_schema_rejects_a_mapping_of_patterns`,
`test_schema_reads_a_blank_field_as_no_patterns` and `test_schema_reads_a_blank_include_as_no_patterns`.

**Blank pattern fields, and why `""` is not allowed to be a silent no-op (NB-5).** `_glob_patterns` turns `""` into
`[]`, and `[]` matches **no file name at all**. So `include: ""` — which a UI user reaches by clearing the box, and
which a template reaches by rendering empty — yields `include=[]` → nothing selected → the service reports
`success: true` having deleted nothing, and the reporter's nightly job stays inert **forever** while reporting
success. Wrong-delete risk from the schema rule itself: none. But a permanently-inert cleanup job that reports success
is its own bug, and the reviewer is right to call it.

**Decision: `""` still validates as "no patterns" (the schema must not reject what the UI submits), and the *handler*
says out loud when a pattern set left nothing a candidate.** `_pattern_note(include, file_count, to_delete, reasons)`
returns a message naming the pattern when every listed file was rejected by `SKIP_NOT_INCLUDED` (or the listing was
empty), and `None` otherwise — so the ordinary "everything is too new" run keeps its plain message and does not become
noise. `success` **stays `True`** in that case: this is the same call the plan already makes in § Two bugs the
measurements caught (an empty selection is a clean run, and an automation that alerts nightly on "nothing to do" is
noise by Thursday). The fix is *visibility*, not a new failure: the notify now reads
"none of the 12 listed file(s) matched the include pattern `['']`, so nothing was a candidate and nothing was
deleted", which is diagnosable instead of mysterious.

The alternative the reviewer offered — **fall back to `DEFAULT_INCLUDE` on a blank field** — was considered and
**rejected**, and the reason belongs in the code comment: `include: ""` is indistinguishable from a template that
rendered to empty (`"{{ state_attr('sensor.x','name') }}*"` on a missing attribute gives `""`), and resolving that to
`*` means **every old file on the printer is deleted by a call that named nothing**. That converts a benign inert-job
bug into a mass-delete bug, which is the exact failure class this whole task is built to refuse. Refusing `""` at the
schema was the other candidate and is also rejected: the UI submits it, so it would make the exclude box unusable.
Pinned by `test_blank_include_is_reported_as_an_inert_pattern_not_silence` and its inverse
`test_everything_too_new_is_not_reported_as_a_bad_pattern`; trap **T11** (blank → `["*"]`) and **T13** (never
distinguish) record both halves.

**`dry_run` defaults to `true`.** The other three services default to *doing the thing*. This one defaults to not
destroying files, because its reply cannot be trusted. Trap T2 confirms that pinning this is load-bearing.

`CleanupReport` is a `NamedTuple` with `as_dict()` dropping only unset `error`/`message`:

```python
class CleanupReport(NamedTuple):
    success: bool
    dry_run: bool
    deleted: list[str]
    failed: dict[str, str]
    skipped: dict[str, str]
    would_delete: list[str]
    error: str | None = None
    message: str | None = None
```

The reason is a Home Assistant failure mode worth knowing: **a key missing from a service response renders as
nothing in a template**, which is indistinguishable from a genuine empty list. Seven outcomes all returning
hand-built dicts guarantees drift; one `NamedTuple` makes "every response has `deleted`" structural. Needs
`from typing import NamedTuple` (there is no existing import of it). Note `RUF012` rejects a mutable default, hence
`would_delete` is a required positional field rather than `= []`.

Handler flow — and this ordering is the feature, not plumbing:

1. `_resolve_cc2_client(hass, call.data["entry_id"])`; return its error dict if `client is None`.
2. `files = await client.get_file_list()` — **fresh, never the cached `printer_data.file_list`.** A daily job
   deleting from a listing up to ten minutes stale could act on files a user deleted by hand.
3. `select_deletions(files, include=…, exclude=…, older_than_days=…, active_filename=client.printer_data.status.print_info.filename)`.
4. `dry_run` → return `would_delete` and **stop**. It must not spend the confirmation re-list either
   (`await_count == 1` is asserted).
5. `_async_delete_each` — sequential, one 1047 per file, no concurrency (one MQTT command channel, 10 s timeout
   each, and a delete storm is the last thing an untested method needs). Stops at the first transport error and
   reports what it got through.
6. If anything was deleted, **`remaining = await client.get_file_list()`** and split `deleted` into confirmed /
   `still listed after delete`. This is the done-when clause.
7. `success` is `not failed and not still` — **a partial success is a failure**, because a daily automation must be
   able to alert on `not response.success` and know the printer is not in the state it asked for. Trap T8.
8. Nothing matched at all → `success: True` with a message. A nightly job that finds nothing new is not an error,
   and an automation that alerts on it would be noise by Thursday. (I got this wrong first and a test caught it —
   see § Two bugs the measurements caught.) **When the reason nothing matched is the `include` pattern itself** —
   every listed file came back `SKIP_NOT_INCLUDED`, which is what `include: ""` and a typo'd `include=EEXC*` both
   look like — that message names the pattern instead of the bland "nothing older than the limit" text, so a job
   that will delete nothing forever is visible in the notify. `success` is unchanged. See NB-5 in § The change item 4.

The **epoch floor is applied inside step 3**, not here: the handler passes the listing straight through and
`select_deletions` turns a below-the-floor `create_time` into `SKIP_NO_TIMESTAMP`. Keeping it in the pure module is
what makes trap T10 fail three selection/service tests rather than depending on handler wiring.

Response fields, always present: `success`, `dry_run`, `deleted`, `failed` (name → reason), `skipped`
(name → one of the five `SKIP_*` reasons), `would_delete`, plus `error` or `message`. `skipped` naming *why* every
file survived is what turns "it deleted nothing" into a diagnosable statement, and it is what makes the dry run
worth reading.

### 5. `services.yaml` — four entries

Append `cleanup_files:` after `upload_gcode`. Field descriptions must carry the safety story, because this is the
only place a UI user reads it: the dry-run default, "excluded is never deleted even when it also matches include",
the no-timestamp rule (**including** "a file whose creation date the printer could not set — a 1970 stamp — is treated
as having no date and is never deleted", since that is the case a user would otherwise expect to be deleted), the
printing-file rule, and **"Whether a delete happened is confirmed by re-reading the printer's file list, not by
trusting its reply."** Validated as YAML and confirmed to parse to
`['update_ip', 'start_print', 'upload_gcode', 'cleanup_files']`. `number` selector `min: 1, max: 3650, mode: box`
for days, and say **whole days** in its description (the schema rejects `60.9`, NB-7); plain `text` for both pattern
fields (`multiple: true` deliberately **not** used — see above). Say in the `include` description that **leaving it
blank matches nothing and deletes nothing** and that omitting it is how you ask for "everything" — the field is the
reporter's, so the sharp edge has to be documented where the reporter will read it.

### 6. Tests — 40 new, and the seam is a stateful printer double

`_cleanup_client` must **not** be a scripted `side_effect=[listing_a, listing_b]`. It holds a `set` of names,
answers `get_file_list` from it, and removes names on delete:

```python
    client = MagicMock(spec=ElegooCC2Client)
    present = set(listing)
    doomed = set(listing) if vanishing is None else set(vanishing)

    async def _get_file_list() -> dict[str, PrinterFile]:
        return {name: listing[name] for name in sorted(present)}

    async def _delete_file(name: str) -> int:
        ...
        if name in doomed:
            present.discard(name)
        return 0
```

Why it is built this way, and the two traps it exists to avoid:

- **A scripted double cannot catch a handler that never re-lists** — it hands over the "after" listing whoever asks
  for it. With state, `await_count == 2` is a real claim. Trap T1 is exactly this and catches 4 tests.
- **`vanishing=[]` expresses the 1047 failure mode**: the printer answers `error_code 0` and keeps the file. That is
  the single scenario the whole design exists for, and it must be expressible in one argument.
- **`MagicMock(spec=ElegooCC2Client)` has no `printer_data`** — verified: `hasattr(ElegooCC2Client, 'printer_data')`
  is `False` (it is assigned in `__init__`, not on the class) and the spec'd mock **raises `AttributeError`**. So
  the existing `_cc2_client()` helper (`tests/test_services.py:330`) cannot be reused as-is for anything that reads
  the active filename; this helper attaches `client.printer_data.status.print_info.filename` explicitly. Do not
  "simplify" by reusing `_cc2_client()`.

Files: `cc2/tests/test_delete_file.py` (4 — wire shape, 1007 returned not raised, no-answer raises, missing
`error_code` raises), `cc2/tests/test_cleanup_selection.py` (**11**), and `TestCleanupFilesService` (**25**) appended to
`tests/test_services.py`. The selection file's 11 are the original 8 plus the three the review demanded:
`test_near_epoch_create_time_is_never_deleted` (seeds `create_time: 84720`, asserts `delete == []` and
`skipped == [(…, SKIP_NO_TIMESTAMP)]`, and asserts `created is not None` first so the test cannot quietly become
vacuous — that assertion is the whole point: **it parses, therefore naive code deletes it**),
`test_creation_dates_below_the_floor_are_treated_as_unset` (1999 below the floor is skipped, 2001 above it is still a
judgeable age, so the floor is pinned as a boundary and not as "reject 1970 specifically"), and
`test_active_guard_compares_the_printers_own_filename_exactly` (NB-6, both directions). The service class's 25 are the
original 20 plus `test_near_epoch_create_time_survives_a_real_delete_run` (the same seed, end to end through
`_async_cleanup_files`: `deleted == []`, `delete_file.assert_not_called()`, `await_count == 1` — the file survives a
**real** delete run, not just the selector), `test_blank_include_is_reported_as_an_inert_pattern_not_silence` and
`test_everything_too_new_is_not_reported_as_a_bad_pattern` (NB-5, both directions),
`test_schema_reads_a_blank_include_as_no_patterns`, and
`test_schema_rejects_a_fractional_age_limit` (NB-7).

Existing test edits: **one line** — `call_count == 3` → `== 4` at `:104`, comment
updated to name `cleanup_files` and point at the new class that pins it by name and index. Nothing else in the
28 existing tests changes; `call_count == 3` is the only registration-count assertion in the repo
(`grep -rn "call_count ==" custom_components/` — the rest are unrelated mocks).

New imports in `test_services.py`: `time`, `PrinterFile`, and
`SERVICE_CLEANUP_FILES, SERVICE_CLEANUP_FILES_SCHEMA, _async_cleanup_files`.

**Where `# noqa` is needed, per directory (NB-4 — the earlier revision implied the opposite).** The two test
directories have **different** ignore lists and the wider one is the *service* test dir:

| Directory | `pyproject.toml` | Ignored | So in that dir you **must** still write |
| --- | --- | --- | --- |
| `custom_components/elegoo_printer/tests/*.py` (where the **25** service tests land) | `:67` | `S101, D102, D103, ANN001, ANN201, ANN202, PLR2004, SLF001, D403, ARG002` | almost nothing — `# noqa: D103` on a bare `def test_x():` is optional here, `1007` needs no constant, and missing `-> None` is tolerated |
| `custom_components/elegoo_printer/cc2/tests/*.py` (where the **11 + 4** = 15 new pure-module tests land) | `:68` | `S101, SLF001, PLC0415` **only** | `-> None` on every test (else `ANN201`), a docstring or `# noqa: D103` on every test (else `D103`), and a named constant instead of a bare magic number (else `PLR2004`) |

Measured with the repo's own `pyproject.toml` and ruff 0.14.1: an annotated, docstringed test in either dir is clean;
strip the annotation and both dirs are still clean **only because** `ANN201` is ignored in `tests/` — in `cc2/tests/`
it fires, together with `D103`; a bare `1007` comparison is silent in `tests/` and `PLR2004` in `cc2/tests/`. That is
why `CC2_ERROR_FILE_DELETE_FAILED` (§ item 1) is needed at all, and why the existing `cc2/tests/test_file_list.py`
writes `def test_corrupt_timestamp_keeps_the_file() -> None:  # noqa: D103` — **copy that shape for the 15 new
`cc2/tests/` tests**; the service tests in `tests/` do not need it. Two caveats so this is not over-claimed:
`D401` (imperative mood) is in neither ignore list and **does fire in both dirs** for non-test helpers (measured: a
`"""Returning a number."""` helper docstring in either dir is flagged), so keep helper docstrings imperative; and
`D401` does **not** fire on functions whose name starts with `test_` in either dir, so the test functions themselves
are free.


### 7. `README.md` + `CHANGELOG.md` — including the video sentence

New `### cleanup_files (Centauri Carbon 2 only)` section under **Services** (after `upload_gcode`, before
`## 📊 Entities`), matching the house style: YAML example, then bullets. It must contain, verbatim in intent:

- **Run the dry run first** — `dry_run` defaults to on, so the first call only answers `would_delete`.
- `exclude` wins over `include`; case matters, and matters the same way on every host.
- **A file with no creation date is never deleted; the file being printed is never deleted.** Spell out the case a
  user will actually hit: a printer whose clock has never been set reports a 1970 creation date, and this service
  treats any creation date before 2000 as "no date" and leaves the file alone — the same rule #426 had to apply to the
  time sensors, applied here where getting it wrong would delete a file rather than misread a sensor.
- **Leaving `include` blank matches nothing, so nothing is deleted** — omit the field to mean "every file". A run in
  that state reports success and says in its message that no listed file matched the pattern, so an automation that
  quietly stops deleting is visible in the notification rather than invisible.
- **Deletion is confirmed by re-reading the file list, not by trusting the printer's reply** — and a file the
  printer accepted and kept comes back in `failed`, with `success` false unless *every* selected file was confirmed.
- **The video limitation, stated plainly**, naming #432's "both gcode and video files", explaining that 1044 is the
  only inventory, that the camera is a live MJPEG stream rather than a file, and that if their firmware *does* list
  videos the patterns will match them because the filter never looks at the extension. Drafted and measured; keep it
  as one honest paragraph, not a footnote.
- A copy-pasteable **daily automation** (time trigger at `03:30`, the service with `response_variable: cleanup`, then
  a notify that reports `cleanup.response.deleted | length` and the refused count). Both YAML blocks were parsed to
  confirm they are valid.

`CHANGELOG.md`, under `[Unreleased]` → `### Added`, in the house's voice (what it does, then the caveat): a new
`cleanup_files` service for the CC2 that deletes old G-code files from the printer's storage by age and name
pattern, dry-run by default, confirmed by re-listing because 1047 is untested on hardware, **G-code only — printer
videos are not deletable and are explained, not hidden** — crediting **@tvidal-net**.

## No blueprint (decided, with the reason)

`blueprints/automation/elegoo_printer/elegoo_printer_progress.yaml` is the only blueprint and the only thing in
`blueprints/`. This task ships **no blueprint**, and the reason is that a blueprint here would be pure overhead:

- The reporter's automation is *one time trigger and one service call*. The README example above is 16 lines of YAML
  that works as pasted. A blueprint adds a file, an input selector for `entry_id`, a selector for the age, a
  selector for the patterns, a selector for the dry run, plus README prose explaining all four — to save someone
  copying six lines. The existing blueprint earns its keep with five entity selectors, a device selector and
  non-obvious trigger logic (progress thresholds, error-state transitions); there is no analogous logic here.
- Nothing tests or validates `blueprints/` (`grep -rln "blueprints" . --include=*.py` outside `.venv` → no files), so
  a shipped blueprint would be an untested user-facing artefact — the worst category in a repo that pins its
  translation key sets.
- Blueprints also cannot express the thing a user most needs here: reading `response.failed`. In a blueprint that
  means a `respond`-style action plus templating inside the blueprint, which is more surface than the service.

If the maintainer wants one later, it is a 30-line follow-up on top of this service and needs no code change. Record
the decision in the PR description so it is not re-litigated on the issue.

## TDD order and exact commands (measured end to end in `/tmp/t5`)

Work in the real tree. Run scoped files as you go; the full gate once at the end.

1. **`cc2/cleanup.py` + `cc2/tests/test_cleanup_selection.py`** → **11 passed** in the file. Pure module, no mocks.
   **Write the two epoch-floor tests in the same commit as the floor** — they are the review's blocking finding and
   the only thing standing between this feature and a nightly mass-delete on a printer with an unset clock. Expect
   `PLR0913` on `select_deletions` (6 args) — take the `# noqa: PLR0913` **on the `def` line**, matching the three
   precedents (§ item 2, NB-2: on a parameter line it yields PLR0913 *and* RUF100), rather than folding two filters
   into one parameter to appease a linter. Remember the `cc2/tests/` ignore list is narrow (NB-4): every test needs
   `-> None` and `# noqa: D103`, and the 1999/2001 boundary test needs named constants rather than inline magic values.
2. **`cc2/const.py` + `delete_file` + `cc2/tests/test_delete_file.py`** → **4 passed**. Expect `PLR2004` on a bare
   `1007` in the test — that is what `CC2_ERROR_FILE_DELETE_FAILED` is for. Expect `F821` if the `const` import is
   missed.
3. **`__init__.py` service + `TestCleanupFilesService`, leaving `call_count == 3` alone**, and watch the reds be the
   predicted ones. Measured, in order of discovery:
   - `test_async_setup_registers_update_ip_service` — the one expected pre-existing red (`4 != 3`).
   - Then three of my own bugs, which is what step 3 is for; see § Two bugs the measurements caught.
   Fix production for the first two, the test for the third. Then update `:104` to `4`. → `test_services.py`
   **53 passed**.
4. **`services.yaml`, `README.md`, `CHANGELOG.md`.** Re-parse the YAML (`python -c "import yaml; …"`) and parse both
   README blocks.
5. **Gate from repo root**: `make format` (the end state needed one `ruff format` pass on the new files and is then a
   no-op — re-measured, 127 files formatted), `make lint` → "All checks passed", `make test` →
   **806 passed, 12 warnings**.

## Count

**806 passed** = 766 baseline + **40**, every one itemised (re-measured in `/tmp/t5` after the review fixes; the
pre-review figure was 798 = 766 + 32, and the further **+8** is 2 epoch/floor tests + 1 active-comparison-basis test in
the selection module, plus 5 in the service class — nothing was removed or renamed):

| New / changed | Count | Where |
| --- | --- | --- |
| `TestCleanupFilesService` | **+25** (28 → 53 in the file) | `tests/test_services.py` |
| `cc2/tests/test_delete_file.py` | **+4** (new file) | wire shape, 1007, no-answer, missing `error_code` |
| `cc2/tests/test_cleanup_selection.py` | **+11** (new file) | include/exclude/age/no-timestamp/**epoch floor ×2**/active/**active comparison basis**/case/boundary/sort |
| Existing tests edited | **1 line**: `call_count == 3` → `4` | `tests/test_services.py:104` |

25 in the service class is a lot for one service, and the breakdown is deliberate: **7 schema** (defaults; bare
string; zero-days + empty-list rejection; mapping rejection; blank `exclude`; blank `include`; fractional days),
**2 registration**, and **16 behaviour** — where the value is in the *failure* paths (refused delete,
accepted-but-still-listed, re-list fails, connection lost mid-delete, nothing matched, **epoch-near create_time
surviving a real run**, **blank include reported as inert**, **everything-too-new NOT blamed on the pattern**,
non-CC2 entry) plus `test_response_carries_every_field_in_every_outcome`, which walks six outcomes and asserts the key
set. If your count differs from 806, explain why rather than adjusting the number.

## The traps — each injected into the finished copy, with the tests that actually caught it

Every row is a real run of the **full 806-test suite** in `/tmp/t5` after injecting the bad implementation. A claim
with no failing test is an unpinned claim, so T7 was rewritten until it caught.

**These counts are my measurements, in one specific throwaway copy (`/tmp/t5`), and a reviewer could not reproduce
them** — they run the suite where the plan's *mechanism* is checkable but the injected copy is not. So each row states
the injection as an exact edit and the mechanism that makes it observable (the stateful double + `await_count`, or a
direct assertion on the pure function). Any of these can be re-derived in five minutes: copy the tree, apply the
edit, run `make test`. Do not treat a count as a fact you cannot re-measure; treat the *failing test name* as the
claim, because that is the part that says what the test is for. T1–T9 were measured once before the review and
**all nine were re-measured after it**, against the 806-test suite, because the new tests change which set each trap
catches. T10–T14 are new, from the review round.

| # | Trap injected (exact edit) | Result (my run, 806-test suite) | Caught by |
| --- | --- | --- | --- |
| T1 | Skip the confirmation re-list (`if deleted:` → `if False:`) — i.e. **trust the printer's reply** | **4 failed, 802 passed** | `test_deletes_old_file_and_confirms_it_by_re_listing`, `test_accepted_but_still_listed_counts_as_not_deleted`, `test_partial_delete_reports_both_halves`, `test_re_list_failure_reports_unconfirmed_not_success` |
| T2 | `dry_run` defaults to `False` | **1 failed, 805 passed** | `test_schema_defaults_to_dry_run_and_sixty_days` |
| T3 | Undated files treated as old and deleted (`created is None` → `delete.append`) | **4 failed, 802 passed** — *was 1 before the review* | `test_file_without_a_creation_date_is_never_deleted`, `test_near_epoch_create_time_is_never_deleted`, `test_creation_dates_below_the_floor_are_treated_as_unset`, `test_near_epoch_create_time_survives_a_real_delete_run` |
| T4 | Active-print guard removed | **3 failed, 803 passed** — *was 2* | `test_active_file_survives_every_filter`, `test_active_guard_compares_the_printers_own_filename_exactly`, `test_never_deletes_the_file_being_printed` |
| T5 | `exclude` ignored | **2 failed, 804 passed** | `test_exclude_wins_over_include`, `test_keeps_young_excluded_and_undated_files_with_reasons` |
| T6 | Missing/no-`error_code` reply counted as success (`result.get("error_code", 0)`) | **2 failed, 804 passed** | `test_delete_without_answer_raises`, `test_delete_without_error_code_raises` |
| T7 | `fnmatch.fnmatch` instead of `fnmatchcase` | **1 failed, 805 passed** *after the test was rewritten* — see below | `test_patterns_ignore_the_host_path_case_rules` |
| T8 | Partial success reported as success (`success = bool(deleted)`) | **1 failed, 805 passed** | `test_partial_delete_reports_both_halves` |
| T9 | `cleanup_files` not registered at all | **3 failed, 803 passed** | `test_async_setup_registers_update_ip_service`, `test_async_setup_registers_cleanup_files_service`, `test_registered_handler_takes_the_call_alone` |
| **T10** | **Epoch floor removed** (`if created is None or created < _MIN_VALID_CREATE_TIME:` → `if created is None:`) — the #426 bug, restored | **3 failed, 803 passed** | `test_near_epoch_create_time_is_never_deleted`, `test_creation_dates_below_the_floor_are_treated_as_unset`, `test_near_epoch_create_time_survives_a_real_delete_run` |
| **T11** | Blank `include` falls back to `["*"]` (the option § item 4 rejects) | **2 failed, 804 passed** | `test_schema_reads_a_blank_field_as_no_patterns`, `test_schema_reads_a_blank_include_as_no_patterns` |
| **T12** | Fractional-day check removed (back to bare `vol.Coerce(int)`, so `60.9` → 60) | **1 failed, 805 passed** | `test_schema_rejects_a_fractional_age_limit` |
| **T13** | `_pattern_note` never distinguishes (early `return None`), so a blank/typo'd include is the bland "nothing matched" message again | **1 failed, 805 passed** | `test_blank_include_is_reported_as_an_inert_pattern_not_silence` |
| **T14** | Active guard made "smarter": `name.casefold().strip() == active.casefold().strip()` | **1 failed, 805 passed** | `test_active_guard_compares_the_printers_own_filename_exactly` |

**T10 is the row that matters most in this table**, because it is the only one where the injected bug deletes a real
user's file. Its mechanism is checkable without running anything: `PrinterFile({"create_time": 84720}).created` is
`1970-01-01 23:32:00+00:00`, not `None` (verified against the real model in the real tree), so any filter that tests
truthiness alone puts that file on the delete side of every cutoff. The test asserts `created is not None` before
asserting the file survives, which is what stops it from being a test of the `None` path in disguise — the mistake
that let this bug through the pre-review design, whose only timestamp test was
`test_corrupt_timestamp_keeps_the_file` with `create_time=10**20` (a value that *raises*, so it exercises `None`).

**T14 is the inverse lesson**: a trap that *adds* robustness to the guard also fails a test, because that test pins
the comparison **basis**, not just the outcome. If someone later decides case-folding is right, they have to change
the test and the sentence in § The change item 2 that explains the trade-off.

**T7 is the one to read, because my first test for it was worthless.** I wrote "assert `ecc*` does not match
`ECC2_x`", ran the trap, and got **798 passed** — the trap was completely invisible. Reason: on Linux
`os.path.normcase` is the identity, so `fnmatch` and `fnmatchcase` behave *identically* on the machine every test
here runs on. The bug would only have appeared for a user running HA on Windows, which is precisely the user the
rule exists to protect. The test now patches `os.path.normcase` to lowercase during the assertion, so `fnmatch`
would match and fail while `fnmatchcase` never calls it and passes — and it asserts the positive case too, so the
patch cannot be making the test vacuous. **Lesson for the rest of this repo: a cross-platform rule tested only
where the platforms agree is not tested.** If you add a case-related rule anywhere else here, do the same. **T10 is
the same class of mistake one level up**: a timestamp rule tested only with values that fail to parse is not a test
of the values that parse *wrong*.

Also true, and worth keeping in mind:

- **Deleting the confirmation but keeping the tests' `await_count` assertions is not the same trap as T1** — T1
  disables the re-list and *all four* tests still fail, because the double is stateful. That is the property that
  makes the done-when clause falsifiable; a scripted double would have passed T1.
- **`storage_media` is a keyword arg defaulting to `"local"`, and nothing in the tree offers another value.**
  Adding a `storage_media` service field (u-disk / sd-card) is tempting and out of scope: 1044 is only ever
  documented and exercised with `path: "/"` on `local`, so a USB-drive cleanup would list nothing and delete
  nothing while reporting success. That is the same silent-no-op failure this plan refuses elsewhere.

## Two bugs the measurements caught (so you don't repeat them)

Both were my implementation being wrong, not the tests being fussy — recorded because they are the two easy ways to
get this service subtly wrong.

1. **`_asdict()` on unset prose sent `error: None`**, and a full-equality assertion caught it. In a Jinja template
   `None` renders as the string `"None"`, so a notify reading `{{ response.error }}` on a successful run would tell
   the user "None". Fixed by filtering unset `error`/`message` out of `as_dict()` — the structural fields stay, the
   prose disappears when there is none to say.
2. **"Nothing matched" was reported as `success: False`.** My first version keyed success on `bool(to_delete)`, so a
   nightly run with no old files raised the automation's failure path every night forever. An empty selection is a
   clean run. `success` now reflects only refusals and unconfirmed deletes.

Both are pinned (1 by `test_deletes_old_file_and_confirms_it_by_re_listing`'s full-dict equality plus the
every-fields test; 2 by `test_nothing_matching_is_reported_as_an_empty_success`).

**A third bug was caught by review, not by measurement, and that is the uncomfortable one.** The epoch-near
`create_time` defect (§ Reviewer round 1) **passed all 798 tests and all nine original traps.** Nothing in the
test suite could see it, because the only timestamp test used a value that *fails to parse* (`10**20`) and so
exercised the `None` path, while the dangerous value (`84720`) *parses successfully*. A suite that tests "missing"
and "corrupt" but not "wrong-but-valid" has no coverage of the case that destroys data. Two lessons for this repo:
**destructive paths need a test for a plausible bad value, not just an impossible one**, and **green is not safe** —
this plan's own trap methodology, which is otherwise rigorous, was not sufficient to find it.

## Scope — the true seam

| File | Change |
| --- | --- |
| `custom_components/elegoo_printer/cc2/cleanup.py` | **new**, 132 lines as measured in `/tmp/t5`, pure (no HA, no transport). Includes `_MIN_VALID_CREATE_TIME` + `_usable_created` — the epoch floor is part of this module, not the handler's problem |
| `custom_components/elegoo_printer/cc2/const.py` | +3 lines: `CC2_ERROR_FILE_DELETE_FAILED = 1007` + its comment. `CC2_CMD_DELETE_FILE` **already exists at `:80`** |
| `custom_components/elegoo_printer/cc2/client.py` | +`delete_file` before `set_fan_speed` (`:1741`); +1 line to the `from .const import` block |
| `custom_components/elegoo_printer/__init__.py` | +`SERVICE_CLEANUP_FILES`, `_glob_patterns`, `_whole_days`, schema, `CleanupReport`, `_pattern_note`, `_async_delete_each`, `_async_cleanup_files`, `_cleanup_failure_text`, `_cleanup_unreachable`; 4th registration; `NamedTuple` import; extend the `.cc2.cleanup` (incl. `SKIP_NOT_INCLUDED`) and `.cc2.const` imports |
| `custom_components/elegoo_printer/services.yaml` | +`cleanup_files` entry |
| `custom_components/elegoo_printer/tests/test_services.py` | +`TestCleanupFilesService` (**25**) + 3 helpers + imports; **1 existing assertion** `3` → `4`; file goes 28 → **53** |
| `custom_components/elegoo_printer/cc2/tests/test_delete_file.py` | **new**, 4 tests |
| `custom_components/elegoo_printer/cc2/tests/test_cleanup_selection.py` | **new**, **11** tests (incl. the 2 epoch-floor tests and the active-guard basis test) |
| `README.md` | +`cleanup_files` section (Services) incl. the video limitation, the unset-clock rule, the blank-`include` warning and the daily automation |
| `CHANGELOG.md` | +1 bullet under `[Unreleased] → Added`, crediting @tvidal-net |
| `docs/CC2_PROTOCOL.md` | see § Protocol doc, below |

**OUT of scope, declared:** `translations/` (services have no translation surface — see § Corrections);
`config_flow.py`, `data.py`, entry options (the reporter's config question is answered by call data);
`blueprints/` (§ No blueprint); `camera.py` (task T4's file, and this task needs nothing from it — "T4" here is the task, not trap T4); `coordinator.py`
(no new poll work — the service lists on demand); `scripts/test_cc2_printer.py` (§ Protocol doc, decision 3);
sdcp/websocket transports (SDCP has `CMD_BATCH_DELETE_FILES = 259` at `sdcp/const.py:29`, **defined and never used**,
and the sibling `CMD_START_PRINT` crashed the first-gen Centauri Carbon per #297 — wiring a second, equally
unverified delete path for a transport nobody asked about is how printers get bricked; CC2 only, exactly as
`start_print` and `upload_gcode` do it).

## Protocol doc: record the unknown, do not invent a reply

`docs/CC2_PROTOCOL.md` is the repo's evidence ledger and its convention is that a claim carries its provenance
("Measured on firmware 02.01.00.00…", "⚠️ Stock firmware: …"). Two edits, both about the unknown, plus one deliberate
**non**-edit:

1. **The "Delete File [Both]" section (`:1603-1614`)** gets an explicit note that no response is documented for
   1047, that the method is marked Untested in the table at `:1457`, that `1007 FILE_DELETE_FAILED` (`:2038`) is the
   only hint its reply resembles the standard envelope, and that **this integration therefore confirms a delete by
   re-listing rather than by reading the reply** — with a placeholder asking whoever first runs 1047 against real
   hardware to paste the raw reply here. This is the cheapest possible way to make the unknown collectable.
2. **The File Operations checklist (`:2407-2412`)** — add a `Delete file (MQTT 1047)` line marked as implemented-but-
   hardware-unverified, so the checklist does not imply the operation was never attempted.
3. **`scripts/test_cc2_printer.py` is NOT given a 1047 handler — and this is a decision, not an omission.** The
   simulator already defines `CC2_CMD_DELETE_FILE = 1047` (`:71`) but has no `elif method ==` branch for it, so it
   answers nothing. Tempting to add one; it would be a **fabricated** response, and every test written against it
   would then be checking our invention rather than the printer. It is also what makes the honest path testable
   today: pointing a test at the simulator's 1047 reproduces the timeout branch, i.e. "we do not know what it says"
   as an observable state. Leave it, and leave the simulator's file store alone — it has no file list to delete
   from (`grep -n "elif method ==" scripts/test_cc2_printer.py` shows no 1044 handler either), which is a pre-existing
   gap, not this task's.

Do **not** mark 1047 "Works" anywhere, and do not add a fabricated response example. `docs/roadmap/triage-2026-10-status.md:95`
already flags this as an open question; § Other notes updates it.

## The issue stays open (do not close #432 on a green gate)

**A green suite here proves the code agrees with a mock, not that the printer cooperates.** Every 1047 assertion runs
against a double whose reply shape this repo invented from the shared envelope. Concretely unproven by this commit:

- whether a real CC2 answers 1047 at all, and with what;
- whether `error_code: 0` means what we assume;
- whether a real delete is visible in a subsequent 1044 (the re-list is designed to survive *any* answer, including
  silence — but nobody has watched it work);
- whether the reporter's printer lists video files at all (§ Videos are out of scope);
- whether `print_status.filename` and the 1044 listing key are ever spelled differently for the same file — the
  active-file guard's exact comparison depends on it and nothing in this repo can settle it (NB-6);
- what an **unset printer clock** looks like in a 1044 `create_time` (the epoch floor assumes "small or zero"; #426's
  evidence is for job times, not the file list).

So: the PR says "implements #432 for G-code files; **#432 stays open until one hardware run confirms it**", and the
CHANGELOG line says the same. The verification that closes it is five minutes with a real printer: run the service
with `dry_run: true`, read `would_delete`, tick one throwaway file into the pattern, run it for real, and confirm the
file is gone from the **Print file** select. Ask **@tvidal-net** to do exactly that — they have the hardware, offered
to PR, and get to be the one who closes their own issue. If they cannot, the issue stays open as a known-unverified
feature rather than quietly closing it, and `docs/CC2_PROTOCOL.md` keeps its placeholder.

## Limit notes (recorded, not silently paid)

- **The entire delete path rests on an undocumented response shape.** Mitigated, not solved: the design never trusts
  the reply, so the worst case is a *reported* failure, never a silent wrong deletion. Still UNVERIFIED.
- **Videos are not handled** (§ Videos are out of scope). The reporter asked for both. The README and the reply to
  them carry this; it is not a hidden scope reduction.
- **No `storage_media` field** — `local` only, because 1044 is only ever documented with `path: "/"` on `local`.
  USB/SD cleanup is a real gap for anyone who stores there. **UNVERIFIED** whether 1044 lists a `u-disk` at all.
- **Files inside directories are not reached.** `_handle_file_list` skips `type != "file"` and the listing is
  root-only, so a printer with model folders keeps them forever. Whether 1044 can even walk a path is undocumented.
- **Sequential deletes are slow**: one command per file with a 10 s timeout each, so a printer full of 40 old files
  could take minutes if it answers slowly — and, if it does not implement 1047, **10 s per file of pure timeout**
  before the service gives up on the first file. Acceptable for a 03:30 automation; not something to put behind a
  UI button. Not parallelised: one MQTT command channel, and this method is untested.
- **`older_than_days` uses `create_time`, not last-printed.** A 2-year-old file printed yesterday is a candidate.
  1044/1046 do report `last_print_time` and `total_print_times` (`docs/CC2_PROTOCOL.md:1550-1551`) and `PrinterFile`
  does **not** parse them, so a "keep anything printed recently" rule is a real follow-up needing two small model
  changes. `include`/`exclude` is today's workaround.
- **A pattern matching nothing is *reported* but still deletes nothing.** `skipped` only ever contains files
  present in the listing, so a typo'd `include=EEXC*` and a blank `include: ""` both select nothing. The review
  (NB-5) is right that a permanently-inert job reporting success is a bug, so `_pattern_note` now says so in the
  message ("none of the 12 listed file(s) matched the include pattern …, so nothing was a candidate"), which makes
  the inertness visible in a notify. What is **not** fixed: `success` stays `True` in that case, deliberately — an
  automation must not alert nightly on "nothing to do" (§ The change item 4) — and a *message* is only read if the
  user reads the notification. The dry run remains the real defence, and `would_delete` being empty is the cue.
  Trap T13 pins the message, T11 pins why the blank field is not silently widened to `*`.
- **The active-file guard fails open on a name mismatch** (NB-6). It compares
  `status.print_info.filename` (`cc2/models.py:229`) with the 1044 listing key by **exact equality**, so if the
  printer ever reports the same file under a different spelling — different case, trailing whitespace, a path
  prefix — the file **currently printing becomes deletable**. Exact equality was chosen over a fuzzy match because
  the repo already treats the two strings as one namespace (`cc2/models.py:315-316` looks the print filename up in
  the 1044-derived `_file_details` cache) and a fuzzy match would *also* skip files a user legitimately wants gone.
  **UNVERIFIED** whether the two ever differ — that needs a real 1044/1047 pair from one printer mid-print, which is
  part of the hardware run in § The issue stays open. `test_active_guard_compares_the_printers_own_filename_exactly`
  pins the basis in both directions and trap T14 fails if someone changes it silently.
- **Race with a print started after the listing**: the active-file guard reads status at selection time, so a job
  started between selection and delete is not protected. The 10-min poll means the status is at most ~2 s stale, and
  the printer refusing to delete a file it is printing (1007) is the backstop — **UNVERIFIED**, since 1047 is
  untested. A daily 03:30 trigger is the practical mitigation, and the README example uses one.
- **`CleanupReport` always allocates the full key set** including `would_delete` on real runs, where it duplicates
  `deleted`. Cheap, and preferred over a response whose shape depends on which branch ran.
- **`_glob_patterns` is hand-rolled** rather than `cv.ensure_list`. Deliberate (§ The change item 4) — the reason
  is *measured*, not stylistic: the `cv` composition rejects `""`, which is what a cleared UI text field submits.
  ~20 lines of validation a stdlib-ish list-of-globs helper would replace if HA ever ships one. Its docstring names
  the two claims about `cv.ensure_list` that are **false** so nobody re-adds them.
- **`older_than_days` rejects fractions rather than rounding them** (NB-7). `vol.Coerce(int)` and HA's
  `cv.positive_int` both truncate `60.9 → 60`, i.e. *more* deletion than asked; refusing the value is the safer
  half of the fix, and the cost is that a template producing `60.0` from arithmetic is accepted (`60.0 == 60`) while
  one producing `60.5` fails the call. Failing loudly is the intended behaviour on a destructive field, but it does
  mean an automation that computes a fractional age breaks rather than rounds.
- **The 2000 epoch floor is a judgement, inherited from #426, not a measurement of this printer.** It rejects any
  `create_time` before `2000-01-01`, which cannot be a real G-code creation date (the format postdates it) and is the
  same boundary `definitions.py:56` already uses for job times. The cost of the choice is one-sided and intentional: a
  genuinely-old file dated before 2000 is never deleted, which is a missed cleanup; the alternative — trusting it — is
  deleting files whose clock was never set. A file dated 2000-2005 with a *correct* clock is likewise treated as old
  and will be deleted, which is correct. **UNVERIFIED** how a CC2 with an unset clock reports `create_time` in the
  field list (the #426 evidence is for job times on a Centauri Carbon after a restart); the hardware run should note
  what an unset clock looks like in 1044 too.
- **UNVERIFIED:** nothing here touched a printer, a broker or a running Home Assistant. Registration tests use a bare
  `MagicMock` hass (the file's existing pattern), so `services.yaml`↔schema agreement is checked by parsing YAML and
  by reading both, not by a test. A real HA start-up with the integration loaded would be the last check before
  merge, and is worth asking a maintainer to do.

## Parallelisation

SERIAL — single task, wave 2 (`T4 -> T5 -> T6`). Owns `__init__.py` (free since T2 `aae757f`; T4 did not touch it at
all) and `cc2/client.py` (free since T3 `e63ea94`) for its duration. **Note for the orchestrator:** T6's seam
(`config_flow.py` + `translations/`) does **not** collide with this task's — `translations/` is OUT of T5 (§ Corrections),
so the wave-2 note at `triage-2026-10-status.md:70` ("T6 touches `translations/*`, shared with T5 -> T6 after T5") has
a false premise once T5 ships. The ordering is still the right one to keep (wave 2 is serial by design and T5's
`__init__.py` churn is worth landing alone), but it is no longer a file-disjointness requirement. Re-deriving the
chain is the orchestrator's call; flagging it rather than quietly leaving a stale rationale.

## Other notes for the orchestrator

- **Correct the parent status row (`triage-2026-10-status.md:80`)** — T5's seam currently reads
  "`cc2/client.py`, `services.yaml`, `translations/*` (18 locales), new test, `blueprints/`" and **three of those five
  are wrong**: `translations/` is out (services have no translation surface), `blueprints/` is out (decided, with
  reasons), and the seam is missing `cc2/cleanup.py`, `cc2/const.py`, `__init__.py` and `README.md`. Same style as
  wave 1's two corrections and T4's.
- **Update the open-questions bullet at `:95`** ("T5's 1047 DELETE_FILE is documented 'Untested' — protocol
  verification is part of that task's grounding") to: shipped with the unknown designed around (confirm-by-relist),
  **still unverified on hardware**, `docs/CC2_PROTOCOL.md` carries a placeholder for the first real reply, and #432
  stays open for a hardware run.
- **Commit shape**: one commit. Suggested split if the wave protocol wants reviewable chunks: (a) `cc2/cleanup.py` +
  `cc2/const.py` + `delete_file` + their two new test files — pure and self-contained; (b) service + `services.yaml`
  + `test_services.py` + README/CHANGELOG/doc. Both green on their own. **The epoch floor and its three tests must
  land in (a) together** — the floor is in the pure module, and one of the three (`test_near_epoch_create_time_survives_a_real_delete_run`)
  is in (b), so if the split is used, (a) must not be merged as a standalone PR without (b) following: the floor
  would be shipped with only two of its three pins.
- **Cross-reference #426 in the PR description.** The epoch floor is the same boundary `definitions.py:54-61` already
  applies to job times, and the PR is the natural place to say "this is #426's lesson applied to the destructive path,
  where the cost of the bug is data loss rather than a wrong sensor reading". It also invites the follow-up the plan
  does *not* take: a shared helper for "printer timestamp below the floor means unset", which would belong in
  `definitions.py` or the models layer once a third caller needs it. Not extracted here — two callers in different
  layers is not yet a pattern, and `cc2/cleanup.py` is deliberately import-free of the HA layer.
- **Credit @tvidal-net** in the CHANGELOG and the PR. They wrote the include/exclude design, kept its vocabulary
  verbatim (`include`/`exclude`, `all`/`nothing` defaults, `ECC*`/`Keep*` examples), and offered to implement it —
  the shipped design is theirs, with the confirmation-by-relisting and dry-run-default added because 1047 is
  untested.
- **Reply to the reporter** with three things: the config question's answer (call data, not entry options —
  § Corrections), the video limitation (§ Videos are out of scope), and the hardware run that closes #432.
