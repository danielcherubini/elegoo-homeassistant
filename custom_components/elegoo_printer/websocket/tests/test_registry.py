"""Tests for the PrinterRegistry class."""

import json
from unittest.mock import Mock

import pytest

from custom_components.elegoo_printer.sdcp.models.printer import Printer
from custom_components.elegoo_printer.websocket.server.registry import PrinterRegistry


@pytest.fixture
def mock_logger() -> Mock:
    """Create a mock logger for testing."""
    return Mock()


@pytest.fixture
def printer_registry() -> PrinterRegistry:
    """Create a PrinterRegistry instance for testing."""
    return PrinterRegistry()


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


class TestPrinterRegistry:
    """Test cases for PrinterRegistry."""

    def test_initialization(self, printer_registry: PrinterRegistry) -> None:
        """Test that PrinterRegistry initializes correctly."""
        assert printer_registry._printers == {}  # noqa: SLF001
        assert printer_registry._printer_ports == {}  # noqa: SLF001

    def test_add_printer(
        self, printer_registry: PrinterRegistry, sample_printer: Printer
    ) -> None:
        """Test adding a printer to the registry."""
        ws_port, video_port = printer_registry.add_printer(sample_printer)

        # Check that ports are returned
        expected_ws_port = 3030  # WEBSOCKET_PORT
        expected_video_port = 3031  # VIDEO_PORT
        assert ws_port == expected_ws_port
        assert video_port == expected_video_port

        # Check that printer is stored
        assert sample_printer.ip_address in printer_registry._printers  # noqa: SLF001
        assert printer_registry._printers[sample_printer.ip_address] == sample_printer  # noqa: SLF001

        # Check that ports are stored
        assert sample_printer.ip_address in printer_registry._printer_ports  # noqa: SLF001
        assert printer_registry._printer_ports[sample_printer.ip_address] == (  # noqa: SLF001
            ws_port,
            video_port,
        )

    def test_remove_printer(
        self, printer_registry: PrinterRegistry, sample_printer: Printer
    ) -> None:
        """Test removing a printer from the registry."""
        # Add printer first
        printer_registry.add_printer(sample_printer)
        assert sample_printer.ip_address in printer_registry._printers  # noqa: SLF001

        # Remove printer
        result = printer_registry.remove_printer(sample_printer.ip_address)

        # Check that printer is removed
        assert result is True
        assert sample_printer.ip_address not in printer_registry._printers  # noqa: SLF001
        assert sample_printer.ip_address not in printer_registry._printer_ports  # noqa: SLF001

    def test_remove_nonexistent_printer(
        self, printer_registry: PrinterRegistry
    ) -> None:
        """Test removing a printer that doesn't exist."""
        # This should not raise an error
        result = printer_registry.remove_printer("nonexistent_ip")

        # Should return False
        assert result is False

    def test_get_printer_by_ip(
        self, printer_registry: PrinterRegistry, sample_printer: Printer
    ) -> None:
        """Test getting a printer by IP address."""
        # Test when printer doesn't exist
        result = printer_registry.get_printer_by_ip("nonexistent_ip")
        assert result is None

        # Add printer and test retrieval
        printer_registry.add_printer(sample_printer)
        result = printer_registry.get_printer_by_ip(sample_printer.ip_address)
        assert result == sample_printer

    def test_get_printer_by_mainboard_id(
        self, printer_registry: PrinterRegistry, sample_printer: Printer
    ) -> None:
        """Test getting a printer by MainboardID."""
        # Test when printer doesn't exist
        result = printer_registry.get_printer_by_mainboard_id("nonexistent_id")
        assert result is None

        # Add printer and test retrieval
        printer_registry.add_printer(sample_printer)
        result = printer_registry.get_printer_by_mainboard_id(sample_printer.id)
        assert result == sample_printer

    def test_get_all_printers(
        self, printer_registry: PrinterRegistry, sample_printer: Printer
    ) -> None:
        """Test getting all printers."""
        # Test empty registry
        result = printer_registry.get_all_printers()
        assert result == {}

        # Add printer and test
        printer_registry.add_printer(sample_printer)
        result = printer_registry.get_all_printers()
        assert len(result) == 1
        assert sample_printer.ip_address in result
        assert result[sample_printer.ip_address] == sample_printer

    def test_multiple_printers(self, printer_registry: PrinterRegistry) -> None:
        """Test managing multiple printers."""
        # Create multiple printers
        printer1_json = json.dumps(
            {
                "Id": "printer1",
                "Data": {
                    "Name": "Printer 1",
                    "MachineName": "Saturn 4",
                    "MainboardIP": "192.168.1.100",
                    "MainboardID": "id1",
                },
            }
        )
        printer1 = Printer(printer1_json)

        printer2_json = json.dumps(
            {
                "Id": "printer2",
                "Data": {
                    "Name": "Printer 2",
                    "MachineName": "Neptune 4",
                    "MainboardIP": "192.168.1.101",
                    "MainboardID": "id2",
                },
            }
        )
        printer2 = Printer(printer2_json)

        # Add both printers
        printer_registry.add_printer(printer1)
        printer_registry.add_printer(printer2)

        # Test retrieval
        all_printers = printer_registry.get_all_printers()
        expected_printer_count = 2
        assert len(all_printers) == expected_printer_count

        assert printer_registry.get_printer_by_ip("192.168.1.100") == printer1
        assert printer_registry.get_printer_by_ip("192.168.1.101") == printer2

        assert printer_registry.get_printer_by_mainboard_id("id1") == printer1
        assert printer_registry.get_printer_by_mainboard_id("id2") == printer2

        # Remove one printer
        printer_registry.remove_printer("192.168.1.100")
        assert len(printer_registry.get_all_printers()) == 1
        assert printer_registry.get_printer_by_ip("192.168.1.100") is None
        assert printer_registry.get_printer_by_ip("192.168.1.101") == printer2

    def test_printer_without_mainboard_id(
        self, printer_registry: PrinterRegistry
    ) -> None:
        """Test handling printer without MainboardID."""
        printer_json = json.dumps(
            {
                "Id": "no_mainboard_id",
                "Data": {
                    "Name": "No ID Printer",
                    "MainboardIP": "192.168.1.102",
                    # MainboardID is missing
                },
            }
        )
        printer = Printer(printer_json)
        printer_registry.add_printer(printer)

        # Should still be findable by IP
        assert printer_registry.get_printer_by_ip("192.168.1.102") == printer

        # Should not be findable by MainboardID since it doesn't have one
        assert printer_registry.get_printer_by_mainboard_id("192.168.1.102") is None

    def test_clear_printers(
        self, printer_registry: PrinterRegistry, sample_printer: Printer
    ) -> None:
        """Test clearing all printers."""
        # Add printer first
        printer_registry.add_printer(sample_printer)
        assert len(printer_registry.get_all_printers()) == 1

        # Clear all printers
        printer_registry.clear()

        # Check that all printers are removed
        assert len(printer_registry.get_all_printers()) == 0
        assert len(printer_registry._printer_ports) == 0  # noqa: SLF001

    def test_count_printers(
        self, printer_registry: PrinterRegistry, sample_printer: Printer
    ) -> None:
        """Test counting printers."""
        # Empty registry
        assert printer_registry.count() == 0

        # Add printer
        printer_registry.add_printer(sample_printer)
        assert printer_registry.count() == 1
