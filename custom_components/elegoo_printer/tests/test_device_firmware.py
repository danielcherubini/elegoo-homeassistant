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
