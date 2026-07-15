"""Tests for CC1 Canvas handler normalization."""

from custom_components.elegoo_printer.websocket.client import (
    normalize_cc1_canvas_data,
)


def test_normalize_cc1_empty_tray_placeholders() -> None:
    """Test CC1 dash placeholders are normalized to empty strings."""
    data = {
        "canvas_list": [
            {
                "canvas_id": 0,
                "connected": 1,
                "tray_list": [
                    {
                        "tray_id": 3,
                        "brand": "— — — —",
                        "filament_type": "?",
                        "filament_name": "— — —",
                        "filament_color": "#FF2F30",
                        "status": 0,
                    }
                ],
            }
        ],
    }
    normalize_cc1_canvas_data(data)
    tray = data["canvas_list"][0]["tray_list"][0]
    assert tray["brand"] == ""
    assert tray["filament_type"] == ""
    assert tray["filament_name"] == ""


def test_normalize_leaves_populated_trays_unchanged() -> None:
    """Test normalization doesn't alter populated tray data."""
    data = {
        "canvas_list": [
            {
                "canvas_id": 0,
                "connected": 1,
                "tray_list": [
                    {
                        "tray_id": 0,
                        "brand": "ELEGOO",
                        "filament_type": "PLA",
                        "filament_name": "PLA",
                        "filament_color": "#2850DF",
                        "status": 1,
                    }
                ],
            }
        ],
    }
    normalize_cc1_canvas_data(data)
    tray = data["canvas_list"][0]["tray_list"][0]
    assert tray["brand"] == "ELEGOO"
    assert tray["filament_type"] == "PLA"
    assert tray["filament_name"] == "PLA"


def test_normalize_handles_empty_canvas_list() -> None:
    """Test normalization handles missing/empty canvas_list."""
    data_empty: dict = {}
    normalize_cc1_canvas_data(data_empty)
    assert data_empty == {}

    data_list: dict = {"canvas_list": []}
    normalize_cc1_canvas_data(data_list)
    assert data_list == {"canvas_list": []}


def test_normalize_handles_null_brand_and_filament_name() -> None:
    """Null brand/filament_name from firmware must not crash .startswith()."""
    data = {
        "canvas_list": [
            {
                "canvas_id": 0,
                "connected": 1,
                "tray_list": [
                    {
                        "tray_id": 0,
                        "brand": None,
                        "filament_type": "PLA",
                        "filament_name": None,
                        "filament_color": "#FFFFFF",
                        "status": 0,
                    }
                ],
            }
        ],
    }
    normalize_cc1_canvas_data(data)
    tray = data["canvas_list"][0]["tray_list"][0]
    assert tray["brand"] is None
    assert tray["filament_name"] is None
    assert tray["filament_type"] == "PLA"
