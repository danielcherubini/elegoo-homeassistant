"""Tests for CC2 file detail response handling."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.elegoo_printer.cc2.models import CC2StatusMapper
from custom_components.elegoo_printer.sdcp.models.printer import FileFilamentData


class TestHandleFileDetailResponse:
    """Test _handle_file_detail_response caches filament data."""

    def _make_client(self) -> MagicMock:
        """Create a minimal mock CC2 client with the real handler method."""
        from custom_components.elegoo_printer.cc2.client import ElegooCC2Client

        client = MagicMock(spec=ElegooCC2Client)
        client._cached_status = {}
        client.logger = MagicMock()
        client._handle_file_detail_response = (
            ElegooCC2Client._handle_file_detail_response.__get__(
                client, ElegooCC2Client
            )
        )
        return client

    def test_caches_total_filament_and_color_map(self) -> None:
        """Full response caches all filament fields."""
        client = self._make_client()
        result = {
            "total_filament_used": 24.8,
            "color_map": [{"color": "#0B6283", "name": "PLA", "t": 3}],
            "print_time": 4690,
            "layer": 722,
        }

        client._handle_file_detail_response("test.gcode", result)

        details = client._cached_status["_file_details"]["test.gcode"]
        assert details["TotalLayers"] == 722
        assert details["total_filament_used"] == 24.8
        assert details["color_map"] == [{"color": "#0B6283", "name": "PLA", "t": 3}]
        assert details["print_time"] == 4690

    def test_caches_only_total_layers(self) -> None:
        """Response with only TotalLayers still caches."""
        client = self._make_client()
        result = {"TotalLayers": 500}

        client._handle_file_detail_response("test.gcode", result)

        details = client._cached_status["_file_details"]["test.gcode"]
        assert details["TotalLayers"] == 500
        assert "total_filament_used" not in details
        assert "color_map" not in details

    def test_caches_filament_without_layers(self) -> None:
        """Response with filament data but no layers still caches."""
        client = self._make_client()
        result = {
            "total_filament_used": 10.5,
            "color_map": [{"color": "#FF0000", "name": "PETG", "t": 0}],
        }

        client._handle_file_detail_response("test.gcode", result)

        details = client._cached_status["_file_details"]["test.gcode"]
        assert details["total_filament_used"] == 10.5
        assert details["color_map"] == [{"color": "#FF0000", "name": "PETG", "t": 0}]
        assert "TotalLayers" not in details

    def test_multi_extruder_color_map(self) -> None:
        """Multi-extruder color_map is fully preserved."""
        client = self._make_client()
        color_map = [
            {"color": "#FF0000", "name": "PLA", "t": 0},
            {"color": "#00FF00", "name": "PETG", "t": 1},
            {"color": "#0000FF", "name": "TPU", "t": 2},
            {"color": "#FFFFFF", "name": "ABS", "t": 3},
        ]
        result = {"total_filament_used": 50.0, "color_map": color_map, "layer": 100}

        client._handle_file_detail_response("multi.gcode", result)

        details = client._cached_status["_file_details"]["multi.gcode"]
        assert len(details["color_map"]) == 4
        assert details["color_map"][3]["name"] == "ABS"

    def test_empty_response_not_cached(self) -> None:
        """Response with no usable data is not cached."""
        client = self._make_client()
        result = {"some_other_key": "value"}

        client._handle_file_detail_response("empty.gcode", result)

        assert "empty.gcode" not in client._cached_status.get("_file_details", {})

    def test_empty_color_map_not_cached_as_filament(self) -> None:
        """Empty color_map without other filament data is not cached."""
        client = self._make_client()
        result = {"color_map": [], "layer": 200}

        client._handle_file_detail_response("test.gcode", result)

        details = client._cached_status["_file_details"]["test.gcode"]
        assert details["TotalLayers"] == 200
        assert "color_map" not in details

    def test_zero_total_filament_is_cached(self) -> None:
        """total_filament_used=0 is still a valid value and should be cached."""
        client = self._make_client()
        result = {"total_filament_used": 0, "layer": 100}

        client._handle_file_detail_response("test.gcode", result)

        details = client._cached_status["_file_details"]["test.gcode"]
        assert details["total_filament_used"] == 0


class TestMapFilamentData:
    """Test CC2StatusMapper.map_filament_data."""

    def test_returns_none_without_filename(self) -> None:
        """No filename means no filament data."""
        assert CC2StatusMapper.map_filament_data({}, None) is None

    def test_returns_none_without_file_details(self) -> None:
        """Missing _file_details returns None."""
        assert CC2StatusMapper.map_filament_data({}, "test.gcode") is None

    def test_returns_none_without_filament_fields(self) -> None:
        """File details with only TotalLayers returns None."""
        cc2_data = {
            "_file_details": {"test.gcode": {"TotalLayers": 500}},
        }
        assert CC2StatusMapper.map_filament_data(cc2_data, "test.gcode") is None

    def test_maps_full_filament_data(self) -> None:
        """Full filament data maps correctly."""
        cc2_data = {
            "_file_details": {
                "test.gcode": {
                    "TotalLayers": 722,
                    "total_filament_used": 24.8,
                    "color_map": [{"color": "#0B6283", "name": "PLA", "t": 3}],
                    "print_time": 4690,
                },
            },
        }

        result = CC2StatusMapper.map_filament_data(cc2_data, "test.gcode")

        assert isinstance(result, FileFilamentData)
        assert result.total_filament_used == 24.8
        assert len(result.color_map) == 1
        assert result.color_map[0]["name"] == "PLA"
        assert result.print_time == 4690
        assert result.filename == "test.gcode"

    def test_maps_color_map_only(self) -> None:
        """Color map without total_filament_used still maps."""
        cc2_data = {
            "_file_details": {
                "test.gcode": {
                    "color_map": [{"color": "#FF0000", "name": "PETG", "t": 0}],
                },
            },
        }

        result = CC2StatusMapper.map_filament_data(cc2_data, "test.gcode")

        assert result is not None
        assert result.total_filament_used is None
        assert len(result.color_map) == 1

    def test_maps_total_filament_only(self) -> None:
        """total_filament_used without color_map still maps."""
        cc2_data = {
            "_file_details": {
                "test.gcode": {"total_filament_used": 10.0},
            },
        }

        result = CC2StatusMapper.map_filament_data(cc2_data, "test.gcode")

        assert result is not None
        assert result.total_filament_used == 10.0
        assert result.color_map == []

    def test_wrong_filename_returns_none(self) -> None:
        """Requesting data for a different filename returns None."""
        cc2_data = {
            "_file_details": {
                "other.gcode": {"total_filament_used": 10.0},
            },
        }
        assert CC2StatusMapper.map_filament_data(cc2_data, "test.gcode") is None
