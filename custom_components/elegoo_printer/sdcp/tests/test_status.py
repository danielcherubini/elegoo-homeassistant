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
