"""Tests for the Printer model in the Elegoo SDCP models."""

import json
from types import MappingProxyType

from custom_components.elegoo_printer.const import CONF_PROXY_ENABLED
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer


def test_printer_initialization_with_valid_data() -> None:
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


def test_printer_initialization_with_invalid_data() -> None:
    """Test that the Printer model handles invalid or empty JSON data."""
    printer = Printer("invalid json")
    assert printer.connection is None
    assert printer.name == ""
    assert printer.model is None

    printer = Printer()
    assert printer.connection is None
    assert printer.name == ""
    assert printer.model is None


def test_printer_to_dict() -> None:
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


def test_printer_initialization_with_proxy_enabled() -> None:
    """Test that the Printer model initializes with proxy enabled."""
    config = MappingProxyType({CONF_PROXY_ENABLED: True})
    printer = Printer(config=config)
    assert printer.proxy_enabled


def test_printer_initialization_with_resin_printer() -> None:
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


def test_printer_from_dict() -> None:
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


def test_printer_initialization_with_legacy_saturn_format() -> None:
    """Test that Printer handles legacy Saturn format with Attributes."""
    printer_json = json.dumps(
        {
            "Id": "legacy123",
            "Data": {
                "Attributes": {
                    "Name": "Saturn 3 Ultra",
                    "MachineName": "ELEGOO Saturn 3 Ultra",
                    "BrandName": "ELEGOO",
                    "MainboardIP": "192.168.1.200",
                    "ProtocolVersion": "V1.0.0",
                    "FirmwareVersion": "V1.4.2",
                    "MainboardID": "ABCD1234ABCD1234",
                },
                "Status": {
                    "CurrentStatus": 0,
                    "PrintInfo": {
                        "Status": 16,
                        "CurrentLayer": 500,
                        "TotalLayer": 500,
                    },
                },
            },
        }
    )
    printer = Printer(printer_json)

    assert printer.connection == "legacy123"
    assert printer.name == "Saturn 3 Ultra"
    assert printer.model == "ELEGOO Saturn 3 Ultra"
    assert printer.brand == "ELEGOO"
    assert printer.ip_address == "192.168.1.200"
    assert printer.protocol == "V1.0.0"
    assert printer.firmware == "V1.4.2"
    assert printer.id == "ABCD1234ABCD1234"
    assert printer.printer_type == PrinterType.RESIN


def test_printer_from_dict_with_legacy_saturn_format() -> None:
    """Test that from_dict handles legacy Saturn format with Attributes."""
    printer_dict = {
        "Id": "legacy456",
        "Data": {
            "Attributes": {
                "Name": "Saturn 3",
                "MachineName": "ELEGOO Saturn 3",
                "BrandName": "ELEGOO",
                "MainboardIP": "192.168.1.201",
                "ProtocolVersion": "V1.0.0",
                "FirmwareVersion": "V1.1.29",
                "MainboardID": "4c851c540107103d",
            },
            "Status": {
                "CurrentStatus": 0,
            },
        },
    }
    printer = Printer.from_dict(printer_dict)

    assert printer.connection == "legacy456"
    assert printer.name == "Saturn 3"
    assert printer.model == "ELEGOO Saturn 3"
    assert printer.brand == "ELEGOO"
    assert printer.ip_address == "192.168.1.201"
    assert printer.protocol == "V1.0.0"
    assert printer.firmware == "V1.1.29"
    assert printer.id == "4c851c540107103d"
    assert printer.printer_type == PrinterType.RESIN
