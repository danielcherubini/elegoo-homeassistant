"""Tests for the device record's firmware version (#436)."""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import frame as frame_helper
from homeassistant.loader import DATA_INTEGRATIONS

from custom_components.elegoo_printer.const import DOMAIN
from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.entity import ElegooPrinterEntity
from custom_components.elegoo_printer.sdcp.models.printer import Printer, PrinterData

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceRegistry

DEVICE_ID = "test_mainboard_id_12345"
ENTRY_ID = "entry1"


@pytest.fixture(autouse=True)
def _frame_helper(hass: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Point core's frame helper at this test's hass, then put it back.

    The coordinator's ``registry.async_get_device`` call is reported as
    deprecated usage on Home Assistant 2026.10, and ``report_usage`` refuses to
    run at all until the frame helper is set up — it raises ``RuntimeError
    ("Frame helper not set up")``. With the helper set up the report resolves to
    a WARNING instead, because the offending frame is in ``custom_components/``,
    so the deprecated call stays visible in the log rather than being silenced.

    The helper holds its hass in a module-level container, so this is patched
    rather than assigned: leaving a test's mock behind would point every later
    test's reporting at a hass that no longer exists. ``async_setup`` is the only
    public way in and has no counterpart to undo it, so the container itself is
    the only thing that can restore the previous value — the one private touch
    here, and the reason it is patched instead of set.
    """
    monkeypatch.setattr(frame_helper._hass, "hass", hass)


async def _registry(hass: MagicMock) -> DeviceRegistry:
    """
    Build a real device registry that the coordinator's ``dr.async_get`` finds.

    Everything here is public API, and the two supported Home Assistant versions
    differ in every step of getting to a usable registry:

    * ``dr.async_get`` is ``@singleton(DATA_REGISTRY)`` on 2025.4 and lazily
      constructs, so it works on an empty ``hass.data``; on 2026.10 it is strict
      and raises ``RuntimeError("Device registry not set up")`` unless something
      put the registry there first. ``dr.async_setup`` (2026.10 only) is what
      puts it there and refuses to run twice, so it is reached through the
      ``async_get`` failure rather than probed for: the first call on a fresh
      ``hass.data`` sets it up, every later call finds it.
    * The registry's collections do not exist until it has loaded — on 2025.4
      ``async_load`` is what assigns ``devices``/``deleted_devices``, on 2026.10
      the same for ``_devices``/``_child_devices``. Loading is therefore the
      public route to a registry that can hold a device, and it replaces
      injecting hand-built collections, which 2026.10 no longer has under those
      names.
    * ``async_load`` reads its store through ``hass.async_add_executor_job``;
      ``{}`` is what ``Store._async_load_data`` treats as "nothing stored", so
      the registry starts empty without touching the filesystem.
    * Every write ends in ``async_schedule_save``, whose delay bookkeeping
      compares ``hass.loop.time() + delay`` against the previous timer's
      ``.when()``. Those are floats here; left as MagicMocks the comparison
      raises ``TypeError`` on the second write. The timer is a mock, so no write
      is ever performed.
    * ``hass.loop_thread_id`` is checked by ``verify_event_loop_thread`` on every
      registry write, and by the frame helper's reporting path; the reporting
      path's own lookup walks ``hass.data[DATA_INTEGRATIONS]``, hence the seeded
      key. See the ``_frame_helper`` fixture for the first half of that.
    """
    hass.data = {DATA_INTEGRATIONS: {}}
    hass.loop_thread_id = threading.get_ident()
    hass.async_add_executor_job = AsyncMock(return_value={})
    hass.loop.time.return_value = 0.0
    hass.loop.call_at.return_value.when.return_value = 0.0

    try:
        registry = dr.async_get(hass)
    except RuntimeError:  # 2026.10: nothing set up yet, and it will not guess.
        dr.async_setup(hass)
        registry = dr.async_get(hass)

    await registry.async_load()
    return registry


def _device(registry: DeviceRegistry, *, sw_version: str | None) -> None:
    """
    Seed the device entry Home Assistant creates when an entity is added.

    ``async_get_or_create`` is the call core itself makes from ``device_info``,
    so the seeded record is one the registry built rather than one inserted into
    it: it gets a real id, and its config entry link through the API that owns
    the link. The entry it points at has to resolve, since the method refuses an
    unknown ``config_entry_id``.
    """
    config_entry = MagicMock()
    config_entry.entry_id = ENTRY_ID
    config_entry.domain = DOMAIN
    config_entry.title = "Test Printer"
    registry.hass.config_entries.async_get_entry.return_value = config_entry
    registry.async_get_or_create(
        config_entry_id=ENTRY_ID,
        identifiers={(DOMAIN, DEVICE_ID)},
        name="Test Printer",
        sw_version=sw_version,
    )


def _stored_version(registry: DeviceRegistry) -> str | None:
    """Read the seeded device's version back out of the registry."""
    devices = dr.async_entries_for_config_entry(registry, ENTRY_ID)
    assert len(devices) == 1, devices
    return devices[0].sw_version


def _coordinator(
    hass: MagicMock, entry: SimpleNamespace, firmware: str | None
) -> ElegooDataUpdateCoordinator:
    """Build a real coordinator whose api reports ``firmware`` as the live version."""
    entry.entry_id = ENTRY_ID
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
    registry = await _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.1.42")

    await coordinator.async_refresh()

    assert _stored_version(registry) == "V1.1.42"
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
    registry = await _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.0.0")

    with caplog.at_level("INFO"):
        await coordinator.async_refresh()

    assert _stored_version(registry) == "V1.0.0"
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
    registry = await _registry(hass)
    coordinator = _coordinator(hass, entry, "V1.1.42")

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert dr.async_entries_for_config_entry(registry, ENTRY_ID) == []


async def test_unknown_live_version_overwrites_nothing(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """A printer that has not reported a version clears nothing."""
    registry = await _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, None)
    # Seeding the device fires the registry's own "create" event, which is core's
    # doing and not this poll's; reset so the assertion below reads only the poll.
    hass.bus.async_fire_internal.reset_mock()

    await coordinator.async_refresh()

    assert _stored_version(registry) == "V1.0.0"
    hass.bus.async_fire_internal.assert_not_called()


async def test_failed_poll_leaves_the_record_alone(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """A switched-off printer does not clear the version it last reported."""
    registry = await _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.1.42")
    entry.runtime_data.api.is_connected = False

    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert _stored_version(registry) == "V1.0.0"


async def test_device_info_reports_the_live_version(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """``device_info`` reads the live version, so a reload re-writes the right one."""
    registry = await _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.1.42")
    printer_entity = ElegooPrinterEntity(coordinator)

    await coordinator.async_refresh()

    assert _stored_version(registry) == "V1.1.42"
    assert printer_entity.device_info["sw_version"] == "V1.1.42"
    # entry.data keeps the version taken at setup: writing it would reload the entry.
    assert entry.data["firmware"] == "V1.0.0"

    # A reload re-adds every entity, and HA re-runs async_get_or_create with
    # device_info: the record must come back holding the live version.
    registry.async_get_or_create(config_entry_id=ENTRY_ID, **printer_entity.device_info)
    assert _stored_version(registry) == "V1.1.42"


async def test_entry_without_an_id_overwrites_nothing(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """An entry with no id has no device to find, and must not raise."""
    registry = await _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.1.42")
    entry.data.pop("id")

    # Spied, not replaced: the registry stays real, the spy only records that
    # it was consulted.
    lookup = MagicMock(side_effect=registry.async_get_device)
    registry.async_get_device = lookup  # type: ignore[method-assign]

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert _stored_version(registry) == "V1.0.0"
    # With no id there is nothing to look up: the guard returns before the
    # registry is consulted at all.
    lookup.assert_not_called()


async def test_a_device_that_vanishes_mid_write_fails_no_poll(
    hass: MagicMock,
    entry: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A registry that refuses the write costs a debug line, not a failed poll.

    The device can go between the lookup and the write -- a user deleting it, an
    entry unload, another integration pruning it -- and ``async_update_device``
    then raises: ``KeyError`` out of its own ``self.devices[device_id]`` lookup,
    ``HomeAssistantError`` from its collision checks. Both escape into
    ``_async_update_data``, where core's catch-all turns any exception into
    "Unexpected error fetching <printer> data" WITH A TRACEBACK on every 2 s
    poll, forever. The net is asserted per exception type, since a bare
    ``except Exception`` would satisfy the shape while hiding which two core
    actually raises.

    The refusal is faked at the write rather than produced by really removing
    the device first, and that is the only way to express the race: the sync
    looks the device up on every poll, so a device that was genuinely gone
    before the poll starts is caught by the lookup and never reaches the write.
    Only a removal that lands in between leaves a lookup that succeeds and a
    write that does not, so the write is where the refusal has to be made to
    happen.
    """
    registry = await _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.1.42")

    for raised in (
        HomeAssistantError("Device id dev1 does not exist"),
        KeyError("dev1"),
    ):
        registry.async_update_device = MagicMock(side_effect=raised)  # type: ignore[method-assign]

        with caplog.at_level("DEBUG"):
            caplog.clear()
            await coordinator.async_refresh()

        # The poll itself succeeded: the printer answered, and a device record
        # nobody can find is not a communication failure.
        assert coordinator.last_update_success is True, raised
        # Core's catch-all is the thing being prevented, pinned by its own text
        # rather than by a record count that other logging could satisfy.
        unexpected = [
            r for r in caplog.records if "Unexpected error fetching" in r.getMessage()
        ]
        assert unexpected == [], raised
        # The refusal is visible to anyone who turns debug on, once, at debug.
        notes = [
            r
            for r in caplog.records
            if "Could not write firmware version" in r.getMessage()
        ]
        assert len(notes) == 1, raised
        assert notes[0].levelno == logging.DEBUG, raised
        # And it is not announced as done: the write never landed.
        assert [
            r.getMessage()
            for r in caplog.records
            if "Updating firmware" in r.getMessage()
        ] == [], raised
        assert _stored_version(registry) == "V1.0.0", raised


async def test_a_refused_registry_write_is_retried_next_poll(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """Catching the raise keeps the sync alive: the next poll writes for real."""
    registry = await _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.1.42")

    real = registry.async_update_device
    failures = [HomeAssistantError("gone"), None]

    def flaky(device_id: str, **kwargs: object) -> object:
        """Fail the first write only, then behave like the real registry."""
        failure = failures.pop(0)
        if failure is not None:
            raise failure
        return real(device_id, **kwargs)

    registry.async_update_device = flaky  # type: ignore[method-assign]

    await coordinator.async_refresh()
    assert _stored_version(registry) == "V1.0.0"

    # Catching the raise cost nothing beyond the one write: the guard logged and
    # returned, it did not disarm the sync. ``_sync_device_firmware`` runs on
    # every poll (the 12 h gate belongs to the update-info request above it, not
    # to this write), so the very next poll looks the device up again and lands.
    await coordinator.async_refresh()
    assert _stored_version(registry) == "V1.1.42"
    assert failures == []


async def test_a_registry_that_was_never_set_up_fails_no_poll(
    hass: MagicMock,
    entry: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A registry core never set up costs a debug line, not a failed poll.

    ``dr.async_get`` is the one call in ``_sync_device_firmware`` outside the
    write's own guard. On 2025.4 it cannot fail — the ``@singleton`` decorator
    builds a registry on demand — so the guard below it has never had to cover
    it. On 2026.10 core made it strict: it raises ``RuntimeError("Device
    registry not set up")`` rather than inventing one, which is the same class of
    "the registry is not there for me right now" the write guard answers, and
    reaches the same core catch-all that prints a traceback every 2 s.

    This pins the net without reaching for the exception type by name: the
    registry is set up exactly as the other tests set it up, and then removed
    again, which is what a registry that was never set up looks like to the
    coordinator. On 2026.10 that leaves `async_get` with nothing to return; on
    2025.4 the ``@singleton`` cache hands back the same registry regardless, so
    the removal is invisible and the poll simply writes as usual. Only the
    two assertions below hold on both, and only 2026.10 exercises the net —
    which is the point: the guard is there for a failure the pinned version
    cannot produce.
    """
    registry = await _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.1.42")
    for key in list(registry.hass.data):
        if registry.hass.data[key] is registry:
            del registry.hass.data[key]

    with caplog.at_level("DEBUG"):
        await coordinator.async_refresh()

    # The printer answered, and a registry the integration cannot reach is not a
    # communication failure either.
    assert coordinator.last_update_success is True
    assert [
        r for r in caplog.records if "Unexpected error fetching" in r.getMessage()
    ] == []
    # And the refusal is never silent: whichever way the poll ends, a firmware
    # version it did not write is one it said it could not write. On 2026.10 the
    # registry is unreachable so the refusal is the branch taken; on 2025.4 the
    # write lands and no refusal is owed. Exactly one of the two, always.
    written = [
        r.getMessage() for r in caplog.records if "Updating firmware" in r.getMessage()
    ]
    refused = [r for r in caplog.records if "Could not" in r.getMessage()]
    assert bool(written) != bool(refused), (written, refused)
    if refused:
        assert len(refused) == 1
        assert refused[0].levelno == logging.DEBUG


async def test_device_info_survives_the_runtime_data_core_deletes(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """
    After an unload drops runtime_data, ``device_info`` still answers.

    Core runs ``object.__delattr__(self, "runtime_data")`` on a successful
    unload, and ``device_info`` is a plain property — so a dereference chain
    through it is a data-only read that raises AttributeError for any caller
    that asks after the entry is gone, and the documented fallback to
    ``entry.data`` never gets its turn. As shipped the only core caller sits
    inside the entry's lifetime, which is exactly the kind of near-miss that
    outlives a refactor: the property is public, so pin the answer rather than
    the absence of a caller.
    """
    registry = await _registry(hass)
    _device(registry, sw_version="V1.0.0")
    coordinator = _coordinator(hass, entry, "V1.1.42")
    printer_entity = ElegooPrinterEntity(coordinator)
    assert printer_entity.device_info["sw_version"] == "V1.1.42"

    # What core does on unload: the attribute is removed, not set to None.
    del entry.runtime_data

    info = printer_entity.device_info
    assert info["sw_version"] == "V1.0.0"
    # Everything the record needs is still there — a degraded firmware field
    # must not cost the identity, or a reload would re-create the device.
    assert info["identifiers"] == {(DOMAIN, DEVICE_ID)}
    assert info["name"] == entry.data["name"]
    assert info["manufacturer"] == entry.data["brand"]


async def test_device_info_falls_back_for_a_partial_runtime_data(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """A runtime data missing ``api``, or an api missing ``printer``, falls back."""
    await _registry(hass)
    coordinator = _coordinator(hass, entry, "V1.1.42")
    printer_entity = ElegooPrinterEntity(coordinator)

    # Each link of the chain in turn: an entry whose runtime data has no api
    # (setup failed partway), an api that never built its printer.
    entry.runtime_data = SimpleNamespace()
    assert printer_entity.device_info["sw_version"] == entry.data["firmware"]
    entry.runtime_data = SimpleNamespace(api=SimpleNamespace())
    assert printer_entity.device_info["sw_version"] == entry.data["firmware"]
    # A printer that has not reported a version yet also falls back — the
    # original ``or`` behaviour, which the chain must not replace.
    entry.runtime_data = SimpleNamespace(api=SimpleNamespace(printer=Printer()))
    assert printer_entity.device_info["sw_version"] == entry.data["firmware"]
