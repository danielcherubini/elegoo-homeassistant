"""Tests for the Printer model in the Elegoo SDCP models."""

import json
from types import MappingProxyType

from custom_components.elegoo_printer.const import CONF_PROXY_ENABLED
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer


def test_printer_initialization_with_valid_data():
    """Test that the Printer model initializes correctly with valid JSON data."""

    printer_json = json.dumps(
        {
            "Id": "12345",
            "Data": {
                "Name": "My Printer",
                "MachineName": "Centauri Carbon",
                "BrandName": "Elegoo",
                "MainboardIP": "192.168.1.100",
                "ProtocolVersion": "2.0",
                "FirmwareVersion": "1.5",
                "MainboardID": "ABCDEF",
            },
        }
    )
    printer = Printer(printer_json)

    assert printer.connection == "12345"
    assert printer.name == "My Printer"
    assert printer.model == "Centauri Carbon"
    assert printer.brand == "Elegoo"
    assert printer.ip_address == "192.168.1.100"
    assert printer.protocol == "2.0"
    assert printer.firmware == "1.5"
    assert printer.id == "ABCDEF"
    assert printer.printer_type == PrinterType.FDM
    assert not printer.proxy_enabled


def test_printer_initialization_with_invalid_data():
    """Test that the Printer model handles invalid or empty JSON data."""
    printer = Printer("invalid json")
    assert printer.connection is None
    assert printer.name == ""
    assert printer.model is None

    printer = Printer()
    assert printer.connection is None
    assert printer.name == ""
    assert printer.model is None


def test_printer_to_dict():
    """Test the to_dict method of the Printer model."""
    printer_json = json.dumps(
        {
            "Id": "12345",
            "Data": {
                "Name": "My Printer",
                "MachineName": "Centauri Carbon",
                "BrandName": "Elegoo",
                "MainboardIP": "192.168.1.100",
                "ProtocolVersion": "2.0",
                "FirmwareVersion": "1.5",
                "MainboardID": "ABCDEF",
            },
        }
    )
    printer = Printer(printer_json)
    printer_dict = printer.to_dict()

    assert printer_dict["connection"] == "12345"
    assert printer_dict["name"] == "My Printer"
    assert printer_dict["model"] == "Centauri Carbon"
    assert printer_dict["brand"] == "Elegoo"
    assert printer_dict["ip_address"] == "192.168.1.100"
    assert printer_dict["protocol"] == "2.0"
    assert printer_dict["firmware"] == "1.5"
    assert printer_dict["id"] == "ABCDEF"
    assert printer_dict["printer_type"] == "fdm"
    assert not printer_dict["proxy_enabled"]


def test_printer_initialization_with_proxy_enabled():
    """Test that the Printer model initializes with proxy enabled."""
    config = MappingProxyType({CONF_PROXY_ENABLED: True})
    printer = Printer(config=config)
    assert printer.proxy_enabled


def test_printer_initialization_with_resin_printer():
    """Test that the Printer model initializes correctly with a resin printer."""
    printer_json = json.dumps(
        {
            "Id": "67890",
            "Data": {
                "Name": "My Resin Printer",
                "MachineName": "Saturn 4 Ultra",
                "BrandName": "Elegoo",
                "MainboardIP": "192.168.1.101",
                "ProtocolVersion": "2.1",
                "FirmwareVersion": "1.6",
                "MainboardID": "GHIJKL",
            },
        }
    )
    printer = Printer(printer_json)

    assert printer.connection == "67890"
    assert printer.name == "My Resin Printer"
    assert printer.model == "Saturn 4 Ultra"
    assert printer.brand == "Elegoo"
    assert printer.ip_address == "192.168.1.101"
    assert printer.protocol == "2.1"
    assert printer.firmware == "1.6"
    assert printer.id == "GHIJKL"
    assert printer.printer_type == PrinterType.RESIN
    assert not printer.proxy_enabled


def test_printer_from_dict():
    """Test the from_dict method of the Printer model."""
    printer_dict = {
        "Id": "12345",
        "Data": {
            "Name": "My Printer",
            "MachineName": "Centauri Carbon",
            "BrandName": "Elegoo",
            "MainboardIP": "192.168.1.100",
            "ProtocolVersion": "2.0",
            "FirmwareVersion": "1.5",
            "MainboardID": "ABCDEF",
        },
    }
    printer = Printer.from_dict(printer_dict)

    assert printer.connection == "12345"
    assert printer.name == "My Printer"
    assert printer.model == "Centauri Carbon"
    assert printer.brand == "Elegoo"
    assert printer.ip_address == "192.168.1.100"
    assert printer.protocol == "2.0"
    assert printer.firmware == "1.5"
    assert printer.id == "ABCDEF"
    assert printer.printer_type == PrinterType.FDM
    assert not printer.proxy_enabled
