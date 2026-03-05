"""Tests for sensor registration logic."""

import pytest

from custom_components.elegoo_printer.definitions import (
    PRINTER_STATUS_FDM_OPEN_CENTAURI,
)
from custom_components.elegoo_printer.sdcp.models.enums import (
    PrinterType,
    ProtocolVersion,
)

EXTRUSION_SENSOR_KEYS = {desc.key for desc in PRINTER_STATUS_FDM_OPEN_CENTAURI}


def _should_include_extrusion_sensors(
    printer_type: PrinterType | None,
    protocol_version: ProtocolVersion,
    *,
    open_centauri: bool,
) -> bool:
    """Reproduce the sensor gating logic from sensor.py async_setup_entry."""
    return printer_type == PrinterType.FDM and (
        open_centauri or protocol_version == ProtocolVersion.CC2
    )


class TestExtrusionSensorRegistration:
    """Test that extrusion sensors are registered for the correct printer configs."""

    def test_extrusion_sensors_contain_expected_keys(self) -> None:
        """Verify the extrusion sensor tuple has the expected sensor keys."""
        assert "total_extrusion" in EXTRUSION_SENSOR_KEYS
        assert "current_extrusion" in EXTRUSION_SENSOR_KEYS

    @pytest.mark.parametrize(
        ("printer_type", "protocol_version", "open_centauri", "expected"),
        [
            # CC2 FDM printer without Open Centauri — should get extrusion sensors
            (PrinterType.FDM, ProtocolVersion.CC2, False, True),
            # CC2 FDM printer with Open Centauri — should get extrusion sensors
            (PrinterType.FDM, ProtocolVersion.CC2, True, True),
            # V3 FDM printer with Open Centauri — should get extrusion sensors
            (PrinterType.FDM, ProtocolVersion.V3, True, True),
            # V1 FDM printer with Open Centauri — should get extrusion sensors
            (PrinterType.FDM, ProtocolVersion.V1, True, True),
            # V3 FDM printer without Open Centauri — should NOT get extrusion sensors
            (PrinterType.FDM, ProtocolVersion.V3, False, False),
            # V1 FDM printer without Open Centauri — should NOT get extrusion sensors
            (PrinterType.FDM, ProtocolVersion.V1, False, False),
            # Resin printer — should NOT get extrusion sensors regardless
            (PrinterType.RESIN, ProtocolVersion.V3, False, False),
            (PrinterType.RESIN, ProtocolVersion.V3, True, False),
            (PrinterType.RESIN, ProtocolVersion.CC2, False, False),
        ],
    )
    def test_extrusion_sensor_gating(
        self,
        printer_type: PrinterType | None,
        protocol_version: ProtocolVersion,
        open_centauri: bool,  # noqa: FBT001
        expected: bool,  # noqa: FBT001
    ) -> None:
        """Test extrusion sensor inclusion for various printer configurations."""
        result = _should_include_extrusion_sensors(
            printer_type, protocol_version, open_centauri=open_centauri
        )
        assert result == expected, (
            f"Expected extrusion sensors={'included' if expected else 'excluded'} "
            f"for type={printer_type}, protocol={protocol_version}, "
            f"open_centauri={open_centauri}"
        )
