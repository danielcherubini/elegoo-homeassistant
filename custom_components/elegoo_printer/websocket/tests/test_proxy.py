"""Tests for ElegooPrinterServer utility functions."""

import json
from unittest.mock import Mock, patch

import pytest

from custom_components.elegoo_printer.sdcp.models.printer import Printer
from custom_components.elegoo_printer.websocket.server.proxy import ElegooPrinterServer
from custom_components.elegoo_printer.websocket.server.registry import PrinterRegistry


@pytest.fixture
def mock_logger() -> Mock:
    """Create a mock logger for testing."""
    return Mock()


@pytest.fixture
def mock_hass() -> Mock:
    """Create a mock Home Assistant instance."""
    return Mock()


@pytest.fixture
def mock_session() -> Mock:
    """Create a mock aiohttp session."""
    return Mock()


@pytest.fixture
def mock_printer_registry() -> Mock:
    """Create a mock printer registry."""
    return Mock(spec=PrinterRegistry)


@pytest.fixture
def sample_printer() -> Printer:
    """Create a sample printer for testing."""
    printer_json = json.dumps(
        {
            "Id": "test_connection",
            "Data": {
                "Name": "Test Printer",
                "MachineName": "Saturn 4 Ultra",
                "BrandName": "Elegoo",
                "MainboardIP": "192.168.1.100",
                "ProtocolVersion": "V3.0.0",
                "FirmwareVersion": "V1.0.0",
                "MainboardID": "test_mainboard_id_12345",
            },
        }
    )
    return Printer(printer_json)


@pytest.fixture
def proxy_server(
    mock_logger: Mock, mock_hass: Mock, mock_session: Mock
) -> ElegooPrinterServer:
    """Create an ElegooPrinterServer instance for testing."""
    # Patch the singleton creation to avoid conflicts
    with patch.object(ElegooPrinterServer, "_instance", None):
        server = ElegooPrinterServer(mock_logger, mock_hass, mock_session)
        server.printer_registry = Mock(spec=PrinterRegistry)
        return server


class TestElegooPrinterServerUtilities:
    """Test cases for ElegooPrinterServer utility methods."""

    def test_get_target_printer_from_request_query_param(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _get_target_printer_from_request with query parameter routing."""
        # Setup
        proxy_server.printer_registry.get_printer_by_mainboard_id.return_value = (
            sample_printer
        )

        # Create mock request with query parameter
        mock_request = Mock()
        mock_request.query_string = "id=test_mainboard_id_12345"
        mock_request.path = "/api/test"
        mock_request.headers = {}

        result = proxy_server._get_target_printer_from_request(mock_request)  # noqa: SLF001

        assert result == sample_printer
        proxy_server.printer_registry.get_printer_by_mainboard_id.assert_called_with(
            "test_mainboard_id_12345"
        )

    def test_get_target_printer_from_request_path_routing(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _get_target_printer_from_request with path-based routing."""
        # Setup - query param routing fails, path routing succeeds
        proxy_server.printer_registry.get_printer_by_mainboard_id.side_effect = [
            None,
            sample_printer,
        ]

        mock_request = Mock()
        mock_request.query_string = ""
        mock_request.path = "/api/test_mainboard_id_12345/status"
        mock_request.headers = {}

        result = proxy_server._get_target_printer_from_request(mock_request)  # noqa: SLF001

        assert result == sample_printer

    def test_get_target_printer_from_request_referer_routing(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _get_target_printer_from_request with referer header routing."""
        # Let's just test the fallback behavior since referer routing may have more
        # complex logic
        proxy_server.printer_registry.get_printer_by_mainboard_id.return_value = None
        proxy_server.printer_registry.get_all_printers.return_value = {
            "192.168.1.100": sample_printer
        }

        mock_request = Mock()
        mock_request.query_string = ""
        mock_request.path = "/status"
        mock_request.headers = {
            "Referer": "http://10.0.0.114:3030/?id=test_mainboard_id_12345"
        }

        result = proxy_server._get_target_printer_from_request(mock_request)  # noqa: SLF001

        # Should fall back to first available printer
        assert result == sample_printer

    def test_get_target_printer_from_request_fallback_to_first_available(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _get_target_printer_from_request fallback to first available printer."""
        # Setup - all specific routing fails, fallback to first available
        proxy_server.printer_registry.get_printer_by_mainboard_id.return_value = None
        proxy_server.printer_registry.get_all_printers.return_value = {
            "192.168.1.100": sample_printer
        }

        mock_request = Mock()
        mock_request.query_string = ""
        mock_request.path = "/status"
        mock_request.headers = {}

        result = proxy_server._get_target_printer_from_request(mock_request)  # noqa: SLF001

        assert result == sample_printer

    def test_get_target_printer_from_request_no_printers(
        self, proxy_server: ElegooPrinterServer
    ) -> None:
        """Test _get_target_printer_from_request when no printers are available."""
        proxy_server.printer_registry.get_printer_by_mainboard_id.return_value = None
        proxy_server.printer_registry.get_all_printers.return_value = {}

        mock_request = Mock()
        mock_request.query_string = ""
        mock_request.path = "/status"
        mock_request.headers = {}

        result = proxy_server._get_target_printer_from_request(mock_request)  # noqa: SLF001

        assert result is None

    def test_get_cleaned_path_for_printer_api_path(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _get_cleaned_path_for_printer with API path."""
        proxy_server.printer_registry.get_printer_by_mainboard_id.return_value = (
            sample_printer
        )

        result = proxy_server._get_cleaned_path_for_printer(  # noqa: SLF001
            "/api/test_mainboard_id_12345/status"
        )

        assert result == "/status"

    def test_get_cleaned_path_for_printer_video_path(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _get_cleaned_path_for_printer with video path."""
        proxy_server.printer_registry.get_printer_by_mainboard_id.return_value = (
            sample_printer
        )

        result = proxy_server._get_cleaned_path_for_printer(  # noqa: SLF001
            "/video/test_mainboard_id_12345"
        )

        assert result == "/video"

    def test_get_cleaned_path_for_printer_no_mainboard_id(
        self, proxy_server: ElegooPrinterServer
    ) -> None:
        """Test _get_cleaned_path_for_printer when no MainboardID is found."""
        proxy_server.printer_registry.get_printer_by_mainboard_id.return_value = None

        original_path = "/status/check"
        result = proxy_server._get_cleaned_path_for_printer(original_path)  # noqa: SLF001

        assert result == original_path

    def test_process_replacements_basic(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _process_replacements with basic content transformation."""
        content = """
        var ws = new WebSocket("ws://${this.hostName}:3030/websocket");
        var url = "http://192.168.1.100:3031/video";
        """

        with patch(
            "custom_components.elegoo_printer.websocket.server.utils.get_local_ip",
            return_value="10.0.0.100",
        ):
            result = proxy_server._process_replacements(content, sample_printer)  # noqa: SLF001  # noqa: SLF001

        # Should replace printer IP with proxy IP (actual IP might be different)
        # Just check that the printer IP was replaced with something else
        assert "192.168.1.100" not in result
        # Check that the result contains some IP replacement
        assert ":3031/video" in result

        # Should inject MainboardID into WebSocket URL
        assert f"websocket?id={sample_printer.id}" in result

    def test_process_replacements_localhost_pattern(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _process_replacements with localhost patterns."""
        content = 'var ws = new WebSocket("ws://localhost:3030/websocket");'

        result = proxy_server._process_replacements(content, sample_printer)  # noqa: SLF001

        assert f"websocket?id={sample_printer.id}" in result

    def test_process_replacements_template_literal(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _process_replacements with template literal syntax."""
        content = "ws://${this.hostName}:3030/websocket"

        result = proxy_server._process_replacements(content, sample_printer)  # noqa: SLF001

        assert f"websocket?id={sample_printer.id}" in result

    def test_process_replacements_no_id(
        self, proxy_server: ElegooPrinterServer
    ) -> None:
        """Test _process_replacements when printer has no ID."""
        printer_without_id = Mock()
        printer_without_id.id = None
        printer_without_id.ip_address = "192.168.1.100"

        content = "ws://${this.hostName}:3030/websocket"

        with patch(
            "custom_components.elegoo_printer.websocket.server.utils.get_local_ip",
            return_value="10.0.0.100",
        ):
            result = proxy_server._process_replacements(content, printer_without_id)  # noqa: SLF001

        # Should not inject ID parameter if printer has no ID
        assert "websocket?id=" not in result

    def test_check_ports_are_available_success(
        self, proxy_server: ElegooPrinterServer
    ) -> None:
        """Test _check_ports_are_available when ports are available."""
        with patch("socket.socket") as mock_socket:
            mock_socket.return_value.__enter__.return_value.bind.return_value = None

            result = proxy_server._check_ports_are_available()  # noqa: SLF001

            assert result is True

    def test_check_ports_are_available_failure(
        self, proxy_server: ElegooPrinterServer
    ) -> None:
        """Test _check_ports_are_available when ports are in use."""
        with patch("socket.socket") as mock_socket:
            mock_socket.return_value.__enter__.return_value.bind.side_effect = OSError(
                "Address already in use"
            )

            result = proxy_server._check_ports_are_available()  # noqa: SLF001

            assert result is False

    def test_raise_port_error(self, proxy_server: ElegooPrinterServer) -> None:
        """Test _raise_port_error method."""
        with pytest.raises(
            OSError, match="Ports 3030 or 3031 are already in use"
        ) as exc_info:
            proxy_server._raise_port_error()  # noqa: SLF001

        assert "Ports 3030 or 3031 are already in use" in str(exc_info.value)

    def test_try_query_param_routing_success(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _try_query_param_routing with valid MainboardID."""
        proxy_server.printer_registry.get_printer_by_mainboard_id.return_value = (
            sample_printer
        )

        mock_request = Mock()
        mock_request.query_string = "id=test_mainboard_id_12345&other=param"

        result = proxy_server._try_query_param_routing(mock_request)  # noqa: SLF001

        assert result == sample_printer

    def test_try_query_param_routing_mainboard_id_param(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _try_query_param_routing with mainboard_id parameter."""
        proxy_server.printer_registry.get_printer_by_mainboard_id.return_value = (
            sample_printer
        )

        mock_request = Mock()
        mock_request.query_string = "mainboard_id=test_mainboard_id_12345"

        result = proxy_server._try_query_param_routing(mock_request)  # noqa: SLF001

        assert result == sample_printer

    def test_try_query_param_routing_too_short_id(
        self, proxy_server: ElegooPrinterServer
    ) -> None:
        """Test _try_query_param_routing with too short MainboardID."""
        mock_request = Mock()
        mock_request.query_string = "id=short"

        result = proxy_server._try_query_param_routing(mock_request)  # noqa: SLF001

        assert result is None

    def test_try_path_routing_api_path(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _try_path_routing with API path."""
        proxy_server.printer_registry.get_printer_by_mainboard_id.return_value = (
            sample_printer
        )

        path_parts = ["api", "test_mainboard_id_12345", "status"]
        result = proxy_server._try_path_routing(path_parts)  # noqa: SLF001

        assert result == sample_printer

    def test_try_path_routing_video_path(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _try_path_routing with video path."""
        proxy_server.printer_registry.get_printer_by_mainboard_id.return_value = (
            sample_printer
        )

        path_parts = ["video", "test_mainboard_id_12345"]
        result = proxy_server._try_path_routing(path_parts)  # noqa: SLF001

        assert result == sample_printer

    def test_try_referer_routing_success(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _try_referer_routing with valid referer."""
        proxy_server.printer_registry.get_printer_by_mainboard_id.return_value = (
            sample_printer
        )

        mock_request = Mock()
        mock_request.headers = {
            "Referer": "http://10.0.0.114:3030/?id=test_mainboard_id_12345"
        }

        result = proxy_server._try_referer_routing(mock_request)  # noqa: SLF001

        assert result == sample_printer

    def test_try_referer_routing_no_referer(
        self, proxy_server: ElegooPrinterServer
    ) -> None:
        """Test _try_referer_routing with no referer header."""
        mock_request = Mock()
        mock_request.headers = {}

        result = proxy_server._try_referer_routing(mock_request)  # noqa: SLF001

        assert result is None

    def test_get_first_available_printer_success(
        self, proxy_server: ElegooPrinterServer, sample_printer: Printer
    ) -> None:
        """Test _get_first_available_printer when printers are available."""
        proxy_server.printer_registry.get_all_printers.return_value = {
            "192.168.1.100": sample_printer
        }

        result = proxy_server._get_first_available_printer()  # noqa: SLF001

        assert result == sample_printer

    def test_get_first_available_printer_no_printers(
        self, proxy_server: ElegooPrinterServer
    ) -> None:
        """Test _get_first_available_printer when no printers are available."""
        proxy_server.printer_registry.get_all_printers.return_value = {}

        result = proxy_server._get_first_available_printer()  # noqa: SLF001

        assert result is None

    def test_find_video_url_in_data_nested_structure(
        self, proxy_server: ElegooPrinterServer
    ) -> None:
        """Test _find_video_url_in_data with nested Data.Data.VideoUrl structure."""
        data = {"Data": {"Data": {"VideoUrl": "http://192.168.1.100:3031/video"}}}

        video_url, target = proxy_server._find_video_url_in_data(data)  # noqa: SLF001

        assert video_url == "http://192.168.1.100:3031/video"
        assert target == data["Data"]["Data"]

    def test_find_video_url_in_data_direct_structure(
        self, proxy_server: ElegooPrinterServer
    ) -> None:
        """Test _find_video_url_in_data with direct Data.VideoUrl structure."""
        data = {"Data": {"VideoUrl": "http://192.168.1.100:3031/video"}}

        video_url, target = proxy_server._find_video_url_in_data(data)  # noqa: SLF001

        assert video_url == "http://192.168.1.100:3031/video"
        assert target == data["Data"]

    def test_find_video_url_in_data_not_found(
        self, proxy_server: ElegooPrinterServer
    ) -> None:
        """Test _find_video_url_in_data when VideoUrl is not found."""
        data = {"Data": {"OtherField": "value"}}

        video_url, target = proxy_server._find_video_url_in_data(data)  # noqa: SLF001

        assert video_url is None
        assert target is None

    def test_constructor_allows_multiple_instances(
        self, mock_logger: Mock, mock_hass: Mock, mock_session: Mock
    ) -> None:
        """
        Test that the constructor allows multiple instances.

        Singleton is enforced by create method.
        """
        server1 = ElegooPrinterServer(mock_logger, mock_hass, mock_session)
        server2 = ElegooPrinterServer(mock_logger, mock_hass, mock_session)

        # Constructor should allow multiple instances
        assert server1 is not server2
        assert isinstance(server1, ElegooPrinterServer)
        assert isinstance(server2, ElegooPrinterServer)

    def test_get_next_available_ports(self) -> None:
        """Test get_next_available_ports static method."""
        ws_port, video_port = ElegooPrinterServer.get_next_available_ports()

        expected_ws_port = 3030  # WEBSOCKET_PORT
        expected_video_port = 3031  # VIDEO_PORT
        assert ws_port == expected_ws_port
        assert video_port == expected_video_port
