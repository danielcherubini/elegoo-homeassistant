"""Models for Canvas/AMS (Automatic Material System) data."""

from typing import Any


class AMSTray:
    """Represents a single filament tray in the Canvas system."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """
        Initialize an AMSTray instance.

        Arguments:
            data (dict[str, Any] | None): Dictionary containing tray data
                from Canvas API. Expected keys: tray_id, brand, filament_type,
                filament_name, filament_color, nozzle_temp_min, nozzle_temp_max,
                bed_temp_min, bed_temp_max, status.

        """
        if data is None:
            data = {}

        # Map Canvas API field names to internal attributes
        # Canvas uses 0-based tray IDs (0, 1, 2, 3)
        # Pad to 2 digits for sensor compatibility (00, 01, 02, 03)
        tray_id = data.get("tray_id", -1)
        self.id: str = str(tray_id).zfill(2) if tray_id >= 0 else ""
        self.brand: str = data.get("brand", "")
        self.filament_type: str = data.get("filament_type", "")
        self.filament_name: str = data.get("filament_name", "")

        # Add # prefix to color if not present
        color = data.get("filament_color", "")
        if color and not color.startswith("#"):
            self.filament_color: str = f"#{color}"
        else:
            self.filament_color: str = color

        # Note: API uses min_nozzle_temp/max_nozzle_temp (not nozzle_temp_min/max)
        self.min_nozzle_temp: int = data.get("min_nozzle_temp", 0)
        self.max_nozzle_temp: int = data.get("max_nozzle_temp", 0)

        # Canvas API doesn't provide bed temps
        self.min_bed_temp: int = 0
        self.max_bed_temp: int = 0

        # Status: 1 = filament present, 0 = empty
        self.status: int = data.get("status", 0)
        self.enabled: bool = self.status == 1

        # Canvas doesn't report these fields, set defaults
        self.from_source: str = "canvas"
        self.serial_number: int | None = None
        self.filament_diameter: str = "1.75"  # Standard for FDM

    def __repr__(self) -> str:
        """Return a string representation of the AMSTray instance."""
        return (
            f"AMSTray(id={self.id}, color={self.filament_color}, "
            f"type={self.filament_type}, brand={self.brand})"
        )


class AMSBox:
    """Represents a Canvas unit containing multiple trays."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """
        Initialize an AMSBox instance.

        Arguments:
            data (dict[str, Any] | None): Dictionary containing Canvas unit data.
                Expected keys: canvas_id, connected, tray_list.

        """
        if data is None:
            data = {}

        # Map Canvas API field names
        # Canvas uses 0-based canvas IDs (0, 1, etc)
        canvas_id = data.get("canvas_id", -1)
        self.id: str = str(canvas_id) if canvas_id >= 0 else ""
        self.connected: bool = bool(data.get("connected", 0))

        # Canvas doesn't report temperature/humidity, set defaults
        self.temperature: float = 0.0
        self.humidity: int = 0

        # Parse tray list
        tray_list_data = data.get("tray_list", [])
        self.tray_list: list[AMSTray] = [
            AMSTray(tray_data) for tray_data in tray_list_data
        ]

    def __repr__(self) -> str:
        """Return a string representation of the AMSBox instance."""
        return (
            f"AMSBox(id={self.id}, connected={self.connected}, "
            f"trays={len(self.tray_list)})"
        )


class AMSStatus:
    """Represents the complete Canvas/AMS status."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """
        Initialize an AMSStatus instance.

        Arguments:
            data (dict[str, Any] | None): Dictionary containing canvas_info data.
                Expected keys: active_canvas_id, active_tray_id, auto_refill,
                canvas_list.

        """
        if data is None:
            data = {}

        # Parse Canvas list
        canvas_list_data = data.get("canvas_list", [])
        self.ams_boxes: list[AMSBox] = [
            AMSBox(box_data) for box_data in canvas_list_data
        ]

        # Check if any Canvas unit is connected
        self.ams_connect_status: bool = any(box.connected for box in self.ams_boxes)
        self.ams_connect_num: int = sum(1 for box in self.ams_boxes if box.connected)

        # Parse active tray info
        # Canvas uses 0-based IDs, so active_canvas_id=0 is valid (not None/missing)
        active_canvas_id = data.get("active_canvas_id")
        active_tray_id = data.get("active_tray_id")

        # Check if active IDs are present (0 is valid, so check for None explicitly)
        if active_canvas_id is not None and active_tray_id is not None:
            # Canvas already uses 0-based IDs, just format for sensors
            # Canvas: canvas_id=0, tray_id=3 → Sensors: AmsId="0", TrayId="03"
            self.ams_current_enabled: dict[str, Any] | None = {
                "AmsId": str(active_canvas_id),
                "TrayId": str(active_tray_id).zfill(2),  # Pad: 0→"00", 3→"03"
                "Status": "active",
            }
        else:
            self.ams_current_enabled = None

        # Additional Canvas-specific fields
        self.auto_refill: bool = data.get("auto_refill", False)
        self.ams_type: str = "canvas"
        self.nozzle_filament_status: bool = active_tray_id > 0

    def __repr__(self) -> str:
        """Return a string representation of the AMSStatus instance."""
        active = "None"
        if self.ams_current_enabled:
            ams_id = self.ams_current_enabled.get("AmsId", "?")
            tray_id = self.ams_current_enabled.get("TrayId", "?")
            active = f"{ams_id}:{tray_id}"
        return (
            f"AMSStatus(connected={self.ams_connect_status}, "
            f"boxes={len(self.ams_boxes)}, active={active})"
        )
