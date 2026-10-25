"""Tests for the device record's firmware version (#436)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from custom_components.elegoo_printer.const import DOMAIN
from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.entity import ElegooPrinterEntity
from custom_components.elegoo_printer.sdcp.models.printer import Printer, PrinterData

if TYPE_CHECKING:
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
    """
    registry = _registry(hass)
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
        assert registry.devices["dev1"].sw_version == "V1.0.0", raised


async def test_a_refused_registry_write_is_retried_next_poll(
    hass: MagicMock, entry: SimpleNamespace
) -> None:
    """Catching the raise keeps the sync alive: the next poll writes for real."""
    registry = _registry(hass)
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
    assert registry.devices["dev1"].sw_version == "V1.0.0"

    # Catching the raise cost nothing beyond the one write: the guard logged and
    # returned, it did not disarm the sync. ``_sync_device_firmware`` runs on
    # every poll (the 12 h gate belongs to the update-info request above it, not
    # to this write), so the very next poll looks the device up again and lands.
    await coordinator.async_refresh()
    assert registry.devices["dev1"].sw_version == "V1.1.42"
    assert failures == []


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
    registry = _registry(hass)
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
    _registry(hass)
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
