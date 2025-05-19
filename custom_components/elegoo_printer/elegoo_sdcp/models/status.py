"""Models for the Elegoo printer."""

import json
from typing import Any

from .enums import ElegooMachineStatus, ElegooPrintError, ElegooPrintStatus


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
        filename (str): Print File Name.
        error_number (ElegooPrintError): Error Number (refer to documentation).
        task_id (str): Current Task ID.

    """

    def __init__(
        self, data: dict[str, Any] = {}, use_seconds: bool = False
    ) -> None:  # noqa: B006
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
        self.remaining_layers: int = (
            self.total_layers - self.current_layer
        )  # Calculate remaining layers
        self.current_ticks: int = int(data.get("CurrentTicks", 0))
        self.total_ticks: int = int(data.get("TotalTicks", 0))
        self.remaining_ticks: int = max(
            0, self.total_ticks - self.current_ticks
        )  # Calculate remaining ticks
        # Get progress directly from data if available
        self.progress: int | None = data.get("Progress")
        if self.progress is not None:
            # If 'Progress' exists, use its value (converted to int)
            self.percent_complete: int = int(self.progress)
        else:
            # If 'Progress' doesn't exist or is None, calculate based on layers
            if self.total_layers > 0:
                # Calculate progress percentage based on layers
                self.percent_complete: int = int(
                    (self.current_layer / self.total_layers) * 100
                )
            else:
                # Handle the case where total layers is 0
                self.percent_complete: int = 0
        self.filename: str = data.get("Filename", "")
        error_number_int: int = data.get("ErrorNumber", 0)
        self.error_number: ElegooPrintError | None = ElegooPrintError.from_int(
            error_number_int
        )  # Use from_int
        self.task_id: str = data.get("TaskId", "")

        if use_seconds:
            self.current_ticks = self.current_ticks * 1000
            self.total_ticks = self.total_ticks * 1000
            self.remaining_ticks = self.remaining_ticks * 1000


class PrinterStatus:
    """
    Represents the status of a 3D printer.

    Attributes:
        current_status (ElegooMachineStatus): Current Machine Status.
        previous_status (int): Previous Machine Status.
        print_screen (int): Total Exposure Screen Usage Time(s).
        release_film (int): Total Release Film Usage Count.
        temp_of_uvled (int): Current UVLED Temperature (℃).
        time_lapse_status (int): Time-lapse Photography Switch Status. 0: Off, 1: On.
        temp_of_box (int): Current Enclosure Temperature (℃).
        temp_target_box (int): Target Enclosure Temperature (℃).
        print_info (PrintInfo): Printing Information.

    """

    def __init__(
        self, data: dict[str, Any] = {}, use_seconds: bool = False
    ) -> None:  # noqa: B006
        """
        Initialize a new PrinterStatus object from a dictionary.

        Args:
            data (Dict[str, Any], optional): A dictionary containing printer status data.
                                             Defaults to an empty dictionary.

        """  # noqa: E501
        status = data.get("Status", {"CurrentStatus": {}})
        current_status_list = status.get("CurrentStatus", [])
        self.current_status: ElegooMachineStatus | None = ElegooMachineStatus.from_list(
            current_status_list
        )
        self.previous_status: int = status.get("PreviousStatus", 0)
        self.print_screen: int = status.get("PrintScreen", 0)
        self.release_film: int = status.get("ReleaseFilm", 0)
        self.time_lapse_status: int = status.get("TimeLapseStatus", 0)
        self.temp_of_uvled: float = round(status.get("TempOfUVLED", 0), 2)
        self.temp_of_box: float = round(status.get("TempOfBox", 0), 2)
        self.temp_target_box: float = round(status.get("TempTargetBox", 0), 2)

        print_info_data = status.get("PrintInfo", {})
        self.print_info: PrintInfo = PrintInfo(print_info_data, use_seconds=use_seconds)

    @classmethod
    def from_json(cls, json_string: str, use_seconds: bool = False) -> "PrinterStatus":
        """
        Create a PrinterStatus object from a JSON string.

        Args:
            json_string (str): A JSON string containing printer status data.

        Returns:
            PrinterStatus: A new PrinterStatus object.

        """
        try:
            data = json.loads(json_string)

        except json.JSONDecodeError:
            data = {}  # Or handle the error as needed
        return cls(data, use_seconds)
