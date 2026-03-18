"""Tests for slot-based extruder sensor functions."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.elegoo_printer.definitions import (
    _get_gcode_extruder_attributes,
    _get_gcode_extruder_filament_type,
    _get_proxy_extruder_weight,
    _get_proxy_extruder_weight_attributes,
)
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
    filament_names=[
        "ElegooPLA-Basic-White",
        "ElegooPLA-Matte-Ruby Red",
        "ElegooPLA-Silk-Red Black",
        "ElegooPLA-Metallic-Blue",
    ],
    total_cost=1.11,
    total_filament_changes=11,
)


def _make_printer_data(filament_data: FileFilamentData | None) -> PrinterData:
    printer_data = MagicMock(spec=PrinterData)
    printer_data.gcode_filament_data = filament_data
    return printer_data


class TestGetGcodeExtruderFilamentType:
    """Filament type should resolve by physical slot index."""

    def test_used_slot_returns_proxy_name(self) -> None:
        """Slot 0 is used in print — returns proxy filament_names[0]."""
        pd = _make_printer_data(PROXY_FILAMENT)
        assert _get_gcode_extruder_filament_type(pd, 0) == "ElegooPLA-Basic-White"

    def test_unused_slot_returns_proxy_name(self) -> None:
        """Slot 1 is not used in print — still returns filament_names[1]."""
        pd = _make_printer_data(PROXY_FILAMENT)
        assert _get_gcode_extruder_filament_type(pd, 1) == "ElegooPLA-Matte-Ruby Red"

    def test_all_four_slots_filled(self) -> None:
        """All 4 slots return names from proxy filament_names."""
        pd = _make_printer_data(PROXY_FILAMENT)
        names = [_get_gcode_extruder_filament_type(pd, i) for i in range(4)]
        assert names == [
            "ElegooPLA-Basic-White",
            "ElegooPLA-Matte-Ruby Red",
            "ElegooPLA-Silk-Red Black",
            "ElegooPLA-Metallic-Blue",
        ]

    def test_falls_back_to_color_map_when_no_proxy_names(self) -> None:
        """Without proxy filament_names, falls back to color_map by tray index."""
        data = FileFilamentData(
            color_map=[
                {"color": "#FF0000", "name": "PETG", "t": 2},
            ],
        )
        pd = _make_printer_data(data)
        assert _get_gcode_extruder_filament_type(pd, 2) == "PETG"
        assert _get_gcode_extruder_filament_type(pd, 0) is None

    def test_out_of_range_returns_none(self) -> None:
        """Index beyond all data sources returns None."""
        pd = _make_printer_data(PROXY_FILAMENT)
        assert _get_gcode_extruder_filament_type(pd, 5) is None

    def test_no_filament_data_returns_none(self) -> None:
        """No gcode_filament_data on printer_data returns None."""
        pd = _make_printer_data(None)
        assert _get_gcode_extruder_filament_type(pd, 0) is None

    def test_none_printer_data_returns_none(self) -> None:
        """None printer_data returns None."""
        assert _get_gcode_extruder_filament_type(None, 0) is None


class TestGetProxyExtruderWeight:
    """Weight should index per_slot_grams by physical slot directly."""

    def test_used_slot_returns_weight(self) -> None:
        """Slot 0 used in print returns its grams."""
        pd = _make_printer_data(PROXY_FILAMENT)
        assert _get_proxy_extruder_weight(pd, 0) == SLOT_0_GRAMS

    def test_unused_slot_returns_zero(self) -> None:
        """Slot 1 not used in print returns 0.0."""
        pd = _make_printer_data(PROXY_FILAMENT)
        assert _get_proxy_extruder_weight(pd, 1) == 0.0

    def test_all_four_slots(self) -> None:
        """All 4 slots return correct per_slot_grams values."""
        pd = _make_printer_data(PROXY_FILAMENT)
        weights = [_get_proxy_extruder_weight(pd, i) for i in range(4)]
        assert weights == [SLOT_0_GRAMS, 0.0, 0.0, SLOT_3_GRAMS]

    def test_out_of_range_returns_none(self) -> None:
        """Index beyond per_slot_grams returns None."""
        pd = _make_printer_data(PROXY_FILAMENT)
        assert _get_proxy_extruder_weight(pd, 5) is None

    def test_no_filament_data_returns_none(self) -> None:
        """No gcode_filament_data on printer_data returns None."""
        pd = _make_printer_data(None)
        assert _get_proxy_extruder_weight(pd, 0) is None

    def test_empty_per_slot_grams(self) -> None:
        """Empty per_slot_grams returns None for any index."""
        data = FileFilamentData(per_slot_grams=[])
        pd = _make_printer_data(data)
        assert _get_proxy_extruder_weight(pd, 0) is None


class TestGetGcodeExtruderAttributes:
    """Extra attributes should be slot-indexed with color from color_map."""

    def test_used_slot_has_color_and_all_metrics(self) -> None:
        """Slot 0 is in color_map — gets color plus all per-slot metrics."""
        pd = _make_printer_data(PROXY_FILAMENT)
        attrs = _get_gcode_extruder_attributes(pd, 0)
        assert attrs["color"] == "#FFFFFF"
        assert attrs["weight_grams"] == SLOT_0_GRAMS
        assert attrs["filament_mm"] == SLOT_0_MM
        assert attrs["filament_cm3"] == SLOT_0_CM3
        assert attrs["cost"] == SLOT_0_COST
        assert attrs["filament_name"] == "ElegooPLA-Basic-White"

    def test_unused_slot_has_metrics_but_no_color(self) -> None:
        """Slot 1 is not in color_map — no color, but per-slot metrics present."""
        pd = _make_printer_data(PROXY_FILAMENT)
        attrs = _get_gcode_extruder_attributes(pd, 1)
        assert "color" not in attrs
        assert attrs["weight_grams"] == 0.0
        assert attrs["filament_name"] == "ElegooPLA-Matte-Ruby Red"

    def test_slot_3_has_color_from_color_map(self) -> None:
        """Slot 3 is in color_map (t=3) — gets color and weight."""
        pd = _make_printer_data(PROXY_FILAMENT)
        attrs = _get_gcode_extruder_attributes(pd, 3)
        assert attrs["color"] == "#17656B"
        assert attrs["weight_grams"] == SLOT_3_GRAMS
        assert attrs["filament_name"] == "ElegooPLA-Metallic-Blue"

    def test_no_filament_data_returns_empty(self) -> None:
        """No gcode_filament_data returns empty dict."""
        pd = _make_printer_data(None)
        assert _get_gcode_extruder_attributes(pd, 0) == {}


class TestGetProxyExtruderWeightAttributes:
    """Weight sensor attributes should be slot-indexed."""

    def test_used_slot_has_type_and_color(self) -> None:
        """Slot 0 is in color_map — gets filament_type and color."""
        pd = _make_printer_data(PROXY_FILAMENT)
        attrs = _get_proxy_extruder_weight_attributes(pd, 0)
        assert attrs["filament_type"] == "PLA"
        assert attrs["color"] == "#FFFFFF"
        assert attrs["cost"] == SLOT_0_COST
        assert attrs["filament_name"] == "ElegooPLA-Basic-White"

    def test_unused_slot_has_cost_and_name(self) -> None:
        """Slot 1 not in color_map — no type/color, but cost and name."""
        pd = _make_printer_data(PROXY_FILAMENT)
        attrs = _get_proxy_extruder_weight_attributes(pd, 1)
        assert "filament_type" not in attrs
        assert "color" not in attrs
        assert attrs["cost"] == SLOT_1_COST
        assert attrs["filament_name"] == "ElegooPLA-Matte-Ruby Red"

    def test_slot_3_has_color_map_data(self) -> None:
        """Slot 3 in color_map — gets type, color, and name."""
        pd = _make_printer_data(PROXY_FILAMENT)
        attrs = _get_proxy_extruder_weight_attributes(pd, 3)
        assert attrs["filament_type"] == "PLA"
        assert attrs["color"] == "#17656B"
        assert attrs["filament_name"] == "ElegooPLA-Metallic-Blue"

    def test_no_filament_data_returns_empty(self) -> None:
        """No gcode_filament_data returns empty dict."""
        pd = _make_printer_data(None)
        assert _get_proxy_extruder_weight_attributes(pd, 0) == {}
