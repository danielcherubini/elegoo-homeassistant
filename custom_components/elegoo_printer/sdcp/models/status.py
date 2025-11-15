"""Models for the Elegoo printer."""

import json
from typing import Any

from .enums import ElegooMachineStatus, ElegooPrintError, ElegooPrintStatus, PrinterType


class CurrentFanSpeed:
    """Represents the speed of the various fans."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """Initialize a new CurrentFanSpeed object."""
        if data is None:
            data = {}
        self.model_fan: int = data.get("ModelFan", 0)
        self.auxiliary_fan: int = data.get("AuxiliaryFan", 0)
        self.box_fan: int = data.get("BoxFan", 0)


class LightStatus:
    """Represents the status of the printer's lights."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """
        Initialize a LightStatus instance with secondary and RGB light values.

        Arguments:
            data (dict[str, Any] | None): Optional dictionary containing "SecondLight"
            and "RgbLight" keys. Defaults to all lights off if not provided.

        """
        if data is None:
            data = {}
        self.second_light: int | None = data.get("SecondLight")
        self.rgb_light: list[int] | None = data.get("RgbLight")

    def to_dict(self) -> dict[str, Any]:
        """
        Return a dictionary representation of the LightStatus instance.

        Returns:
            dict: A dictionary with keys "LightStatus", "SecondLight", and "RgbLight".

        """
        return {
            "LightStatus": {
                "SecondLight": self.second_light,
                "RgbLight": self.rgb_light,
            }
        }

    def __repr__(self) -> str:
        """Return a string representation of the LightStatus instance."""
        return (
            f"LightStatus(second_light={self.second_light}, rgb_light={self.rgb_light})"
        )

    def __str__(self) -> str:
        """Return a string describing the secondary light status and RGB light values."""  # noqa: E501
        return f"Secondary Light: {'On' if self.second_light else 'Off'}, RGB: {self.rgb_light}"  # noqa: E501


class PrintInfo:
    """
    Represents information about a print job.

    Attributes:
        status (ElegooPrintStatus): Printing Sub-status.
        current_layer (int | None): Current printing layer, or None if unknown.
        total_layers (int | None): Total number of print layers, or None if unknown.
        remaining_layers (int | None): Remaining layers to print, or None if unknown.
        current_ticks (int | None): Current print time in ms, or None if unknown.
        total_ticks (int | None): Estimated total print time in ms, or None if unknown.
        remaining_ticks (int | None): Remaining print time in ms, or None if unknown.
        progress (int | None): Device-reported print progress (0-100), or None
            if unknown.
        percent_complete (int | None): Percentage complete, clamped to [0, 100], or None
            if unknown.
        print_speed_pct (int): The current print speed as a percentage.
        filename (str): Print File Name.
        error_number (ElegooPrintError): Error Number (refer to documentation).
        task_id (str): Current Task ID.
        total_extrusion (float | None): Total filament extrusion in mm for
            the print job.
        current_extrusion (float | None): Current filament extrusion in mm.

    """

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        printer_type: PrinterType | None = None,
    ) -> None:
        """
        Initialize a new PrintInfo object.

        Arguments:
            data (dict[str, Any] | None, optional): A dictionary containing print
                info data.
            printer_type (PrinterType | None, optional): The type of printer.

        """
        if data is None:
            data = {}
        status_int: int = data.get("Status", 0)
        self.status: ElegooPrintStatus | None = ElegooPrintStatus.from_int(status_int)
        self.current_layer: int | None = data.get("CurrentLayer")
        self.total_layers: int | None = data.get("TotalLayer")
        self.remaining_layers: int | None = None
        if self.current_layer is not None and self.total_layers is not None:
            self.remaining_layers = max(0, self.total_layers - self.current_layer)

        self.current_ticks: int | None = data.get("CurrentTicks")
        self.total_ticks: int | None = data.get("TotalTicks")
        self.remaining_ticks: int | None = None
        if printer_type == PrinterType.FDM:
            if self.current_ticks is not None:
                self.current_ticks *= 1000
            if self.total_ticks is not None:
                self.total_ticks *= 1000
        if self.current_ticks is not None and self.total_ticks is not None:
            self.remaining_ticks = max(0, self.total_ticks - self.current_ticks)
        self.progress: int | None = data.get("Progress")
        self.print_speed_pct: int = data.get("PrintSpeedPct", 100)
        self.end_time = None
        # percent_complete is optional when printer is idle/unknown
        self.percent_complete: int | None = None

        percent_complete = None
        # Report progress only during an active job to avoid leaking stale values.
        active_statuses = {
            ElegooPrintStatus.PRINTING,
            ElegooPrintStatus.PAUSED,
            ElegooPrintStatus.PAUSING,
            ElegooPrintStatus.LIFTING,
            ElegooPrintStatus.DROPPING,
            ElegooPrintStatus.RECOVERY,
            ElegooPrintStatus.PRINTING_RECOVERY,
            ElegooPrintStatus.PREHEATING,
            ElegooPrintStatus.LEVELING,
        }
        if self.status in active_statuses:
            if self.progress is not None:
                percent_complete = int(self.progress)
            elif (
                self.current_layer is not None
                and self.total_layers is not None
                and self.total_layers > 0
            ):
                # Optional: round to reduce downward bias from truncation
                percent_complete = round((self.current_layer / self.total_layers) * 100)
        else:
            percent_complete = None

        if percent_complete is not None:
            self.percent_complete = max(0, min(100, percent_complete))
        else:
            self.percent_complete = None

        self.filename = data.get("Filename")
        error_number_int = data.get("ErrorNumber", 0)
        self.error_number = ElegooPrintError.from_int(error_number_int)
        self.task_id = data.get("TaskId")

        # Filament extrusion data (FDM only)
        # The printer sends these with hex-encoded keys, so we need to
        # check both formats. Use explicit None check to handle 0 values.
        self.total_extrusion: float | None = (
            data.get("TotalExtrusion")
            if "TotalExtrusion" in data
            # Hex for "TotalExtrusion"
            else data.get("54 6F 74 61 6C 45 78 74 72 75 73 69 6F 6E 00")
        )
        self.current_extrusion: float | None = (
            data.get("CurrentExtrusion")
            if "CurrentExtrusion" in data
            # Hex for "CurrentExtrusion"
            else data.get("43 75 72 72 65 6E 74 45 78 74 72 75 73 69 6F 6E 00")
        )


class PrinterStatus:
    """
    Represents the status of a 3D printer.

    Attributes:
        current_status (ElegooMachineStatus): The current status of the machine.
        previous_status (int): The previous status of the machine.
        print_screen (int): The print screen status.
        release_film (int): The release film status.
        time_lapse_status (int): The time lapse status.
        platform_type (int): The platform type.
        temp_of_uvled (float): The temperature of the UV LED.
        temp_of_box (float): The temperature of the box.
        temp_target_box (float): The target temperature of the box.
        temp_of_hotbed (float): The temperature of the hotbed.
        temp_of_nozzle (float): The temperature of the nozzle.
        temp_target_hotbed (float): The target temperature of the hotbed.
        temp_target_nozzle (float): The target temperature of the nozzle.
        current_coord (str): The current coordinates of the printer.
        z_offset (float): The z-offset of the printer.
        current_fan_speed (CurrentFanSpeed): The current fan speed.
        light_status (LightStatus): The status of the lights.
        print_info (PrintInfo): Information about the current print job.

    """

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        printer_type: PrinterType | None = None,
    ) -> None:
        """Initialize a new PrinterStatus object from a dictionary."""
        if data is None:
            data = {}

        # Support both legacy Saturn (nested) and flat formats
        status = data.get("Status", data)

        current_status_data = status.get("CurrentStatus", [])
        # MQTT printers send CurrentStatus as int, WebSocket as list
        if isinstance(current_status_data, int):
            self.current_status: ElegooMachineStatus | None = (
                ElegooMachineStatus.from_int(current_status_data)
            )
        else:
            self.current_status: ElegooMachineStatus | None = (
                ElegooMachineStatus.from_list(current_status_data)
            )

        # Generic Status
        self.previous_status: int = status.get("PreviousStatus", 0)
        self.print_screen: int = status.get("PrintScreen", 0)
        self.release_film: int = status.get("ReleaseFilm", 0)
        self.time_lapse_status: int = status.get("TimeLapseStatus", 0)
        self.platform_type: int = status.get("PlatFormType", 1)

        # Temperatures
        self.temp_of_uvled: float = round(status.get("TempOfUVLED", 0), 2)
        self.temp_of_box: float = round(status.get("TempOfBox", 0), 2)
        self.temp_target_box: float = round(status.get("TempTargetBox", 0), 2)
        self.temp_of_hotbed: float = round(status.get("TempOfHotbed", 0.0), 2)
        self.temp_of_nozzle: float = round(status.get("TempOfNozzle", 0.0), 2)
        self.temp_target_hotbed: float = round(status.get("TempTargetHotbed", 0), 2)
        self.temp_target_nozzle: float = round(status.get("TempTargetNozzle", 0), 2)

        # Position and Offset
        self.current_coord: str = status.get("CurrenCoord", "0.00,0.00,0.00")
        self.z_offset: float = status.get("ZOffset", 0.0)

        # Nested Status Objects
        fan_speed_data = status.get("CurrentFanSpeed", {})
        self.current_fan_speed = CurrentFanSpeed(fan_speed_data)

        light_status_data = status.get("LightStatus", {})
        self.light_status = LightStatus(light_status_data)

        print_info_data = status.get("PrintInfo", {})
        self.print_info: PrintInfo = PrintInfo(print_info_data, printer_type)

    @classmethod
    def from_json(
        cls, json_string: str, printer_type: PrinterType | None = None
    ) -> "PrinterStatus":
        """Create a PrinterStatus object from a JSON string."""
        try:
            data = json.loads(json_string)
        except json.JSONDecodeError:
            data = {}  # Or handle the error as needed
        return cls(data, printer_type)
