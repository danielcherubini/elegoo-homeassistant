"""Models for the Elegoo printer."""

import json
from typing import Any, List

from .enums import ElegooMachineStatus, ElegooPrintError, ElegooPrintStatus


class CurrentFanSpeed:
    """Represents the speed of the various fans."""

    def __init__(self, data: dict[str, Any] = {}) -> None:  # noqa: B006
        """Initialize a new CurrentFanSpeed object."""
        self.model_fan: int = data.get("ModelFan", 0)
        self.auxiliary_fan: int = data.get("AuxiliaryFan", 0)
        self.box_fan: int = data.get("BoxFan", 0)


class LightStatus:
    """Represents the status of the printer's lights."""

    def __init__(self, data: dict[str, Any] = {}) -> None:  # noqa: B006
        """Initialize a new LightStatus object."""
        self.second_light: int = data.get("SecondLight", 0)
        self.rgb_light: List[int] = data.get("RgbLight", [0, 0, 0])


class PrintInfo:
    """
    Represents information about a print job.

    Attributes:
        status (ElegooPrintStatus): Printing Sub-status.
        current_layer (int): Current Printing Layer.
        total_layers (int): Total Number of Print Layers.
        remaining_layers (int): Remaining layers to print
        current_ticks (int): Current Print Time (ms).
        total_ticks (int): Estimated Total Print Time(ms).
        remaining_ticks (int): Remaining Print Time(ms).
        progress (int): Print Progress (%).
        percent_complete (int): Percentage Complete.
        print_speed_pct (int): The current print speed as a percentage.
        filename (str): Print File Name.
        error_number (ElegooPrintError): Error Number (refer to documentation).
        task_id (str): Current Task ID.
    """

    def __init__(self, data: dict[str, Any] = {}) -> None:  # noqa: B006
        """
        Initialize a new PrintInfo object.

        Args:
            data (Dict[str, Any], optional): A dictionary containing print info data.
                                            Defaults to an empty dictionary.
        """
        status_int: int = data.get("Status", 0)
        self.status: ElegooPrintStatus | None = ElegooPrintStatus.from_int(status_int)
        self.current_layer: int = data.get("CurrentLayer", 0)
        self.total_layers: int = data.get("TotalLayer", 0)
        self.remaining_layers: int = self.total_layers - self.current_layer
        self.current_ticks: int = int(data.get("CurrentTicks", 0))
        self.total_ticks: int = int(data.get("TotalTicks", 0))
        self.remaining_ticks: int = max(0, self.total_ticks - self.current_ticks)
        self.progress: int | None = data.get("Progress")
        self.print_speed_pct: int = data.get("PrintSpeedPct", 100)

        if self.progress is not None:
            self.percent_complete: int = int(self.progress)
        else:
            if self.total_layers > 0:
                self.percent_complete: int = int(
                    (self.current_layer / self.total_layers) * 100
                )
            else:
                self.percent_complete: int = 0
        self.filename: str = data.get("Filename", "")
        error_number_int: int = data.get("ErrorNumber", 0)
        self.error_number: ElegooPrintError | None = ElegooPrintError.from_int(
            error_number_int
        )
        self.task_id: str = data.get("TaskId", "")


class PrinterStatus:
    """
    Represents the status of a 3D printer.
    """

    def __init__(self, data: dict[str, Any] = {}) -> None:  # noqa: B006
        """
        Initialize a new PrinterStatus object from a dictionary.
        """
        status = data.get("Status", {"CurrentStatus": {}})
        current_status_list = status.get("CurrentStatus", [])
        self.current_status: ElegooMachineStatus | None = ElegooMachineStatus.from_list(
            current_status_list
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
        self.print_info: PrintInfo = PrintInfo(print_info_data)

    @classmethod
    def from_json(cls, json_string: str) -> "PrinterStatus":
        """
        Create a PrinterStatus object from a JSON string.
        """
        try:
            data = json.loads(json_string)
        except json.JSONDecodeError:
            data = {}  # Or handle the error as needed
        return cls(data)
