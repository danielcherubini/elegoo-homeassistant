"""Tests for CC2 file detail response handling."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.elegoo_printer.cc2.models import CC2StatusMapper
from custom_components.elegoo_printer.sdcp.models.printer import FileFilamentData

TOTAL_LAYERS = 722
TOTAL_FILAMENT_USED = 24.8
PRINT_TIME = 4690
COLOR_MAP_SINGLE = [{"color": "#0B6283", "name": "PLA", "t": 3}]

FILE_DETAIL_FULL = {
    "total_filament_used": TOTAL_FILAMENT_USED,
    "color_map": COLOR_MAP_SINGLE,
    "print_time": PRINT_TIME,
    "layer": TOTAL_LAYERS,
}


class TestHandleFileDetailResponse:
    """Test _handle_file_detail_response caches filament data."""

    def _make_client(self) -> MagicMock:
        """Create a minimal mock CC2 client with the real handler method."""
        from custom_components.elegoo_printer.cc2.client import ElegooCC2Client

        client = MagicMock(spec=ElegooCC2Client)
        client._cached_status = {}
        client._integration_data = {}
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

        client._handle_file_detail_response("test.gcode", FILE_DETAIL_FULL)

        details = client._integration_data["_file_details"]["test.gcode"]
        assert details["TotalLayers"] == TOTAL_LAYERS
        assert details["total_filament_used"] == TOTAL_FILAMENT_USED
        assert details["color_map"] == COLOR_MAP_SINGLE
        assert details["print_time"] == PRINT_TIME

    def test_caches_only_total_layers(self) -> None:
        """Response with only TotalLayers still caches."""
        client = self._make_client()
        result = {"TotalLayers": 500}

        client._handle_file_detail_response("test.gcode", result)

        details = client._integration_data["_file_details"]["test.gcode"]
        assert details["TotalLayers"] == 500  # noqa: PLR2004
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

        details = client._integration_data["_file_details"]["test.gcode"]
        assert details["total_filament_used"] == 10.5  # noqa: PLR2004
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

        details = client._integration_data["_file_details"]["multi.gcode"]
        assert len(details["color_map"]) == 4  # noqa: PLR2004
        assert details["color_map"][3]["name"] == "ABS"

    def test_empty_response_not_cached(self) -> None:
        """Response with no usable data is not cached."""
        client = self._make_client()
        result = {"some_other_key": "value"}

        client._handle_file_detail_response("empty.gcode", result)

        assert "empty.gcode" not in client._integration_data.get("_file_details", {})

    def test_preserves_existing_proxy_filament(self) -> None:
        """File detail arriving after proxy must not nuke proxy_filament."""
        client = self._make_client()
        client._integration_data["_file_details"] = {
            "test.gcode": {
                "proxy_filament": {
                    "filename": "test.gcode",
                    "filament": {
                        "per_slot_grams": [0.0, 0.0, 0.94, 11.95],
                        "total_filament_changes": 1,
                    },
                },
            },
        }
        client._handle_file_detail_response("test.gcode", FILE_DETAIL_FULL)

        details = client._integration_data["_file_details"]["test.gcode"]
        assert details["TotalLayers"] == TOTAL_LAYERS
        assert details["total_filament_used"] == TOTAL_FILAMENT_USED
        assert details["color_map"] == COLOR_MAP_SINGLE
        assert details["print_time"] == PRINT_TIME
        assert details["proxy_filament"]["filament"]["per_slot_grams"] == [
            0.0,
            0.0,
            0.94,
            11.95,
        ]

    def test_proxy_arriving_after_file_detail_merges(self) -> None:
        """Proxy data arriving after file detail adds to existing entry."""
        client = self._make_client()
        client._handle_file_detail_response("test.gcode", FILE_DETAIL_FULL)

        details = client._integration_data["_file_details"]["test.gcode"]
        details["proxy_filament"] = {
            "filename": "test.gcode",
            "filament": {"per_slot_grams": [1.0, 2.0]},
        }

        assert details["TotalLayers"] == TOTAL_LAYERS
        assert details["proxy_filament"]["filament"]["per_slot_grams"] == [1.0, 2.0]

    def test_empty_color_map_not_cached_as_filament(self) -> None:
        """Empty color_map without other filament data is not cached."""
        client = self._make_client()
        result = {"color_map": [], "layer": 200}

        client._handle_file_detail_response("test.gcode", result)

        details = client._integration_data["_file_details"]["test.gcode"]
        assert details["TotalLayers"] == 200  # noqa: PLR2004
        assert "color_map" not in details

    def test_zero_total_filament_is_cached(self) -> None:
        """total_filament_used=0 is still a valid value and should be cached."""
        client = self._make_client()
        result = {"total_filament_used": 0, "layer": 100}

        client._handle_file_detail_response("test.gcode", result)

        details = client._integration_data["_file_details"]["test.gcode"]
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
                    "TotalLayers": TOTAL_LAYERS,
                    "total_filament_used": TOTAL_FILAMENT_USED,
                    "color_map": COLOR_MAP_SINGLE,
                    "print_time": PRINT_TIME,
                },
            },
        }

        result = CC2StatusMapper.map_filament_data(cc2_data, "test.gcode")

        assert isinstance(result, FileFilamentData)
        assert result.total_filament_used == TOTAL_FILAMENT_USED
        assert len(result.color_map) == 1
        assert result.color_map[0]["name"] == "PLA"
        assert result.print_time == PRINT_TIME
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
        assert result.total_filament_used == 10.0  # noqa: PLR2004
        assert result.color_map == []

    def test_wrong_filename_returns_none(self) -> None:
        """Requesting data for a different filename returns None."""
        cc2_data = {
            "_file_details": {
                "other.gcode": {"total_filament_used": 10.0},
            },
        }
        assert CC2StatusMapper.map_filament_data(cc2_data, "test.gcode") is None
