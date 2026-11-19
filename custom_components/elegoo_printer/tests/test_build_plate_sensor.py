"""
Tests for the FDM build plate sensor (issue #450).

The printer reports which of its two plates is selected in the SDCP
`PlatFormType` field. The sensor maps that to an ENUM state, and because
HA raises when an ENUM sensor's state falls outside its declared
`options`, every value the mapping can produce must be one of those
options — an unrecognised or non-int wire value reads as "unknown" rather
than passing through.
"""

from homeassistant.components.sensor.const import SensorDeviceClass

from custom_components.elegoo_printer.definitions import (
    _BUILD_PLATE_BY_PLATFORM_TYPE,
    BUILD_PLATE_UNKNOWN,
    PRINTER_STATUS_COMMON,
    PRINTER_STATUS_FDM,
    PRINTER_STATUS_RESIN,
    ElegooPrinterSensorEntityDescription,
)
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import PrinterData
from custom_components.elegoo_printer.sdcp.models.status import PrinterStatus

KEY = "build_plate"


def _fdm_description() -> ElegooPrinterSensorEntityDescription:
    """Return the build plate description from the FDM sensor tuple."""
    return next(d for d in PRINTER_STATUS_FDM if d.key == KEY)


def _value_for_platform_type(platform_type: object) -> object:
    """Read the sensor for a status payload carrying the given wire value."""
    payload = {"Status": {"PlatFormType": platform_type}}
    printer_data = PrinterData(PrinterStatus(payload, PrinterType.FDM))
    return _fdm_description().value_fn(printer_data)


class TestBuildPlateRegistration:
    """The sensor belongs to the FDM set only (the plate is an FDM concept)."""

    def test_present_in_fdm_tuple(self) -> None:
        """CC2 and other FDM printers keep the FDM set, so they get it."""
        assert KEY in {description.key for description in PRINTER_STATUS_FDM}

    def test_absent_from_shared_and_resin_tuples(self) -> None:
        """Resin printers have no selectable plate — no entity for them."""
        assert KEY not in {description.key for description in PRINTER_STATUS_COMMON}
        assert KEY not in {description.key for description in PRINTER_STATUS_RESIN}


class TestBuildPlateDescriptionShape:
    """The declared options are the contract the state value is checked against."""

    def test_declares_enum_class_and_options(self) -> None:
        """ENUM device class with exactly the a / b / unknown option set."""
        description = _fdm_description()
        assert description.device_class is SensorDeviceClass.ENUM
        assert description.options == ["a", "b", BUILD_PLATE_UNKNOWN]

    def test_every_emitted_value_is_a_declared_option(self) -> None:
        """
        HA raises on an ENUM state outside `options`.

        Structural guarantee: whatever the mapping holds, plus the unknown
        fallback, is a subset of the options list — so adding a mapping
        entry without its option fails here rather than at runtime.
        """
        description = _fdm_description()
        reachable = set(_BUILD_PLATE_BY_PLATFORM_TYPE.values()) | {BUILD_PLATE_UNKNOWN}
        assert reachable <= set(description.options)


class TestBuildPlateValue:
    """The wire value maps to a state, and anything else reads as unknown."""

    def test_plate_a(self) -> None:
        """PlatFormType 0 is plate A."""
        assert _value_for_platform_type(0) == "a"

    def test_plate_b(self) -> None:
        """PlatFormType 1 is plate B."""
        assert _value_for_platform_type(1) == "b"

    def test_unrecognised_int_is_unknown(self) -> None:
        """A code the map does not know is visible as unknown, never a state."""
        assert _value_for_platform_type(2) == BUILD_PLATE_UNKNOWN

    def test_none_is_unknown(self) -> None:
        """A printer that reports no platform type reads as unknown."""
        assert _value_for_platform_type(None) == BUILD_PLATE_UNKNOWN

    def test_non_int_wire_value_is_unknown(self) -> None:
        """
        The field is typed Any on the wire, so a stringified value is possible.

        It must not be looked up as-is nor passed through: 0 and "0" are
        different keys and only the int is plate A.
        """
        assert _value_for_platform_type("0") == BUILD_PLATE_UNKNOWN

    def test_falsy_printer_data_is_unknown(self) -> None:
        """
        Defensive branch (never None): a falsy payload reads as unknown.

        PrinterData is always truthy in practice; this pins the guard's
        shape so a future refactor keeps the never-return-None contract.
        """
        assert _fdm_description().value_fn(None) == BUILD_PLATE_UNKNOWN


class TestBuildPlatePayload:
    """The sensor reads the field from a real FDM status payload."""

    def test_reads_plate_a_from_status_payload(self) -> None:
        """A Centauri Carbon style payload with PlatFormType 0 reads as a."""
        payload = {
            "Status": {
                "CurrentStatus": [1],
                "PlatFormType": 0,
                "PrintInfo": {
                    "Status": 13,
                    "CurrentLayer": 42,
                    "TotalLayer": 100,
                    "Progress": 42,
                    "Filename": "/local/test.gcode",
                    "TaskId": "task-1",
                },
            }
        }
        status = PrinterStatus(payload, PrinterType.FDM)

        assert status.platform_type == 0
        assert _fdm_description().value_fn(PrinterData(status)) == "a"
