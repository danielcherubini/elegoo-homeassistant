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
        # Canvas uses 1-based tray IDs (1, 2, 3, 4)
        # Convert to 0-based and pad (00, 01, 02, 03)
        tray_id = data.get("tray_id", 0)
        self.id: str = str(tray_id - 1).zfill(2) if tray_id > 0 else ""
        self.brand: str = data.get("brand", "")
        self.filament_type: str = data.get("filament_type", "")
        self.filament_name: str = data.get("filament_name", "")

        # Add # prefix to color if not present
        color = data.get("filament_color", "")
        if color and not color.startswith("#"):
            self.filament_color: str = f"#{color}"
        else:
            self.filament_color: str = color

        self.min_nozzle_temp: int = data.get("nozzle_temp_min", 0)
        self.max_nozzle_temp: int = data.get("nozzle_temp_max", 0)
        self.min_bed_temp: int = data.get("bed_temp_min", 0)
        self.max_bed_temp: int = data.get("bed_temp_max", 0)

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
        # Canvas uses 1-based canvas IDs (1, 2, etc), convert to 0-based (0, 1, etc)
        canvas_id = data.get("canvas_id", 0)
        self.id: str = str(canvas_id - 1) if canvas_id > 0 else ""
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
        active_canvas_id = data.get("active_canvas_id", 0)
        active_tray_id = data.get("active_tray_id", 0)

        if active_canvas_id and active_tray_id:
            # Convert Canvas 1-based IDs to 0-based for sensor compatibility
            # Canvas: canvas_id=1, tray_id=1 → Sensors: AmsId="0", TrayId="00"
            self.ams_current_enabled: dict[str, Any] | None = {
                "AmsId": str(active_canvas_id - 1),
                "TrayId": str(active_tray_id - 1).zfill(2),  # e.g., 1 → "00", 2 → "01"
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
