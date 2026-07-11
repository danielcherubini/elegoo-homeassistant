"""Tests for CC1 Canvas handler normalization."""


def _normalize_cc1_canvas_data(data: dict) -> dict:
  """Replicate the normalization logic from _canvas_handler."""
  canvas_list = data.get("canvas_list", [])
  for canvas in canvas_list:
    for tray in canvas.get("tray_list", []):
      if tray.get("brand", "").startswith("—"):
        tray["brand"] = ""
      if tray.get("filament_type") == "?":
        tray["filament_type"] = ""
      if tray.get("filament_name", "").startswith("—"):
        tray["filament_name"] = ""
  return data


def test_normalize_cc1_empty_tray_placeholders() -> None:
  """Test CC1 dash placeholders are normalized to empty strings."""
  data = {
    "canvas_list": [{
      "canvas_id": 0,
      "connected": 1,
      "tray_list": [{
        "tray_id": 3,
        "brand": "— — — —",
        "filament_type": "?",
        "filament_name": "— — —",
        "filament_color": "#FF2F30",
        "status": 0,
      }],
    }],
  }
  normalized = _normalize_cc1_canvas_data(data)
  tray = normalized["canvas_list"][0]["tray_list"][0]
  assert tray["brand"] == ""
  assert tray["filament_type"] == ""
  assert tray["filament_name"] == ""


def test_normalize_leaves_populated_trays_unchanged() -> None:
  """Test normalization doesn't alter populated tray data."""
  data = {
    "canvas_list": [{
      "canvas_id": 0,
      "connected": 1,
      "tray_list": [{
        "tray_id": 0,
        "brand": "ELEGOO",
        "filament_type": "PLA",
        "filament_name": "PLA",
        "filament_color": "#2850DF",
        "status": 1,
      }],
    }],
  }
  normalized = _normalize_cc1_canvas_data(data)
  tray = normalized["canvas_list"][0]["tray_list"][0]
  assert tray["brand"] == "ELEGOO"
  assert tray["filament_type"] == "PLA"
  assert tray["filament_name"] == "PLA"


def test_normalize_handles_empty_canvas_list() -> None:
  """Test normalization handles missing/empty canvas_list."""
  assert _normalize_cc1_canvas_data({}) == {}
  assert _normalize_cc1_canvas_data({"canvas_list": []}) == {"canvas_list": []}
