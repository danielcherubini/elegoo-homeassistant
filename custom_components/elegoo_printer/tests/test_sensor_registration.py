"""Tests for sensor registration logic."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from custom_components.elegoo_printer.definitions import (
    PRINTER_STATUS_CC2_GCODE_FILAMENT,
    PRINTER_STATUS_FDM_CURRENT_EXTRUSION,
    PRINTER_STATUS_FDM_TOTAL_EXTRUSION,
)
from custom_components.elegoo_printer.sdcp.models.enums import (
    PrinterType,
    ProtocolVersion,
)
from custom_components.elegoo_printer.sensor import async_setup_entry

CURRENT_EXTRUSION_KEYS = {desc.key for desc in PRINTER_STATUS_FDM_CURRENT_EXTRUSION}
TOTAL_EXTRUSION_KEYS = {desc.key for desc in PRINTER_STATUS_FDM_TOTAL_EXTRUSION}
GCODE_FILAMENT_KEYS = {desc.key for desc in PRINTER_STATUS_CC2_GCODE_FILAMENT}


class _FakeSensor:
    """Stand-in for ElegooPrinterSensor that skips HA entity initialisation."""

    def __init__(self, coordinator: object, entity_description: object) -> None:  # noqa: ARG002
        self.entity_description = entity_description


def _registered_keys(
    printer_type: PrinterType | None,
    protocol_version: ProtocolVersion,
    *,
    open_centauri: bool,
) -> set[str]:
    """Call the real async_setup_entry and return the set of registered sensor keys."""
    printer = MagicMock()
    printer.printer_type = printer_type
    printer.protocol_version = protocol_version
    printer.open_centauri = open_centauri
    printer.has_vat_heater = False

    coordinator = MagicMock()
    coordinator.config_entry.runtime_data.api.printer = printer

    entry = MagicMock()
    entry.runtime_data.coordinator = coordinator

    async_add_entities = MagicMock()

    with patch(
        "custom_components.elegoo_printer.sensor.ElegooPrinterSensor",
        _FakeSensor,
    ):
        asyncio.run(async_setup_entry(MagicMock(), entry, async_add_entities))

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    return {e.entity_description.key for e in entities}


class TestExtrusionSensorDefinitions:
    """Verify the sensor tuples contain the expected keys."""

    def test_current_extrusion_key(self) -> None:
        """Verify current_extrusion is in the current-extrusion tuple."""
        assert "current_extrusion" in CURRENT_EXTRUSION_KEYS

    def test_total_extrusion_key(self) -> None:
        """Verify total_extrusion is in the total-extrusion tuple."""
        assert "total_extrusion" in TOTAL_EXTRUSION_KEYS

    def test_no_overlap(self) -> None:
        """Verify the two tuples have no overlapping sensor keys."""
        assert CURRENT_EXTRUSION_KEYS.isdisjoint(TOTAL_EXTRUSION_KEYS)


class TestCurrentExtrusionGating:
    """Test current_extrusion sensor inclusion via async_setup_entry."""

    @pytest.mark.parametrize(
        ("printer_type", "protocol_version", "open_centauri", "expected"),
        [
            (PrinterType.FDM, ProtocolVersion.CC2, False, True),
            (PrinterType.FDM, ProtocolVersion.CC2, True, True),
            (PrinterType.FDM, ProtocolVersion.V3, True, True),
            (PrinterType.FDM, ProtocolVersion.V1, True, True),
            (PrinterType.FDM, ProtocolVersion.V3, False, False),
            (PrinterType.FDM, ProtocolVersion.V1, False, False),
            (PrinterType.RESIN, ProtocolVersion.V3, False, False),
            (PrinterType.RESIN, ProtocolVersion.CC2, False, False),
            (None, ProtocolVersion.V3, False, False),
            (None, ProtocolVersion.CC2, False, False),
            (None, ProtocolVersion.V1, False, False),
        ],
    )
    def test_current_extrusion_gating(
        self,
        printer_type: PrinterType | None,
        protocol_version: ProtocolVersion,
        open_centauri: bool,  # noqa: FBT001
        expected: bool,  # noqa: FBT001
    ) -> None:
        """Test current_extrusion inclusion for various printer configurations."""
        keys = _registered_keys(
            printer_type, protocol_version, open_centauri=open_centauri
        )
        assert CURRENT_EXTRUSION_KEYS.issubset(keys) == expected


class TestTotalExtrusionGating:
    """Test total_extrusion sensor inclusion via async_setup_entry."""

    @pytest.mark.parametrize(
        ("printer_type", "protocol_version", "open_centauri", "expected"),
        [
            # Open Centauri FDM — only case that gets total_extrusion
            (PrinterType.FDM, ProtocolVersion.V3, True, True),
            (PrinterType.FDM, ProtocolVersion.V1, True, True),
            (PrinterType.FDM, ProtocolVersion.CC2, True, True),
            # CC2 without Open Centauri — should NOT get total_extrusion
            (PrinterType.FDM, ProtocolVersion.CC2, False, False),
            # Non-Open-Centauri, non-CC2
            (PrinterType.FDM, ProtocolVersion.V3, False, False),
            (PrinterType.FDM, ProtocolVersion.V1, False, False),
            # Resin — never
            (PrinterType.RESIN, ProtocolVersion.V3, True, False),
            (PrinterType.RESIN, ProtocolVersion.CC2, False, False),
            # Unknown printer type — never
            (None, ProtocolVersion.V3, False, False),
            (None, ProtocolVersion.CC2, False, False),
            (None, ProtocolVersion.V1, False, False),
        ],
    )
    def test_total_extrusion_gating(
        self,
        printer_type: PrinterType | None,
        protocol_version: ProtocolVersion,
        open_centauri: bool,  # noqa: FBT001
        expected: bool,  # noqa: FBT001
    ) -> None:
        """Test total_extrusion inclusion for various printer configurations."""
        keys = _registered_keys(
            printer_type, protocol_version, open_centauri=open_centauri
        )
        assert TOTAL_EXTRUSION_KEYS.issubset(keys) == expected


class TestGcodeFilamentGating:
    """Test gcode filament sensor inclusion via async_setup_entry."""

    @pytest.mark.parametrize(
        ("printer_type", "protocol_version", "open_centauri", "expected"),
        [
            # CC2 FDM — included
            (PrinterType.FDM, ProtocolVersion.CC2, False, True),
            (PrinterType.FDM, ProtocolVersion.CC2, True, True),
            # Non-CC2 FDM — excluded
            (PrinterType.FDM, ProtocolVersion.V3, False, False),
            (PrinterType.FDM, ProtocolVersion.V3, True, False),
            (PrinterType.FDM, ProtocolVersion.V1, False, False),
            (PrinterType.FDM, ProtocolVersion.V1, True, False),
            # Resin — excluded
            (PrinterType.RESIN, ProtocolVersion.CC2, False, False),
            (PrinterType.RESIN, ProtocolVersion.V3, False, False),
            # Unknown printer type — excluded
            (None, ProtocolVersion.CC2, False, False),
            (None, ProtocolVersion.V3, False, False),
            (None, ProtocolVersion.V1, False, False),
        ],
    )
    def test_gcode_filament_gating(
        self,
        printer_type: PrinterType | None,
        protocol_version: ProtocolVersion,
        open_centauri: bool,  # noqa: FBT001
        expected: bool,  # noqa: FBT001
    ) -> None:
        """Test gcode filament sensor inclusion for various printer configurations."""
        keys = _registered_keys(
            printer_type, protocol_version, open_centauri=open_centauri
        )
        assert GCODE_FILAMENT_KEYS.issubset(keys) == expected
