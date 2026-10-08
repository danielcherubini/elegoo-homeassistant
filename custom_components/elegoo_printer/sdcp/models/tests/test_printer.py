"""Tests for the Printer model."""

from types import MappingProxyType

import pytest

from custom_components.elegoo_printer.const import (
    CONF_PROXY_HOST,
    CONF_SERIAL,
)
from custom_components.elegoo_printer.sdcp.models.attributes import PrinterAttributes
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer


class TestOpenCentauriDetection:
    """Test Open Centauri firmware detection."""

    @pytest.mark.parametrize(
        ("model", "firmware", "expected"),
        [
            # Valid Open Centauri firmware versions
            ("Centauri Carbon", "V0.1.0 O", True),
            ("Centauri Carbon", "V0.1.0 o", True),
            ("Centauri Carbon", "V0.1.0O", True),
            ("Centauri Carbon", "V0.1.0o", True),
            ("Centauri Carbon", "V0.2.0OC", True),
            ("Centauri Carbon", "V0.2.0oc", True),
            ("Centauri Carbon", "V0.2.0 OC", True),
            ("Centauri Carbon", "V0.2.0 oc", True),
            ("centauri carbon", "V0.1.0 O", True),  # Case-insensitive model
            ("CENTAURI CARBON", "v0.1.0 o", True),  # Mixed case
            # Invalid - not Open Centauri firmware (no OC or standalone O)
            ("Centauri Carbon", "V0.1.0", False),
            ("Centauri Carbon", "V0.1.0 OCEAN", False),  # O not standalone
            ("Centauri Carbon", "V0.1.0 OFFICIAL", False),  # O not standalone
            ("Centauri Carbon", "V1.0.0", False),
            # Invalid - not Centauri printer
            ("Neptune 4", "V0.1.0 O", False),
            ("Neptune 4 Pro", "V0.2.0OC", False),
            ("Saturn 3", "V0.1.0 O", False),
            # Edge cases
            (None, "V0.1.0 O", False),  # No model
            ("Centauri Carbon", None, False),  # No firmware
            (None, None, False),  # Neither
            ("", "V0.1.0 O", False),  # Empty model
            ("Centauri Carbon", "", False),  # Empty firmware
        ],
    )
    def test_is_open_centauri(
        self,
        model: str | None,
        firmware: str | None,
        expected: bool,  # noqa: FBT001
    ) -> None:
        """Test Open Centauri detection with various firmware patterns."""
        result = Printer._is_open_centauri(model, firmware)  # noqa: SLF001
        assert result == expected, (
            f"Expected {expected} for model='{model}' firmware='{firmware}', "
            f"got {result}"
        )


class TestSyncFromAttributes:
    """Test Printer.sync_from_attributes() method."""

    def test_sync_from_attributes_updates_firmware(self) -> None:
        """Verify firmware is updated from attrs."""
        printer = Printer()
        printer.firmware = "V1.0.0"

        attrs = PrinterAttributes({"Attributes": {"FirmwareVersion": "V2.0.0"}})
        result = printer.sync_from_attributes(attrs)

        assert result is True
        assert printer.firmware == "V2.0.0"

    def test_sync_from_attributes_skips_empty_values(self) -> None:
        """Verify empty strings don't overwrite existing values."""
        printer = Printer()
        printer.firmware = "V1.0.0"
        printer.model = "Saturn 3"
        printer.name = "My Printer"
        printer.brand = "Elegoo"

        attrs = PrinterAttributes(
            {"Attributes": {"FirmwareVersion": "", "MachineName": ""}}
        )
        result = printer.sync_from_attributes(attrs)

        assert result is False
        assert printer.firmware == "V1.0.0"
        assert printer.model == "Saturn 3"
        assert printer.name == "My Printer"
        assert printer.brand == "Elegoo"

    def test_sync_from_attributes_skips_unchanged_values(self) -> None:
        """Verify returning False when nothing changed."""
        printer = Printer()
        printer.firmware = "V1.0.0"
        printer.model = "Saturn 3"
        printer.name = "My Printer"
        printer.brand = "Elegoo"

        attrs = PrinterAttributes(
            {
                "Attributes": {
                    "FirmwareVersion": "V1.0.0",
                    "MachineName": "Saturn 3",
                    "Name": "My Printer",
                    "BrandName": "Elegoo",
                }
            }
        )
        result = printer.sync_from_attributes(attrs)

        assert result is False

    def test_sync_from_attributes_rederives_printer_type(self) -> None:
        """Verify printer_type updates when model changes."""
        printer = Printer()
        printer.model = "Saturn 3"
        printer.printer_type = PrinterType.FDM

        attrs = PrinterAttributes({"Attributes": {"MachineName": "Saturn 4 Ultra 16K"}})
        result = printer.sync_from_attributes(attrs)

        assert result is True
        assert printer.model == "Saturn 4 Ultra 16K"
        assert printer.printer_type == PrinterType.RESIN

    def test_sync_from_attributes_rederives_open_centauri(self) -> None:
        """Verify open_centauri flag updates when firmware changes."""
        printer = Printer()
        printer.model = "Centauri Carbon"
        printer.firmware = "V0.1.0"
        printer.open_centauri = False

        attrs = PrinterAttributes({"Attributes": {"FirmwareVersion": "V0.1.0 O"}})
        result = printer.sync_from_attributes(attrs)

        assert result is True
        assert printer.firmware == "V0.1.0 O"
        assert printer.open_centauri is True

    def test_sync_from_attributes_rederives_has_vat_heater(self) -> None:
        """Verify has_vat_heater flag updates when model changes."""
        printer = Printer()
        printer.model = "Saturn 3"
        printer.has_vat_heater = False

        attrs = PrinterAttributes({"Attributes": {"MachineName": "Saturn 4 Ultra 16K"}})
        result = printer.sync_from_attributes(attrs)

        assert result is True
        assert printer.model == "Saturn 4 Ultra 16K"
        assert printer.has_vat_heater is True

    def test_sync_from_attributes_syncs_all_fields(self) -> None:
        """Verify model, name, brand are also synced."""
        printer = Printer()
        printer.firmware = "V1.0.0"
        printer.model = "Old Model"
        printer.name = "Old Name"
        printer.brand = "Old Brand"

        attrs = PrinterAttributes(
            {
                "Attributes": {
                    "FirmwareVersion": "V2.0.0",
                    "MachineName": "New Model",
                    "Name": "New Name",
                    "BrandName": "New Brand",
                }
            }
        )
        result = printer.sync_from_attributes(attrs)

        assert result is True
        assert printer.firmware == "V2.0.0"
        assert printer.model == "New Model"
        assert printer.name == "New Name"
        assert printer.brand == "New Brand"

    def test_sync_from_attributes_skips_all_empty_attrs(self) -> None:
        """Verify PrinterAttributes({}) with no Attributes key skips everything."""
        printer = Printer()
        printer.firmware = "V1.0.0"
        printer.model = "Saturn 3"
        printer.name = "My Printer"
        printer.brand = "Elegoo"

        attrs = PrinterAttributes({})
        result = printer.sync_from_attributes(attrs)

        assert result is False
        assert printer.firmware == "V1.0.0"
        assert printer.model == "Saturn 3"
        assert printer.name == "My Printer"
        assert printer.brand == "Elegoo"


class TestProxyHost:
    """Test Printer.proxy_host and the derived connection_host property."""

    def test_conf_proxy_host_and_serial_constants(self) -> None:
        """Verify the CC2 config keys have the documented string values."""
        assert CONF_PROXY_HOST == "proxy_host"
        assert CONF_SERIAL == "serial"

    def test_proxy_host_defaults_to_none_without_config(self) -> None:
        """Verify proxy_host is None when no config provides it."""
        printer = Printer()
        assert printer.proxy_host is None

    @pytest.mark.parametrize("proxy_host", [None, ""])
    def test_connection_host_falls_back_to_ip_address(
        self,
        proxy_host: str | None,
    ) -> None:
        """Verify connection_host is the printer IP when proxy_host is unset."""
        config = MappingProxyType({CONF_PROXY_HOST: proxy_host})
        printer = Printer(config=config)
        printer.ip_address = "192.168.1.50"

        assert printer.proxy_host == proxy_host
        assert printer.connection_host == "192.168.1.50"

    def test_connection_host_prefers_proxy_host(self) -> None:
        """Verify connection_host is the proxy host when proxy_host is set."""
        config = MappingProxyType({CONF_PROXY_HOST: "10.0.0.9"})
        printer = Printer(config=config)
        printer.ip_address = "192.168.1.50"

        assert printer.connection_host == "10.0.0.9"

    def test_to_dict_includes_proxy_host(self) -> None:
        """Verify to_dict persists the proxy_host value."""
        config = MappingProxyType({CONF_PROXY_HOST: "10.0.0.9"})
        printer = Printer(config=config)

        assert printer.to_dict()["proxy_host"] == "10.0.0.9"

    def test_to_dict_safe_keeps_proxy_host(self) -> None:
        """Verify proxy_host is not redacted - a host is not a secret."""
        config = MappingProxyType({CONF_PROXY_HOST: "10.0.0.9"})
        printer = Printer(config=config)

        assert printer.to_dict_safe()["proxy_host"] == "10.0.0.9"

    def test_proxy_host_round_trips_through_to_dict_from_dict(self) -> None:
        """Verify proxy_host and connection_host survive serialization."""
        config = MappingProxyType({CONF_PROXY_HOST: "10.0.0.9"})
        printer = Printer(config=config)
        printer.ip_address = "192.168.1.50"

        restored = Printer.from_dict(printer.to_dict())

        assert restored.proxy_host == "10.0.0.9"
        assert restored.ip_address == "192.168.1.50"
        assert restored.connection_host == "10.0.0.9"

    def test_connection_host_round_trips_without_proxy_host(self) -> None:
        """Verify a printer without proxy_host still resolves to its IP."""
        printer = Printer()
        printer.ip_address = "192.168.1.50"

        restored = Printer.from_dict(printer.to_dict())

        assert restored.proxy_host is None
        assert restored.connection_host == "192.168.1.50"
