"""
Custom integration to integrate elegoo_printer with Home Assistant.

For more details about this integration, please refer to
https://github.com/danielcherubini/elegoo-homeassistant
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import unquote

import voluptuous as vol
from aiohttp import ClientError
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_IP_ADDRESS, Platform, UnitOfTime
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_registry import (
    EntityRegistry,
    async_get,
)
from homeassistant.loader import async_get_loaded_integration

from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient

from .api import ElegooPrinterApiClient
from .cc2.cleanup import (
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    DEFAULT_OLDER_THAN_DAYS,
    SKIP_NOT_INCLUDED,
    select_deletions,
)
from .cc2.client import ElegooCC2Client
from .cc2.const import CC2_ERROR_FILE_DELETE_FAILED, CC2_ERROR_PRINTER_BUSY
from .cc2.upload import CHUNK_SIZE as UPLOAD_CHUNK_SIZE
from .cc2.upload import UploadFile
from .const import (
    CONF_PROXY_ENABLED,
    CONFIG_VERSION_1,
    CONFIG_VERSION_2,
    CONFIG_VERSION_3,
    CONFIG_VERSION_4,
    CONFIG_VERSION_5,
    DOMAIN,
    LOGGER,
)
from .coordinator import ElegooDataUpdateCoordinator
from .data import ElegooPrinterData
from .sdcp.exceptions import (
    PRINT_TRANSPORT_ERRORS,
    ElegooPrinterConnectionError,
    ElegooPrinterNotConnectedError,
)
from .websocket.server import ElegooPrinterServer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    import aiohttp
    from homeassistant.core import HomeAssistant, ServiceCall

    from .data import ElegooPrinterConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.IMAGE,
    Platform.CAMERA,
    Platform.LIGHT,
    Platform.BUTTON,
    Platform.FAN,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.UPDATE,
]

SERVICE_UPDATE_IP = "update_ip"

# min-length: a watcher script that forwards an empty address must fail loudly,
# not silently. Plain str (no IP regex): hostnames are allowed, matching the
# config flow's plain TextSelector.
SERVICE_UPDATE_IP_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): str,
        vol.Required(CONF_IP_ADDRESS): vol.Length(min=1),
    }
)


async def _async_update_ip(hass: HomeAssistant, call: ServiceCall) -> dict:
    """
    Update a config entry's printer IP address and reload the entry.

    Accepts ``entry_id`` and ``ip_address``; writes the new address into the
    entry data, reloads the entry so the integration reconnects at the new
    address, and returns a definitive ``{success, error|message}`` result
    (supports_response=optional).
    """
    entry_id = call.data["entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)
    # The services.yaml selector constrains the entry picker in the UI, but a
    # programmatic/automation call can pass any entry_id — this domain check
    # is the real guard, not dead code.
    if entry is None or entry.domain != DOMAIN:
        return {
            "success": False,
            "error": f"Config entry {entry_id} not found for {DOMAIN}",
        }

    new_ip = call.data[CONF_IP_ADDRESS]
    LOGGER.info("Updating printer IP to %s for entry %s", new_ip, entry_id)

    # The runtime config is read data-first, options-second
    # (``{**(entry.data or {}), **(entry.options or {})}`` — see
    # async_setup_entry, coordinator and sensor), so options win on a
    # conflicting key. The options flow saves the whole Printer.to_dict()
    # payload -- ip_address included -- into entry.options, so writing
    # entry.data alone leaves the stale address winning and the printer is
    # still sought at its old address. Both stores must be written.
    # Pin note: async_update_entry is a sync @callback on the HA 2025.4.0 pin
    # — it must NOT be awaited. If the HA pin is ever advanced, re-verify
    # this call form.
    new_data = {**entry.data, CONF_IP_ADDRESS: new_ip}
    new_options = {**(entry.options or {}), CONF_IP_ADDRESS: new_ip}
    hass.config_entries.async_update_entry(
        entry,
        data=new_data,
        options=new_options,
    )

    try:
        # On a still-LOADED entry, the data write above also fires the
        # entry's add_update_listener(async_reload_entry) reload. HA
        # serializes both on the entry's setup lock (the final state is
        # deterministic) but the second full unload→setup cycle would still
        # run (extra printer API connect/disconnect, MQTT broker
        # stop/start, entity re-registration churn, ~2x latency). In the
        # headline use case (the printer is unreachable at the old IP, the
        # entry is in SETUP_RETRY/SETUP_ERROR, and there is no live update
        # listener) this explicit reload is the only cycle. No awaitable
        # public API exists to wait for the listener-driven reload (verified
        # HA 2025.4 and 2026.8), so this explicit one is what lets the
        # handler return a definitive result.
        await hass.config_entries.async_reload(entry.entry_id)
    except ConfigEntryError as err:
        # DEFENSIVE catch: on HA 2025.4 the entry setup flow catches
        # ConfigEntryError internally (marking the entry SETUP_ERROR) instead
        # of propagating it, so the state check below is the real failure
        # signal; this branch guards other HA versions/paths.
        return {
            "success": False,
            "error": f"Reload failed after IP update: {err}",
        }

    # The real failure signal: on HA 2025.4 a failed reload leaves the entry
    # not-LOADED rather than raising.
    if entry.state is not ConfigEntryState.LOADED:
        LOGGER.warning(
            "Reload after IP update failed for entry %s (state: %s)",
            entry_id,
            entry.state,
        )
        return {
            "success": False,
            "error": (
                f"Printer unreachable at new address {new_ip} "
                f"(entry state: {entry.state})"
            ),
        }

    LOGGER.info("IP updated to %s and entry %s reloaded", new_ip, entry_id)
    return {"success": True, "message": f"IP updated to {new_ip} and entry reloaded"}


SERVICE_START_PRINT = "start_print"

# `tray` is validated here because the printer does not: an out-of-range
# tray_id is acknowledged with error_code 0 and silently printed from tray 0
# (measured on a CC2, firmware 02.01.00.00). Four trays is what the Canvas has.
SERVICE_START_PRINT_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): str,
        vol.Required("filename"): vol.Length(min=1),
        vol.Optional("tray"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3)),
        # default matches ElegooSlicer, which sends printer_check: true on
        # every job; users who want a faster reprint pass false.
        vol.Optional("bed_leveling", default=True): bool,
    }
)


async def _async_start_print(hass: HomeAssistant, call: ServiceCall) -> dict:
    """
    Start a file that is already in a Centauri Carbon 2's local storage.

    CC2 only - the SDCP transports are deliberately not wired up, since
    CMD_START_PRINT crashed the first-gen Centauri Carbon (#297). Accepts
    ``entry_id``, ``filename``, optional ``tray`` (0-based Canvas tray mapped
    to every G-code tool the file reports) and ``bed_leveling`` (default on,
    as the slicer does), and returns a
    definitive ``{success, message|error}`` result (supports_response=optional).
    """
    entry_id = call.data["entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)
    # Same guard as update_ip: the selector limits the UI, not automations.
    if entry is None or entry.domain != DOMAIN:
        return {
            "success": False,
            "error": f"Config entry {entry_id} not found for {DOMAIN}",
        }
    if entry.state is not ConfigEntryState.LOADED:
        return {
            "success": False,
            "error": f"Config entry {entry_id} is not loaded (state: {entry.state})",
        }

    client = entry.runtime_data.api.client
    if not isinstance(client, ElegooCC2Client):
        return {
            "success": False,
            "error": "start_print is only available for Centauri Carbon 2 printers",
        }

    filename = call.data["filename"]
    tray = call.data.get("tray")
    bed_leveling = call.data.get("bed_leveling", True)
    try:
        code = await client.print_start(
            filename, tray_id=tray, bed_leveling=bed_leveling
        )
    except (ElegooPrinterNotConnectedError, ElegooPrinterConnectionError) as err:
        LOGGER.warning("start_print failed for entry %s: %r", entry_id, err)
        return {
            "success": False,
            "error": f"Printer not reachable ({err.__class__.__name__})",
        }

    return _start_print_result(code, filename)


def _start_print_result(code: int, filename: str) -> dict:
    """Turn the printer's 1020 error_code into the service response."""
    if code != 0:
        reason = (
            "Printer is busy"
            if code == CC2_ERROR_PRINTER_BUSY
            else "Printer refused the job"
        )
        return {"success": False, "error": f"{reason} (error_code {code})"}
    LOGGER.info("Print started: %s", filename)
    return {"success": True, "message": f"Print started: {filename}"}


def _resolve_cc2_client(
    hass: HomeAssistant, entry_id: str
) -> tuple[ElegooCC2Client | None, dict | None]:
    """Return the entry's CC2 client, or the error response to hand back."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None, {
            "success": False,
            "error": f"Config entry {entry_id} not found for {DOMAIN}",
        }
    if entry.state is not ConfigEntryState.LOADED:
        return None, {
            "success": False,
            "error": f"Config entry {entry_id} is not loaded (state: {entry.state})",
        }
    client = entry.runtime_data.api.client
    if not isinstance(client, ElegooCC2Client):
        return None, {
            "success": False,
            "error": "This service is only available for Centauri Carbon 2 printers",
        }
    return client, None


SERVICE_UPLOAD_GCODE = "upload_gcode"

SERVICE_UPLOAD_GCODE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): str,
        # the file_id the frontend's file selector returns after uploading to
        # /api/file_upload; process_uploaded_file hands us the file and removes it
        vol.Required("file"): str,
        vol.Optional("start", default=False): bool,
        vol.Optional("tray"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3)),
        vol.Optional("bed_leveling", default=True): bool,
    }
)


def _remove_quietly(path: Path) -> None:
    """Delete the staged copy; a missing file is not an error."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        LOGGER.warning("Could not remove staged upload %s", path)


def _stage_upload(
    hass: HomeAssistant, file_id: str
) -> tuple[str, Path | None, UploadFile | None]:
    """
    Copy the uploaded file aside in one pass, hashing as it goes.

    Runs in the executor. HA deletes its own copy when the context closes,
    but the upload has to outlive it and must not sit in memory (up to
    100 MB). Returns the original name, the staged path and the file's size
    and MD5; the path and file are None when the name is not a .gcode file.
    The caller removes the staged copy, whatever happens in between.
    """
    with process_uploaded_file(hass, file_id) as path:
        # The name arrives percent-encoded ("Rapid%20PLA%2B"), whichever client
        # uploaded it, and the printer stores it decoded. Decode here too, so
        # the response and the start command name the file the printer shows.
        name = unquote(path.name)
        # the selector's `accept` is a browser hint only; the service is also
        # reachable from automations and the API, where any file id can be
        # handed in. Checked before copying, so a wrong file is never touched.
        if not name.lower().endswith(".gcode"):
            return name, None, None
        fd, tmp = tempfile.mkstemp(prefix="elegoo-upload-", suffix=".gcode")
        staged = Path(tmp)
        digest = hashlib.md5(usedforsecurity=False)
        size = 0
        try:
            with os.fdopen(fd, "wb") as dst, path.open("rb") as src:
                while chunk := src.read(UPLOAD_CHUNK_SIZE):
                    digest.update(chunk)
                    dst.write(chunk)
                    size += len(chunk)
        except OSError:
            _remove_quietly(staged)
            raise
        return name, staged, UploadFile(name, size, digest.hexdigest())


async def _read_chunks(hass: HomeAssistant, path: Path) -> AsyncIterator[bytes]:
    """Yield the staged file in upload-sized chunks, reading in the executor."""
    fh = await hass.async_add_executor_job(partial(path.open, "rb"))
    try:
        while chunk := await hass.async_add_executor_job(fh.read, UPLOAD_CHUNK_SIZE):
            yield chunk
    finally:
        await hass.async_add_executor_job(fh.close)


async def _upload_then_start(
    client: ElegooCC2Client,
    session: aiohttp.ClientSession,
    file: UploadFile,
    chunks: AsyncIterator[bytes],
    call: ServiceCall,
) -> dict:
    """Send the file and, if asked, start it - under the printer's upload lock."""
    entry_id = call.data["entry_id"]
    tray = call.data.get("tray")
    bed_leveling = call.data.get("bed_leveling", True)
    # held across upload and start: the printer assembles an upload from
    # several ranged PUTs and cannot tell two of them apart
    async with client.upload_lock:
        try:
            size = await client.upload_gcode(session, file, chunks)
        except (ElegooPrinterNotConnectedError, ElegooPrinterConnectionError) as err:
            LOGGER.warning("upload_gcode failed for entry %s: %s", entry_id, err)
            return {"success": False, "error": str(err) or err.__class__.__name__}
        LOGGER.info("Uploaded %s (%d bytes) to entry %s", file.name, size, entry_id)

        if not call.data.get("start", False):
            return {
                "success": True,
                "message": f"Uploaded {file.name} ({size} bytes)",
                "filename": file.name,
            }
        try:
            code = await client.print_start(
                file.name, tray_id=tray, bed_leveling=bed_leveling
            )
        except (ElegooPrinterNotConnectedError, ElegooPrinterConnectionError) as err:
            return {
                "success": False,
                "error": (
                    f"Uploaded {file.name}, but starting failed "
                    f"({err.__class__.__name__})"
                ),
                "filename": file.name,
            }
        LOGGER.info(
            "Started %s on entry %s (tray=%s, bed_leveling=%s): code %s",
            file.name,
            entry_id,
            "auto" if tray is None else tray,
            bed_leveling,
            code,
        )
    result = _start_print_result(code, file.name)
    result["filename"] = file.name
    return result


async def _async_upload_gcode(hass: HomeAssistant, call: ServiceCall) -> dict:
    """
    Upload a G-code file chosen in the UI to a Centauri Carbon 2, optionally print it.

    The ``file`` field carries the id of a file the frontend uploaded to
    Home Assistant; the handler copies it aside, sends it to the printer's
    local storage under its original name in 1 MB chunks and, with
    ``start``, issues the same start as ``start_print``. CC2 only.
    """
    client, error = _resolve_cc2_client(hass, call.data["entry_id"])
    if client is None:
        return error or {"success": False, "error": "unknown"}

    try:
        filename, staged, file = await hass.async_add_executor_job(
            _stage_upload, hass, call.data["file"]
        )
    except (OSError, ValueError) as err:
        return {"success": False, "error": f"Uploaded file not available: {err!r}"}
    if staged is None or file is None:
        return {
            "success": False,
            "error": f"Not a G-code file: {filename}",
            "filename": filename,
        }

    chunks = _read_chunks(hass, staged)
    try:
        return await _upload_then_start(
            client, async_get_clientsession(hass), file, chunks, call
        )
    finally:
        # an upload that failed mid-stream leaves the generator open; close it
        # here so the file handle is released before the copy is removed
        await chunks.aclose()
        await hass.async_add_executor_job(_remove_quietly, staged)


SERVICE_CLEANUP_FILES = "cleanup_files"

# How the printer's own reply maps onto the per-file reasons the service
# reports. 1007 is the only delete-specific code the protocol lists, and even
# that is unverified (1047 documents no response at all).
_DELETE_REFUSED = "printer refused the delete"
_DELETE_REFUSED_BUSY = "printer is busy"
_DELETE_UNCONFIRMED = "still listed after delete"
_DELETE_NO_REPLY = "printer did not answer"


def _glob_patterns(value: object) -> list[str]:
    """
    Validate ``include``/``exclude`` into a list of glob patterns.

    Hand-rolled rather than a ``cv`` composition, and the reason is measured:
    ``vol.All(cv.ensure_list, [vol.All(str, vol.Length(min=1))])`` — the
    obvious spelling — **rejects the empty string** ("length of value must be
    at least 1 @ data[0]"), and a cleared ``text`` field in the HA UI is
    submitted as ``""``. The reporter's "exclude = nothing" has to validate, so
    the blank case needs explicit handling that no stdlib composition
    expresses. Two claims sometimes made for the stdlib route are false and
    must not be re-added here: a ``text`` selector with ``multiple: true``
    validates a **list of strings** (not a mapping), and ``cv.ensure_list`` does
    **not** coerce a dict into a string — it passes it through and the inner
    ``str`` check rejects it.

    So this takes a ``str`` or a ``list[str]``, rejects anything else (a mapping
    included — the selector shapes the UI only, and a programmatic or REST
    caller can send any JSON at all, which is why the same reasoning already
    re-checks the entry domain in ``_async_update_ip``), and reads ``""`` as "no
    patterns".

    ``""`` becoming ``[]`` — which matches no file name at all — is deliberate
    and NOT widened to ``DEFAULT_INCLUDE``: a blank field is indistinguishable
    from a template that rendered to empty, and resolving that to ``*`` would
    delete every old file on a call that named nothing. That trade is pinned by
    test_schema_reads_a_blank_field_as_no_patterns, and the resulting inert run
    is reported out loud by ``_pattern_note`` rather than left silent.
    """
    if isinstance(value, str):
        return [] if value == "" else [value]
    if isinstance(value, list):
        patterns: list[str] = []
        for entry in value:
            if not isinstance(entry, str):
                msg = "each pattern must be a string"
                raise vol.Invalid(msg)
            if entry:
                patterns.append(entry)
        return patterns
    msg = "include/exclude must be a pattern or a list of patterns"
    raise vol.Invalid(msg)


def _whole_days(value: object) -> int:
    """
    Validate ``older_than_days`` as a WHOLE number of days, rejecting fractions.

    ``vol.Coerce(int)`` — and HA's own ``cv.positive_int``, which is built the
    same way — **truncates**: ``60.9`` becomes ``60``. A lower day count is a
    lower cutoff, so truncating hands out slightly MORE deletion than the caller
    asked for, which is the wrong direction to round on a destructive field.
    Refusing the value is therefore the fix; a string still parses ("60"),
    because automations template these.

    A ``bool`` is refused for the same kind of reason: Home Assistant's YAML
    loader turns ``older_than_days: yes`` (or ``on``) into ``True``, and
    ``int(True)`` is ``1`` — silently the most aggressive cutoff the field
    allows. A day count that arrives as a boolean is a caller that did not mean
    what it sent, so it fails loudly instead.
    """
    if isinstance(value, bool):
        msg = "older_than_days must be a number of days, not a yes/no value"
        raise vol.Invalid(msg)
    days = vol.Coerce(int)(value)
    try:
        fractional = days != float(value)
    except OverflowError:
        # A value too large for float() is not a usable day count either; fail
        # as a validation error rather than letting an OverflowError escape the
        # schema and surface as an unhandled exception in the call.
        fractional = True
    if fractional:
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
        # the safe answer is the default: report, delete nothing. The other
        # three services default to doing the thing; this one defaults to not
        # destroying files, because its reply cannot be trusted.
        vol.Optional("dry_run", default=True): bool,
    }
)


class CleanupReport(NamedTuple):
    """
    The one shape every ``cleanup_files`` response is built from.

    Seven outcomes all returning hand-built dicts guarantees drift, and a key
    missing from a service response renders as nothing in a template — which is
    indistinguishable from a genuinely empty list. Making the fields
    structural means "every response carries ``deleted``" is a property of the
    type rather than of each branch.

    ``error`` and ``message`` are prose and are dropped by ``as_dict()`` when
    unset: in a Jinja template ``None`` renders as the string "None", so a
    notify reading ``{{ response.error }}`` on a successful run would tell the
    user "None".
    """

    success: bool
    dry_run: bool
    deleted: list[str]
    failed: dict[str, str]
    skipped: dict[str, str]
    would_delete: list[str]
    error: str | None = None
    message: str | None = None

    def as_dict(self) -> dict:
        """Return the response body, dropping prose that was never set."""
        body = self._asdict()
        return {
            key: value
            for key, value in body.items()
            if value is not None or key not in ("error", "message")
        }


def _pattern_note(
    include: Sequence[str],
    file_count: int,
    to_delete: Sequence[str],
    reasons: Mapping[str, str],
) -> str | None:
    """
    Say out loud when the include pattern left nothing a candidate.

    ``include: ""`` (a cleared UI box, or a template that rendered empty) and a
    typo'd ``include=EEXC*`` both look identical to "nothing is old enough":
    every listed file comes back ``SKIP_NOT_INCLUDED``, the service reports
    success, and a nightly cleanup stays inert forever while saying so. A run
    that simply found nothing old keeps its plain message, so this does not turn
    into nightly noise.

    The message is visibility only — ``success`` stays True, because an
    automation must not alert on "nothing to do" — and the pattern set is named
    (an empty one as "(empty)", so a cleared field is recognisable as itself),
    which makes the notification diagnosable instead of mysterious.
    """
    if to_delete:
        return None
    if not file_count:
        # An empty listing: nothing was ever a candidate, pattern or not.
        return "the printer listed no files, so there was nothing to clean up"
    if reasons and set(reasons.values()) == {SKIP_NOT_INCLUDED}:
        named = ", ".join(include) if include else "(empty)"
        return (
            f"none of the {file_count} listed file(s) matched the include "
            f"pattern(s) {named}, so nothing was a candidate and nothing was "
            "deleted; omit 'include' to mean every file"
        )
    return None


def _cleanup_failure_text(code: int) -> str:
    """Turn a delete error_code into the per-file reason the service reports."""
    if code == CC2_ERROR_FILE_DELETE_FAILED:
        return _DELETE_REFUSED
    if code == CC2_ERROR_PRINTER_BUSY:
        return _DELETE_REFUSED_BUSY
    return f"{_DELETE_REFUSED} (error_code {code})"


async def _async_delete_each(
    client: ElegooCC2Client, names: Sequence[str]
) -> tuple[list[str], dict[str, str]]:
    """
    Send one 1047 per file in order, stopping at the first transport error.

    Sequential on purpose: one MQTT command channel, a 10 s timeout each, and a
    delete storm is the last thing an untested method needs. A refused delete is
    data reported per file, not an abort — one locked file must not cost the
    other thirty-nine. When the printer stops answering, whatever was already
    handled is reported rather than thrown away, and the files never attempted
    are reported as never attempted.
    """
    deleted: list[str] = []
    failed: dict[str, str] = {}
    for index, name in enumerate(names):
        try:
            code = await client.delete_file(name)
        except PRINT_TRANSPORT_ERRORS as err:
            LOGGER.warning("cleanup_files stopped at %s: %r", name, err)
            failed[name] = f"{_DELETE_NO_REPLY} ({err.__class__.__name__})"
            for rest in names[index + 1 :]:
                failed[rest] = _DELETE_NO_REPLY
            return deleted, failed
        if code == 0:
            deleted.append(name)
        else:
            failed[name] = _cleanup_failure_text(code)
    return deleted, failed


def _pattern_list(
    call_data: Mapping[str, object], key: str, default: list[str]
) -> list[str]:
    """
    Read a pattern field from call data, mirroring the schema's default.

    The schema fills `include` with `["*"]` and `exclude` with `[]`, but a
    programmatic or REST caller can bypass it, so the handler needs the same
    defaults. The two cases are NOT the same and must not be collapsed: a
    MISSING key means "the caller did not say", which is the reporter's
    `include = all` default, while a key present as `""` or `[]` means the
    caller cleared the box, which stays inert and is reported as such by
    ``_pattern_note``. Widening a cleared field to `*` is the mass-delete bug
    this service refuses.
    """
    if key not in call_data:
        return list(default)
    return list(call_data[key] or ())


async def _async_confirm_deletions(
    client: ElegooCC2Client, deleted: list[str]
) -> tuple[list[str], dict[str, str]]:
    """
    Re-read the file list (1044) and split ``deleted`` into confirmed and not.

    This is the whole point of the design: 1047 documents no response, so a
    printer that answers ``error_code 0`` and keeps the file is a scenario this
    repo has to survive, not one it gets to disbelieve. Only a listing that no
    longer carries the file counts as a delete; a re-list that fails reports
    every accepted file as unconfirmed rather than as gone.
    """
    try:
        remaining = await client.get_file_list()
    except PRINT_TRANSPORT_ERRORS as err:
        reason = f"delete not confirmed, re-list failed ({err!r})"
        return [], dict.fromkeys(deleted, reason)

    still = dict.fromkeys((n for n in deleted if n in remaining), _DELETE_UNCONFIRMED)
    return [name for name in deleted if name not in remaining], still


async def _async_cleanup_files(hass: HomeAssistant, call: ServiceCall) -> dict:
    """
    Delete old files from a Centauri Carbon 2's local storage (issue #432).

    Takes ``entry_id``, ``older_than_days`` (default 60, whole days only),
    ``include`` (default every file), ``exclude`` (default nothing) and
    ``dry_run`` (**default on**). Selection is the reporter's own vocabulary;
    the safety rules are because method 1047 is documented "Untested": the file
    listing is fetched fresh rather than read from the ten-minute cache, the dry
    run deletes nothing, and a delete is confirmed by re-listing (1044) instead
    of by trusting 1047's reply. A partial success is reported as a failure, so
    an automation can alert on ``not response.success`` and know the printer is
    not in the state it asked for. CC2 only.
    """
    client, error = _resolve_cc2_client(hass, call.data["entry_id"])
    if client is None:
        return error or {"success": False, "error": "unknown"}

    include = _pattern_list(call.data, "include", list(DEFAULT_INCLUDE))
    exclude = _pattern_list(call.data, "exclude", list(DEFAULT_EXCLUDE))
    older_than_days = call.data.get("older_than_days", DEFAULT_OLDER_THAN_DAYS)
    dry_run = call.data.get("dry_run")
    # A programmatic or REST caller can send anything, including no key at all
    # or None; the schema's default is not a guard there. Only exactly the
    # boolean False turns the dry run off, so an omitted key, a None, a "false"
    # string or a 0 all keep the handler from deleting: the destructive branch
    # has to be asked for by name, and cannot be reached by accident.
    dry_run = dry_run is not False

    entry_id = call.data["entry_id"]
    try:
        files = await client.get_file_list()
    except PRINT_TRANSPORT_ERRORS as err:
        return _cleanup_unreachable("file list", err, dry_run=dry_run)

    # The guard reads the name the printer itself reported for the running job;
    # select_deletions compares it exactly, before any pattern is consulted.
    active = client.printer_data.status.print_info.filename
    to_delete, skipped = select_deletions(
        files,
        include=tuple(include),
        exclude=tuple(exclude),
        older_than_days=older_than_days,
        active_filename=active,
    )
    reasons = dict(skipped)
    note = _pattern_note(include, len(files), to_delete, reasons)

    if dry_run:
        message = (
            note
            or f"{len(to_delete)} file(s) older than {older_than_days} day(s) "
            "would be deleted; nothing was deleted (dry run)"
        )
        LOGGER.info("cleanup_files dry run for entry %s: %s", entry_id, message)
        return CleanupReport(
            success=True,
            dry_run=True,
            deleted=[],
            failed={},
            skipped=reasons,
            would_delete=to_delete,
            message=message,
        ).as_dict()

    deleted, failed = await _async_delete_each(client, to_delete)

    # The done-when clause: 1047 documents no response, so the printer's
    # "error_code 0" is not proof of anything. Re-list and believe the listing.
    still: list[str] = []
    if deleted:
        deleted, unconfirmed = await _async_confirm_deletions(client, deleted)
        still = list(unconfirmed)
        failed.update(unconfirmed)

    if note:
        # The include pattern itself left nothing a candidate — say that rather
        # than the bland "nothing was old enough" text.
        message = note
    elif to_delete:
        message = f"deleted {len(deleted)} of {len(to_delete)} selected file(s)"
        if failed:
            message += f"; {len(failed)} refused or unconfirmed"
    else:
        message = (
            f"no file was older than {older_than_days} day(s) and allowed by "
            "the patterns"
        )

    # A partial success is a failure: a daily automation must be able to alert
    # on `not response.success` and know the printer is not in the state it
    # asked for. `still` is folded into `failed` above, so the second half is
    # belt-and-braces rather than a second condition. An EMPTY selection is not
    # a failure — a nightly run with nothing old to do is a clean run, and
    # alerting on it would be noise by Thursday.
    report = CleanupReport(
        success=not failed and not still,
        dry_run=False,
        deleted=deleted,
        failed=failed,
        skipped=reasons,
        would_delete=to_delete,
        message=message,
    )
    LOGGER.info("cleanup_files for entry %s: %s", entry_id, message)
    return report.as_dict()


def _cleanup_unreachable(what: str, err: Exception, *, dry_run: bool) -> dict:
    """
    Report a printer that could not be reached, in the full response shape.

    ``dry_run`` echoes what the caller actually asked for rather than a fixed
    False, so a failed dry run is not misreported as a failed delete run.
    """
    LOGGER.warning("cleanup_files could not %s: %r", what, err)
    return CleanupReport(
        success=False,
        dry_run=dry_run,
        deleted=[],
        failed={},
        skipped={},
        would_delete=[],
        error=f"Printer not reachable ({err.__class__.__name__})",
    ).as_dict()


# https://developers.home-assistant.io/docs/creating_integration_file_structure/#defining-services
async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: ARG001
    """Set up the Elegoo Printer component."""
    # `config` is part of the HA async_setup contract (the manifest-level
    # service registration is unconditional); ARG001 noqa is intentional.
    #
    # HA invokes a service handler with the ServiceCall only, so the
    # (hass, call) handlers are bound with partial. Registering them bare
    # raises "missing 1 required positional argument: 'call'" on every call.
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_IP,
        partial(_async_update_ip, hass),
        schema=SERVICE_UPDATE_IP_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_START_PRINT,
        partial(_async_start_print, hass),
        schema=SERVICE_START_PRINT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPLOAD_GCODE,
        partial(_async_upload_gcode, hass),
        schema=SERVICE_UPLOAD_GCODE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEANUP_FILES,
        partial(_async_cleanup_files, hass),
        schema=SERVICE_CLEANUP_FILES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
) -> bool:
    """
    Asynchronously sets up the Elegoo printer integration from a configuration entry.

    Initializes the data update coordinator and printer API client,
    performs the first data refresh, forwards setup to supported platforms,
    and registers a listener for entry updates.
    Raises ConfigEntryNotReady if the printer cannot be reached, unless the
    entry tolerates a powered-off printer (CONF_ALLOW_PRINTER_OFFLINE), in which
    case setup completes offline and entities stay unavailable until reachable.

    Returns:
        bool: True if the integration is set up successfully.

    """
    coordinator = ElegooDataUpdateCoordinator(hass=hass, entry=entry)

    config = {
        **(entry.data or {}),
        **(entry.options or {}),
    }

    client = await ElegooPrinterApiClient.async_create(
        config=MappingProxyType(config),
        logger=LOGGER,
        hass=hass,
        config_entry=entry,
    )

    if client is None:
        msg = "Failed to connect to the printer"
        raise ConfigEntryNotReady(msg)

    entry.runtime_data = ElegooPrinterData(
        api=client,
        integration=async_get_loaded_integration(hass, entry.domain),
        coordinator=coordinator,
    )

    # https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
    # The entry tolerates a powered-off printer and the api deliberately came up
    # offline (#171). The first refresh cannot be used here: it raises
    # ConfigEntryNotReady on a failed refresh — undoing the option — and its
    # cleanup would release the broker/proxy references the offline client keeps
    # on purpose. async_start_offline() only marks state; the first entity
    # listener arms the 30 s retry poll that reconnects the client.
    #
    # Branching on offline_setup and not on is_connected is deliberate: any
    # failure path that did not opt into offline setup keeps today's fail-loud
    # behaviour (a first refresh that raises ConfigEntryNotReady).
    if client.offline_setup:
        LOGGER.info(
            "Setting up %s without a connection: the printer was not reachable."
            " All entities stay unavailable until it is reachable again — if it"
            " never comes back, check its IP address.",
            entry.title,
        )
        coordinator.async_start_offline()
    else:
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception:
            # If first refresh fails, clean up the client resources
            try:
                await client.elegoo_disconnect()
                await client.elegoo_stop_mqtt_broker()
                if client.server:
                    await ElegooPrinterServer.release_reference()
                    # Nulling the token is what makes ``server`` the record of
                    # ownership that ``async_unload_entry`` gates on: an entry
                    # that never got past this cleanup must not have the unload
                    # release a reference it no longer holds.
                    client.server = None
            except Exception as cleanup_error:  # noqa: BLE001
                LOGGER.warning(
                    "Error during cleanup after failed setup: %s", cleanup_error
                )
            raise

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and (client := entry.runtime_data.api):
        # Disconnect client first
        try:
            await asyncio.shield(client.elegoo_disconnect())
        except (asyncio.CancelledError, ClientError, OSError, RuntimeError) as e:
            LOGGER.warning("Error disconnecting client: %s", e, exc_info=True)

        # Two different debts are settled here, and conflating them was a bug
        # (#171 review):
        #   * the registry entry — ``reconnect()`` gives the proxy reference back
        #     as soon as the printer stops answering but leaves the printer
        #     registered, so ownership of the reference is NOT the record of
        #     membership. A printer left in the registry keeps being routed to
        #     and broadcast at by the shared proxy after its entry is gone.
        #   * the proxy reference — an entry that came up offline (#171) never
        #     acquired one, and releasing one anyway would decrement another
        #     entry's count and stop the shared server under a live entry.
        registered = ElegooPrinterServer.is_printer_registered(client.printer)
        if registered or client.server is not None:
            try:
                server_continues = True
                if registered:
                    should_stop = await asyncio.shield(
                        ElegooPrinterServer.remove_printer_from_server(
                            client.printer, LOGGER
                        )
                    )
                    server_continues = not should_stop
                if server_continues and client.server is not None:
                    # Server continues with other printers, just decrement ref count
                    await asyncio.shield(ElegooPrinterServer.release_reference())
                    # Keep ``server`` honest as the ownership record: it has been
                    # given back, so nothing downstream may release it twice.
                    client.server = None
                # NOTE: should_stop is True only once the registry is empty, and
                # nothing stops the server or releases its reference here — a
                # pre-existing leak, deliberately out of scope for #171.
            except (asyncio.CancelledError, OSError, RuntimeError) as e:
                LOGGER.warning(
                    "Error removing printer from proxy server: %s", e, exc_info=True
                )

        # Stop MQTT broker if it was started by this client
        # Don't use shield here - we want this to be cancellable
        try:
            await client.elegoo_stop_mqtt_broker()
        except (OSError, RuntimeError) as e:
            LOGGER.warning("Error stopping MQTT broker: %s", e, exc_info=True)

    return unload_ok


async def async_remove_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: ElegooPrinterConfigEntry,
) -> None:
    """
    Forget a video release parked for a config entry that is being removed.

    A camera entity that could not release the shared printer video (a foreign
    viewer was holding it) hands the debt to the next camera entity of the same
    config entry. Once the entry is removed there is no next entity and no live
    printer connection, so the parked entry id would be kept forever. This hook
    is the only place that observes a real removal independently of a still
    running teardown retry: a retry that has already exhausted its attempts is
    gone by the time core drops the entry, so nothing else would ever notice.

    Removal is terminal, so this forgets and parks nothing. It is deliberately
    the only thing it does — the client is already disconnected and the MQTT
    broker stopped by ``async_unload_entry``, which core always runs first.

    ``hass`` is part of the hook's signature but unused; the registry is keyed by
    entry id alone.

    Arguments:
        hass: The Home Assistant instance (unused).
        entry: The config entry being removed.

    """
    # Deferred import: the camera platform drags in the camera component and
    # ffmpeg, and this hook must not make the integration's own import depend on
    # them (same reason the config flow imports its clients lazily).
    from .camera import forget_pending_video_release  # noqa: PLC0415

    forget_pending_video_release(entry.entry_id)


async def async_reload_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(  # noqa: PLR0911, PLR0912, PLR0915
    hass: HomeAssistant, config_entry: ElegooPrinterConfigEntry
) -> bool:
    """Migrate old entry."""
    if config_entry.version == CONFIG_VERSION_1:
        try:
            # Migrating data by removing printer and re-adding it
            config = {
                **(config_entry.data or {}),
                **(config_entry.options or {}),
            }
            ip_address = config.get(CONF_IP_ADDRESS)
            proxy_enabled = config.get(CONF_PROXY_ENABLED, False)
            if ip_address is None:
                LOGGER.error("Config migration failed, IP address is null")
                return False

            LOGGER.debug(
                "Migrating from version %s with ip_address: %s and proxy: %s",
                config_entry.version,
                ip_address,
                proxy_enabled,
            )
            client = ElegooPrinterClient(
                ip_address=ip_address,
                logger=LOGGER,
                session=async_get_clientsession(hass),
            )
            printer = await hass.async_add_executor_job(
                client.discover_printer, ip_address
            )
            if printer and len(printer) > 0:
                printer[0].proxy_enabled = proxy_enabled
                new_data = printer[0].to_dict()

                hass.config_entries.async_update_entry(
                    config_entry, data=new_data, version=2
                )
                LOGGER.debug("Migration to version 2 successful")
            else:
                LOGGER.error("Config migration failed, no printer found")
                return False
        except (ConnectionError, IndexError, KeyError) as e:
            LOGGER.error(f"Error migrating config entry: {e}")
            return False

    if config_entry.version == CONFIG_VERSION_2:
        # Migrate to version 3: Update native_unit_of_measurement for remaining_print_time  # noqa: E501
        entity_registry: EntityRegistry = async_get(hass)
        entries = entity_registry.entities.get_entries_for_config_entry_id(
            config_entry.entry_id
        )
        for entry in entries:
            if (
                entry.device_class == SensorDeviceClass.DURATION
                and entry.native_unit_of_measurement == UnitOfTime.SECONDS
            ):
                entity_registry.async_update_entity(
                    entry.entity_id,
                    native_unit_of_measurement=UnitOfTime.MILLISECONDS,
                )
        hass.config_entries.async_update_entry(config_entry, version=3)
        LOGGER.debug("Migration to version 3 successful")

    if config_entry.version == CONFIG_VERSION_3:
        LOGGER.debug("Migrating to version 4: updating unique IDs from 'name' to 'id'.")
        try:
            # --- 1. Get Old and New Identifiers ---
            config = {
                **(config_entry.data or {}),
                **(config_entry.options or {}),
            }
            # Old ID was based on the 'name' field.
            old_identifier_name = config.get("name")
            # New stable ID is the 'id' field.
            new_identifier = config.get("id")

            if not old_identifier_name or not new_identifier:
                LOGGER.error(
                    "Migration v3->v4 failed: 'name' or 'id' field is missing."
                )
                return False

            old_identifier_slug = old_identifier_name.lower().replace(" ", "_")
            LOGGER.debug(
                "MIGRATION CHECK: Using old identifier prefix: '%s'",
                old_identifier_slug,
            )

            if not old_identifier_slug or not new_identifier:
                LOGGER.error(
                    "Migration v3->v4 failed: 'name' or 'id' field is missing."
                )
                return False

            if old_identifier_slug == new_identifier:
                LOGGER.debug("Identifier is already up-to-date. Finalizing migration.")
                hass.config_entries.async_update_entry(config_entry, version=4)
                return True

            # --- 2. Get Device and Entity Registries ---
            device_registry = dr.async_get(hass)
            entity_registry = er.async_get(hass)

            # --- 3. Migrate the Device Registry ---
            device_entries = dr.async_entries_for_config_entry(
                device_registry, config_entry.entry_id
            )
            device_entry = device_entries[0] if device_entries else None
            if device_entry:
                new_identifiers = {(DOMAIN, new_identifier)}
                LOGGER.debug("Updating device identifiers to %s", new_identifiers)
                device_registry.async_update_device(
                    device_entry.id, new_identifiers=new_identifiers
                )

            # --- 4. Migrate the Entity Registry ---
            entity_entries = er.async_get(
                hass
            ).entities.get_entries_for_config_entry_id(config_entry.entry_id)
            for entry in entity_entries:
                LOGGER.debug(
                    "MIGRATION CHECK: Comparing with entity unique_id: '%s'",
                    entry.unique_id,
                )
                # If it's the camera entity, remove it so it can be recreated cleanly.
                if entry.domain == "camera":
                    LOGGER.debug(
                        "Removing old camera entity '%s' to allow for clean re-creation.",  # noqa: E501
                        entry.entity_id,
                    )
                    entity_registry.async_remove(entry.entity_id)
                    continue  # Skip to the next entity

                # Check if the entity's unique_id uses the old identifier
                if entry.unique_id.lower().startswith(old_identifier_slug.lower()):
                    LOGGER.debug("MIGRATION MATCH FOUND!")
                    # Replace the old identifier prefix with the new one
                    new_unique_id = entry.unique_id.replace(
                        old_identifier_slug, new_identifier, 1
                    )
                    LOGGER.debug(
                        "Migrating entity '%s' to new unique_id: %s",
                        entry.entity_id,
                        new_unique_id,
                    )
                    entity_registry.async_update_entity(
                        entry.entity_id, new_unique_id=new_unique_id
                    )

            # --- 5. Update Config Entry Version ---
            hass.config_entries.async_update_entry(config_entry, version=4)
            LOGGER.info(
                "Migration to version 4 successful for printer ID %s", new_identifier
            )

        except (KeyError, ValueError) as e:
            LOGGER.error(
                "Error migrating config entry to version 4: %s", e, exc_info=True
            )
            return False

    if config_entry.version == CONFIG_VERSION_4:
        new_data = {**config_entry.data}
        transport = new_data.get("transport_type")
        new_data["has_canvas"] = transport == "cc2_mqtt"
        hass.config_entries.async_update_entry(
            config_entry, data=new_data, version=CONFIG_VERSION_5
        )
        LOGGER.debug(
            "Migration to version 5 successful: has_canvas=%s",
            new_data["has_canvas"],
        )

    return True
