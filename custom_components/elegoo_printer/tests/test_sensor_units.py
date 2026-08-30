"""
Tests for sensor unit pinning (issue #397).

Distance-class sensors (current/total extrusion, xy/z position, z offset)
and the G-code proxy length sensors must pin their display unit to
millimeters via `suggested_unit_of_measurement`, so HA does not
convert `state` to the user's unit-system unit (e.g. inches for
US custom users) — important for consumers like Spoolman expecting mm.
"""

from homeassistant.const import UnitOfLength

from custom_components.elegoo_printer.definitions import (
    PRINTER_STATUS_FDM,
    PRINTER_STATUS_FDM_CURRENT_EXTRUSION,
    PRINTER_STATUS_FDM_TOTAL_EXTRUSION,
    PRINTER_STATUS_GCODE_PROXY_FILAMENT,
)

# key -> expected: must pin suggested unit to mm (+ native mm)
PINNED_MILLIMETER_KEYS = [
    "total_extrusion",
    "current_extrusion",
    "z_offset",
    "current_x",
    "current_y",
    "current_z",
    "a1_length_millimeters",
    "a2_length_millimeters",
    "a3_length_millimeters",
    "a4_length_millimeters",
]

ALL_TUPLES = (
    PRINTER_STATUS_FDM,
    PRINTER_STATUS_FDM_TOTAL_EXTRUSION,
    PRINTER_STATUS_FDM_CURRENT_EXTRUSION,
    PRINTER_STATUS_GCODE_PROXY_FILAMENT,
)


def _descriptions_by_key() -> dict:
    """Flatten the relevant definition tuples into {key: description}."""
    out = {}
    for items in ALL_TUPLES:
        for description in items:
            out[description.key] = description
    return out


class TestSensorUnitPinning:
    """Distance-class sensors pin the suggested display unit to mm."""

    def test_expected_sensor_keys_present(self) -> None:
        """The targeted keys all exist in the relevant definition tuples."""
        by_key = _descriptions_by_key()
        missing = [k for k in PINNED_MILLIMETER_KEYS if k not in by_key]
        assert not missing, f"keys missing from definitions: {missing}"

    def test_distance_sensors_pin_mm(self) -> None:
        """
        Distance and length sensors pin their display unit to mm.

        They have a matching native unit of mm — so
        `native == display == mm`, and HA's state stays in
        millimeters (no conversion to the user's unit system).
        """
        by_key = _descriptions_by_key()
        for key in PINNED_MILLIMETER_KEYS:
            description = by_key[key]
            assert (
                description.suggested_unit_of_measurement == UnitOfLength.MILLIMETERS
            ), (
                f"{key} should pin suggested_unit_of_measurement to mm "
                f"(got {description.suggested_unit_of_measurement})"
            )
            assert description.native_unit_of_measurement == UnitOfLength.MILLIMETERS, (
                f"{key} native unit is "
                f"{description.native_unit_of_measurement}, expected mm"
            )
