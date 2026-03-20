"""Tests for A1-A4 slot sensor helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.elegoo_printer.definitions import (
    _get_slot_attributes,
    _get_slot_cm3,
    _get_slot_color,
    _get_slot_filament_type,
    _get_slot_grams,
    _get_slot_mm,
    _get_slot_name,
    _has_gcode_filament_slot,
    _has_slot,
    _has_total_filament_changes_data,
    _has_total_filament_cost_data,
    _has_total_filament_used_data,
)
from custom_components.elegoo_printer.sdcp.models.ams import AMSStatus
from custom_components.elegoo_printer.sdcp.models.printer import (
    FileFilamentData,
    PrinterData,
)

SLOT_0_GRAMS = 11.11
SLOT_3_GRAMS = 22.22
SLOT_0_MM = 111.11
SLOT_3_MM = 222.22
SLOT_0_CM3 = 11.11
SLOT_3_CM3 = 22.22
SLOT_0_COST = 11.11
SLOT_1_COST = 22.22
SLOT_0_DENSITY = 1.24
SLOT_3_DENSITY = 1.27
SLOT_0_DIAMETER = 1.75
SLOT_3_DIAMETER = 1.75

PROXY_FILAMENT = FileFilamentData(
    total_filament_used=111.11,
    color_map=[
        {"color": "#FFFFFF", "name": "PLA", "t": 0},
        {"color": "#17656B", "name": "PLA", "t": 3},
    ],
    per_slot_grams=[SLOT_0_GRAMS, 0.0, 0.0, SLOT_3_GRAMS],
    per_slot_mm=[SLOT_0_MM, 0.0, 0.0, SLOT_3_MM],
    per_slot_cm3=[SLOT_0_CM3, 0.0, 0.0, SLOT_3_CM3],
    per_slot_cost=[SLOT_0_COST, SLOT_1_COST, 0.0, 0.0],
    per_slot_density=[SLOT_0_DENSITY, 0.0, 0.0, SLOT_3_DENSITY],
    per_slot_diameter=[SLOT_0_DIAMETER, 1.75, 1.75, SLOT_3_DIAMETER],
    filament_names=[
        "ElegooPLA-Basic-White",
        "ElegooPLA-Matte-Ruby Red",
        "ElegooPLA-Silk-Red Black",
        "ElegooPLA-Metallic-Blue",
    ],
    total_cost=1.11,
    total_filament_changes=11,
)

CANVAS_TRAY_DATA = {
    "canvas_list": [
        {
            "canvas_id": 0,
            "connected": 1,
            "tray_list": [
                {
                    "tray_id": 0,
                    "brand": "ELEGOO",
                    "filament_type": "PLA",
                    "filament_name": "Canvas-White",
                    "filament_color": "#CCCCCC",
                    "min_nozzle_temp": 190,
                    "max_nozzle_temp": 230,
                    "status": 1,
                },
                {
                    "tray_id": 1,
                    "brand": "ELEGOO",
                    "filament_type": "PETG",
                    "filament_name": "Canvas-Red",
                    "filament_color": "#FF0000",
                    "min_nozzle_temp": 220,
                    "max_nozzle_temp": 260,
                    "status": 1,
                },
            ],
        }
    ],
}


def _make_printer_data(
    filament_data: FileFilamentData | None = None,
    ams_status: AMSStatus | None = None,
) -> PrinterData:
    printer_data = MagicMock(spec=PrinterData)
    printer_data.gcode_filament_data = filament_data
    printer_data.ams_status = ams_status
    return printer_data


def _make_canvas_status() -> AMSStatus:
    return AMSStatus(CANVAS_TRAY_DATA)


class TestHasGcodeFilamentSlot:
    """_has_gcode_filament_slot gates proxy quantity sensors per slot."""

    def test_canvas_tray_implies_slot(self) -> None:
        pd = _make_printer_data(ams_status=_make_canvas_status())
        assert _has_gcode_filament_slot(pd, 0) is True
        assert _has_gcode_filament_slot(pd, 1) is True

    def test_proxy_color_map_implies_slot_without_canvas(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        assert _has_gcode_filament_slot(pd, 0) is True
        assert _has_gcode_filament_slot(pd, 3) is True

    def test_unused_proxy_slot_false(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        assert _has_gcode_filament_slot(pd, 1) is False
        assert _has_gcode_filament_slot(pd, 2) is False

    def test_no_data_false(self) -> None:
        pd = _make_printer_data()
        assert _has_gcode_filament_slot(pd, 0) is False

    def test_none_printer_data(self) -> None:
        assert _has_gcode_filament_slot(None, 0) is False


class TestHasTotalFilamentProxyTotals:
    """exists_fn helpers for total used / cost / changes sensors."""

    def test_used_when_present(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        assert _has_total_filament_used_data(pd) is True

    def test_used_when_absent(self) -> None:
        pd = _make_printer_data(filament_data=FileFilamentData())
        assert _has_total_filament_used_data(pd) is False

    def test_used_zero_counts(self) -> None:
        pd = _make_printer_data(
            filament_data=FileFilamentData(total_filament_used=0.0),
        )
        assert _has_total_filament_used_data(pd) is True

    def test_used_no_gcode_filament_object(self) -> None:
        pd = _make_printer_data()
        assert _has_total_filament_used_data(pd) is False

    def test_used_none_printer_data(self) -> None:
        assert _has_total_filament_used_data(None) is False

    def test_cost_when_present(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        assert _has_total_filament_cost_data(pd) is True

    def test_cost_when_absent(self) -> None:
        pd = _make_printer_data(filament_data=FileFilamentData())
        assert _has_total_filament_cost_data(pd) is False

    def test_changes_when_present(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        assert _has_total_filament_changes_data(pd) is True

    def test_changes_when_absent(self) -> None:
        pd = _make_printer_data(filament_data=FileFilamentData())
        assert _has_total_filament_changes_data(pd) is False


class TestHasSlot:
    """_has_slot checks Canvas tray existence by 0-based index."""

    def test_existing_tray(self) -> None:
        pd = _make_printer_data(ams_status=_make_canvas_status())
        assert _has_slot(pd, 0) is True
        assert _has_slot(pd, 1) is True

    def test_missing_tray(self) -> None:
        pd = _make_printer_data(ams_status=_make_canvas_status())
        assert _has_slot(pd, 2) is False
        assert _has_slot(pd, 3) is False

    def test_no_ams_status(self) -> None:
        pd = _make_printer_data()
        assert _has_slot(pd, 0) is False

    def test_none_printer_data(self) -> None:
        assert _has_slot(None, 0) is False


class TestGetSlotColor:
    """Color uses proxy color_map first, Canvas tray fallback."""

    def test_proxy_color_map_takes_priority(self) -> None:
        pd = _make_printer_data(
            filament_data=PROXY_FILAMENT,
            ams_status=_make_canvas_status(),
        )
        assert _get_slot_color(pd, 0) == "#FFFFFF"

    def test_falls_back_to_canvas_when_no_proxy(self) -> None:
        pd = _make_printer_data(ams_status=_make_canvas_status())
        assert _get_slot_color(pd, 0) == "#CCCCCC"

    def test_falls_back_to_canvas_for_slot_not_in_color_map(self) -> None:
        pd = _make_printer_data(
            filament_data=PROXY_FILAMENT,
            ams_status=_make_canvas_status(),
        )
        assert _get_slot_color(pd, 1) == "#FF0000"

    def test_no_data_returns_none(self) -> None:
        pd = _make_printer_data()
        assert _get_slot_color(pd, 0) is None

    def test_none_printer_data(self) -> None:
        assert _get_slot_color(None, 0) is None


class TestGetSlotName:
    """Name resolves: proxy filament_names -> color_map name -> Canvas."""

    def test_proxy_filament_names_takes_priority(self) -> None:
        pd = _make_printer_data(
            filament_data=PROXY_FILAMENT,
            ams_status=_make_canvas_status(),
        )
        assert _get_slot_name(pd, 0) == "ElegooPLA-Basic-White"

    def test_falls_back_to_color_map_name(self) -> None:
        data = FileFilamentData(
            color_map=[{"color": "#FF0000", "name": "PETG", "t": 2}],
        )
        pd = _make_printer_data(filament_data=data)
        assert _get_slot_name(pd, 2) == "PETG"

    def test_falls_back_to_canvas_name(self) -> None:
        pd = _make_printer_data(ams_status=_make_canvas_status())
        assert _get_slot_name(pd, 0) == "Canvas-White"

    def test_no_data_returns_none(self) -> None:
        pd = _make_printer_data()
        assert _get_slot_name(pd, 0) is None

    def test_none_printer_data(self) -> None:
        assert _get_slot_name(None, 0) is None

    def test_all_four_proxy_names(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        names = [_get_slot_name(pd, i) for i in range(4)]
        assert names == [
            "ElegooPLA-Basic-White",
            "ElegooPLA-Matte-Ruby Red",
            "ElegooPLA-Silk-Red Black",
            "ElegooPLA-Metallic-Blue",
        ]


class TestGetSlotFilamentType:
    """Filament type comes from Canvas tray data."""

    def test_returns_canvas_type(self) -> None:
        pd = _make_printer_data(ams_status=_make_canvas_status())
        assert _get_slot_filament_type(pd, 0) == "PLA"
        assert _get_slot_filament_type(pd, 1) == "PETG"

    def test_missing_tray_returns_none(self) -> None:
        pd = _make_printer_data(ams_status=_make_canvas_status())
        assert _get_slot_filament_type(pd, 3) is None

    def test_no_ams_returns_none(self) -> None:
        pd = _make_printer_data()
        assert _get_slot_filament_type(pd, 0) is None


class TestGetSlotAttributes:
    """Attributes merge Canvas metadata with proxy density/diameter/cost."""

    def test_canvas_only_attributes(self) -> None:
        pd = _make_printer_data(ams_status=_make_canvas_status())
        attrs = _get_slot_attributes(pd, 0)
        assert attrs["brand"] == "ELEGOO"
        assert attrs["source"] == "canvas"
        assert attrs["diameter"] == SLOT_0_DIAMETER
        assert attrs["nozzle_temp_range"] == "190-230°C"
        assert attrs["enabled"] is True
        assert "density" not in attrs
        assert "cost" not in attrs

    def test_proxy_overrides_diameter(self) -> None:
        pd = _make_printer_data(
            filament_data=PROXY_FILAMENT,
            ams_status=_make_canvas_status(),
        )
        attrs = _get_slot_attributes(pd, 0)
        assert attrs["diameter"] == SLOT_0_DIAMETER
        assert attrs["density"] == SLOT_0_DENSITY
        assert attrs["cost"] == SLOT_0_COST
        assert attrs["brand"] == "ELEGOO"

    def test_proxy_only_no_canvas(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        attrs = _get_slot_attributes(pd, 0)
        assert attrs["density"] == SLOT_0_DENSITY
        assert attrs["diameter"] == SLOT_0_DIAMETER
        assert attrs["cost"] == SLOT_0_COST
        assert "brand" not in attrs

    def test_no_data_returns_empty(self) -> None:
        pd = _make_printer_data()
        assert _get_slot_attributes(pd, 0) == {}

    def test_none_printer_data(self) -> None:
        assert _get_slot_attributes(None, 0) == {}


class TestGetSlotGrams:
    """Grams comes from proxy per_slot_grams."""

    def test_returns_weight(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        assert _get_slot_grams(pd, 0) == SLOT_0_GRAMS

    def test_unused_slot_returns_zero(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        assert _get_slot_grams(pd, 1) == 0.0

    def test_all_four_slots(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        weights = [_get_slot_grams(pd, i) for i in range(4)]
        assert weights == [SLOT_0_GRAMS, 0.0, 0.0, SLOT_3_GRAMS]

    def test_out_of_range_returns_none(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        assert _get_slot_grams(pd, 5) is None

    def test_no_filament_data_returns_none(self) -> None:
        pd = _make_printer_data()
        assert _get_slot_grams(pd, 0) is None

    def test_empty_per_slot_grams(self) -> None:
        data = FileFilamentData(per_slot_grams=[])
        pd = _make_printer_data(filament_data=data)
        assert _get_slot_grams(pd, 0) is None


class TestGetSlotCm3:
    """Cubic centimeters comes from proxy per_slot_cm3."""

    def test_returns_volume(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        assert _get_slot_cm3(pd, 0) == SLOT_0_CM3

    def test_out_of_range_returns_none(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        assert _get_slot_cm3(pd, 5) is None

    def test_no_filament_data_returns_none(self) -> None:
        pd = _make_printer_data()
        assert _get_slot_cm3(pd, 0) is None


class TestGetSlotMm:
    """Length millimeters comes from proxy per_slot_mm."""

    def test_returns_length(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        assert _get_slot_mm(pd, 0) == SLOT_0_MM

    def test_out_of_range_returns_none(self) -> None:
        pd = _make_printer_data(filament_data=PROXY_FILAMENT)
        assert _get_slot_mm(pd, 5) is None

    def test_no_filament_data_returns_none(self) -> None:
        pd = _make_printer_data()
        assert _get_slot_mm(pd, 0) is None
