"""Tests for Canvas/AMS model parsing."""

from custom_components.elegoo_printer.sdcp.models.ams import (
  AMSStatus,
  AMSTray,
)


def test_ams_status_from_cc1_data() -> None:
  """Test AMSStatus parsing with CC1 format data."""
  data = {
    "active_canvas_id": 0,
    "active_tray_id": 0,
    "auto_refill": 1,
    "canvas_list": [{
      "canvas_id": 0,
      "connected": 1,
      "tray_list": [
        {
          "tray_id": 0,
          "brand": "ELEGOO",
          "filament_type": "PLA",
          "filament_name": "PLA",
          "filament_code": "0x00000",
          "filament_color": "#2850DF",
          "min_nozzle_temp": 190,
          "max_nozzle_temp": 230,
          "status": 1,
        },
      ],
    }],
  }
  status = AMSStatus(data)
  assert status.ams_connect_status is True
  assert status.auto_refill  # CC1 sends int 1, which is truthy
  assert len(status.ams_boxes) == 1
  assert status.ams_boxes[0].connected is True
  assert len(status.ams_boxes[0].tray_list) == 1
  tray = status.ams_boxes[0].tray_list[0]
  assert tray.brand == "ELEGOO"
  assert tray.filament_color == "#2850DF"


def test_ams_status_from_cc2_data() -> None:
  """Test AMSStatus parsing with CC2 format data."""
  data = {
    "active_canvas_id": 0,
    "active_tray_id": 1,
    "auto_refill": True,
    "canvas_list": [{
      "canvas_id": 0,
      "connected": True,
      "tray_list": [
        {
          "tray_id": 0,
          "brand": "ELEGOO",
          "filament_type": "PLA",
          "filament_name": "PLA",
          "filament_color": "#FF0000",
          "min_nozzle_temp": 190,
          "max_nozzle_temp": 230,
          "status": 1,
        },
      ],
    }],
  }
  status = AMSStatus(data)
  assert status.ams_connect_status is True
  assert status.auto_refill is True


def test_ams_status_empty_data() -> None:
  """Test AMSStatus with empty/missing data."""
  status = AMSStatus({})
  assert status.ams_connect_status is False
  assert status.ams_connect_num == 0
  assert status.ams_current_enabled is None
  assert len(status.ams_boxes) == 0


def test_ams_status_no_canvas_connected() -> None:
  """Test AMSStatus when no Canvas box is connected."""
  data = {
    "canvas_list": [{
      "canvas_id": 0,
      "connected": 0,
      "tray_list": [],
    }],
  }
  status = AMSStatus(data)
  assert status.ams_connect_status is False


def test_ams_tray_color_prefix() -> None:
  """Test AMSTray adds # prefix to colors without it."""
  tray = AMSTray({"tray_id": 0, "filament_color": "FF0000"})
  assert tray.filament_color == "#FF0000"

  tray_with_hash = AMSTray({"tray_id": 0, "filament_color": "#FF0000"})
  assert tray_with_hash.filament_color == "#FF0000"


def test_ams_tray_id_padding() -> None:
  """Test AMSTray pads IDs to 2 digits."""
  assert AMSTray({"tray_id": 0}).id == "00"
  assert AMSTray({"tray_id": 3}).id == "03"
  assert AMSTray({"tray_id": 10}).id == "10"


def test_ams_active_tray_negative_one() -> None:
  """Test AMSStatus handles active_tray_id=-1 (idle, no active tray)."""
  data = {
    "active_canvas_id": 0,
    "active_tray_id": -1,
    "canvas_list": [],
  }
  status = AMSStatus(data)
  assert status.ams_current_enabled is not None
