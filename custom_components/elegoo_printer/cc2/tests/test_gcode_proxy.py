"""Tests for GCodeProxyClient."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.gcode_proxy import GCodeProxyClient


def _response_context(resp: AsyncMock) -> MagicMock:
    """Async context manager that yields resp (matches aiohttp session.get)."""
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=resp)
    context_manager.__aexit__ = AsyncMock(return_value=None)
    return context_manager


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
        session.get = MagicMock(return_value=_response_context(resp))

        result = asyncio.run(client.fetch_filament_data("CC2_benchy.gcode"))

        assert result == SAMPLE_RESPONSE
        session.get.assert_called_once()

    def test_404_returns_none(self) -> None:
        """404 response returns None."""
        client, session = _make_client()
        resp = AsyncMock()
        resp.status = 404
        session.get = MagicMock(return_value=_response_context(resp))

        result = asyncio.run(client.fetch_filament_data("unknown.gcode"))

        assert result is None

    def test_timeout_returns_none(self) -> None:
        """Timeout returns None."""
        client, session = _make_client()
        failing = MagicMock()
        failing.__aenter__ = AsyncMock(side_effect=TimeoutError)
        failing.__aexit__ = AsyncMock(return_value=None)
        session.get = MagicMock(return_value=failing)

        result = asyncio.run(client.fetch_filament_data("test.gcode"))

        assert result is None

    def test_connection_error_returns_none(self) -> None:
        """Connection error returns None."""
        client, session = _make_client()
        failing = MagicMock()
        failing.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError)
        failing.__aexit__ = AsyncMock(return_value=None)
        session.get = MagicMock(return_value=failing)

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
        session.get = MagicMock(return_value=_response_context(resp))

        assert asyncio.run(client.check_health()) is True

    def test_unhealthy_status(self) -> None:
        """Non-200 status returns False."""
        client, session = _make_client()
        resp = AsyncMock()
        resp.status = 500
        session.get = MagicMock(return_value=_response_context(resp))

        assert asyncio.run(client.check_health()) is False

    def test_unreachable(self) -> None:
        """Unreachable proxy returns False."""
        client, session = _make_client()
        failing = MagicMock()
        failing.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError)
        failing.__aexit__ = AsyncMock(return_value=None)
        session.get = MagicMock(return_value=failing)

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


class TestRequestProxyFilamentEndToEnd:
    """Exercise ``_request_proxy_filament`` with a real client and mock proxy."""

    def test_caches_payload_and_refreshes_gcode_filament_data(self) -> None:
        """Proxy JSON is stored under ``_file_details`` and mapper sees it."""

        async def _run() -> None:
            proxy = AsyncMock()
            proxy.fetch_filament_data = AsyncMock(return_value=SAMPLE_RESPONSE)
            client = ElegooCC2Client(
                printer_ip="192.0.2.1",
                serial_number="test-serial",
                gcode_proxy=proxy,
            )
            client._cached_status = {
                "machine_status": {"status": 2},
                "print_status": {"filename": "CC2_benchy.gcode"},
            }
            await client._request_proxy_filament("CC2_benchy.gcode")

            proxy.fetch_filament_data.assert_awaited_once_with("CC2_benchy.gcode")
            cached = client._integration_data["_file_details"]["CC2_benchy.gcode"]
            assert cached["proxy_filament"] == SAMPLE_RESPONSE
            assert client.printer_data.gcode_filament_data is not None
            filament = client.printer_data.gcode_filament_data
            assert filament.filename == "CC2_benchy.gcode"
            assert filament.per_slot_grams == [1.1, 0.6, 0.0, 0.0]
            assert filament.slicer_version == "ElegooSlicer 1.3.2.9"

        asyncio.run(_run())

    def test_empty_proxy_response_skips_cache_and_status_refresh(self) -> None:
        """Falsy API payload does not mutate integration data."""

        async def _run() -> None:
            proxy = AsyncMock()
            proxy.fetch_filament_data = AsyncMock(return_value=None)
            client = ElegooCC2Client(
                printer_ip="192.0.2.1",
                serial_number="test-serial",
                gcode_proxy=proxy,
            )
            client._cached_status = {
                "print_status": {"filename": "job.gcode"},
            }
            await client._request_proxy_filament("job.gcode")

            assert client._integration_data == {}
            assert client.printer_data.gcode_filament_data is None

        asyncio.run(_run())

    def test_fetch_oserror_is_swallowed(self) -> None:
        """Network errors are logged and do not propagate."""

        async def _run() -> None:
            proxy = AsyncMock()
            proxy.fetch_filament_data = AsyncMock(side_effect=OSError("boom"))
            client = ElegooCC2Client(
                printer_ip="192.0.2.1",
                serial_number="test-serial",
                gcode_proxy=proxy,
            )
            client._cached_status = {"print_status": {"filename": "x.gcode"}}
            await client._request_proxy_filament("x.gcode")

            assert "_file_details" not in client._integration_data

        asyncio.run(_run())
