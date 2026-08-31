"""
Characterization tests: entity platform setup for the 8 untested platforms.

The component's 9 platforms are
``binary_sensor, button, fan, image, light, number, select, sensor, camera``;
``camera`` is covered by ``tests/test_camera.py``. Each platform is a
top-level module whose ``async_setup_entry(hass, entry, async_add_entities)``
takes the add-entities callback as the third argument (HA 2025.4 — the
callback is NOT ``hass.async_add_entities``).

The conftest ``entry`` double is used; ``entry.runtime_data.api`` (a
MagicMock) is given a real FDM/RESIN ``Printer`` so the per-``printer_type``
gated compositions exercise the FDM-only tuples.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    from types import SimpleNamespace

from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer

PLATFORMS = [
    "binary_sensor",
    "button",
    "fan",
    "image",
    "light",
    "number",
    "select",
    "sensor",
]


def _printer_from(json_data: dict) -> Printer:
    """Build a real Printer from a JSON payload (so printer_type resolves)."""
    return Printer(json.dumps(json_data))


def _fdm_printer() -> Printer:
    """FDM printer (Neptune 4 is an FDM series per PrinterType.from_model)."""
    return _printer_from(
        {
            "Id": "test_connection",
            "Data": {
                "Name": "Neptune 4",
                "MachineName": "Neptune 4",
                "BrandName": "Elegoo",
                "MainboardIP": "192.168.1.100",
                "ProtocolVersion": "V3.0.0",
                "FirmwareVersion": "V1.0.0",
                "MainboardID": "test_mainboard_id_12345",
            },
        }
    )


def _resin_printer() -> Printer:
    """RESIN printer (Mars 4 is a resin series per PrinterType.from_model)."""
    return _printer_from(
        {
            "Id": "test_connection",
            "Data": {
                "Name": "Mars 4",
                "MachineName": "Mars 4",
                "BrandName": "Elegoo",
                "MainboardIP": "192.168.1.100",
                "ProtocolVersion": "V3.0.0",
                "FirmwareVersion": "V1.0.0",
                "MainboardID": "test_mainboard_id_12345",
            },
        }
    )


def _wire_entry(entry: SimpleNamespace, printer: Printer) -> None:
    """Point the conftest entry's api mock at the given printer."""
    entry.runtime_data.api.printer = printer


def _count_entities(add_entities: AsyncMock) -> int:
    """
    Count entities across all calls (materializing generator arguments).

    HA's real ``async_add_entities`` consumes the lazy iterable; the mock
    does not, so count a generator argument by draining it.
    """

    def _count(payload: object) -> int:
        if isinstance(payload, (list, tuple, set)):
            return len(payload)
        return sum(1 for _ in payload)  # generator / other iterable

    return sum(_count(call.args[0]) for call in add_entities.call_args_list)


async def _setup_platform(
    platform: str, hass: MagicMock, entry: SimpleNamespace
) -> int:
    """Run the platform's async_setup_entry and count the entities added."""
    module = importlib.import_module(f"custom_components.elegoo_printer.{platform}")
    add_entities = AsyncMock()
    await module.async_setup_entry(hass, entry, add_entities)
    assert add_entities.called, f"{platform} did not call async_add_entities"
    total = _count_entities(add_entities)
    assert total >= 1, f"{platform} added no entities"
    return total


# Exact per-platform entity counts for the FDM suite — pinning these guards
# the definitions.py tuple split from silently dropping entities (a test
# asserting only "total >= 1" would still pass if a whole tuple vanished).
FDM_ENTITY_COUNTS = {
    "binary_sensor": 3,
    "button": 7,
    "fan": 3,
    "image": 1,
    "light": 1,
    "number": 2,
    "select": 1,
    "sensor": 34,
}


@pytest.mark.parametrize("platform", PLATFORMS)
async def test_platform_setups_fdm_entities(
    hass: MagicMock, entry: SimpleNamespace, platform: str
) -> None:
    """Each of the 8 platforms adds the recorded number of FDM V3 entities."""
    printer = _fdm_printer()
    assert printer.printer_type == PrinterType.FDM
    _wire_entry(entry, printer)

    total = await _setup_platform(platform, hass, entry)
    assert total == FDM_ENTITY_COUNTS[platform]


async def test_camera_platform_not_included() -> None:
    """
    camera is the 9th platform (covered by tests/test_camera.py).

    Pins the platform set: exactly the 8 listed in PLATFORMS + camera.
    """
    assert set(PLATFORMS) == {
        "binary_sensor",
        "button",
        "fan",
        "image",
        "light",
        "number",
        "select",
        "sensor",
    }


async def test_sensor_platform_setups_resin_entities(
    hass: MagicMock,
    entry: SimpleNamespace,
) -> None:
    """The sensor platform adds entities for a RESIN printer too."""
    printer = _resin_printer()
    assert printer.printer_type == PrinterType.RESIN
    _wire_entry(entry, printer)

    total = await _setup_platform("sensor", hass, entry)
    # Recorded count is 27; keep a floor (not an exact pin) so resin-only
    # additions can vary without breaking, but a dropped tuple is caught.
    assert total >= 15
