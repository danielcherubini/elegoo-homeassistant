#!/usr/bin/env python3
"""
Probe which parameter shape method 1047 (DELETE_FILE) actually accepts.

Background (issue #432)
-----------------------
On real firmware (02.01.01.07) `1047` answers every delete with
`{"result": {"error_code": 1003}}` and deletes nothing — identically for a file
that exists and one that does not — while the very same file deletes fine from
the printer's own touchscreen. `1003` is `CC2_ERROR_INVALID_PARAMETER`
(`cc2/const.py`): a *request-shape* rejection. This integration's sibling file
methods do not share one parameter spelling — 1045 works being sent `file_name`
and 1046 works being sent `filename` — so `filename` may simply be the wrong key
for 1047. That is a HYPOTHESIS, and this script exists to discriminate, not to
confirm it. The reporter's alternative reading — that `1003` is this firmware's
generic "not available / not handled" bucket (they also saw 1045 answer 1003 for
one file in the same session) — is equally untested, and if every variant answers
alike this script says so plainly. That is a real, publishable result.

Usage
-----
    # DRY RUN (the default): connects, resolves the serial, checks the printer
    # is idle, uploads ONE disposable file to prove the upload path, prints the
    # exact matrix it would send, and cleans up that one file.
    uv run python scripts/cc2_probe_delete.py --ip 192.168.1.100

    # THE REAL PROBE: uploads one fresh file per variant and deletes them.
    uv run python scripts/cc2_probe_delete.py --ip 192.168.1.100 --run

    # Pairing-protected printers, and the in-repo simulator:
    uv run python scripts/cc2_probe_delete.py --ip 192.168.1.100 --access-code 123456 --run
    uv run python scripts/cc2_probe_delete.py --ip 127.0.0.1 --run --upload-port 8080

Run it under `uv run` (or the repo venv), never a bare interpreter
---------------------------------------------------------------
This script imports the SHIPPED `ElegooCC2Client` rather than re-implementing
MQTT, so that a positive answer is immediately actionable as a one-line change
to `delete_file` — a probe with its own protocol code would only ever prove its
own code works. Importing `custom_components.elegoo_printer.cc2.client` executes
the parent package's `__init__.py`, which imports `homeassistant` (line 23), so
`homeassistant` must be importable: it is an installed project dependency, which
is why `uv run` works and a bare `python3` on a volunteer's laptop does not.

Home Assistant must NOT be running
---------------------------------
The printer accepts a limited number of registered clients and answers further
registrations with `too many clients` (`client.py` registration path), so an
integration holding the slot makes this script fail to register. Stop Home
Assistant (or at least remove/pause the Elegoo integration) before running it.

Safety properties, and why each exists
--------------------------------------
* **It only ever deletes a file it uploaded itself in this run.** Every delete
  goes through `_delete_own_file`, which refuses any name it did not create.
* **It refuses to run against a printer that is working.** The load-bearing
  guard is the *filename* the printer names, not the status code:
  `CC2StatusMapper.map_status` resolves a machine code it does not recognise to
  `ElegooMachineStatus.IDLE` (`cc2/models.py`), so firmware reporting a new
  printing-ish code arrives already looking idle and the mapped status can never
  fail closed. The mapped status is only the backstop. (The integration itself
  documents this in `custom_components/elegoo_printer/__init__.py`.)
* **`--ip` is never silently overridden.** `connect_printer` overwrites both the
  IP and the serial from whatever `Printer` it is handed
  (`self.printer_ip = printer.connection_host or self.printer_ip`,
  `self.serial_number = printer.id or self.serial_number`), so passing the
  `Printer` that discovery returned would make `--ip` a dead argument and, on a
  LAN with more than one CC2, would delete files on the wrong machine. This
  script resolves IP and serial into plain strings, aborts if `--ip` and
  discovery disagree, and hands `connect_printer` a bare `Printer()` so the
  constructor values survive.
* **One fresh upload per variant.** The only trustworthy signal is "was the file
  still listed afterwards", and that signal is destroyed the moment one variant
  succeeds: with a shared file, every later variant would report
  `still_listed: false` for a reason unrelated to its parameters.
* **Silence is never reported as agreement.** A variant whose 1047 was never
  answered keeps `status: "no_reply"` and contributes no error code to the
  analysis; the report counts `variants_without_reply` out loud, and the
  conclusion has its own wording for "nothing answered" (inconclusive — check
  connectivity and that Home Assistant was off, then re-run) that never claims
  the variants answered alike. A printer on wifi drops requests; a run where that
  happened must read as a failed capture, not as a finding for #432.
* **The capture survives an abort.** A crash mid-matrix or a Ctrl-C produces the
  same stdout report and JSON file, marked `aborted`, built from whatever was
  measured, because that report is the only thing that names the probe files the
  volunteer has to delete by hand. Clean-up is attempted only while the
  connection is alive; otherwise the files are named, never blindly deleted. The
  matrix also stops at the first variant it cannot send once the link is gone,
  rather than uploading more unconfirmable files, and every successful upload is
  echoed on its own greppable `PROBE UPLOADED <name>` line so terminal scrollback
  alone can recover the leftovers.
* **It waits for its own upload to appear in the 1044 listing before deleting
  it.** A just-uploaded file is not guaranteed to show up in the next listing,
  and a transient absence reads exactly like a successful delete. A variant
  whose file never appears is reported INCONCLUSIVE, never as a success.
* **Dry run by default.** `--run` is required to send the matrix. The dry run
  uploads a single preflight file rather than one per variant, so a volunteer
  who runs it against a printer whose deletes are dead (i.e. the printer this
  script exists to investigate) does not leave eleven junk files behind.
* **A refusal is never reported as a deletion, and an unread listing is never
  reported as either.** The identical-answer analysis compares `still_listed`,
  and a set of nothing but `None`s (every confirming 1044 failed to read)
  satisfies "they all agreed" just as well as real agreement — so the conclusion
  claims only "none of them was CONFIRMED to have removed its own file" and
  counts the listing failures out loud. Identical replies are evidence about the
  reply, never about the file.
* **Every exit path emits a capture, and the teardown can never eat it.** The
  unresolved-target (exit 2) and failed-connect (exit 3) paths raise the same
  `_EarlyExit` the other early exits use, so the single `emit_capture` at the end
  of `probe` handles them too — a run that never reached a printer still leaves a
  JSON naming the `--ip` that was tried and why it stopped, marked
  `printer_reached: false` so it can never be mistaken for a measurement. Both
  the abort clean-up and the final `disconnect()` are bounded
  (`ABORT_CLEANUP_TIMEOUT`, `DISCONNECT_TIMEOUT`), because a stalled teardown on
  a half-dead wifi link would otherwise bury a report that has already been
  measured.

Output
------
A paste-ready report on stdout and in `scripts/cc2_probe_<timestamp>.json`:
per variant the exact request JSON, the full raw reply JSON, elapsed ms and
whether its own file was still listed, plus firmware, model, `token_status`, the
caveats, and the leftovers a human has to delete from the UI. Paste the whole
block into https://github.com/danielcherubini/elegoo-homeassistant/issues/432.

Each variant carries a `status`, and the difference between them is the whole
point of the report: `sent` (the printer answered — its `error_code` is
evidence), `no_reply` (a 1047 went out and nothing came back — NOT evidence),
`link_dropped` (never sent, because the connection was already gone),
`upload_failed`, `inconclusive_never_listed` and, on a dry run,
`dry_run_upload_only`. Only `sent` rows feed the identical-answer analysis.

Validating this script without hardware
---------------------------------------
`scripts/test_cc2_printer.py` is the target: start it with `--idle` (it otherwise
boots mid-print and this script correctly refuses to run) and point
`--upload-port` at its HTTP endpoint (8080 by default; the real printer uses 80,
which needs root). Flip the simulator's `DELETE_PARAM_KEY` to `"file_name"` and
re-run: the error codes and the `still_listed` column must SWAP. If they do not,
the probe is not discriminating and must not go near a real printer.
"""

import argparse
import asyncio
import hashlib
import json
import re
import secrets
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

# Repo idiom: make `custom_components` importable regardless of the cwd this
# script is started from (see scripts/extract_cc2_data.py,
# scripts/test_embedded_mqtt_broker.py). Must happen before the imports below.
sys.path.insert(0, str(Path(__file__).parent.parent))

import aiohttp  # noqa: E402
from loguru import logger  # noqa: E402

from custom_components.elegoo_printer.cc2 import upload as cc2_upload  # noqa: E402
from custom_components.elegoo_printer.cc2.client import (  # noqa: E402
    CC2UnusableFileListError,
    ElegooCC2Client,
    CC2UnusableStatusError,
)
from custom_components.elegoo_printer.cc2.const import (  # noqa: E402
    CC2_CMD_DELETE_FILE,
    CC2_CMD_GET_ATTRIBUTES,
    CC2_DISCOVERY_RETRIES,
    CC2_DISCOVERY_TIMEOUT,
)
from custom_components.elegoo_printer.cc2.discovery import CC2Discovery  # noqa: E402
from custom_components.elegoo_printer.cc2.upload import (  # noqa: E402
    UploadFile,
    UploadTarget,
    upload_gcode,
)
from custom_components.elegoo_printer.sdcp.exceptions import (  # noqa: E402
    ElegooPrinterConnectionError,
    ElegooPrinterNotConnectedError,
)
from custom_components.elegoo_printer.sdcp.models.enums import (  # noqa: E402
    ElegooMachineStatus,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer  # noqa: E402

# What can go wrong on a command round-trip. ElegooPrinterNotConnectedError is
# NOT a subclass of ElegooPrinterConnectionError, and `_send_command` raises it
# when the link dropped between calls, so catching only the connection error
# would let the probe die with a traceback instead of a readable report.
TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    ElegooPrinterConnectionError,
    ElegooPrinterNotConnectedError,
)
# ... plus the client's own "it answered, but I cannot trust the answer" errors.
LISTING_ERRORS: tuple[type[Exception], ...] = (
    *TRANSPORT_ERRORS,
    CC2UnusableFileListError,
)
STATUS_ERRORS: tuple[type[Exception], ...] = (
    *TRANSPORT_ERRORS,
    CC2UnusableStatusError,
)
# ... plus everything the HTTP upload path can raise. Written as a flat tuple
# rather than `(TRANSPORT_ERRORS, aiohttp.ClientError, OSError)` on purpose: a
# NESTED tuple in an `except` clause is not flattened by Python, so the clause
# itself raises `TypeError: catching classes that do not inherit from
# BaseException` and the variant is recorded as nothing at all — the exception
# escapes the matrix and the whole capture is lost. Same reason the three tuples
# above are flat.
UPLOAD_ERRORS: tuple[type[Exception], ...] = (
    *TRANSPORT_ERRORS,
    aiohttp.ClientError,
    OSError,
)

# The machine states in which the printer is not reading a G-code file out of
# storage, so a delete cannot pull a file out from under a running job.
#
# Re-derived here rather than imported: the integration keeps the same set as
# `_IDLE_MACHINE_STATUSES` in `custom_components/elegoo_printer/__init__.py`, and
# importing that module pulls the whole Home Assistant runtime in (it imports
# `homeassistant` at line 23). Two members, both from `sdcp/models/enums.py`.
IDLE_MACHINE_STATUSES: frozenset[ElegooMachineStatus] = frozenset(
    {ElegooMachineStatus.IDLE, ElegooMachineStatus.STOPPED}
)

# How long to wait for a freshly uploaded file to show up in the 1044 listing.
SETTLE_TIMEOUT = 30.0
SETTLE_POLL = 1.0

# How long the abort path may spend trying to clean up before it gives up and
# just prints the leftovers. A run that died because the printer stopped
# answering would otherwise spend 10 s per command (CC2_COMMAND_TIMEOUT) timing
# out — minutes — before the volunteer saw the list of files they must delete.
# The report is the deliverable; the clean-up is a bonus.
ABORT_CLEANUP_TIMEOUT = 30.0

# How long the final `disconnect()` may take before it is abandoned. Same reason
# as above, one step later: `disconnect` awaits the heartbeat and listener tasks
# and the MQTT close, so a printer on wifi that has gone half-dead can hang it —
# and the report is produced AFTER that `finally`, so an unbounded disconnect
# would cost the volunteer a capture that had already been measured. Five seconds
# is generous for a close that is going to work at all, and small enough that the
# paste-ready block still appears in the same breath as the last log line.
DISCONNECT_TIMEOUT = 5.0

# The only delete shape a dry run may use when it cleans up its one preflight
# file: what `delete_file` ships today. `cleanup` re-points the filename key at
# each of our own leftovers, so the placeholder never reaches the printer.
DRY_RUN_CLEANUP_SHAPE: dict[str, Any] = {
    "storage_media": "local",
    "filename": "<replaced per file>",
}

# The HTTP token fallback the client itself uses when no access code is configured
# (`client.py` tries "" then this). The access code IS the MQTT password and the
# HTTP X-Token; there is no derivation to invent.
DEFAULT_HTTP_TOKEN = "123456"

ISSUE_URL = "https://github.com/danielcherubini/elegoo-homeassistant/issues/432"

CAVEATS = [
    "The probe files are hand-written G-code, not slicer output. Whether this "
    "printer accepts a bare hand-written body for upload AND for delete is "
    "itself unmeasured — real sliced files carry a slicer header and a thumbnail "
    "block. A 1003 against one of these files is therefore ambiguous between "
    "'wrong parameter shape' and 'this file is not deletable'. Names end in "
    ".gcode and the bodies are non-trivially sized to keep the files as "
    "plausible as a hand-written file can be.",
    "The alternative reading of 1003 is NOT excluded by this script: the reporter "
    "of #432 also saw method 1045 answer 1003 for one file in the same session, "
    "and suggested 1003 may be this firmware's generic 'not available / not "
    "handled' bucket. That reading is equally untested. The discriminating "
    "signal is whether ANY variant both answers error_code 0 and makes its own "
    "file disappear from the next 1044; if nothing does, this report says so and "
    "the alternative reading stands.",
    "Every delete here targets a file this script uploaded itself. No pre-existing "
    "user file was touched, and the printer was checked idle with a filename "
    "guard before anything was sent.",
]


class Target(NamedTuple):
    """Where to connect, resolved to plain strings BEFORE any Printer exists."""

    ip: str
    serial: str
    model: str | None
    name: str | None
    token_status: Any
    discovered: bool


class Variant(NamedTuple):
    """One row of the probe matrix."""

    slug: str
    label: str
    build: Any  # callable(name) -> params dict


# The matrix, in the order the plan fixed. `N` is the variant's own uploaded file.
VARIANTS: list[Variant] = [
    Variant(
        "local_filename",
        "what we ship today (1044's storage_media + 1046's key)",
        lambda n: {"storage_media": "local", "filename": n},
    ),
    Variant(
        "local_file_name",
        "THE HYPOTHESIS: 1045 works being sent file_name",
        lambda n: {"storage_media": "local", "file_name": n},
    ),
    Variant(
        "local_filename_path",
        "1044 sends a path; 1047 may want one too",
        lambda n: {"storage_media": "local", "filename": n, "path": "/"},
    ),
    Variant(
        "local_file_name_path",
        "file_name plus path",
        lambda n: {"storage_media": "local", "file_name": n, "path": "/"},
    ),
    Variant(
        "bare_filename",
        "is storage_media itself the offending key?",
        lambda n: {"filename": n},
    ),
    Variant(
        "control_empty",
        "CONTROL: what a bare request answers (its file is never named in it)",
        lambda n: {},
    ),
    Variant(
        "sdcard_filename",
        "sd-card spelled the way the HTTP download paths spell it",
        lambda n: {"storage_media": "sdcard", "filename": n},
    ),
    Variant(
        "sdcard_file_name",
        "same, underscored key",
        lambda n: {"storage_media": "sdcard", "file_name": n},
    ),
    Variant(
        "udisk_filename",
        "u-disk spelled the way the HTTP download paths spell it",
        lambda n: {"storage_media": "udisk", "filename": n},
    ),
    Variant(
        "sd_card_filename",
        "sd-card spelled the way the protocol doc's storage table spells it",
        lambda n: {"storage_media": "sd-card", "filename": n},
    ),
    Variant(
        "u_disk_filename",
        "u-disk spelled the way the protocol doc's storage table spells it",
        lambda n: {"storage_media": "u-disk", "filename": n},
    ),
]


# =============================================================================
# Small helpers
# =============================================================================


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class PercentLogAdapter:
    """
    Feed the shipped client's `%s`-style logging calls into loguru.

    `ElegooCC2Client` logs with stdlib conventions (`logger.info("x=%s", x)`),
    which loguru does not interpolate — it would print the literal `%s` and hide
    the very values a volunteer needs to paste into #432. `debug.py` has this
    wart too; here it matters, because the client's own registration and timeout
    lines are the diagnosis when a run fails.
    """

    def __init__(self, level: str = "INFO") -> None:
        self._log = logger.bind(probe_client=True)
        self._level = level

    def _emit(self, method, message: str, *args) -> None:
        if args:
            try:
                message = message % args
            except (TypeError, ValueError):
                message = f"{message} {args!r}"
        method(message)

    def debug(self, message: str, *args: Any) -> None:
        self._emit(self._log.debug, message, *args)

    def info(self, message: str, *args: Any) -> None:
        self._emit(self._log.info, message, *args)

    def warning(self, message: str, *args: Any) -> None:
        self._emit(self._log.warning, message, *args)

    def error(self, message: str, *args: Any) -> None:
        self._emit(self._log.error, message, *args)

    def exception(self, message: str, *args: Any) -> None:
        self._emit(self._log.exception, message, *args)

    def critical(self, message: str, *args: Any) -> None:
        self._emit(self._log.critical, message, *args)

    def log(self, level: int, message: str, *args: Any) -> None:
        self._emit(self._log.info, message, *args)

    def isEnabledFor(self, level: int) -> bool:  # noqa: N802 - stdlib spelling
        return True


def json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=False)


def error_code_of(reply: Any) -> Any:
    """The error_code inside a raw 1047 envelope, or None if there is none."""
    if not isinstance(reply, dict):
        return None
    result = reply.get("result")
    if not isinstance(result, dict):
        return None
    return result.get("error_code")


def gcode_body(approx_bytes: int = 8192) -> bytes:
    """
    A small but non-trivial hand-written G-code body.

    Plausible enough not to be dismissed as junk — a slicer header, real
    commands, a travel pattern — while staying a few KiB. See CAVEATS: it is
    still not slicer output, and the report says so.
    """
    lines = [
        "; generated by scripts/cc2_probe_delete.py (issue #432 parameter probe)",
        "; hand-written probe file, safe to delete",
        "M106 P0 S0",
        "G28 ; home all axes",
        "G92 E0",
        "M104 S210",
        "M140 S60",
        "G1 Z0.2 F1200",
    ]
    x = 10.0
    y = 10.0
    direction = 1
    layer = 0
    while sum(len(line) + 1 for line in lines) < approx_bytes:
        x += direction * 5.0
        if x > 90.0 or x < 10.0:
            direction = -direction
            y += 5.0
            if y > 90.0:
                y = 10.0
                layer += 1
                lines.append(f"G1 Z{0.2 + 0.2 * layer:.2f} F1200")
        lines.append(f"G1 X{x:.3f} Y{y:.3f} E{len(lines) * 0.02:.4f} F3000")
    lines.append("M107")
    lines.append("M104 S0")
    lines.append("M140 S0")
    lines.append("G28 Z")
    lines.append("M84 ; steppers off")
    return ("\n".join(lines) + "\n").encode("utf-8")


def probe_name(slug: str) -> str:
    """A fresh, plausibly-named, collision-proof probe file name."""
    clean = re.sub(r"[^a-z0-9_]", "_", slug.lower())
    return f"cc2_probe_{clean}_{secrets.token_hex(4)}.gcode"


class LinkDroppedError(RuntimeError):
    """The MQTT link died mid-matrix, so the matrix must stop.

    Not a transport error, and deliberately NOT in `TRANSPORT_ERRORS`: a variant
    whose request was never sent is a different fact from a variant that was sent
    and went unanswered, and conflating them is the thing this script exists to
    avoid. It propagates to the abort path in `probe`, which reports the capture
    as partial and names the leftovers.
    """


class _EarlyExit(Exception):
    """Internal: an early exit out of `probe` that must still produce a capture.

    It exists so that the early exits (target unresolved, connect failed, printer
    not idle, dry-run upload failed) still fall through to the report block,
    instead of returning past it the way a `return` inside the `try` does. A
    volunteer whose run dies at connect has nothing to paste into #432 without
    that, and "it failed at connect" is exactly the fact a maintainer asks for.

    `code` is the process exit code and stays whatever it always was; `reason`
    goes into the report's `aborted` field, so a capture that never measured
    anything says WHY rather than looking like an empty result; `detail` is merged
    into the report for the few fields a normal report cannot express (see
    `build_report`).
    """

    def __init__(
        self, code: int, reason: str, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(f"early exit {code}: {reason}")
        self.code = code
        self.reason = reason
        self.detail: dict[str, Any] = detail or {}


# =============================================================================
# Step 1 + 2: resolve the target, without letting discovery retarget it
# =============================================================================


def resolve_target(args: argparse.Namespace) -> Target:
    """
    Work out the IP and the serial as plain strings, or explain why we cannot.

    A wrong serial is silent death: every topic is `elegoo/{sn}/...`, so
    registration and every command simply 404. So an unresolvable serial aborts
    here rather than becoming `None` further down — and it aborts by raising
    `_EarlyExit(2, ...)`, because a volunteer whose run never got as far as a
    target still needs a JSON capture naming the `--ip` that was tried and what
    discovery did about it. The detail says what was ATTEMPTED and never claims a
    printer was reached.
    """
    if args.serial:
        # Both halves given by hand: skip the broadcast. A discovery round-trip
        # costs 10 s per attempt for nothing.
        logger.info(
            "Using --serial {} for {}; skipping UDP discovery.", args.serial, args.ip
        )
        return Target(
            ip=args.ip,
            serial=args.serial,
            model=None,
            name=None,
            token_status="unknown (no discovery round-trip; --serial was given)",
            discovered=False,
        )

    def unresolved(reason: str, error: Any = None, seen: Any = None) -> _EarlyExit:
        # The payload a post-mortem needs when there is no Target to describe:
        # the address we were asked about, that discovery DID run (that is the
        # difference between "no printer on the LAN" and "wrong --serial"), and
        # whatever the discovery call said.
        return _EarlyExit(
            2,
            reason,
            {
                "printer_reached": False,
                "target_resolution": {
                    "attempted_ip": args.ip,
                    "serial_argument": None,
                    "discovery_ran": True,
                    "discovery_error": error,
                    "printers_answered": seen,
                    "reason": reason,
                },
            },
        )

    logger.info(
        "Resolving the serial by UDP discovery (up to ~{} s; pass --serial to skip).",
        CC2_DISCOVERY_TIMEOUT * (CC2_DISCOVERY_RETRIES + 1),
    )
    try:
        printers = CC2Discovery.discover_as_printers()
    except Exception as err:  # noqa: BLE001 - a broadcast failure is exit 2, not a traceback
        # A socket error (no route, no permission, an interface that went away)
        # is the same class of failure as "nobody answered": the target could not
        # be resolved. It used to escape `probe` as a bare traceback.
        logger.error(
            "UDP discovery raised {}: {!r}.\n"
            "  Aborting rather than guessing a serial: without it every MQTT "
            "topic (elegoo/<sn>/...) 404s. Run with --serial <serial number> to "
            "skip discovery entirely.",
            type(err).__name__,
            err,
        )
        raise unresolved(
            f"UDP discovery raised {type(err).__name__} ({err!r})", error=repr(err)
        ) from None
    if not printers:
        logger.error(
            "No CC2 printer answered UDP discovery on port 52700.\n"
            "  Aborting rather than guessing: without the serial every MQTT "
            "topic (elegoo/<sn>/...) 404s and the probe would look silent "
            "rather than wrong.\n"
            "  Fix: run with --serial <serial number> (printer's Settings -> "
            "Network / About). Check the printer is on the same subnet/VLAN as "
            "this machine and that nothing blocks UDP 52700.",
        )
        raise unresolved("no CC2 printer answered UDP discovery on port 52700")

    matching = [p for p in printers if p.ip_address == args.ip]
    if not matching:
        seen = ", ".join(
            f"{p.name or '?'} @ {p.ip_address} (sn={p.id})" for p in printers
        )
        logger.error(
            "--ip says {} but discovery answered from something else: {}.\n"
            "  Aborting rather than picking one. connect_printer() overwrites "
            "the client's IP and serial from the Printer it is handed, so "
            "connecting to the discovered machine would make --ip a dead "
            "argument and this script would delete files on the wrong "
            "printer.\n"
            "  If the discovered machine is the one you meant, re-run with "
            "--ip <that address>. If {} is the one you meant, re-run with "
            "--serial <its serial> to skip discovery.",
            args.ip,
            seen,
            args.ip,
        )
        raise unresolved(
            f"--ip {args.ip} was not among the printers discovery answered ({seen})",
            seen=[
                {"name": p.name, "ip": p.ip_address, "serial": p.id} for p in printers
            ],
        )

    printer = matching[0]
    if not printer.id:
        logger.error(
            "Discovery answered from {} but named no serial number. "
            "Run with --serial <serial number>.",
            printer.ip_address,
        )
        raise unresolved(
            f"discovery answered from {printer.ip_address} but named no serial number",
            seen=[
                {"name": printer.name, "ip": printer.ip_address, "serial": printer.id}
            ],
        )
    if len(printers) > 1:
        logger.warning(
            "Discovery saw {} CC2 printers; using the one matching --ip ({}) "
            "and ignoring the rest.",
            len(printers),
            printer.ip_address,
        )
    return Target(
        ip=printer.ip_address,
        serial=printer.id,
        model=printer.model,
        name=printer.name,
        token_status=printer.cc2_token_status,
        discovered=True,
    )


async def disconnect_bounded(client: ElegooCC2Client) -> None:
    """
    Disconnect, but never longer than `DISCONNECT_TIMEOUT`.

    `ElegooCC2Client.disconnect` awaits the heartbeat and listener tasks and then
    the MQTT context manager's `__aexit__`, and a half-dead wifi link can leave
    any of those hanging. The abort-cleanup arm of `probe` has always been
    bounded for exactly that reason; without the same bound here a stalled
    disconnect swallows the whole deliverable — the report is built and the JSON
    written AFTER the `finally`, so the volunteer with a printer that stopped
    answering would wait forever for output that already exists. Abandoning the
    disconnect costs a socket on a machine we can no longer talk to; losing the
    capture costs the run.

    Catches `BaseException` for the same reason the abort arm does: on the
    interrupt path every await here can be cancelled again, and a cancelled
    teardown must not replace the report.
    """
    try:
        await asyncio.wait_for(client.disconnect(), timeout=DISCONNECT_TIMEOUT)
    except BaseException as err:  # noqa: BLE001 - the capture outranks the teardown
        logger.warning(
            "Disconnecting {} was abandoned after {}s ({!r}) — moving on to the "
            "report, because the capture matters more than closing the socket.",
            client.printer_ip,
            DISCONNECT_TIMEOUT,
            err,
        )


async def connect(target: Target, access_code: str | None) -> ElegooCC2Client:
    """
    Connect and register with the SHIPPED client, at exactly the resolved IP.

    A bare `Printer()` is passed on purpose: its `connection_host` and `id` are
    None, so `connect_printer` keeps the constructor's IP and serial instead of
    overwriting them with a discovery answer.

    On failure it raises `_EarlyExit(3, ...)` rather than returning None, so the
    run still produces a capture: a failed connect is the single most likely way
    a volunteer's run dies, and #432 cannot diagnose what it cannot see. The
    detail says no printer was reached, so the capture can never read as a
    measurement.
    """
    client = ElegooCC2Client(
        printer_ip=target.ip,
        serial_number=target.serial,
        access_code=access_code,
        logger=PercentLogAdapter(),
    )
    logger.info("Connecting to {} as serial {} ...", target.ip, target.serial)

    def failed(reason: str, error: Any = None) -> _EarlyExit:
        return _EarlyExit(
            3,
            reason,
            {
                "printer_reached": False,
                "connect_failure": {
                    "attempted_ip": target.ip,
                    "attempted_serial": target.serial,
                    "discovered_via_udp": target.discovered,
                    "error": error,
                    "reason": reason,
                },
            },
        )

    try:
        connected = await client.connect_printer(Printer())
    except Exception as err:  # noqa: BLE001 - a raising connect is still exit 3
        # The client swallows its own transport errors, but a newer version may
        # raise: that is a connect failure (exit 3), not a traceback that costs
        # the volunteer the capture.
        logger.error(
            "Connecting to {} raised {}: {!r}.", target.ip, type(err).__name__, err
        )
        await disconnect_bounded(client)
        raise failed(
            f"connecting to {target.ip} raised {type(err).__name__} ({err!r})",
            error=repr(err),
        ) from None
    if not connected:
        logger.error(
            "Could not connect to or register with {}.\n"
            "  Most likely causes:\n"
            "    * Home Assistant is running and holds the registration slot "
            "(the printer answers 'too many clients'). Stop it, or remove the "
            "Elegoo integration, and retry.\n"
            "    * The printer needs its access code: retry with "
            "--access-code <code> (it is both the MQTT password and the HTTP "
            "X-Token).\n"
            "    * The serial is wrong, so every topic 404s: retry with "
            "--serial <serial number>.",
            target.ip,
        )
        # The client may hold a half-open socket from the failed attempt; bound
        # it, because a printer that would not answer the connect may equally
        # refuse to answer the close.
        await disconnect_bounded(client)
        raise failed(f"could not connect to or register with {target.ip}")
    if client.printer_ip != target.ip or client.serial_number != target.serial:
        # Belt and braces on the claim above: if connect_printer ever starts
        # retargeting, this script must refuse rather than delete elsewhere.
        logger.error(
            "After connecting, the client is talking to {} as {} although this "
            "run resolved {} as {}. Refusing to continue.",
            client.printer_ip,
            client.serial_number,
            target.ip,
            target.serial,
        )
        await disconnect_bounded(client)
        raise failed(
            "refusing to continue: after connecting, the client was talking to "
            f"{client.printer_ip} as {client.serial_number} although this run "
            f"resolved {target.ip} as {target.serial}",
            error="client retargeted away from --ip",
        )
    return client


# =============================================================================
# Step 3: the idle gate (filename first, mapped status only as the backstop)
# =============================================================================


async def guard_printer_is_idle(client: ElegooCC2Client) -> str | None:
    """Return None when it is safe to delete, or the reason it is not."""
    try:
        await client.refresh_status()
    except STATUS_ERRORS as err:
        # A refused or unreadable 1002 is NOT "idle". Treating it as idle is how
        # a printer that never answered ends up having its files deleted.
        return f"status check (1002) failed: {err!r}"

    status = client.printer_data.status
    filename = getattr(status.print_info, "filename", None)
    if isinstance(filename, str) and filename.strip():
        # PRIMARY guard. The mapper copies the printer's filename through
        # whatever it thinks the machine state is, so this is the one field that
        # cannot silently read as idle.
        return (
            "PRIMARY GUARD (filename): the printer names a file it is working "
            f"on: {filename!r}. Refusing to delete anything."
        )

    machine = status.current_status
    if machine not in IDLE_MACHINE_STATUSES:
        # Backstop for a printer that says it is busy while naming nothing.
        return (
            f"BACKSTOP GUARD (machine status): printer reports {machine} while "
            "naming no file."
        )
    logger.info(
        "Printer is idle and names no file (status {}); safe to probe.", machine
    )
    return None


async def read_firmware(client: ElegooCC2Client) -> tuple[Any, Any]:
    """
    Ask 1001 for the attributes and read the firmware string back.

    1002 does not carry firmware: `printer_data.attributes.firmware_version` is
    filled from the 1001 attributes reply (`_handle_attributes`, parsing
    `software_version.ota_version`). A capture without the firmware it was taken
    on is not usable, so both the parsed value and the raw reply are recorded.
    """
    try:
        reply = await client._send_command(CC2_CMD_GET_ATTRIBUTES, {})
    except TRANSPORT_ERRORS as err:
        return None, {"error": repr(err)}
    return client.printer_data.attributes.firmware_version or None, reply


def attributes_result(raw_attributes: Any) -> dict[str, Any]:
    """The body of a 1001 reply (the envelope's ``result``), or {}."""
    if isinstance(raw_attributes, dict) and isinstance(
        raw_attributes.get("result"), dict
    ):
        return raw_attributes["result"]
    return raw_attributes if isinstance(raw_attributes, dict) else {}


# =============================================================================
# Step 4: create the disposable targets
# =============================================================================


async def upload_probe_file(
    session: aiohttp.ClientSession, host: str, token: str, name: str
) -> dict[str, Any]:
    """
    Upload one disposable file and return its record.

    Raises whatever `upload_gcode` raises on any refusal — the caller records the
    variant as failed and never falls back to a file it did not create.
    """
    body = gcode_body()
    size = len(body)
    md5 = hashlib.md5(body).hexdigest()

    async def chunks():
        for offset in range(0, size, cc2_upload.CHUNK_SIZE):
            yield body[offset : offset + cc2_upload.CHUNK_SIZE]

    sent = await upload_gcode(
        session,
        UploadTarget(host=host, token=token),
        UploadFile(name=name, size=size, md5=md5),
        chunks(),
    )
    return {"name": name, "size": size, "md5": md5, "uploaded_bytes": sent}


async def wait_until_listed(
    client: ElegooCC2Client, name: str, timeout: float = SETTLE_TIMEOUT
) -> dict[str, Any]:
    """
    Re-list (1044) until ``name`` appears, bounded by ``timeout``.

    Nothing in the protocol promises that a just-uploaded file shows up in the
    next listing, and a transient absence reads exactly like a successful
    delete. So the probe waits, and if the file never appears the variant is
    inconclusive — never a success.
    """
    deadline = time.monotonic() + timeout
    attempts = 0
    last_error: Any = None
    while True:
        attempts += 1
        try:
            files = await client.get_verified_file_list()
            last_error = None
            if name in files:
                return {
                    "listed": True,
                    "attempts": attempts,
                    "listing_count": len(files),
                    "timeout": timeout,
                    "error": None,
                }
        except LISTING_ERRORS as err:
            # A transient listing failure is retried, not treated as "not listed".
            last_error = repr(err)
        if time.monotonic() >= deadline:
            return {
                "listed": False,
                "attempts": attempts,
                "listing_count": None,
                "timeout": timeout,
                "error": last_error,
            }
        await asyncio.sleep(SETTLE_POLL)


async def read_still_listed(client: ElegooCC2Client, name: str) -> dict[str, Any]:
    """Ask the printer whether ``name`` is still there after a delete."""
    try:
        files = await client.get_verified_file_list()
    except LISTING_ERRORS as err:
        # None, not False: "could not read the listing" must never be read as
        # "the file is gone".
        return {"still_listed": None, "listing_count": None, "error": repr(err)}
    return {
        "still_listed": name in files,
        "listing_count": len(files),
        "error": None,
    }


# =============================================================================
# Step 5 + 8: send one 1047, and clean up only our own files
# =============================================================================


# Names this run uploaded, so every delete can be policed. Populated by
# upload_probe_file's callers and read by _delete_own_file below.
OWNED: set[str] = set()


async def _delete_own_file(
    client: ElegooCC2Client, name: str, params: dict[str, Any]
) -> Any:
    """
    Delete ``name`` with ``params`` — but only if this run uploaded it.

    Every delete in this script goes through here. It is the single place where
    the "never deletes a file it did not create" promise lives.
    """
    if name not in OWNED:
        raise RuntimeError(
            f"refusing to delete {name!r}: this run did not upload it "
            "(the probe only ever deletes its own files)"
        )
    return await client._send_command(CC2_CMD_DELETE_FILE, params)


def verdict_for(record: dict[str, Any]) -> str:
    """One line saying what this variant proved, in the strongest terms it can."""
    if record.get("status") == "no_reply":
        # Silence is not an answer. Say so before the note, so the verdict column
        # alone can never be misread as "this variant agreed with the others".
        return (
            "NO REPLY: the printer never answered this 1047 — this variant "
            "proves NOTHING, in either direction"
        )
    if record.get("status") != "sent":
        return record.get("note", record.get("status", "not sent"))
    code = record.get("error_code")
    still = record.get("still_listed")
    if code == 0 and still is True:
        return "accepted (error_code 0) but its own file is STILL LISTED — accept-and-ignore"
    if code == 0 and still is False:
        return "SUCCESS: accepted (error_code 0) and its own file is gone from 1044"
    if code == 0 and still is None:
        return "accepted (error_code 0) but the confirming 1044 could not be read — inconclusive"
    if still is None:
        return f"refused (error_code {code!r}) and the confirming 1044 could not be read — inconclusive"
    return f"refused (error_code {code!r}), file {'still listed' if still else 'gone'}"


async def probe_variant(
    client: ElegooCC2Client,
    session: aiohttp.ClientSession,
    target: Target,
    token: str,
    variant: Variant,
    index: int,
    total: int,
    sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Upload this variant's own file, settle, send its 1047, re-list, record."""
    name = probe_name(variant.slug)
    record: dict[str, Any] = {
        "index": index,
        "slug": variant.slug,
        "label": variant.label,
        "file": name,
        "params": variant.build(name),
    }
    # Published before anything can fail, and mutated in place from here on: a
    # run that dies mid-variant (an unexpected exception, a Ctrl-C) still has
    # THIS row in its report, so the volunteer sees which variant was in flight
    # instead of finding a hole in the matrix.
    if sink is not None:
        sink.append(record)
    logger.info("[{}/{}] {} -> {}", index, total, variant.slug, json_block(record["params"]))

    if not client.is_connected:
        # The link died during an earlier variant. Sending anything now would be
        # pointless (the client raises before publishing) and uploading another
        # probe file would be worse: it would land on the printer over HTTP,
        # unconfirmable, adding to the pile the volunteer has to clear by hand.
        # So the matrix STOPS here and says why, rather than running out the
        # remaining variants as eleven identical no-replies.
        record["status"] = "link_dropped"
        record["still_listed"] = None
        record["error_code"] = None
        # Nothing was uploaded and nothing was sent, so neither the file name nor
        # the params may be printed as if they had been: a volunteer hunting the
        # printer's UI for a file that was never created is a worse report than a
        # row that says "never sent".
        record["file"] = None
        record["params"] = None
        record["note"] = (
            "never sent: the connection to the printer had already dropped when "
            "this variant was reached, so neither its upload nor its delete was "
            "attempted"
        )
        raise LinkDroppedError(
            f"the connection to the printer dropped before variant {variant.slug} "
            f"({index}/{total}) could be sent"
        )

    # 1. Create the disposable target. Never reuse an existing file.
    try:
        record["upload"] = await upload_probe_file(session, target.ip, token, name)
        OWNED.add(name)
        # Its own greppable line, because this is the one fact the report cannot
        # afford to lose: if the process is killed hardest (a second Ctrl-C, an
        # OOM, a closed terminal) the scrollback is all that is left to say which
        # files the volunteer has to delete by hand. `grep 'PROBE UPLOADED'`.
        logger.info("PROBE UPLOADED {}", name)
    except UPLOAD_ERRORS as err:
        record["status"] = "upload_failed"
        record["note"] = (
            f"could not upload its own target file, variant aborted (no existing "
            f"file was substituted): {err!r}"
        )
        record["still_listed"] = None
        return record

    # 2. Wait for the printer to actually list it, or give up as inconclusive.
    settle = await wait_until_listed(client, name)
    record["settle"] = settle
    if not settle["listed"]:
        record["status"] = "inconclusive_never_listed"
        record["still_listed"] = None
        record["note"] = (
            f"INCONCLUSIVE: the file uploaded but never appeared in the 1044 "
            f"listing within {settle['timeout']:.0f}s "
            f"({settle['attempts']} attempt(s)"
            + (f", last listing error {settle['error']}" if settle["error"] else "")
            + "). No 1047 was sent for it, because an unverifiable target cannot "
            "distinguish a working delete from a missing file. Delete it from the "
            "printer's UI if it is visible there."
        )
        logger.warning("  variant {} inconclusive: file never listed", variant.slug)
        return record

    # 3. The probe itself: the raw envelope comes back untouched (there is no
    #    1047 branch in _handle_response), which is exactly what #432 needs.
    request = {"method": CC2_CMD_DELETE_FILE, "params": record["params"]}
    started = time.perf_counter()
    try:
        reply = await _delete_own_file(client, name, record["params"])
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        record["request"] = request
        record["reply"] = reply
        record["error_code"] = error_code_of(reply)
    except TRANSPORT_ERRORS as err:
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        record["request"] = request
        record["status"] = "no_reply"
        record["error_code"] = None
        record["reply"] = None
        record["still_listed"] = None
        record["note"] = (
            f"the printer sent no usable 1047 reply at all ({err!r}); still "
            "checked whether the file is listed"
        )

    # 4. The only trustworthy signal: is our own file still on the printer?
    still = await read_still_listed(client, name)
    record.update(still)
    if record.get("status") != "no_reply":
        # The delete round-trip answered, so this variant is evidence. A variant
        # that got NO reply must keep `no_reply`: overwriting it with "sent"
        # turns silence into agreement, because build_report reads its codes only
        # from "sent" records and an all-None set of codes reads as "every
        # variant answered identically". On a printer on wifi this is the LIKELY
        # failure, not the exotic one.
        record["status"] = "sent"
    if not any(key in record["params"] for key in ("filename", "file_name")):
        # The control (and any variant that names no file at all) cannot possibly
        # have removed its own file, so `still_listed: true` here is arithmetic,
        # not evidence. Say so, so nobody reads it as a refusal of the file.
        # Prepending keeps an existing note (the no-reply one) intact.
        record["note"] = (
            "this request names NO file, so still_listed=true is expected "
            "whatever the reply says; read only the error_code. "
            + str(record.get("note") or "")
        ).strip()
    logger.info(
        "  error_code={} still_listed={} in {} ms{}",
        record.get("error_code"),
        still["still_listed"],
        record.get("elapsed_ms"),
        "" if record.get("status") == "sent" else " (NO REPLY — not evidence)",
    )
    return record


async def cleanup(
    client: ElegooCC2Client,
    winning: dict | None,
    *,
    dry_run_shape: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Remove this run's files. Where that is impossible, name them for a human.

    Only names in OWNED can ever reach `_delete_own_file`, so a file the printer
    lists that this run did not upload is reported, never deleted.

    `winning` is the variant record that provably worked, when there is one.
    `dry_run_shape` is used instead on a dry run: the dry run still has to clean
    up the one file it uploaded, and the only shape it may guess at is the one
    this integration ships today. A dry run never sends the probe matrix.
    """
    shape = winning["params"] if winning else dry_run_shape
    try:
        files = await client.get_verified_file_list()
    except LISTING_ERRORS as err:
        return {"error": f"could not re-list for cleanup: {err!r}", "leftovers": sorted(OWNED)}

    leftovers = sorted(name for name in OWNED if name in files)
    if not leftovers:
        logger.info("Cleanup: nothing of ours left on the printer.")
        return {"leftovers": [], "attempts": [], "shape_used": None}

    if shape is None:
        logger.warning(
            "Cleanup: no variant found a working delete, so these {} probe "
            "files are still on the printer and have to be removed from its UI: "
            "{}",
            len(leftovers),
            ", ".join(leftovers),
        )
        return {"leftovers": leftovers, "attempts": [], "shape_used": None}

    logger.info(
        "Cleanup: removing {} of our own upload(s) with {}",
        len(leftovers),
        json_block(shape),
    )
    attempts = []
    for name in leftovers:
        params = dict(shape)
        # Re-point the shape at this leftover, keeping whichever key the shape
        # used to name the file.
        named = False
        for key in ("filename", "file_name"):
            if key in params:
                params[key] = name
                named = True
        if not named:
            # The shape names no file (e.g. a bare {} answering 0), so it cannot
            # be aimed at a specific leftover. Report rather than guess.
            attempts.append(
                {
                    "file": name,
                    "error": "the available shape names no file, so it cannot "
                    "be used to clean this one up",
                }
            )
            continue
        try:
            reply = await _delete_own_file(client, name, params)
            attempts.append({"file": name, "params": params, "reply": reply})
        except TRANSPORT_ERRORS as err:
            attempts.append({"file": name, "params": params, "error": repr(err)})
    try:
        files = await client.get_verified_file_list()
        remaining = sorted(name for name in OWNED if name in files)
    except LISTING_ERRORS:
        remaining = leftovers
    if remaining:
        logger.warning(
            "Cleanup left {} of our probe files on the printer; remove them from "
            "its UI: {}",
            len(remaining),
            ", ".join(remaining),
        )
    else:
        logger.info("Cleanup: all of our own uploads are gone.")
    return {"leftovers": remaining, "attempts": attempts, "shape_used": shape}


def stamp_unfinished_records(records: list[dict[str, Any]]) -> None:
    """
    Give a status to any row the abort caught mid-flight.

    `probe_variant` publishes its row before its first await so the row survives
    a crash, but a row interrupted between the upload and the delete has had no
    status assigned yet, and a report full of `status: null` is unreadable. This
    says what is actually known: the variant was reached, and the run stopped
    before it produced an answer. Mutated in place, because the report reads
    these same dicts.
    """
    for record in records:
        if record.get("status"):
            continue
        record["status"] = "aborted_mid_variant"
        record.setdefault("still_listed", None)
        record.setdefault("error_code", None)
        uploaded = bool(record.get("upload"))
        record["note"] = (
            "never answered: the run was interrupted while this variant was in "
            "flight. "
            + (
                "Its file WAS uploaded, so it is named in the leftovers unless "
                "the cleanup below confirmed otherwise."
                if uploaded
                else "Its file was never confirmed uploaded."
            )
        )


def unconfirmed_leftovers(records: list[dict[str, Any]]) -> list[str]:
    """
    Every name this run uploaded that it never PROVED it removed.

    Used when the run blew up and no listing could be trusted. Deliberately
    pessimistic: a file whose delete was never confirmed is named, because the
    one thing this script promises is that a volunteer is never left guessing
    which `cc2_probe_*.gcode` files are sitting on their printer.
    """
    confirmed_gone = {
        str(record["file"])
        for record in records
        if record.get("file") and record.get("still_listed") is False
    }
    return sorted(OWNED - confirmed_gone)


async def cleanup_after_abort(
    client: ElegooCC2Client,
    records: list[dict[str, Any]],
    winning: dict | None,
    *,
    dry_run_shape: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Name this run's files after the matrix blew up, without ever guessing.

    Two cases, and the difference matters. If the link is still up, the ordinary
    `cleanup` can run: it re-lists, and where a working shape exists it deletes
    (only ever OWNED names, through `_delete_own_file`). If the link is gone,
    NOTHING may be attempted — a delete over a dead connection cannot be
    confirmed, and a name we cannot confirm is exactly the report's job: list
    every file this run uploaded that this run did not prove it deleted, and tell
    the volunteer to remove them from the printer's UI.
    """
    note: str | None = None
    if client.is_connected:
        try:
            # Same shapes, same rules as the normal path: `cleanup` only ever
            # re-points a filename key at names in OWNED, and on a dry run the
            # only shape it may use is still the one delete_file ships.
            return await cleanup(client, winning, dry_run_shape=dry_run_shape)
        except Exception as err:  # noqa: BLE001 - the report outranks the cause
            # Whatever it is (a model shape change, a broker half-dead), it must
            # not cost the volunteer the leftovers list.
            note = (
                f"cleanup could not complete on the abort path ({err!r}), so "
                "every file this run uploaded that it did not confirm deleting "
                "is named below"
            )
    else:
        note = (
            "the connection was already gone, so nothing was deleted or confirmed "
            "from here; every probe file this run uploaded is named below and has "
            "to be removed from the printer's UI"
        )
    return {
        "leftovers": unconfirmed_leftovers(records),
        "attempts": [],
        "shape_used": None,
        "note": note,
    }


# =============================================================================
# Step 7: the report
# =============================================================================


def build_report(
    args: argparse.Namespace,
    target: Target | None,
    firmware: Any,
    raw_attributes: Any,
    records: list[dict[str, Any]],
    cleanup_info: dict[str, Any],
    winning: dict | None,
    aborted: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    The whole capture, from whatever the run managed to measure.

    `target` is None on the exits that happened before a target existed (an
    unresolved `--ip`): the report then carries the address that was asked about
    and nothing that could be mistaken for a printer's identity. `detail` is what
    an `_EarlyExit` wanted said about the failure; it is merged last because a
    raise site knows things the shape of the run cannot show — most importantly
    that no printer was ever reached.
    """
    # Only variants that ACTUALLY ANSWERED contribute a code or a still_listed.
    # Counting `no_reply` rows here would feed the identical-analysis with
    # `error_code: None` placeholders, and eleven placeholders read as perfect
    # agreement — the exact way a probe on wifi turns silence into a finding.
    answered = [r for r in records if r.get("status") == "sent"]
    silent = [r for r in records if r.get("status") == "no_reply"]
    codes = [r.get("error_code") for r in answered]
    stills = [r.get("still_listed") for r in answered]
    answered_identically = (
        bool(codes) and len(set(codes)) == 1 and len(set(stills)) <= 1
    )
    # Answered variants whose confirming 1044 could not be read. `stills` reads
    # `None` for them, and `len(set(stills)) <= 1` is satisfied by a set of
    # nothing but `None`s — so "the variants answered identically" can be TRUE
    # while not one of the deletions was ever confirmed either way. The
    # conclusion counts these out loud instead of letting them agree by
    # omission.
    unconfirmed = [r for r in answered if r.get("still_listed") is None]
    report = {
        "tool": "scripts/cc2_probe_delete.py",
        "issue": ISSUE_URL,
        "timestamp": now_iso(),
        "mode": "run" if args.run else "dry_run",
        "aborted": aborted,
        # Whether a printer was ever reached. True is only correct because every
        # call site below the successful `connect` in `probe` has a live client;
        # the two exits that happen BEFORE a connection exists (unresolved target,
        # failed connect) override this to False through `detail`, so a capture of
        # a run that never talked to anything can never read as if it had.
        "printer_reached": True,
        # What `resolve_target` attempted, when it never got a Target. None on
        # every normal capture (see `resolve_target`).
        "target_resolution": None,
        "printer_ip": target.ip if target else None,
        "serial": target.serial if target else None,
        "discovered_via_udp": target.discovered if target else None,
        "printer_name": target.name if target else None,
        "model_from_discovery": target.model if target else None,
        "model_from_attributes": attributes_result(raw_attributes).get("machine_model"),
        "firmware": firmware,
        "raw_1001_attributes_reply": raw_attributes,
        "token_status": target.token_status if target else None,
        # The arguments exactly as they were given: the only thing a capture that
        # never connected can honestly report about the run.
        "arguments": {"ip": args.ip, "serial": args.serial, "run": bool(args.run)},
        "access_code_supplied": args.access_code is not None,
        "upload_port": args.upload_port,
        "delete_method": CC2_CMD_DELETE_FILE,
        "variants": records,
        "variants_attempted": len(records),
        "variants_answered": len(answered),
        "variants_without_reply": len(silent),
        # Of the ANSWERED rows, how many never got a confirming 1044 read. The
        # conclusion needs it: identical replies are evidence about the reply,
        # never about whether the file disappeared, and that second half is only
        # evidence when the listing was actually read.
        "variants_answered_without_listing_confirmation": len(unconfirmed),
        "answered_variants_answered_identically": answered_identically,
        "winning_shape": winning["params"] if winning else None,
        "winning_shape_note": (
            "the params exactly as the first working variant sent them, so the "
            "file named in them is that variant's own upload; a fix to "
            "delete_file changes the KEY and the extra fields, not the value"
            if winning
            else None
        ),
        # Strong claim, so it is only ever true when the matrix RAN IN FULL and
        # every variant that was attempted answered, and they all said the same.
        # Silence, an aborted run, and a matrix cut short mid-way are not
        # agreement — `len(answered) == len(VARIANTS)` is what stops a capture
        # that only got through three rows from claiming anything about "every"
        # variant.
        "every_variant_answered_identically": answered_identically
        and not silent
        and not aborted
        and len(answered) == len(records) == len(VARIANTS),
        "caveats": CAVEATS,
        "cleanup": cleanup_info,
    }
    # Last, so a raise site can correct the shape it forced: an early exit knows
    # it never reached a printer, and `probe` has no other way to say so.
    report.update(detail or {})
    return report


def deletion_claim(sent: list[dict[str, Any]]) -> str:
    """
    Say about deletion exactly what the confirming 1044 listings support.

    The identical-answer analysis compares `still_listed` values, and
    `len(set(stills)) <= 1` is satisfied by a set holding nothing but `None` —
    which is precisely what a run produces when every confirming 1044 failed to
    read. Identical replies are therefore evidence about the REPLY and never
    about the file, so they can never license "none of them removed its own
    file" on their own. And the mirror case is worse than unproven: if every
    listing read `False` while the replies were refusals, that sentence would be
    contradicted by this very capture. Both are reported as what they are — not
    confirmed, plus how many listings could not be read.
    """
    listed = [r for r in sent if r.get("still_listed") is True]
    gone = [r for r in sent if r.get("still_listed") is False]
    unread = [r for r in sent if r.get("still_listed") is None]
    if not unread and not gone:
        # Every deletion judgement in this capture came from a listing that was
        # actually read, so the strong wording is earned.
        return (
            f"none of them removed its own file (all {len(listed)} confirming "
            "1044 read(s) saw the variant's own file still listed)"
        )
    clauses = []
    if unread:
        clauses.append(
            f"the confirming 1044 could not be read for {len(unread)} of them, so "
            "whether those files were removed is UNKNOWN"
        )
    if gone:
        clauses.append(
            f"{len(gone)} of them were listed GONE afterwards despite the reply "
            "they got, which this capture does not explain"
        )
    return "none of them was CONFIRMED to have removed its own file" + (
        " (" + "; ".join(clauses) + ")" if clauses else ""
    )


def conclusion_lines(report: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if not report.get("printer_reached", True):
        # The run never talked to a printer, so NOTHING below this point may read
        # as a measurement, and the dry-run wording would be the worst possible
        # summary ("re-run with --run" when --run could not have helped). Say what
        # was attempted and what failed, and stop.
        resolution = report.get("target_resolution") or {}
        stopped = report.get("aborted") or "The run stopped before it connected."
        lines.append(
            "NO PRINTER WAS REACHED — this capture exists only so that a failed "
            f"run can be reported. {stopped}"
        )
        if resolution:
            ran = (
                "UDP discovery ran"
                if resolution.get("discovery_ran")
                else "discovery skipped (--serial was given)"
            )
            error = (
                f"; discovery error {resolution['discovery_error']}"
                if resolution.get("discovery_error")
                else ""
            )
            lines.append(
                f"  Target resolution: --ip {resolution.get('attempted_ip')} "
                f"({ran}{error})."
            )
        lines.append(
            "  Nothing was uploaded, nothing was sent, and no file on any printer "
            "was touched, so there is nothing to clean up. The fields above name "
            "the arguments that were tried; the most common causes are a wrong "
            "--ip, a printer on another subnet/VLAN, UDP 52700 blocked "
            "(discovery), or Home Assistant still holding the printer's single "
            "registration slot (connect). Please attach this capture to " + ISSUE_URL
        )
        return lines
    if report["mode"] != "run":
        lines.append(
            "DRY RUN — no 1047 matrix was sent. Re-run with --run to probe."
        )
        return lines
    if report.get("aborted"):
        if report.get("variants_attempted"):
            lines.append(
                f"THIS RUN WAS ABORTED ({report['aborted']}) — the matrix below "
                "is PARTIAL and is reported only so the probe files this run "
                "uploaded can be named for clean-up. Read it as a partial "
                "capture, not as a result."
            )
        else:
            # Nothing was ever measured and (on the not-idle exit) nothing was
            # ever uploaded, so promising a leftovers list here would be its own
            # small untruth.
            lines.append(
                f"THIS RUN WAS ABORTED ({report['aborted']}) — no 1047 was sent "
                "and no probe file was uploaded, so there is nothing to "
                "conclude and nothing to clean up."
            )
    sent = [r for r in report["variants"] if r.get("status") == "sent"]
    silent = [r for r in report["variants"] if r.get("status") == "no_reply"]
    # Rows the reader is NOT getting an answer for: sent-and-unanswered, plus
    # every variant of the matrix this run never reached (upload failed, never
    # listed, or never sent because the link dropped mid-matrix).
    missing = len(VARIANTS) - len(sent)
    if not sent:
        if silent:
            # The all-silent case, spelled out because the alternative is the
            # single most misleading thing this report could say: eleven
            # `error_code: null` rows must never be summarised as "the printer
            # answered 1003 to all of them". It answered NOTHING.
            lines.append(
                f"INCONCLUSIVE — NO REPLY WAS RECEIVED FOR ANY OF THE {len(silent)} "
                "DELETE(S) THIS RUN SENT. The printer never answered method 1047 "
                "at all, so this capture supports NEITHER hypothesis: not the "
                "key-name hypothesis, and not the reading that 1003 is this "
                "firmware's generic 'not available' answer. Silence is not "
                "agreement, and nothing here distinguishes '1047 is not served' "
                "from 'the wifi dropped the request'."
            )
            lines.append(
                "  Before reading anything into it: check the printer is on the "
                "same network and reachable, check Home Assistant (or anything "
                "else holding the registration slot) was OFF, and RE-RUN. Every "
                "request below was sent and timed out, so the files named in it "
                "may or may not have been deleted — check the printer's UI."
            )
            return lines
        lines.append(
            "No variant produced an answer for this report to analyse"
            + (
                f" — the run was {report['aborted']} before any 1047 was answered."
                if report.get("aborted")
                else " (every upload or listing step failed)."
            )
            + (
                " Nothing can be concluded from it; the capture exists only to "
                "name the probe files this run uploaded."
                if report.get("variants_attempted")
                else " Nothing can be concluded from it."
            )
        )
        return lines
    winners = [
        r
        for r in sent
        if r.get("error_code") == 0 and r.get("still_listed") is False
    ]
    if missing:
        # Say it before the analysis, so the reader of a partial capture knows
        # how much of the matrix is actually missing, and why.
        parts = []
        if silent:
            parts.append(f"{len(silent)} received NO REPLY AT ALL")
        unmeasured = missing - len(silent)
        if unmeasured > 0:
            parts.append(
                f"{unmeasured} of the {len(VARIANTS)} planned variants produced no "
                "answer at all (upload failed, never listed, or never sent)"
            )
        lines.append(
            f"PARTIAL CAPTURE: only {len(sent)} of the {len(VARIANTS)} planned "
            f"variants answered; " + "; ".join(parts) + ". Those rows contribute "
            "nothing below — do not read missing rows as agreeing with the ones "
            "that answered."
        )
    if winners:
        lines.append(
            "A WORKING SHAPE EXISTS: "
            + ", ".join(json_block(r["params"]) for r in winners)
            + " — the printer answered 0 AND its own file was gone from the next "
            "1044. That is actionable as a one-line change to delete_file."
        )
    elif report["every_variant_answered_identically"]:
        lines.append(
            "EVERY VARIANT ANSWERED IDENTICALLY and "
            + deletion_claim(sent)
            + ". That is a real finding, and it supports the ALTERNATIVE "
            "reading recorded above: on this firmware 1003 is not about the "
            "parameter key at all — 1047 is simply not served, or 1003 is its "
            "generic 'not available / not handled' answer. The key-name "
            "hypothesis is not supported by this capture."
        )
    elif report["answered_variants_answered_identically"]:
        # The answered variants did all say the same thing, but the claim is
        # qualified: `missing` rows are absent, so "EVERY variant" is false, and
        # a reader must not mistake the qualified line for the strong one above.
        lines.append(
            f"THE {len(sent)} VARIANT(S) THAT ANSWERED answered IDENTICALLY, and "
            f"{deletion_claim(sent)}; {missing} of the {len(VARIANTS)} "
            "planned variants produced no answer to compare. That is a real "
            "finding for the answered subset, and it leans towards the "
            "ALTERNATIVE reading recorded above (1047 is not served, or 1003 is "
            "its generic 'not available / not handled' answer) — but with "
            f"{missing} rows missing it is NOT the full-matrix result the "
            "paragraph above would claim, and it settles neither that reading "
            "nor the key-name hypothesis. Re-run to fill in the missing "
            "variants."
        )
    else:
        lines.append(
            "The variants answered DIFFERENTLY, but none was CONFIRMED both to "
            "answer 0 and to remove its own file"
            + (
                f" ({report['variants_answered_without_listing_confirmation']} of "
                "the answered variants never got a readable confirming 1044, so "
                "their fate is unknown)"
                if report.get("variants_answered_without_listing_confirmation")
                else ""
            )
            + ". Whatever the differing codes mean, no parameter shape in this "
            "matrix was PROVEN to delete a file on this firmware. A variant that "
            "answered 0 while its file stayed listed would be the "
            "accept-and-ignore trap; check the per-variant verdicts below."
        )
    return lines


def render_report(report: dict[str, Any]) -> str:
    bar = "=" * 78
    thin = "-" * 78
    out: list[str] = [bar, "CC2 METHOD 1047 (DELETE_FILE) PARAMETER-SHAPE PROBE", bar]
    out.append(f"captured at      : {report['timestamp']}")
    out.append(f"mode             : {report['mode']}")
    arguments = report.get("arguments") or {}
    if not report.get("printer_reached", True):
        # The two exits that happen before a connection exists. The header must
        # name what was ATTEMPTED and never imply a printer answered: the fields
        # below are None because nothing was ever read, not because the printer
        # reported nothing.
        out.append(
            "printer reached  : NO — the run stopped before a connection, so "
            "nothing below was measured"
        )
        resolution = report.get("target_resolution") or {}
        connect_failure = report.get("connect_failure") or {}
        attempted = resolution.get("attempted_ip") or connect_failure.get(
            "attempted_ip"
        )
        out.append(
            f"attempted --ip   : {attempted}   "
            f"(--serial given: {arguments.get('serial')})"
        )
        if resolution:
            out.append(
                "discovery        : "
                + (
                    f"ran; error {resolution['discovery_error']}"
                    if resolution.get("discovery_error")
                    else "ran; nothing usable answered"
                    if resolution.get("discovery_ran")
                    else "skipped (--serial was given)"
                )
            )
            if resolution.get("printers_answered"):
                out.append(
                    f"discovery answers: {json_block(resolution['printers_answered'])}"
                )
        else:
            out.append(
                "discovery        : "
                + (
                    "UDP discovery answered this address"
                    if connect_failure.get("discovered_via_udp")
                    else "skipped (--serial was given)"
                )
            )
            out.append(f"serial attempted : {connect_failure.get('attempted_serial')}")
        stopped = (
            resolution.get("reason")
            or connect_failure.get("reason")
            or report.get("aborted")
        )
        out.append(f"why it stopped   : {stopped}")
    out.append(f"printer ip       : {report['printer_ip']}")
    out.append(f"serial           : {report['serial']}")
    if report.get("printer_reached", True):
        out.append(
            f"serial source    : {'UDP discovery' if report['discovered_via_udp'] else '--serial flag'}"
        )
    else:
        # Never the '--serial flag' / 'UDP discovery' claim: neither produced a
        # serial on these paths, and a line saying one did would be the exact
        # overclaim this report exists to avoid.
        out.append("serial source    : none (no serial was ever resolved)")
    out.append(f"printer name     : {report['printer_name']}")
    out.append(f"model            : {report['model_from_attributes'] or report['model_from_discovery']}")
    out.append(f"firmware         : {report['firmware']}   (from 1001 software_version.ota_version)")
    out.append(f"token_status     : {report['token_status']}   (1 = access code required)")
    out.append(
        f"access code      : {'supplied' if report['access_code_supplied'] else 'not supplied (client fallback)'}"
    )
    out.append(f"upload port      : {report['upload_port']}")
    if report.get("aborted"):
        out.append(f"ABORTED          : {report['aborted']}")
    out.append(
        f"variants         : {report.get('variants_answered', '?')} answered, "
        f"{report.get('variants_without_reply', 0)} got NO REPLY AT ALL "
        f"(of {report.get('variants_attempted', '?')} attempted)"
    )
    if report.get("variants_without_reply"):
        out.append(
            "                   ^ a variant that got no reply is NOT evidence and "
            "must not be read as agreeing with the ones that answered."
        )
    unconfirmed = report.get("variants_answered_without_listing_confirmation") or 0
    if unconfirmed:
        out.append(
            f"                   and {unconfirmed} answered variant(s) never got a "
            "readable confirming 1044: their deletion is UNCONFIRMED, not proven "
            "either way (still_listed: null)."
        )
    out.append("")
    out.append("HOW TO READ THIS")
    for caveat in report["caveats"]:
        out.append(f"  * {caveat}")
    out.append("")
    out.append(
        "Every variant was measured against ITS OWN file, uploaded by this run; "
        "one variant's success cannot contaminate another's."
    )
    out.append(thin)

    for record in report["variants"]:
        out.append(f"--- variant {record['index']}: {record['slug']} — {record['label']}")
        out.append(f"file            : {record.get('file')}")
        if record.get("upload"):
            up = record["upload"]
            out.append(f"uploaded        : {up['uploaded_bytes']} bytes, md5 {up['md5']}")
        if record.get("params") is not None:
            out.append(
                "request         : "
                f'{{"id": <auto>, "method": 1047, "params": {json_block(record["params"])}}}'
            )
        else:
            out.append("request         : (none — no 1047 was sent for this entry)")
        if "reply" in record and record["reply"] is not None:
            out.append(f"raw reply       : {json_block(record['reply'])}")
        out.append(f"error_code      : {record.get('error_code')}")
        out.append(f"status          : {record.get('status')}")
        out.append(f"elapsed         : {record.get('elapsed_ms')} ms")
        out.append(f"still_listed    : {record.get('still_listed')}")
        if record.get("listing_count") is not None:
            out.append(f"listing size    : {record['listing_count']} entries")
        if record.get("note"):
            out.append(f"note            : {record['note']}")
        out.append(f"verdict         : {verdict_for(record)}")
        out.append("")

    out.append(thin)
    out.append("CONCLUSION")
    for line in conclusion_lines(report):
        out.append(f"  {line}")
    if report.get("winning_shape") is not None:
        out.append(f"  winning shape   : {json_block(report['winning_shape'])}")
        out.append(
            "                    (the file in it is that variant's own upload; a "
            "fix to delete_file changes the KEY/fields, not the value)"
        )
    leftovers = report.get("cleanup", {}).get("leftovers") or []
    if leftovers:
        out.append("")
        out.append(
            f"  LEFTOVERS this run could not delete ({len(leftovers)}), remove "
            "them from the printer's UI:"
        )
        for name in leftovers:
            out.append(f"    {name}")
    out.append(bar)
    out.append(
        f"Paste THIS WHOLE BLOCK (and the JSON file next to it) into {ISSUE_URL}"
    )
    out.append(bar)
    return "\n".join(out)


# =============================================================================
# Main
# =============================================================================


def emit_capture(report: dict[str, Any]) -> Path | None:
    """
    Print the paste-ready block and write the JSON, whatever state we are in.

    Called on the normal path AND on the abort path, because a run that dies
    half-way is precisely the run that leaves files on the printer: without this
    block the volunteer is left with eleven `cc2_probe_*.gcode` files and no
    record of what any of them were called. The whole thing is defensive — if
    rendering or writing itself fails, the reason is printed and the caller's
    exit code stands, because losing the JSON must not turn into a traceback that
    hides the report already on screen.
    """
    out_path: Path | None = None
    try:
        text = render_report(report)
        print()
        print(text)
    except Exception as err:  # noqa: BLE001 - the report is best-effort by definition
        print(f"\nReport could not be rendered in full: {err!r}", file=sys.stderr)
    try:
        out_path = (
            Path(__file__).parent
            / f"cc2_probe_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
        )
        out_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as err:  # noqa: BLE001 - same reason
        print(f"JSON capture could not be written: {err!r}", file=sys.stderr)
        out_path = None
    if out_path is not None:
        print(f"Saved machine-readable capture to: {out_path}")
    print(
        f"Then paste the whole block above (and that file) into {ISSUE_URL}. "
        "Please do not summarise it — the raw replies are the point."
    )
    return out_path


async def probe(args: argparse.Namespace) -> int:
    # upload_gcode builds its URL from the module-level UPLOAD_PORT; UploadTarget
    # carries only host and token, so this is the only route to a non-80 endpoint
    # (the simulator cannot bind 80).
    cc2_upload.UPLOAD_PORT = args.upload_port
    token = args.access_code or DEFAULT_HTTP_TOKEN

    # Everything the report needs, declared before the try so the `finally` and
    # the single emit path below can always see it. `target` and `client` stay
    # None on the exits that happen before they exist (an unresolvable --ip, a
    # failed connect): those runs now produce a capture too, because "it died at
    # connect" is precisely the thing a maintainer needs to see on #432, and a
    # bare traceback in a volunteer's terminal is not reportable.
    target: Target | None = None
    client: ElegooCC2Client | None = None
    records: list[dict[str, Any]] = []
    winning: dict | None = None
    firmware: Any = None
    raw_attributes: Any = None
    cleanup_info: dict[str, Any] = {}
    # The two things the report block needs to know about how the run ended.
    # `aborted` is None on a run that finished, and a description otherwise.
    aborted: str | None = None
    # Extra report fields an `_EarlyExit` supplied (what --ip was attempted, that
    # no printer was reached). Empty on every normal run.
    detail: dict[str, Any] = {}
    exit_code = 0

    try:
        target = resolve_target(args)
        client = await connect(target, args.access_code)

        blocked = await guard_printer_is_idle(client)
        if blocked:
            logger.error(
                "Refusing to run: {}. This probe deletes files and will not do "
                "that to a printer that is working.",
                blocked,
            )
            # `_EarlyExit`, not `return`: a `return` here would jump over the
            # report block in the `finally` below, which is the one place the
            # capture is produced.
            raise _EarlyExit(4, f"refused: printer is not idle ({blocked})")

        firmware, raw_attributes = await read_firmware(client)
        if firmware:
            logger.info(
                "Firmware {} (model {})",
                firmware,
                attributes_result(raw_attributes).get("machine_model"),
            )
        else:
            logger.warning(
                "No firmware string from the 1001 attributes reply — the report "
                "will be much less useful without it."
            )

        async with aiohttp.ClientSession() as session:
            if args.run:
                for index, variant in enumerate(VARIANTS, start=1):
                    # `records` is passed as the sink and probe_variant publishes
                    # its row into it BEFORE the first await, so the row survives
                    # whatever kills the loop; the return value is that same dict.
                    record = await probe_variant(
                        client,
                        session,
                        target,
                        token,
                        variant,
                        index,
                        len(VARIANTS),
                        sink=records,
                    )
                    if (
                        winning is None
                        and record.get("error_code") == 0
                        and record.get("still_listed") is False
                    ):
                        winning = record
            else:
                # Dry run: prove the whole non-destructive path (upload, settle,
                # listing) with ONE file, then show the matrix. One file, not
                # eleven, because on a printer whose deletes are dead the
                # clean-up cannot work and junk files would be left behind.
                name = probe_name("preflight")
                logger.info("Dry run: uploading one preflight file {}.", name)
                try:
                    record = await upload_probe_file(session, target.ip, token, name)
                    OWNED.add(name)
                    logger.info("PROBE UPLOADED {}", name)
                    logger.info("  uploaded {} bytes", record["size"])
                    settle = await wait_until_listed(client, name)
                    logger.info(
                        "  listed after {} attempt(s): {}",
                        settle["attempts"],
                        settle["listed"],
                    )
                    records.append(
                        {
                            "index": 0,
                            "slug": "preflight",
                            "label": "dry-run upload check (no 1047 sent)",
                            "file": name,
                            "params": None,
                            "upload": record,
                            "settle": settle,
                            "status": "dry_run_upload_only",
                            "still_listed": None,
                            "error_code": None,
                            "note": (
                                "dry run: no delete request was sent. The matrix "
                                "below is exactly what --run would send, with "
                                "cc2_probe_<variant>_<8 hex>.gcode in place of N."
                            ),
                        }
                    )
                except UPLOAD_ERRORS as err:
                    logger.error(
                        "Dry run could not upload its preflight file: {!r}. "
                        "Check --upload-port (the real printer serves PUT /upload "
                        "on 80) and --access-code.",
                        err,
                    )
                    # Recorded so the report names the file it tried, in case the
                    # upload half-succeeded and the printer is holding it.
                    records.append(
                        {
                            "index": 0,
                            "slug": "preflight",
                            "label": "dry-run upload check (no 1047 sent)",
                            "file": name,
                            "params": None,
                            "status": "upload_failed",
                            "still_listed": None,
                            "error_code": None,
                            "note": (
                                f"dry run: the preflight upload failed ({err!r}), "
                                "so no 1047 was sent and nothing was deleted"
                            ),
                        }
                    )
                    raise _EarlyExit(
                        5, f"dry-run preflight upload failed ({err!r})"
                    ) from None

                logger.info("Matrix this run WOULD send (one file per variant):")
                for index, variant in enumerate(VARIANTS, start=1):
                    logger.info(
                        "  {}/{} {}",
                        index,
                        len(VARIANTS),
                        json_block({"method": 1047, "params": variant.build("N")}),
                    )

            # Clean up our own uploads. On a --run this uses the shape that
            # provably worked; on a dry run the only shape it may use is the one
            # this integration ships today — that single 1047 can only ever name
            # the probe's own file, and if it happens to work the cleanup
            # section of the report shows that.
            cleanup_info = await cleanup(
                client,
                winning,
                dry_run_shape=None if args.run else DRY_RUN_CLEANUP_SHAPE,
            )
    except _EarlyExit as early:
        # A deliberate early exit (target unresolved, connect refused/failed, not
        # idle, dry-run upload failed): not a crash, but the matrix still never
        # ran, and a capture with no rows has to say why rather than look like an
        # empty result. The first two of those happen before this function has a
        # target or a client at all, which is exactly why they come through here
        # as a raise instead of a `return`.
        exit_code = early.code
        aborted = early.reason
        detail = early.detail
    except (KeyboardInterrupt, asyncio.CancelledError) as err:
        # Ctrl-C arrives here as CancelledError: asyncio.run cancels the task and
        # only re-raises KeyboardInterrupt at the top level, once this coroutine
        # has finished unwinding. The `finally` below still runs and may still
        # await (a cancelled task is not a frozen loop), so the abort cleanup does
        # happen — but it is bounded, and it sends nothing new to the printer
        # beyond the listing and the deletes of our own files it needs to name the
        # leftovers. A second Ctrl-C short-circuits it into the same leftovers
        # list, computed synchronously.
        aborted = "interrupted"
        exit_code = 130
        logger.warning(
            "Interrupted ({}). Skipping any further commands and going straight "
            "to the report, because the files this run uploaded still have to be "
            "named.",
            type(err).__name__,
        )
    except Exception as err:  # noqa: BLE001 - the capture outranks the exception
        # An AttributeError/KeyError from a client model shape change, or
        # anything else the matrix did not expect to raise. The client may be
        # half-dead by now, so this goes through the abort-aware cleanup, which
        # names the files rather than trusting a listing it cannot get.
        aborted = repr(err)
        exit_code = 1
        logger.error(
            "The probe aborted mid-run: {!r}.\n"
            "  The capture below is PARTIAL. It is printed and saved anyway, "
            "because it is the only thing that names the probe files this run "
            "uploaded to your printer.",
            err,
        )
    finally:
        if aborted is not None:
            # Rows the abort caught mid-flight get a status before anything reads
            # them, so the report never shows `status: null`. Purely synchronous,
            # so it runs even on the exits that never had a client (where
            # `records` is empty anyway).
            stamp_unfinished_records(records)
        if aborted is not None and client is not None and not cleanup_info and OWNED:
            # The abort path: name what we uploaded, and clean up only if the
            # link is still up. Skipped entirely when OWNED is empty — on the
            # early exits nothing was ever uploaded, so there is nothing to name
            # and no reason to send the printer so much as a listing after it has
            # been refused. Bounded, because a run that died on a dead printer
            # would otherwise spend 10 s per command (CC2_COMMAND_TIMEOUT)
            # timing out — minutes — before the volunteer saw the list of files
            # they must delete. The report is the deliverable; the clean-up is a
            # bonus. `cleanup_after_abort` catches what it can and falls back to
            # naming the files, so a failure here cannot replace the real error.
            try:
                cleanup_info = await asyncio.wait_for(
                    cleanup_after_abort(
                        client,
                        records,
                        winning,
                        dry_run_shape=None if args.run else DRY_RUN_CLEANUP_SHAPE,
                    ),
                    timeout=ABORT_CLEANUP_TIMEOUT,
                )
            except BaseException as err:  # noqa: BLE001 - never mask the abort
                # Deliberately BaseException: on the interrupt path every await
                # here can be cancelled again, and losing the leftovers list
                # would be the one failure this path cannot afford.
                cleanup_info = {
                    "leftovers": unconfirmed_leftovers(records),
                    "attempts": [],
                    "shape_used": None,
                    "note": (
                        f"cleanup could not even be attempted ({err!r}); every "
                        "probe file this run uploaded is named and has to be "
                        "removed from the printer's UI"
                    ),
                }
        if client is not None:
            # Bounded for the same reason the abort-cleanup arm above is: this
            # await sits between the last measurement and the report, so a
            # disconnect that never returns on a half-dead wifi link would bury
            # a capture that already exists. `disconnect_bounded` logs the
            # abandonment and falls through to `emit_capture` below.
            await disconnect_bounded(client)

    report = build_report(
        args,
        target,
        firmware,
        raw_attributes,
        records,
        cleanup_info,
        winning,
        aborted=aborted,
        detail=detail,
    )

    emit_capture(report)
    return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe which parameter shape CC2 method 1047 (DELETE_FILE) accepts. "
            "Dry run by default; --run is required to send any delete."
        )
    )
    parser.add_argument(
        "--ip", required=True, help="printer address; never overridden by discovery"
    )
    parser.add_argument(
        "--serial",
        help="skip UDP discovery entirely (a discovery round-trip costs ~10-30 s)",
    )
    parser.add_argument(
        "--access-code",
        help="pairing code; it is both the MQTT password and the HTTP X-Token",
    )
    parser.add_argument(
        "--upload-port",
        type=int,
        default=80,
        help="port of the printer's PUT /upload endpoint (80 on real hardware, "
        "8080 on the in-repo simulator)",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="actually send the 1047 matrix (deletes this run's own uploads); "
        "without it, dry run only",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    logger.remove()
    logger.add(sys.stdout, level="INFO", colorize=True)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        raise SystemExit(asyncio.run(probe(args)))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
