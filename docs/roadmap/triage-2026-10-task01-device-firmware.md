---
status: committed
parent: docs/roadmap/triage-2026-10-status.md
done-when: After a successful coordinator poll on a loaded entry, the device record in the Home Assistant device registry carries the printer's live firmware version (`api.printer.firmware`) rather than the version captured at config-flow time, with no config-entry reload and no write to `entry.data`; a new `custom_components/elegoo_printer/tests/test_device_firmware.py` pins the change (a poll with a new version moves the registry's `sw_version` without touching `entry.data` or the connection-time api helper, an unchanged version logs and writes nothing, and the no-device / no-live-version / no-id / failed-poll cases overwrite nothing), every guard in the new method is falsifiable by the mutation table below, it fails against the pristine tree and passes with the fix, `ElegooPrinterEntity.device_info` reads the live version so a reload cannot write the stale one back, and the gate is green from the 750 baseline: `make format` no-op, `make lint` "All checks passed", `make test` 757 passed.
---

# T1 — #436 the device record's firmware version is frozen at setup

**Issue:** [#436](https://github.com/danielcherubini/elegoo-homeassistant/issues/436) — "My integration/device still
shows the firmware version as V1.1.29. I haven't run that version in a long time… If I run `uv run python debug.py`
then I see `"firmware": "V0.4.0-o"` which is correct. … the 'Firmware' configuration entity shows the correct version
so I can just ignore it."

**One task.** Make the *device record's* firmware version follow the printer. Two source edits
(`coordinator.py`, `entity.py`) plus one new test file. Nothing else changes — not `api.py`, not
`config_flow.py`, not `update.py`, not `entry.data`.

## Grounding (verified against the tree at `feature/triage-2026-10`, not asserted)

I have no subagent tool, so this was grounded inline: every path below was read, every signature was
grepped from the installed HA, and the **whole end-state was executed** in a throwaway copy of the tree
(`.venv` symlinked) — red/green in both directions, the mutation table, plus the full suite. The repo
tree was left pristine: `git status` shows only the untracked roadmap docs.

**Revision note (post-review).** A first review of this plan returned no blocking findings and
independently reproduced the central claim in a throwaway copy: with the registry-only fix, polling wrote
`V1.1.42` and then `async_get_or_create(**frozen device_info)` restored `V1.0.0`; with both edits it stays
`V1.1.42`. So **Edit 2 is confirmed necessary — keep it.** The review also caught pointer drift and two
of my "measured" notes that were wrong. Everything below was then re-derived from scratch: every line
citation re-opened against the tree and the installed HA, the harness re-measured variant by variant, and
every assertion in the test file checked against a mutated implementation (the mutation table). Two
changes came out of that beyond the pointer fixes: the "no reload" assertion needed a second, actually
falsifiable half, and the guard that the plan claimed the bus assertion pinned turned out not to be pinned
by it at all. Both are recorded where they were found.

### The value path, confirmed

- `custom_components/elegoo_printer/entity.py:36` — `device_info` is a `@property` on
  `ElegooPrinterEntity`; `:45` reads `device_firmware = config_data[CONF_FIRMWARE]`, passed at `:66` as
  `sw_version=device_firmware` inside the single `DeviceInfo(` at `:61`. `CONF_FIRMWARE = "firmware"`
  (`const.py:16`), `CONF_ID = "id"` (`:17`), `DOMAIN = "elegoo_printer"` (`const.py:6`).
- It is the **only** `DeviceInfo(` construction and the only `device_info` definition in the integration
  (`grep` over the package: one hit each; no `_attr_device_info` anywhere). `PLATFORMS`
  (`__init__.py:70-81`) is **10 platforms**, and 12 classes across those 10 modules subclass
  `ElegooPrinterEntity` directly — including `camera.py:250` `ElegooVideoStreamLifecycle(ElegooPrinterEntity)`,
  whose two subclasses `ElegooStreamCamera` (`camera.py:952`) and `ElegooMjpegCamera` (`camera.py:1111`)
  inherit `device_info` and call `ElegooPrinterEntity.__init__` at `camera.py:963` / `:1159`. One property
  governs every device record. (`camera.py:192` `ElegooCameraMjpeg` subclasses `CameraMjpeg`, not us — it
  is an ffmpeg wrapper, not an entity, and registers no device.)
- `entry.data["firmware"]` is written once at config-flow time from `Printer.to_dict()`
  (`sdcp/models/printer.py:325` `"firmware": self.firmware`, inside `to_dict` at `:314`).
- The live value exists: `Printer.sync_from_attributes` (`sdcp/models/printer.py:358`) assigns
  `self.firmware = attrs.firmware_version` at `:380-383`, reached from `sdcp/transport/base.py:384`
  `_apply_attributes` (the `sync_from_attributes` call is at `base.py:398`, guarded by `if self.printer:`
  at `:397`, shared by ws + MQTT) and
  `cc2/client.py:1353` `_handle_attributes` (call at `:1361`). `api.printer` is the object the transports
  mutate: `api.py:129` `self.printer = printer` is the only assignment in `__init__`, and
  `websocket/client.py:355` `connect_printer` re-points its own `self.printer` to that same instance at
  `:363`, so `_apply_attributes` — which mutates `self.printer` at `base.py:398` — moves the object
  `api.printer` points at. `update.py:88` (in `installed_version`, `:79-90`) already reads exactly this
  source (`runtime_data.api.printer.firmware or self._info.get("current_version")`) and the reporter
  confirms that entity shows the right version. Use the same source.
- **Do not use `coordinator.data.printer`.** Measured on the WS transport, with a mechanism this time: the
  ws client builds its *own* `Printer` in its `__init__` (`websocket/client.py:111-113`,
  `Printer.from_dict(dict(config))`) and `sdcp/transport/base.py:96` binds `PrinterData(printer=printer)`
  to *that* object. `connect_printer` later re-points only `client.printer` (`:363`), so
  `client.printer_data.printer` — which is what `api.printer_data` (`api.py:725`/`:746`) and therefore
  `coordinator.data` return — stays bound to the original. Measured: after that re-point, setting
  `api.printer.firmware = "V9.9.9"` leaves `printer_data.printer.firmware` at `"V1.0.0"`. The attributes
  frames mutate the object we want and never the one `coordinator.data` exposes.

### Two tree facts that govern the design (both re-verified here)

1. **HA reads `entity.device_info` exactly once.** `.venv/.../homeassistant/helpers/entity_platform.py:820`
   `if self.config_entry and (device_info := entity.device_info):` →
   `dev_reg.async_get(self.hass).async_get_or_create(...)` at `:822`. Grepping `\.device_info\b` over the
   installed HA's `helpers/` + `core.py` + `config_entries.py` finds that as the only read of the *entity*
   property (the other hit, `device_registry.py:182`, is a `DeviceEntry` attribute assignment and unrelated).
   So **making `device_info` return a fresher value does not, by itself, update the device record** — the
   registry must be written explicitly.
2. **Writing `entry.data` reloads the entry.** `__init__.py:552`
   `entry.async_on_unload(entry.add_update_listener(async_reload_entry))`, and `async_reload_entry`
   (`__init__.py:650`) calls `hass.config_entries.async_reload` at `:655`. In HA,
   `ConfigEntries._async_update_entry` (`config_entries.py:2345`) sets `changed = True` for a data change at
   the `if data is not UNDEFINED and entry.data != data:` comparison (`config_entries.py:2420`, `changed` at
   `:2421`) and then notifies listeners. A poll-driven `async_update_entry` for a firmware change would tear
   down and rebuild everything (printer reconnect, MQTT broker stop/start, entity re-registration) for a
   display field. Unacceptable churn — **do not call `api.py:1272`
   `ElegooPrinterApiClient._update_config_entry_if_needed` from the poll loop** (it reaches
   `hass.config_entries.async_update_entry` at `api.py:1321`). Its two call sites are both connection-time —
   `api.py:540` inside `async_create` (def `:265`) and `api.py:704` inside `reconnect()` (def `:600`) — and
   both run *before* the attributes frame that sets `printer.firmware` arrives, which is precisely why the
   bug survives.

### The precedent, and the registry API

- `__init__.py:759-772` (inside `async_migrate_entry`) already updates the device registry directly:
  `device_registry = dr.async_get(hass)` at `:759` through `device_registry.async_update_device(...)` at
  `:770-772`. `from homeassistant.helpers import device_registry as dr` is already imported at
  `__init__.py:28`.
- `DeviceRegistry.async_update_device(device_id, *, sw_version: str | None | UndefinedType = UNDEFINED, ...)`
  — `device_registry.py:913`, `sw_version` param at `:942`. It no-ops when the value is unchanged
  (`:1154 if value is not UNDEFINED and value != getattr(old, attr_name)` builds nothing, then `:1161-1162
  if not new_values: return old`), so a per-poll call costs nothing when nothing changed. Measured directly:
  calling it with the value the entry already holds returns the same object, with `async_schedule_save` and
  `hass.bus.async_fire_internal` both untouched. `async_get_device(identifiers=...)` (`:704`) returns `None`
  for an unknown device — no raise.
- `async_get_or_create` (`:756`) routes `sw_version` straight into `async_update_device` at
  `device_registry.py:903` (the call starts at `:885`) — this is why Edit 2 is load-bearing.
- **Trap avoided:** `async_update_device` raises `HomeAssistantError("A device must have at least one of
  identifiers or connections")` at `:991-994` when both `new_connections` and `new_identifiers` are falsy.
  They default to `UNDEFINED` (`helpers/typing.py:31 class UndefinedType(Enum)`, `:37 UNDEFINED = ...`),
  which is an `Enum` singleton and **truthy** — measured: `bool(UNDEFINED) is True`. So calling it with only
  `sw_version=` is safe. Do not pass `new_identifiers=set()`.

### Why the hook is the poll, and where exactly

`ElegooDataUpdateCoordinator._async_update_data` (`coordinator.py:119`) is the only place with both the live
printer and a per-poll cadence. The call goes **inside the success path**, after
`self._replay_cc2_print_status_transitions()` (`coordinator.py:171`) and before `self.online = True`
(`:173`). Being inside the `try` (opened at `:132`) means a failed poll never reaches it, so a powered-off
printer cannot wipe a good version — the #171 contract holds without extra code. `DataUpdateCoordinator.__init__`
sets `self.hass` (`update_coordinator.py:78`), so `self.hass` is available. `entry.runtime_data` is assigned
at `__init__.py:505`, *before* platform forwarding at `:551`, so `runtime_data.api` is set before any poll runs.

## The change

### Edit 1 — `custom_components/elegoo_printer/coordinator.py`

Imports. `:13` `from custom_components.elegoo_printer.const import CONF_HAS_CANVAS, LOGGER` becomes a
multi-line import, and `dr` joins the **existing** homeassistant block (ruff `I001` rejects a block of its
own — measured):

```python
import httpx
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.const import (
    CONF_HAS_CANVAS,
    CONF_ID,
    DOMAIN,
    LOGGER,
)
```

Call site, in the success path of `_async_update_data`:

```python
            self._replay_cc2_print_status_transitions()
            self._sync_device_firmware()

            self.online = True
```

New method, placed after `_async_update_data` and before `_log_failed_poll` (def at `coordinator.py:210`).
Note the repo's docstring style — summary on its own line (ruff `D213`, with `D212` ignored in
`pyproject.toml`):

```python
    def _sync_device_firmware(self) -> None:
        """
        Keep the device record's firmware version in step with the printer.

        Home Assistant reads ``entity.device_info`` once, when the entity is
        added, so a fresher value there never reaches the device record. The
        printer's live version (kept from its attributes frames) is written
        straight to the registry instead, and only when it differs: writing
        ``entry.data`` would fire the entry's update listener, which is wired
        to a full reload (#436).
        """
        entry = self.config_entry
        live = entry.runtime_data.api.printer.firmware
        identifier = entry.data.get(CONF_ID)
        if not live or not identifier:
            return

        registry = dr.async_get(self.hass)
        device_entry = registry.async_get_device(identifiers={(DOMAIN, identifier)})
        if device_entry is None or device_entry.sw_version == live:
            return

        LOGGER.info(
            "Updating firmware version for printer %s: '%s' -> '%s'",
            entry.title,
            device_entry.sw_version,
            live,
        )
        registry.async_update_device(device_entry.id, sw_version=live)
```

Guards, and why each one is load-bearing (each is falsified by exactly one mutation row in the table
further down):

- `not live` — `Printer.firmware` is `str | None` (`printer.py:154`) and is `None` until the printer answers;
  writing `None` would blank a good version. Measured: with this guard removed, the record's `sw_version`
  goes to `None`.
- `not identifier` — `entry.data.get(CONF_ID)` rather than `[CONF_ID]`, so a malformed entry cannot raise
  `KeyError` out of the poll. Anything escaping `_async_update_data` lands in HA's catch-all
  (`update_coordinator.py:448-451`, logged at `:451` as "Unexpected error fetching %s data"), which prints a
  traceback **every 2 s poll** — the exact noise class #171 exists to remove. `async_get_or_create` builds
  identifiers from the same `entry.data[CONF_ID]`, so this is the same key HA used.
- `device_entry is None` — offline setup (#171) can finish with no entity added yet, and
  `async_start_offline` (`coordinator.py:55`) does no refresh and no I/O at all (`self.data` is never
  assigned, so it stays `None` from `DataUpdateCoordinator.__init__`, `update_coordinator.py:99`); there may
  be no device record.
- `device_entry.sw_version == live` — a 2 s poll would otherwise call `async_update_device` ~43 000 times a
  day. HA's own no-op (`:1161`) already prevents the disk write (`async_schedule_save`, `:1188`) and the
  event, so this guard is about **our** work, not HA's: it skips the lookup follow-up, the `LOGGER.info`
  line, and the `verify_event_loop_thread` call. It is *not* what stops the log line firing 43 000 times —
  drop it and the INFO line prints on every poll, which is what the pin below actually catches.

### Edit 2 — `custom_components/elegoo_printer/entity.py:45`

```python
        # entry.data holds the version seen at setup; the printer's own value
        # is the live one and wins when it has reported it (#436).
        device_firmware = (
            self.coordinator.config_entry.runtime_data.api.printer.firmware
            or config_data[CONF_FIRMWARE]
        )
```

This is **not** the fix — edit 1 is. It is here because of a fact measured in the copy and confirmed by the
reviewer independently: a reload re-adds every entity, `entity_platform.py:820` re-runs
`async_get_or_create`, and that routes `sw_version` into `async_update_device` (`device_registry.py:903`).
Against a real registry, after the poll fix had written `V1.1.42`, re-running `get_or_create` with the frozen
`V1.0.0` **put V1.0.0 back**. Edit 2 closes that: with `device_info` reading the live value, the reload
re-writes the right one (verified: `sw_version` stays `V1.1.42`). It also costs nothing — the `or` fallback
keeps today's behaviour whenever the printer has not reported a version. Without it the fix silently
regresses on every reload, options save, or `update_ip` call, which is a bug of the same shape as #436.

## New test file — `custom_components/elegoo_printer/tests/test_device_firmware.py`

There is **no** existing test touching `device_info` / `sw_version` / `DeviceInfo` (grep over every test dir:
zero hits), so this file is the pin. Verbatim, and it is the file I ran — 7 tests, lint-clean under this
repo's `select = ["ALL"]`:

```python
"""Tests for the device record's firmware version (#436)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from homeassistant.helpers import device_registry as dr

from custom_components.elegoo_printer.const import DOMAIN
from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.entity import ElegooPrinterEntity
from custom_components.elegoo_printer.sdcp.models.printer import Printer, PrinterData

if TYPE_CHECKING:
    from types import SimpleNamespace

    import pytest
    from homeassistant.helpers.device_registry import DeviceRegistry

DEVICE_ID = "test_mainboard_id_12345"


def _registry(hass: MagicMock) -> DeviceRegistry:
    """Build a real device registry that the coordinator's ``dr.async_get`` finds."""
    hass.data = {}
    registry = dr.async_get(hass)
    registry.devices = dr.ActiveDeviceRegistryItems()
    registry.deleted_devices = dr.DeviceRegistryItems()
    return registry


def _device(registry: DeviceRegistry, *, sw_version: str | None) -> None:
    """Seed the device entry Home Assistant creates when an entity is added."""
    registry.devices["dev1"] = dr.DeviceEntry(
        id="dev1",
        identifiers={(DOMAIN, DEVICE_ID)},
        config_entries={"entry1"},
        config_entries_subentries={"entry1": {None}},
        sw_version=sw_version,
        name="Test Printer",
    )


def _coordinator(
    hass: MagicMock, entry: SimpleNamespace, firmware: str | None
) -> ElegooDataUpdateCoordinator:
    """Build a real coordinator whose api reports ``firmware`` as the live version."""
    entry.entry_id = "entry1"
    entry.async_on_unload = MagicMock()
    entry.pref_disable_polling = False
    printer = Printer.from_dict(entry.data)
    printer.firmware = firmware
    entry.runtime_data.api.printer = printer
    entry.runtime_data.api.async_get_printer_data.return_value = PrinterData(
        printer=printer
    )
    entry.runtime_data.api.async_get_firmware_update_info.return_value = None
    return ElegooDataUpdateCoordinator(hass, entry=entry)


async def test_poll_updates_the_device_firmware(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """The device record follows the printer's live version, no reload needed."""
    registry = _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.1.42")

    await coordinator.async_refresh()

    assert registry.devices["dev1"].sw_version == "V1.1.42"
    # The version stored at setup is what the entry still holds.
    assert entry.data["firmware"] == "V1.0.0"
    # Three routes to the forbidden reload, each pinned by what it touches.
    entry.runtime_data.api._update_config_entry_if_needed.assert_not_called()
    hass.config_entries.async_update_entry.assert_not_called()


async def test_unchanged_version_writes_nothing(
    hass: MagicMock,
    entry: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A poll that found no new version leaves the registry untouched."""
    registry = _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.0.0")

    with caplog.at_level("INFO"):
        await coordinator.async_refresh()

    assert registry.devices["dev1"].sw_version == "V1.0.0"
    # Home Assistant's own no-op makes the registry silent either way, so the
    # guard is pinned by the line the coordinator logs only when it decides to
    # write.
    writes = [
        r.getMessage() for r in caplog.records if "Updating firmware" in r.getMessage()
    ]
    assert writes == []


async def test_missing_device_entry_is_not_fatal(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """An empty registry (offline setup, no entity added yet) does not raise."""
    registry = _registry(hass)
    coordinator = _coordinator(hass, entry, "V1.1.42")

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert list(registry.devices.values()) == []


async def test_unknown_live_version_overwrites_nothing(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """A printer that has not reported a version clears nothing."""
    registry = _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, None)

    await coordinator.async_refresh()

    assert registry.devices["dev1"].sw_version == "V1.0.0"
    hass.bus.async_fire_internal.assert_not_called()


async def test_failed_poll_leaves_the_record_alone(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """A switched-off printer does not clear the version it last reported."""
    registry = _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.1.42")
    entry.runtime_data.api.is_connected = False

    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert registry.devices["dev1"].sw_version == "V1.0.0"


async def test_device_info_reports_the_live_version(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """``device_info`` reads the live version, so a reload re-writes the right one."""
    registry = _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.1.42")
    printer_entity = ElegooPrinterEntity(coordinator)
    registry.async_schedule_save = MagicMock()

    await coordinator.async_refresh()

    assert registry.devices["dev1"].sw_version == "V1.1.42"
    assert printer_entity.device_info["sw_version"] == "V1.1.42"
    # entry.data keeps the version taken at setup: writing it would reload the entry.
    assert entry.data["firmware"] == "V1.0.0"

    # A reload re-adds every entity, and HA re-runs async_get_or_create with
    # device_info: the record must come back holding the live version.
    registry.async_get_or_create(config_entry_id="entry1", **printer_entity.device_info)
    assert registry.devices["dev1"].sw_version == "V1.1.42"


async def test_entry_without_an_id_overwrites_nothing(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """An entry with no id has no device to find, and must not raise."""
    registry = _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.1.42")
    entry.data.pop("id")

    # Spied, not replaced: the registry stays real, the spy only records that
    # it was consulted.
    lookup = MagicMock(side_effect=registry.async_get_device)
    registry.async_get_device = lookup  # type: ignore[method-assign]

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert registry.devices["dev1"].sw_version == "V1.0.0"
    # With no id there is nothing to look up: the guard returns before the
    # registry is consulted at all.
    lookup.assert_not_called()
```

### Mutation table — proof that every assertion can go red

Each row is a one-line edit to the *implemented* tree in the copy, with the test file above unchanged.
This is the reason the assertions are shaped the way they are; do not "simplify" them without re-running
this table.

| Mutation | Result | Caught by |
| --- | --- | --- |
| *(none — correct fix)* | 7 passed | — |
| Pristine tree, test file only | 2 failed | the two done-when claims |
| Edit 1 only, `entity.py` pristine | 1 failed | Edit 2's necessity |
| Poll calls `api._update_config_entry_if_needed` | 1 failed | `..._update_config_entry_if_needed.assert_not_called()` |
| Poll calls `hass.config_entries.async_update_entry` (new dict) | 1 failed | `hass.config_entries...assert_not_called()` |
| Poll really writes `entry.data["firmware"]` | 2 failed | `entry.data["firmware"] == "V1.0.0"` |
| Drop `sw_version == live` from the second guard | 1 failed | the `caplog` assertion |
| Drop `not live` | 1 failed | `test_unknown_live_version_overwrites_nothing` |
| Drop `not identifier` | 1 failed | `lookup.assert_not_called()` |
| Drop `device_entry is None` | 1 failed | `test_missing_device_entry_is_not_fatal` |
| Wrong identifier key in the lookup | 2 failed | `test_poll_updates_the_device_firmware` + `test_device_info_reports_the_live_version` (both assert the write landed) |
| Move the call to the failure branch | 3 failed | `test_failed_poll_leaves_the_record_alone` (plus the two success-path tests, which then never reach the sync) |
| Revert Edit 2 | 1 failed | `test_device_info_reports_the_live_version` |

### Why the harness looks like this (each fact measured — do not "simplify" it)

- The repo has **no** `pytest-homeassistant-custom-component` (not in `.venv`); `conftest.py:139` `hass` is a
  `MagicMock` and `conftest.py:178` `entry` is a `SimpleNamespace`. So the tests drive the **real**
  coordinator through `async_refresh()` against a **real** `DeviceRegistry` — no HA core is mocked where it
  matters.
- `hass.data = {}` is required. `dr.async_get` is `@singleton(DATA_REGISTRY)` and does
  `if data_key not in hass.data`. Against the stock `MagicMock` that test is `False` (`MagicMock` supports
  `__contains__`), so `async_get` **stores into and returns `hass.data[...]`, a MagicMock** — measured:
  `type(dr.async_get(MagicMock())).__name__ == 'MagicMock'`. Measured the consequence too: with that line
  removed the two "it wrote" tests fail, i.e. a harness without it silently asserts against a mock. With
  `hass.data = {}` it returns a real `DeviceRegistry`.
- `registry.devices` / `registry.deleted_devices` must be set by hand: they are only assigned by `async_load`
  (`device_registry.py:1345-1346`), and `async_load()` against a mock hass raises
  `TypeError: object MagicMock can't be used in 'await' expression` (measured).
  **Correction to the first revision of this plan**, which justified the `ActiveDeviceRegistryItems` choice
  badly: I claimed plain `DeviceRegistryItems` was impossible because it lacks
  `get_devices_for_config_entry_id`. Measured, that is wrong — that method is reached only from
  `async_clear_config_entry` (`:1363`), `async_clear_config_subentry` (`:1399`) and module-level
  `async_entries_for_config_entry` (`:1502`), and **this plan's code path reaches none of them**: the fix
  goes through `async_get_device` (`:704` → `devices.get_entry`) and `async_update_device` (`:913`), and the
  reload simulation goes through `async_get_or_create` (`:756`). Swapping in the base class, all 7 tests
  still pass. `ActiveDeviceRegistryItems` stays for **type fidelity**, not necessity: `devices` is declared
  `ActiveDeviceRegistryItems` (`:679`) and `async_load` builds exactly that (`:1278`). Use it so the harness
  matches production, but do not repeat the old justification — it does not hold, and a wrong "measured"
  note is worse than none.
- `registry.deleted_devices` **is** load-bearing, for a different reason than the above: `async_update_device`
  calls `_async_get_deleted_devices` (`:1175` → `:726`), so without it the write raises
  `AttributeError: 'DeviceRegistry' object has no attribute 'deleted_devices'` (measured, and it is swallowed
  into a failed poll — the test that keeps it goes red as "poll failed", not as an AttributeError).
- `registry.async_schedule_save = MagicMock()` appears in one test only, and only because that test performs
  a **second** registry write: the real `async_schedule_save` reaches `hass.loop.call_later(...).when()` and
  compares two MagicMocks (`TypeError: '<' not supported between instances of 'MagicMock' and 'MagicMock'`,
  measured, raised from `storage.py:453`). The other tests write at most once, so they need no stub.
- **Two "nothing happened" assertions, because they pin different things.**
  1. `hass.bus.async_fire_internal.assert_not_called()` (in
     `test_unknown_live_version_overwrites_nothing`) pins the `not live` guard. `sw_version` is not in
     `RUNTIME_ONLY_ATTRS` (`device_registry.py:70`), so a real write *does* fire `device_registry_updated`
     (`:1196`) — measured: dropping `not live` makes this assertion fail with the event's `changes` showing
     `{'sw_version': 'V1.0.0'}`.
  2. The `caplog` assertion (in `test_unchanged_version_writes_nothing`) pins the `sw_version == live` guard,
     and **a bus assertion cannot**: HA's own no-op (`:1161-1162`) returns before the save and the event, so
     removing our guard leaves the bus silent. Measured — with the guard deleted, the bus assertion still
     passes while the coordinator logs its INFO line. So the guard is pinned by the line only *our* code
     emits. (The first revision of this plan claimed the bus assertion made that guard "genuinely
     observable"; it does not, and the claim was wrong.)
- `test_poll_updates_the_device_firmware` pins the "no reload" half of the done-when with **two** assertions,
  because there are two reachable routes and each is invisible to the other:
  - `entry.runtime_data.api._update_config_entry_if_needed.assert_not_called()` — the obvious-but-forbidden
    move is to call the existing connection-time helper from the poll. That helper reaches HA's
    `async_update_entry` through `self.hass` *inside the api object*, which this harness never asserts on, so
    the bus/`hass` assertion below stays green against it — measured: mutating the poll to call that helper
    passes the `hass.config_entries...` assertion and fails only this one. `entry.runtime_data.api` is a real
    `MagicMock` from `conftest.py`'s `_build_api_mock`, so this is a live collaborator, not an
    auto-attribute.
  - `hass.config_entries.async_update_entry.assert_not_called()` — pins the direct route. `self.hass` *is*
    the same mock the test holds, so this is falsifiable, not decorative: mutating the poll to call
    `self.hass.config_entries.async_update_entry(...)` makes it fail with `Called 1 times`.
  Do not drop either half. Note the honest limit: neither one proves HA would reload — that is a
  `config_entries` behaviour the plan argues from source (tree fact 2) and the tests only pin that we never
  enter it.
- `test_entry_without_an_id_overwrites_nothing` spies on `async_get_device` rather than only checking the
  record is unchanged. Without the spy, dropping the `not identifier` guard leaves all 7 tests green —
  measured — because `identifiers={(DOMAIN, None)}` simply finds nothing and the "nothing was written"
  assertion passes for the wrong reason. The spy asserts the guard returns *before* the registry is
  consulted. It is a `side_effect` on the real method, so the registry stays real.
- `entry.data` is the `sample_printer.to_dict()` dict from `conftest.py`, carrying
  `"FirmwareVersion": "V1.0.0"` → `data["firmware"] == "V1.0.0"` and
  `data["id"] == "test_mainboard_id_12345"`, which is why `DEVICE_ID` matches the seeded identifiers.

## TDD order and exact commands

```bash
cd /home/daniel/Coding/HomeAssistant/elegoo-homeassistant

# 1. Write ONLY the test file, then prove it is RED on the pristine tree.
VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_device_firmware.py -q
#   expect: 2 failed (test_poll_updates_the_device_firmware,
#                    test_device_info_reports_the_live_version), 5 passed
#   measured failure: AssertionError: assert 'V1.0.0' == 'V1.1.42'

# 2. Edit 1 (coordinator). Re-run: the poll test goes green, the device_info one stays red.
VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_device_firmware.py -q
#   expect: 1 failed, 6 passed

# 3. Edit 2 (entity). All green.
VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_device_firmware.py -q
#   expect: 7 passed

# 4. Gate (AGENTS.md requires all three), from the repo root.
make format   # expect: no reformat of the touched files
make lint     # expect: All checks passed!
make test     # expect: 757 passed, 12 warnings   (baseline 750 + 7 new)

# 5. Changelog: one line under ## [Unreleased] → ### Fixed (create the section if absent), e.g.
#    "The device's Firmware version now follows the printer instead of staying at whatever it was
#     when the integration was set up; no reload needed. Reported by @… in #436."
```

Then re-run the mutation table (one-line edits to `coordinator.py` / `entity.py`, test file unchanged) and
confirm every row still goes red. Measured in the copy: step 1 `2 failed, 5 passed`; step 2
`1 failed, 6 passed`; step 3 `7 passed`; step 4 `757 passed`, `ruff check` "All checks passed",
`ruff format --check` "124 files already formatted". The test file as pasted above is already
format-stable and lint-clean — measured: `ruff format --check` reports "1 file already formatted" and
`ruff check` "All checks passed" on it verbatim, so `make format` should be a no-op. `SLF001` does **not**
fire on the private-member access (`_update_config_entry_if_needed`) in the test — measured, `--select
SLF001` on the file is clean, so no `noqa` is needed and adding one would trip `RUF100`.

## Deliberately NOT doing

- **Not calling `api._update_config_entry_if_needed` from the poll.** It is the obvious move and it is
  wrong: it writes `entry.data`, which fires the update listener at `__init__.py:552`, which reloads the
  entry (tree fact 2). Pinned by an assertion, per the mutation table.
- **Not touching `entry.data` at all.** `entry.data["firmware"]` keeps its at-setup meaning — pinned by two
  tests. See the limit note.
- **Not changing `update.py`** — the reporter's working 'Firmware' entity is already correct and is the
  source this fix reads from.
- **Not wrapping the registry call in `try/except`.** The guards make the realistic raises impossible, and a
  broad `except` would hide a genuine registry bug. The one residual raise is recorded below.
- **Not syncing `name` / `model` / `brand`.** `sync_from_attributes` can move those too
  (`printer.py:358` copies firmware, model, name and brand), but #436 is about firmware, and every extra
  field widens the blast radius of a per-poll write. Recorded as a follow-up.

## Limit notes (recorded, not silently paid)

- **`entry.data["firmware"]` stays the at-setup value.** Keeping it honest needs
  `hass.config_entries.async_update_entry`, which reloads the entry — a full unload/setup, printer
  reconnect, MQTT broker stop/start and entity re-registration, every time the printer's version changes.
  I found no cheap way to write `entry.data` without that reload: `add_update_listener`
  (`config_entries.py:1115`) is the only hook and this entry's listener is already the reload
  (`__init__.py:552`). It is acceptable because nothing user-visible reads that key any more: `entity.py:45`
  (this task) and `update.py:88` both read the live value, and grepping `CONF_FIRMWARE` / `"firmware"`
  outside `sdcp/models/printer.py` finds only `api.py:1296` (a `fields_to_check` entry inside
  `_update_config_entry_if_needed`, i.e. the comparison, not a consumer) and `websocket/server/discovery.py:118`
  (a `getattr(printer, "firmware", "V1.0.0")` default on the *Printer object*, not on `entry.data`). The cost
  of fixing it properly is a reload-on-firmware-change, or restructuring the entry's update listener for one
  display field; out of scope for a one-task plan.
- **Residual raise:** if `entry.runtime_data.api` or its `.printer` were ever missing,
  `_sync_device_firmware` would raise and HA would log "Unexpected error fetching … data" every 2 s. That
  risk is **not new**: `_async_update_data` already dereferences `self.config_entry.runtime_data.api`
  unconditionally as its first statement inside the same `try` (`coordinator.py:133`), and
  `_replay_cc2_print_status_transitions` — the line immediately before the new call — dereferences
  `runtime_data.api.printer` too (`coordinator.py:281-282`). The new code therefore adds no failure mode that
  the poll does not already carry, so I left it un-guarded rather than adding a `try` that would also swallow
  real registry bugs. **UNVERIFIED:** I did not exercise the true `async_setup_entry` path in a test — this
  repo has no `pytest-homeassistant-custom-component`, so no fixture can build a real `ConfigEntry`.
- **Test fidelity:** the registry is real, but `hass` is still a `MagicMock` and `entry` a
  `SimpleNamespace`, so no test drives `entity_platform.py:820` itself. The "reload re-writes the right
  value" claim is pinned by calling `async_get_or_create(**device_info)` directly, which is the same call HA
  makes with the same kwargs (`config_entry_id` + `**device_info`). **UNVERIFIED:** the end-to-end reload path
  (a real reload through a real HA) was not exercised for that reason.
- **Proxy mode:** `api.py:1093` (in `_setup_proxy_if_enabled`, def `:1054`) replaces `api.printer` with the
  object returned by `self.server.get_printer(specific_printer=printer)` (`:1086`). What that returns is
  `websocket/server/proxy.py:169` `get_printer`: the primary branch (`:183-188`) looks the printer up **by IP
  address** in the registry (`registry.py:56` `get_printer_by_ip`, keyed at `registry.py:47`) and returns that
  registered object; if that lookup misses, the fallback at `:191-193` returns
  `next(iter(printers.values()))` — **an arbitrary registered printer, not necessarily this one**. So the
  honest statement is: in the normal single-printer-per-IP case the proxy object is the one registered from
  this entry's printer and the device lookup lands, but the code does not *guarantee* the returned object
  carries this entry's `id`, and `registry.py:64` `get_printer_by_mainboard_id` — which would match on `id` —
  is never consulted by `get_printer`. **UNVERIFIED:** not exercised against a live proxy. The conclusion
  does not depend on it: if the object ever carried a different `id`, the lookup finds no device and nothing
  is written (fail-safe, not destructive). Note this is the one case where a wrong `id` could in principle
  point at *another* entry's device — recorded, not fixed, since both entries would be reporting their own
  printers' firmware to the same registry key and #436's fail-safe still holds for the entry whose device is
  not found.

## Parallelisation

**SERIAL.** Single task, single commit.

**True seam (correcting the status doc):** this plan edits **`coordinator.py` and `entity.py`** and adds
**`custom_components/elegoo_printer/tests/test_device_firmware.py`** — three files, two of them existing
modules. `docs/roadmap/triage-2026-10-status.md:76` describes T1's seam as
"`entity.py` (owned outright); reads coordinator data, edits no other module", which understates it: the fix
is *in* `coordinator.py` and `entity.py` alone cannot work (tree fact 1). The orchestrator should correct the
row when it marks it, e.g. "`coordinator.py` + `entity.py` + new `tests/test_device_firmware.py`".

Still disjoint from the wave-1 siblings. T2's seam is `__init__.py` + `tests/test_services.py`
(`triage-2026-10-status.md:77`, restated at `triage-2026-10-task02-update-ip-options.md:134`) and T3's is
`cc2/client.py` + `definitions.py` + a new test file (`:78`, corrected in T3's own plan at
`triage-2026-10-task03-tray-selection.md:322` and `:331`). Neither edits `coordinator.py` or `entity.py`:
T2 mentions `coordinator.py:38` only as a grounding *read* of `merged_config`
(`triage-2026-10-task02-update-ip-options.md:25`), and T3 already states the correct T1 seam —
"T1 owns `entity.py` + `coordinator.py` … disjoint" (`triage-2026-10-task03-tray-selection.md:128`, `:337-338`)
— so this plan and T3 agree, and only the parent status row lags. No other wave task lists
`coordinator.py` or `entity.py` in its seam (`:79-81` — T4 takes `camera.py`/`__init__.py`/`data.py`, T5
`cc2/client.py` + config, T6 `config_flow.py`/`api.py`), and the earlier `coordinator.py` change the status
doc records as shipped (#419, `:52`) is already merged, so it is not a live conflict.

## Other notes for the orchestrator

- `parent: docs/roadmap/triage-2026-10.md` follows the sibling plans, but **that file does not exist** in the
  tree (`ls docs/roadmap/` shows only `triage-2026-10-status.md` and the task plans). Kept as the convention
  dictates; flagging it rather than inventing a different parent.
- Commit the plan doc and the implementation as **separate commits** (the loop protocol has the orchestrator
  commit the plan).
- One `LOGGER.info` line per actual version change is the only log this adds; nothing is logged when nothing
  changed. That line is also the thing `test_unchanged_version_writes_nothing` asserts on, so if a future
  edit renames it, keep the substring `"Updating firmware"` or update the assertion in the same commit.
