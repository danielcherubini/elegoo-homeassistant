"""Tests for the Printer model."""

import pytest

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
        assert result == expected, (  # noqa: S101
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

        assert result is True  # noqa: S101
        assert printer.firmware == "V2.0.0"  # noqa: S101

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

        assert result is False  # noqa: S101
        assert printer.firmware == "V1.0.0"  # noqa: S101
        assert printer.model == "Saturn 3"  # noqa: S101
        assert printer.name == "My Printer"  # noqa: S101
        assert printer.brand == "Elegoo"  # noqa: S101

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

        assert result is False  # noqa: S101

    def test_sync_from_attributes_rederives_printer_type(self) -> None:
        """Verify printer_type updates when model changes."""
        printer = Printer()
        printer.model = "Saturn 3"
        printer.printer_type = PrinterType.FDM

        attrs = PrinterAttributes({"Attributes": {"MachineName": "Saturn 4 Ultra 16K"}})
        result = printer.sync_from_attributes(attrs)

        assert result is True  # noqa: S101
        assert printer.model == "Saturn 4 Ultra 16K"  # noqa: S101
        assert printer.printer_type == PrinterType.RESIN  # noqa: S101

    def test_sync_from_attributes_rederives_open_centauri(self) -> None:
        """Verify open_centauri flag updates when firmware changes."""
        printer = Printer()
        printer.model = "Centauri Carbon"
        printer.firmware = "V0.1.0"
        printer.open_centauri = False

        attrs = PrinterAttributes({"Attributes": {"FirmwareVersion": "V0.1.0 O"}})
        result = printer.sync_from_attributes(attrs)

        assert result is True  # noqa: S101
        assert printer.firmware == "V0.1.0 O"  # noqa: S101
        assert printer.open_centauri is True  # noqa: S101

    def test_sync_from_attributes_rederives_has_vat_heater(self) -> None:
        """Verify has_vat_heater flag updates when model changes."""
        printer = Printer()
        printer.model = "Saturn 3"
        printer.has_vat_heater = False

        attrs = PrinterAttributes({"Attributes": {"MachineName": "Saturn 4 Ultra 16K"}})
        result = printer.sync_from_attributes(attrs)

        assert result is True  # noqa: S101
        assert printer.model == "Saturn 4 Ultra 16K"  # noqa: S101
        assert printer.has_vat_heater is True  # noqa: S101

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

        assert result is True  # noqa: S101
        assert printer.firmware == "V2.0.0"  # noqa: S101
        assert printer.model == "New Model"  # noqa: S101
        assert printer.name == "New Name"  # noqa: S101
        assert printer.brand == "New Brand"  # noqa: S101

    def test_sync_from_attributes_skips_all_empty_attrs(self) -> None:
        """Verify PrinterAttributes({}) with no Attributes key skips everything."""
        printer = Printer()
        printer.firmware = "V1.0.0"
        printer.model = "Saturn 3"
        printer.name = "My Printer"
        printer.brand = "Elegoo"

        attrs = PrinterAttributes({})
        result = printer.sync_from_attributes(attrs)

        assert result is False  # noqa: S101
        assert printer.firmware == "V1.0.0"  # noqa: S101
        assert printer.model == "Saturn 3"  # noqa: S101
        assert printer.name == "My Printer"  # noqa: S101
        assert printer.brand == "Elegoo"  # noqa: S101
