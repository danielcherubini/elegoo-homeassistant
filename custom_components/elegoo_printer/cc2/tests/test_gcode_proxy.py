"""Tests for GCodeProxyClient."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from custom_components.elegoo_printer.cc2.gcode_proxy import GCodeProxyClient

SAMPLE_RESPONSE = {
    "filename": "CC2_benchy.gcode",
    "slicer_version": "ElegooSlicer 1.3.2.9",
    "filament": {
        "per_slot_grams": [1.1, 0.6, 0.0, 0.0],
        "per_slot_cost": [0.41, 0.24, 0.0, 0.0],
        "filament_names": ["ElegooPLA-Basic-White", "ElegooPLA-Matte-Ruby Red"],
        "total_cost": 0.65,
        "total_filament_changes": 46,
    },
}


def _make_client() -> tuple[GCodeProxyClient, MagicMock]:
    """Create a proxy client with a mock session."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = GCodeProxyClient("http://192.168.50.49", session)
    return client, session


class TestFetchFilamentData:
    """Test fetch_filament_data with mocked HTTP responses."""

    def test_success(self) -> None:
        """Successful response returns parsed JSON."""
        client, session = _make_client()
        resp = AsyncMock()
        resp.status = 200
        resp.json = AsyncMock(return_value=SAMPLE_RESPONSE)
        session.get = AsyncMock(return_value=resp)

        result = asyncio.run(client.fetch_filament_data("CC2_benchy.gcode"))

        assert result == SAMPLE_RESPONSE
        session.get.assert_called_once()

    def test_404_returns_none(self) -> None:
        """404 response returns None."""
        client, session = _make_client()
        resp = AsyncMock()
        resp.status = 404
        session.get = AsyncMock(return_value=resp)

        result = asyncio.run(client.fetch_filament_data("unknown.gcode"))

        assert result is None

    def test_timeout_returns_none(self) -> None:
        """Timeout returns None."""
        client, session = _make_client()
        session.get = AsyncMock(side_effect=TimeoutError)

        result = asyncio.run(client.fetch_filament_data("test.gcode"))

        assert result is None

    def test_connection_error_returns_none(self) -> None:
        """Connection error returns None."""
        client, session = _make_client()
        session.get = AsyncMock(side_effect=aiohttp.ClientError)

        result = asyncio.run(client.fetch_filament_data("test.gcode"))

        assert result is None


class TestCheckHealth:
    """Test check_health with mocked HTTP responses."""

    def test_healthy(self) -> None:
        """Healthy proxy returns True."""
        client, session = _make_client()
        resp = AsyncMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"status": "ok"})
        session.get = AsyncMock(return_value=resp)

        assert asyncio.run(client.check_health()) is True

    def test_unhealthy_status(self) -> None:
        """Non-200 status returns False."""
        client, session = _make_client()
        resp = AsyncMock()
        resp.status = 500
        session.get = AsyncMock(return_value=resp)

        assert asyncio.run(client.check_health()) is False

    def test_unreachable(self) -> None:
        """Unreachable proxy returns False."""
        client, session = _make_client()
        session.get = AsyncMock(side_effect=aiohttp.ClientError)

        assert asyncio.run(client.check_health()) is False


class TestMapFilamentDataWithProxy:
    """Test CC2StatusMapper.map_filament_data merges proxy data correctly."""

    def test_merges_mqtt_and_proxy_data(self) -> None:
        """Both MQTT and proxy data merge into a single FileFilamentData."""
        from custom_components.elegoo_printer.cc2.models import CC2StatusMapper

        cc2_data = {
            "_file_details": {
                "test.gcode": {
                    "total_filament_used": 24.8,
                    "color_map": [{"color": "#0B6283", "name": "PLA", "t": 3}],
                    "print_time": 4690,
                    "proxy_filament": SAMPLE_RESPONSE,
                },
            },
        }

        result = CC2StatusMapper.map_filament_data(cc2_data, "test.gcode")

        assert result is not None
        assert result.total_filament_used == 24.8  # noqa: PLR2004
        assert result.per_slot_grams == [1.1, 0.6, 0.0, 0.0]
        assert result.per_slot_cost == [0.41, 0.24, 0.0, 0.0]
        assert result.filament_names == [
            "ElegooPLA-Basic-White",
            "ElegooPLA-Matte-Ruby Red",
        ]
        assert result.total_cost == 0.65  # noqa: PLR2004
        assert result.total_filament_changes == 46  # noqa: PLR2004
        assert result.slicer_version == "ElegooSlicer 1.3.2.9"

    def test_proxy_only_no_mqtt(self) -> None:
        """Proxy data alone should produce a result."""
        from custom_components.elegoo_printer.cc2.models import CC2StatusMapper

        cc2_data = {
            "_file_details": {
                "test.gcode": {
                    "proxy_filament": SAMPLE_RESPONSE,
                },
            },
        }

        result = CC2StatusMapper.map_filament_data(cc2_data, "test.gcode")

        assert result is not None
        assert result.total_filament_used is None
        assert result.per_slot_grams == [1.1, 0.6, 0.0, 0.0]

    def test_mqtt_only_no_proxy(self) -> None:
        """MQTT data without proxy still works (Phase 1 behavior)."""
        from custom_components.elegoo_printer.cc2.models import CC2StatusMapper

        cc2_data = {
            "_file_details": {
                "test.gcode": {
                    "total_filament_used": 10.0,
                    "color_map": [{"color": "#FF0000", "name": "PETG", "t": 0}],
                },
            },
        }

        result = CC2StatusMapper.map_filament_data(cc2_data, "test.gcode")

        assert result is not None
        assert result.total_filament_used == 10.0  # noqa: PLR2004
        assert result.per_slot_grams == []
        assert result.filament_names == []
