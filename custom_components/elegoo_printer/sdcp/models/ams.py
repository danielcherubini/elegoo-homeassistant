"""Models for Canvas/AMS (Automatic Material System) data."""

from typing import Any


class AMSTray:
    """Represents a single filament tray in the AMS."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """
        Initialize an AMSTray instance.

        Arguments:
            data (dict[str, Any] | None): Dictionary containing tray data.
                Expected keys: TrayId, FromSource, Brand, FilamentType,
                FilamentName, FilamentColor, SerialNumber, FilamentDiameter,
                MinNozzleTemp, MaxNozzleTemp, MinBedTemp, MaxBedTemp, Enable.

        """
        if data is None:
            data = {}
        self.id: str = data.get("TrayId", "")
        self.from_source: str = data.get("FromSource", "null")
        self.brand: str = data.get("Brand", "")
        self.filament_type: str = data.get("FilamentType", "")
        self.filament_name: str = data.get("FilamentName", "")
        self.filament_color: str = data.get("FilamentColor", "")
        self.serial_number: int | None = data.get("SerialNumber")
        self.filament_diameter: str = data.get("FilamentDiameter", "")
        self.min_nozzle_temp: int = data.get("MinNozzleTemp", 0)
        self.max_nozzle_temp: int = data.get("MaxNozzleTemp", 0)
        self.min_bed_temp: int = data.get("MinBedTemp", 0)
        self.max_bed_temp: int = data.get("MaxBedTemp", 0)
        self.enabled: bool = data.get("Enable", False)

    def __repr__(self) -> str:
        """Return a string representation of the AMSTray instance."""
        return (
            f"AMSTray(id={self.id}, color={self.filament_color}, "
            f"type={self.filament_type}, brand={self.brand})"
        )


class AMSBox:
    """Represents an AMS box containing multiple trays."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """
        Initialize an AMSBox instance.

        Arguments:
            data (dict[str, Any] | None): Dictionary containing box data.
                Expected keys: AmsId, Temperature, Humidity, TrayList.

        """
        if data is None:
            data = {}
        self.id: str = data.get("AmsId", "")
        self.temperature: float = float(data.get("Temperature", 0))
        self.humidity: int = int(data.get("Humidity", 0))

        # Parse tray list
        tray_list_data = data.get("TrayList", [])
        self.tray_list: list[AMSTray] = [
            AMSTray(tray_data) for tray_data in tray_list_data
        ]

    def __repr__(self) -> str:
        """Return a string representation of the AMSBox instance."""
        return (
            f"AMSBox(id={self.id}, temp={self.temperature}°C, "
            f"humidity={self.humidity}%, trays={len(self.tray_list)})"
        )


class AMSStatus:
    """Represents the complete AMS status."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """
        Initialize an AMSStatus instance.

        Arguments:
            data (dict[str, Any] | None): Dictionary containing AMS status data.
                Expected keys: AmsConnectStatus, AmsType, AmsConnectNum,
                NozzleFilamentStatus, AmsCurrentEnabled, AmsList.

        """
        if data is None:
            data = {}
        self.ams_connect_status: bool = data.get("AmsConnectStatus", False)
        self.ams_type: str = data.get("AmsType", "")
        self.ams_connect_num: int = data.get("AmsConnectNum", 0)
        self.nozzle_filament_status: bool = data.get("NozzleFilamentStatus", False)

        # Parse currently enabled tray (can be None)
        self.ams_current_enabled: dict[str, Any] | None = data.get("AmsCurrentEnabled")

        # Parse AMS boxes list
        ams_list_data = data.get("AmsList", [])
        self.ams_boxes: list[AMSBox] = [AMSBox(box_data) for box_data in ams_list_data]

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
