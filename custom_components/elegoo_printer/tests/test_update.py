"""Tests for the firmware update entity."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from custom_components.elegoo_printer import update
from custom_components.elegoo_printer.api import ElegooPrinterApiClient
from custom_components.elegoo_printer.sdcp.models.enums import ProtocolVersion
from custom_components.elegoo_printer.sdcp.models.printer import (
    Printer,
    PrinterData,
)

if TYPE_CHECKING:
    from types import SimpleNamespace
    from unittest.mock import MagicMock


def _entity(
    entry: SimpleNamespace, firmware_update_info: dict
) -> update.ElegooPrinterFirmwareUpdate:
    """Build the entity on the conftest coordinator double with the given info."""
    printer = Printer.from_dict(entry.data)
    # The live printer normally runs what the last firmware check saw.
    printer.firmware = firmware_update_info.get("current_version", printer.firmware)
    entry.runtime_data.api.printer = printer
    coordinator = entry.runtime_data.coordinator
    coordinator.data = PrinterData(printer=printer)
    coordinator.data.firmware_update_info = firmware_update_info
    return update.ElegooPrinterFirmwareUpdate(
        coordinator, update.PRINTER_FIRMWARE_UPDATE
    )


async def test_update_available(entry: SimpleNamespace) -> None:
    """Elegoo's update flag turns the entity on with the server's version."""
    entity = _entity(
        entry,
        {
            "update_available": True,
            "current_version": "V1.1.40",
            "latest_version": "1.4.46",
            "changelog": "Fixes",
        },
    )

    assert entity.installed_version == "V1.1.40"
    assert entity.latest_version == "1.4.46"
    assert entity.state == "on"
    assert await entity.async_release_notes() == "Fixes"


async def test_no_update(entry: SimpleNamespace) -> None:
    """Without an update the latest version is the installed one."""
    entity = _entity(
        entry,
        {
            "update_available": False,
            "current_version": "V1.1.40",
            "latest_version": None,
            "changelog": None,
        },
    )

    assert entity.latest_version == "V1.1.40"
    assert entity.state == "off"


async def test_installed_version_prefers_live_printer(
    entry: SimpleNamespace,
) -> None:
    """The printer's live version beats the 12-hourly snapshot."""
    entity = _entity(entry, {"update_available": False, "current_version": "V0.9"})
    entry.runtime_data.api.printer.firmware = "V1.1.42"

    assert entity.installed_version == "V1.1.42"


async def test_installed_version_falls_back_to_snapshot(
    entry: SimpleNamespace,
) -> None:
    """Without a live version, the firmware check's version is used."""
    entity = _entity(entry, {"update_available": False, "current_version": "V1.1.40"})
    entry.runtime_data.api.printer.firmware = None

    assert entity.installed_version == "V1.1.40"
    assert entity.state == "off"


async def test_off_right_after_updating(entry: SimpleNamespace) -> None:
    """A stale update flag doesn't keep the entity on once the printer runs it."""
    entity = _entity(
        entry,
        {
            "update_available": True,
            "current_version": "V1.1.40",
            "latest_version": "1.1.42",
        },
    )
    entry.runtime_data.api.printer.firmware = "V1.1.42"

    assert entity.state == "off"


async def test_unknown_when_flag_has_no_version(entry: SimpleNamespace) -> None:
    """An update flag without a version is unknown, not silently off."""
    entity = _entity(
        entry,
        {
            "update_available": True,
            "current_version": "V1.1.40",
            "latest_version": None,
        },
    )

    assert entity.state is None


async def test_versions_compared_without_v_prefix(entry: SimpleNamespace) -> None:
    """The printer's V1.1.40 and the server's 1.4.46 compare as versions, flag aside."""
    entity = _entity(entry, {"update_available": False, "current_version": "V1.1.40"})

    assert entity.version_is_newer("1.4.46", "V1.1.40") is True
    assert entity.version_is_newer("1.1.40", "V1.1.40") is False


async def test_lowercase_v_prefix_compares_as_a_version(
    entry: SimpleNamespace,
) -> None:
    """A lowercase ``v`` prefix compares equal, not as a different version."""
    entity = _entity(entry, {"update_available": False, "current_version": "v1.2.2"})

    assert entity.version_is_newer("1.2.2", "v1.2.2") is False
    assert entity.version_is_newer("1.2.3", "v1.2.2") is True


async def test_off_after_installing_newer_than_advertised(
    entry: SimpleNamespace,
) -> None:
    """A stale flag doesn't advertise older firmware than the printer runs."""
    entity = _entity(
        entry,
        {
            "update_available": True,
            "current_version": "V1.1.40",
            "latest_version": "1.4.46",
        },
    )
    entry.runtime_data.api.printer.firmware = "V1.4.48"

    assert entity.state == "off"


async def test_flag_decides_uncomparable_versions(entry: SimpleNamespace) -> None:
    """Versions that can't be compared fall back to Elegoo's flag."""
    entity = _entity(entry, {"update_available": True, "current_version": "beta-x"})
    assert entity.version_is_newer("foo", "beta-x") is True

    entity.coordinator.data.firmware_update_info["update_available"] = False
    assert entity.version_is_newer("foo", "beta-x") is False


async def test_unknown_before_a_successful_check(entry: SimpleNamespace) -> None:
    """Without a successful firmware check the entity is unknown, not off."""
    # PrinterData's startup default, before the first check succeeds.
    entity = _entity(entry, PrinterData().firmware_update_info)

    assert entity.latest_version is None
    assert entity.state is None


async def test_unknown_without_coordinator_data(entry: SimpleNamespace) -> None:
    """No coordinator data at all is unknown, and has no release notes."""
    entity = _entity(entry, {})
    entity.coordinator.data = None

    assert entity.state is None
    assert await entity.async_release_notes() is None


async def test_failed_check_returns_nothing(sample_printer: Printer) -> None:
    """A failed check returns {}, so the coordinator keeps the last result."""
    api = object.__new__(ElegooPrinterApiClient)
    api.printer = sample_printer
    api.async_check_firmware_update = AsyncMock(return_value=None)

    assert await api.async_get_firmware_update_info() == {}


async def test_successful_check_maps_the_server_response(
    sample_printer: Printer,
) -> None:
    """The server's update/version/packageUrl/log land under the entity's keys."""
    api = object.__new__(ElegooPrinterApiClient)
    api.printer = sample_printer
    api.async_check_firmware_update = AsyncMock(
        return_value={
            "update": True,
            "version": "1.4.46",
            "packageUrl": "https://example.invalid/fw.bin",
            "log": "Fixes",
        }
    )

    assert await api.async_get_firmware_update_info() == {
        "update_available": True,
        "current_version": sample_printer.firmware,
        "latest_version": "1.4.46",
        "package_url": "https://example.invalid/fw.bin",
        "changelog": "Fixes",
    }


async def test_release_summary_carries_the_changelog(entry: SimpleNamespace) -> None:
    """The changelog doubles as the inline summary on the Updates card."""
    entity = _entity(
        entry,
        {"update_available": True, "current_version": "V1.1.40", "changelog": "Fixes"},
    )

    assert entity.release_summary == "Fixes"


async def test_release_summary_is_none_without_a_check(entry: SimpleNamespace) -> None:
    """No changelog means no summary, rather than an empty one."""
    entity = _entity(entry, {})

    assert entity.release_summary is None


@pytest.mark.parametrize("protocol_version", [ProtocolVersion.V1, ProtocolVersion.CC2])
async def test_setup_skips_non_v3_printers(
    hass: MagicMock, entry: SimpleNamespace, protocol_version: ProtocolVersion
) -> None:
    """
    Only V3 printers get the entity, like the binary sensor.

    CC2 is pinned alongside V1 because it is a current product, and the
    proxy-configured CC2 path builds its printer without discovery, so it
    relies on this gate rather than on a discovery-set protocol.
    """
    entry.runtime_data.api.printer.protocol_version = protocol_version
    add_entities = AsyncMock()

    await update.async_setup_entry(hass, entry, add_entities)

    add_entities.assert_not_called()
