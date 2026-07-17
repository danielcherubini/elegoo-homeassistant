"""Tests for Canvas tray placeholder normalization."""

from custom_components.elegoo_printer.sdcp.models.ams import AMSTray


def test_placeholder_brand_normalized() -> None:
  """CC1 dash-placeholder brand is normalized to empty string."""
  tray = AMSTray({"tray_id": 0, "brand": "— — — —"})
  assert tray.brand == ""


def test_placeholder_filament_type_normalized() -> None:
  """CC1 '?' filament_type is normalized to empty string."""
  tray = AMSTray({"tray_id": 0, "filament_type": "?"})
  assert tray.filament_type == ""


def test_placeholder_filament_name_normalized() -> None:
  """CC1 dash-placeholder filament_name is normalized to empty string."""
  tray = AMSTray({"tray_id": 0, "filament_name": "— — —"})
  assert tray.filament_name == ""


def test_populated_tray_unchanged() -> None:
  """Real tray data is not altered by normalization."""
  tray = AMSTray({
      "tray_id": 0,
      "brand": "ELEGOO",
      "filament_type": "PLA",
      "filament_name": "PLA",
  })
  assert tray.brand == "ELEGOO"
  assert tray.filament_type == "PLA"
  assert tray.filament_name == "PLA"


def test_null_brand_and_filament_name() -> None:
  """Null brand/filament_name from firmware becomes empty string."""
  tray = AMSTray({
      "tray_id": 0,
      "brand": None,
      "filament_type": "PLA",
      "filament_name": None,
  })
  assert tray.brand == ""
  assert tray.filament_name == ""
  assert tray.filament_type == "PLA"
