"""Tests for the DiscoveryProtocol class."""

import json
import time
from unittest.mock import Mock, patch

import pytest

from custom_components.elegoo_printer.const import DISCOVERY_MESSAGE
from custom_components.elegoo_printer.sdcp.models.printer import Printer
from custom_components.elegoo_printer.websocket.server.discovery import (
    DiscoveryProtocol,
)
from custom_components.elegoo_printer.websocket.server.registry import PrinterRegistry
from custom_components.elegoo_printer.websocket.server.utils import (
    DISCOVERY_RATE_LIMIT_SECONDS,
)


@pytest.fixture
def mock_logger() -> Mock:
    """Create a mock logger for testing."""
    return Mock()


@pytest.fixture
def mock_printer_registry() -> Mock:
    """Create a mock printer registry for testing."""
    return Mock(spec=PrinterRegistry)


@pytest.fixture
def discovery_protocol(
    mock_logger: Mock, mock_printer_registry: Mock
) -> DiscoveryProtocol:
    """Create a DiscoveryProtocol instance for testing."""
    return DiscoveryProtocol(mock_logger, mock_printer_registry, "10.0.0.100")


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


class TestDiscoveryProtocol:
    """Test cases for DiscoveryProtocol."""

    def test_initialization(
        self,
        discovery_protocol: DiscoveryProtocol,
        mock_logger: Mock,
        mock_printer_registry: Mock,
    ) -> None:
        """Test that DiscoveryProtocol initializes correctly."""
        assert discovery_protocol.logger == mock_logger
        assert discovery_protocol.printer_registry == mock_printer_registry
        assert discovery_protocol.proxy_ip == "10.0.0.100"
        assert discovery_protocol.transport is None
        assert discovery_protocol._last_discovery_time == 0.0  # noqa: SLF001

    def test_connection_made(self, discovery_protocol: DiscoveryProtocol) -> None:
        """Test connection_made method."""
        mock_transport = Mock()
        discovery_protocol.connection_made(mock_transport)
        assert discovery_protocol.transport == mock_transport

    def test_datagram_received_invalid_message(
        self, discovery_protocol: DiscoveryProtocol, mock_logger: Mock
    ) -> None:
        """Test datagram_received with invalid discovery message."""
        discovery_protocol.datagram_received(b"invalid message", ("127.0.0.1", 12345))

        # Should not log anything since it's not a valid discovery message
        mock_logger.debug.assert_not_called()

    def test_datagram_received_rate_limiting(
        self, discovery_protocol: DiscoveryProtocol, mock_logger: Mock
    ) -> None:
        """Test rate limiting in datagram_received."""
        discovery_protocol._last_discovery_time = time.time()  # noqa: SLF001

        discovery_protocol.datagram_received(
            DISCOVERY_MESSAGE.encode(), ("127.0.0.1", 12345)
        )

        # Should log rate limiting message
        mock_logger.debug.assert_called_once()
        assert "Rate limiting" in mock_logger.debug.call_args[0][0]

    def test_datagram_received_no_printers(
        self,
        discovery_protocol: DiscoveryProtocol,
        mock_logger: Mock,  # noqa: ARG002
        mock_printer_registry: Mock,
    ) -> None:
        """Test datagram_received when no printers are available."""
        # Setup
        mock_printer_registry.get_all_printers.return_value = {}
        mock_transport = Mock()
        discovery_protocol.transport = mock_transport
        discovery_protocol._last_discovery_time = 0.0  # noqa: SLF001  # Allow discovery  # noqa: SLF001

        # Test
        discovery_protocol.datagram_received(
            DISCOVERY_MESSAGE.encode(), ("127.0.0.1", 12345)
        )

        # Verify response was sent
        mock_transport.sendto.assert_called_once()
        sent_data, addr = mock_transport.sendto.call_args[0]

        # Parse and verify response uses legacy Saturn format with Attributes
        response = json.loads(sent_data.decode())
        assert "Attributes" in response["Data"]
        assert "Status" in response["Data"]
        assert response["Data"]["Attributes"]["Name"] == "Elegoo Proxy Server"
        assert response["Data"]["Attributes"]["MachineName"] == "Elegoo Proxy Server"
        assert response["Data"]["Attributes"]["MainboardIP"] == "10.0.0.100"
        assert response["Data"]["Attributes"]["MainboardID"] == "proxy"
        assert response["Data"]["Status"]["CurrentStatus"] == 0
        assert addr == ("127.0.0.1", 12345)

    def test_datagram_received_with_printers(
        self,
        discovery_protocol: DiscoveryProtocol,
        mock_logger: Mock,  # noqa: ARG002
        mock_printer_registry: Mock,
        sample_printer: Printer,
    ) -> None:
        """Test datagram_received when printers are available."""
        # Setup
        printers = {"192.168.1.100": sample_printer}
        mock_printer_registry.get_all_printers.return_value = printers
        mock_transport = Mock()
        discovery_protocol.transport = mock_transport
        discovery_protocol._last_discovery_time = 0.0  # noqa: SLF001  # Allow discovery  # noqa: SLF001

        # Test
        discovery_protocol.datagram_received(
            DISCOVERY_MESSAGE.encode(), ("127.0.0.1", 12345)
        )

        # Verify response was sent for each printer
        mock_transport.sendto.assert_called_once()
        sent_data, _addr = mock_transport.sendto.call_args[0]

        # Parse and verify response uses legacy Saturn format with Attributes
        response = json.loads(sent_data.decode())
        assert response["Id"] == sample_printer.connection
        assert "Attributes" in response["Data"]
        assert "Status" in response["Data"]
        assert response["Data"]["Attributes"]["Name"] == sample_printer.name
        assert response["Data"]["Attributes"]["MachineName"] == sample_printer.name
        assert response["Data"]["Attributes"]["BrandName"] == sample_printer.brand
        # Points to proxy
        assert response["Data"]["Attributes"]["MainboardIP"] == "10.0.0.100"
        assert response["Data"]["Attributes"]["MainboardID"] == sample_printer.id
        assert (
            response["Data"]["Attributes"]["ProtocolVersion"] == sample_printer.protocol
        )
        assert (
            response["Data"]["Attributes"]["FirmwareVersion"] == sample_printer.firmware
        )
        assert response["Data"]["Status"]["CurrentStatus"] == 0

    def test_datagram_received_printer_without_attributes(
        self, discovery_protocol: DiscoveryProtocol, mock_printer_registry: Mock
    ) -> None:
        """Test datagram_received with printer missing some attributes."""
        # Create minimal printer mock
        minimal_printer = Mock()
        minimal_printer.name = None
        minimal_printer.connection = None
        minimal_printer.brand = None
        minimal_printer.id = None
        minimal_printer.protocol = None
        minimal_printer.firmware = None

        printers = {"192.168.1.100": minimal_printer}
        mock_printer_registry.get_all_printers.return_value = printers
        mock_transport = Mock()
        discovery_protocol.transport = mock_transport
        discovery_protocol._last_discovery_time = 0.0  # noqa: SLF001  # noqa: SLF001

        # Test
        discovery_protocol.datagram_received(
            DISCOVERY_MESSAGE.encode(), ("127.0.0.1", 12345)
        )

        # Verify response handles missing attributes gracefully
        mock_transport.sendto.assert_called_once()
        sent_data, _ = mock_transport.sendto.call_args[0]
        response = json.loads(sent_data.decode())

        # Verify legacy Saturn format with Attributes
        assert "Attributes" in response["Data"]
        assert "Status" in response["Data"]
        # Should handle None attributes (getattr returns None when attr exists
        # but is None)
        assert (
            response["Data"]["Attributes"]["Name"] is None
        )  # getattr returns None when attribute exists but is None
        assert (
            response["Data"]["Attributes"]["BrandName"] is None
        )  # getattr returns None when attribute exists but is None
        assert (
            response["Data"]["Attributes"]["MainboardID"] == "192.168.1.100"
        )  # Falls back to IP when id is None
        assert (
            response["Data"]["Attributes"]["ProtocolVersion"] is None
        )  # getattr returns None when attribute exists but is None
        assert (
            response["Data"]["Attributes"]["FirmwareVersion"] is None
        )  # getattr returns None when attribute exists but is None

    def test_datagram_received_multiple_printers(
        self, discovery_protocol: DiscoveryProtocol, mock_printer_registry: Mock
    ) -> None:
        """Test datagram_received with multiple printers."""
        # Create multiple printers
        printer1 = Mock()
        printer1.name = "Printer 1"
        printer1.connection = "conn1"
        printer1.brand = "Elegoo"
        printer1.id = "id1"
        printer1.protocol = "V3.0.0"
        printer1.firmware = "V1.0.0"

        printer2 = Mock()
        printer2.name = "Printer 2"
        printer2.connection = "conn2"
        printer2.brand = "Elegoo"
        printer2.id = "id2"
        printer2.protocol = "V3.0.0"
        printer2.firmware = "V1.0.0"

        printers = {"192.168.1.100": printer1, "192.168.1.101": printer2}
        mock_printer_registry.get_all_printers.return_value = printers
        mock_transport = Mock()
        discovery_protocol.transport = mock_transport
        discovery_protocol._last_discovery_time = 0.0  # noqa: SLF001

        # Test
        discovery_protocol.datagram_received(
            DISCOVERY_MESSAGE.encode(), ("127.0.0.1", 12345)
        )

        # Should send response for each printer
        expected_call_count = 2
        assert mock_transport.sendto.call_count == expected_call_count

    def test_datagram_received_unicode_decode_error(
        self, discovery_protocol: DiscoveryProtocol, mock_logger: Mock
    ) -> None:
        """Test datagram_received with non-UTF8 data."""
        # Send invalid UTF-8 bytes
        invalid_utf8 = b"\xff\xfe"
        discovery_protocol.datagram_received(invalid_utf8, ("127.0.0.1", 12345))

        # Should log debug message about non-UTF8 packet
        mock_logger.debug.assert_called_once()
        assert "Non-UTF8" in mock_logger.debug.call_args[0][0]

    def test_datagram_received_exception_handling(
        self,
        discovery_protocol: DiscoveryProtocol,
        mock_logger: Mock,
        mock_printer_registry: Mock,
    ) -> None:
        """Test exception handling in datagram_received."""
        # Setup to raise exception
        mock_printer_registry.get_all_printers.side_effect = Exception("Test error")
        discovery_protocol._last_discovery_time = 0.0  # noqa: SLF001

        # Test
        discovery_protocol.datagram_received(
            DISCOVERY_MESSAGE.encode(), ("127.0.0.1", 12345)
        )

        # Should log exception
        mock_logger.exception.assert_called_once()

    def test_error_received(
        self, discovery_protocol: DiscoveryProtocol, mock_logger: Mock
    ) -> None:
        """Test error_received method."""
        test_exception = Exception("Test UDP error")
        discovery_protocol.error_received(test_exception)

        mock_logger.warning.assert_called_once()
        assert "UDP Discovery Server Error" in mock_logger.warning.call_args[0][0]

    @patch("os.urandom")
    def test_random_id_generation(
        self,
        mock_urandom: Mock,
        discovery_protocol: DiscoveryProtocol,
        mock_printer_registry: Mock,
    ) -> None:
        """Test that random IDs are generated properly."""
        mock_urandom.return_value = b"\x12\x34\x56\x78\xab\xcd\xef\x01"
        mock_printer_registry.get_all_printers.return_value = {}
        mock_transport = Mock()
        discovery_protocol.transport = mock_transport
        discovery_protocol._last_discovery_time = 0.0  # noqa: SLF001

        discovery_protocol.datagram_received(
            DISCOVERY_MESSAGE.encode(), ("127.0.0.1", 12345)
        )

        # Verify random ID was used
        sent_data, _ = mock_transport.sendto.call_args[0]
        response = json.loads(sent_data.decode())
        assert response["Id"] == "12345678abcdef01"  # hex representation

    def test_no_transport_available(
        self, discovery_protocol: DiscoveryProtocol, mock_printer_registry: Mock
    ) -> None:
        """Test datagram_received when transport is not available."""
        mock_printer_registry.get_all_printers.return_value = {}
        discovery_protocol.transport = None  # No transport
        discovery_protocol._last_discovery_time = 0.0  # noqa: SLF001

        # Should not raise exception
        discovery_protocol.datagram_received(
            DISCOVERY_MESSAGE.encode(), ("127.0.0.1", 12345)
        )

    def test_rate_limit_timing(
        self, discovery_protocol: DiscoveryProtocol, mock_printer_registry: Mock
    ) -> None:
        """Test rate limiting timing precision."""
        mock_printer_registry.get_all_printers.return_value = {}
        mock_transport = Mock()
        discovery_protocol.transport = mock_transport

        # First request should go through
        discovery_protocol._last_discovery_time = 0.0  # noqa: SLF001
        discovery_protocol.datagram_received(
            DISCOVERY_MESSAGE.encode(), ("127.0.0.1", 12345)
        )
        mock_transport.sendto.assert_called_once()

        # Second request within rate limit should be blocked
        mock_transport.reset_mock()
        current_time = time.time()
        discovery_protocol._last_discovery_time = current_time - (  # noqa: SLF001
            DISCOVERY_RATE_LIMIT_SECONDS - 0.1
        )
        discovery_protocol.datagram_received(
            DISCOVERY_MESSAGE.encode(), ("127.0.0.1", 12345)
        )
        mock_transport.sendto.assert_not_called()

        # Third request after rate limit should go through
        mock_transport.reset_mock()
        discovery_protocol._last_discovery_time = current_time - (  # noqa: SLF001
            DISCOVERY_RATE_LIMIT_SECONDS + 0.1
        )
        discovery_protocol.datagram_received(
            DISCOVERY_MESSAGE.encode(), ("127.0.0.1", 12345)
        )
        mock_transport.sendto.assert_called_once()
