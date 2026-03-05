"""Tests for sensor registration logic."""

import pytest

from custom_components.elegoo_printer.definitions import (
    PRINTER_STATUS_FDM_CURRENT_EXTRUSION,
    PRINTER_STATUS_FDM_TOTAL_EXTRUSION,
)
from custom_components.elegoo_printer.sdcp.models.enums import (
    PrinterType,
    ProtocolVersion,
)

CURRENT_EXTRUSION_KEYS = {desc.key for desc in PRINTER_STATUS_FDM_CURRENT_EXTRUSION}
TOTAL_EXTRUSION_KEYS = {desc.key for desc in PRINTER_STATUS_FDM_TOTAL_EXTRUSION}


def _should_include_current_extrusion(
    printer_type: PrinterType | None,
    protocol_version: ProtocolVersion,
    *,
    open_centauri: bool,
) -> bool:
    """Reproduce the current-extrusion gating logic from sensor.py."""
    return printer_type == PrinterType.FDM and (
        open_centauri or protocol_version == ProtocolVersion.CC2
    )


def _should_include_total_extrusion(
    printer_type: PrinterType | None,
    protocol_version: ProtocolVersion,  # noqa: ARG001
    *,
    open_centauri: bool,
) -> bool:
    """Reproduce the total-extrusion gating logic from sensor.py."""
    return printer_type == PrinterType.FDM and open_centauri


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
    """Test current_extrusion sensor inclusion (Open Centauri or CC2)."""

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
        result = _should_include_current_extrusion(
            printer_type, protocol_version, open_centauri=open_centauri
        )
        assert result == expected


class TestTotalExtrusionGating:
    """Test total_extrusion sensor inclusion (Open Centauri only)."""

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
        result = _should_include_total_extrusion(
            printer_type, protocol_version, open_centauri=open_centauri
        )
        assert result == expected
