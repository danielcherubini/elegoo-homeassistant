"""Tests for the PrinterStatus model."""

import json

from custom_components.elegoo_printer.sdcp.models.status import PrinterStatus

# ruff: noqa: PLR2004  # Magic values in tests are expected


def test_printer_status_with_legacy_saturn_format() -> None:
    """Test PrinterStatus parsing with legacy Saturn nested Status format."""
    # Legacy Saturn MQTT format with nested Status
    status_json = json.dumps(
        {
            "Status": {
                "CurrentStatus": [1],
                "PreviousStatus": 0,
                "TempOfNozzle": 210.5,
                "TempTargetNozzle": 210.0,
                "PrintInfo": {
                    "Status": 3,
                    "CurrentLayer": 150,
                    "TotalLayer": 500,
                    "CurrentTicks": 60000,
                    "TotalTicks": 200000,
                    "Filename": "test_print.gcode",
                    "ErrorNumber": 0,
                },
            }
        }
    )

    status = PrinterStatus.from_json(status_json)

    assert status.current_status is not None
    assert status.previous_status == 0
    assert status.temp_of_nozzle == 210.5
    assert status.temp_target_nozzle == 210.0
    assert status.print_info.current_layer == 150
    assert status.print_info.total_layers == 500
    assert status.print_info.filename == "test_print.gcode"


def test_printer_status_with_modern_flat_format() -> None:
    """Test PrinterStatus parsing with modern flat format."""
    # Modern flat format (direct status fields)
    status_json = json.dumps(
        {
            "CurrentStatus": [1],
            "PreviousStatus": 0,
            "TempOfNozzle": 210.5,
            "TempTargetNozzle": 210.0,
            "PrintInfo": {
                "Status": 3,
                "CurrentLayer": 150,
                "TotalLayer": 500,
                "CurrentTicks": 60000,
                "TotalTicks": 200000,
                "Filename": "test_print.gcode",
                "ErrorNumber": 0,
            },
        }
    )

    status = PrinterStatus.from_json(status_json)

    assert status.current_status is not None
    assert status.previous_status == 0
    assert status.temp_of_nozzle == 210.5
    assert status.temp_target_nozzle == 210.0
    assert status.print_info.current_layer == 150
    assert status.print_info.total_layers == 500
    assert status.print_info.filename == "test_print.gcode"


def test_printer_status_with_empty_data() -> None:
    """Test PrinterStatus with empty/missing data."""
    status_json = json.dumps({})
    status = PrinterStatus.from_json(status_json)

    # Empty data results in None/default values
    assert status.current_status is None  # No CurrentStatus provided
    assert status.previous_status == 0
    assert status.print_info is not None


def test_extrusion_with_normal_keys() -> None:
    """Test extrusion fields with standard TotalExtrusion/CurrentExtrusion keys."""
    status_json = json.dumps(
        {
            "PrintInfo": {
                "TotalExtrusion": 1234.56,
                "CurrentExtrusion": 789.12,
            }
        }
    )

    status = PrinterStatus.from_json(status_json)

    assert status.print_info.total_extrusion == 1234.56
    assert status.print_info.current_extrusion == 789.12


def test_extrusion_with_hex_keys_only() -> None:
    """Test extrusion fields with hex-encoded keys (printer firmware quirk)."""
    status_json = json.dumps(
        {
            "PrintInfo": {
                # Hex for "TotalExtrusion"
                "54 6F 74 61 6C 45 78 74 72 75 73 69 6F 6E 00": 2345.67,
                # Hex for "CurrentExtrusion"
                "43 75 72 72 65 6E 74 45 78 74 72 75 73 69 6F 6E 00": 890.23,
            }
        }
    )

    status = PrinterStatus.from_json(status_json)

    assert status.print_info.total_extrusion == 2345.67
    assert status.print_info.current_extrusion == 890.23


def test_extrusion_with_mixed_keys() -> None:
    """Test extrusion with one normal key and one hex key (fallback test)."""
    status_json = json.dumps(
        {
            "PrintInfo": {
                "TotalExtrusion": 3456.78,
                # Hex for "CurrentExtrusion"
                "43 75 72 72 65 6E 74 45 78 74 72 75 73 69 6F 6E 00": 901.34,
            }
        }
    )

    status = PrinterStatus.from_json(status_json)

    assert status.print_info.total_extrusion == 3456.78
    assert status.print_info.current_extrusion == 901.34


def test_extrusion_with_zero_values() -> None:
    """Test that zero extrusion values are handled correctly (not treated as None)."""
    status_json = json.dumps(
        {
            "PrintInfo": {
                "TotalExtrusion": 0.0,
                "CurrentExtrusion": 0.0,
            }
        }
    )

    status = PrinterStatus.from_json(status_json)

    # Zero should be preserved, not converted to None
    assert status.print_info.total_extrusion == 0.0
    assert status.print_info.current_extrusion == 0.0


def test_extrusion_missing_values() -> None:
    """Test that missing extrusion values default to None."""
    status_json = json.dumps({"PrintInfo": {}})

    status = PrinterStatus.from_json(status_json)

    assert status.print_info.total_extrusion is None
    assert status.print_info.current_extrusion is None


def test_extrusion_hex_fallback_priority() -> None:
    """Test that normal keys take priority over hex keys when both are present."""
    status_json = json.dumps(
        {
            "PrintInfo": {
                "TotalExtrusion": 9999.99,
                # Hex for "TotalExtrusion" (should be ignored)
                "54 6F 74 61 6C 45 78 74 72 75 73 69 6F 6E 00": 1111.11,
            }
        }
    )

    status = PrinterStatus.from_json(status_json)

    # Normal key should take priority
    assert status.print_info.total_extrusion == 9999.99
