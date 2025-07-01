"""Tests for the enums in the Elegoo SDCP models."""
from custom_components.elegoo_printer.elegoo_sdcp.models.enums import PrinterType


def test_printer_type_from_model():
    """Test the from_model method of the PrinterType enum."""
    # Test FDM printers
    assert PrinterType.from_model("Elegoo Centauri Carbon") == PrinterType.FDM
    assert PrinterType.from_model("Elegoo Centauri") == PrinterType.FDM

    # Test Resin printers
    assert PrinterType.from_model("Elegoo Mars 5") == PrinterType.RESIN
    assert PrinterType.from_model("Elegoo Mars 5 Ultra") == PrinterType.RESIN
    assert PrinterType.from_model("Elegoo Saturn 4") == PrinterType.RESIN
    assert PrinterType.from_model("Elegoo Saturn 4 Ultra") == PrinterType.RESIN
    assert PrinterType.from_model("Elegoo Saturn 4 Ultra 16k") == PrinterType.RESIN

    # Test unknown models
    assert PrinterType.from_model("Unknown Model") is None
    assert PrinterType.from_model("") is None
    assert PrinterType.from_model(None) is None

    # Test partial matches
    assert PrinterType.from_model("My Centauri Printer") == PrinterType.FDM
    assert PrinterType.from_model("My Mars 5 Printer") == PrinterType.RESIN

    # Test case sensitivity (should be case-sensitive)
    assert PrinterType.from_model("elegoo centauri carbon") is None
