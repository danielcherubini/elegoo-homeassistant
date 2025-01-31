"""Models for the Elegoo printer."""  # noqa: INP001

import json

from .const import LOGGER
from .enums import ElegooMachineStatus, ElegooPrintError, ElegooPrintStatus


class Printer:
    """
    Represent a printer with various attributes.

    Attributes:
        connection (str): The connection ID of the printer.
        name (str): The name of the printer.
        model (str): The model name of the printer.
        brand (str): The brand of the printer.
        ip (str): The IP address of the printer.
        protocol (str): The protocol version used by the printer.
        firmware (str): The firmware version of the printer.
        id (str): The unique ID of the printer's mainboard.

    Example usage:

    >>> printer_json = '''
    ... {
    ...     "Id": "12345",
    ...     "Data": {
    ...         "Name": "My Printer",
    ...         "MachineName": "Model XYZ",
    ...         "BrandName": "Acme",
    ...         "MainboardIP": "192.168.1.100",
    ...         "ProtocolVersion": "2.0",
    ...         "FirmwareVersion": "1.5",
    ...         "MainboardID": "ABCDEF"
    ...     }
    ... }
    ... '''
    >>> my_printer = Printer(printer_json)
    >>> print(my_printer.name)
    My Printer

    """

    def __init__(self, json_string: str | None = None) -> None:
        """
        Initialize a new Printer object from a JSON string.

        Args:
            json_string (str, optional): A JSON string containing printer data.
                                         Defaults to None, creating a "nulled" printer.

        """
        if json_string is None:
            self.connection: str | None = None
            self.name: str = ""
            self.model: str | None = None
            self.brand: str | None = None
            self.ip: str | None = None
            self.protocol: str | None = None
            self.firmware: str | None = None
            self.id: str | None = None
        else:
            try:
                j: dict = json.loads(json_string)  # Decode the JSON string
            except json.JSONDecodeError as e:
                # Handle the error appropriately (e.g., log it, raise an exception)
                LOGGER.error(f"Error decoding JSON: {e}")  # noqa: TRY400
                return

            self.connection = j.get("Id")

            data = j.get("Data", {})
            self.name = data.get("Name")
            self.model = data.get("MachineName")
            self.brand = data.get("BrandName")
            self.ip = data.get("MainboardIP")
            self.protocol = data.get("ProtocolVersion")
            self.firmware = data.get("FirmwareVersion")
            self.id = data.get("MainboardID")


class PrintInfo:
    """Represent a printer info object."""

    def __init__(  # noqa: D107, PLR0913
        self,
        status: int,
        current_layer: int,
        total_layer: int,
        current_ticks: int,
        total_ticks: int,
        error_number: int,
        filename: str,
        task_id: str,
    ) -> None:
        self.status: ElegooPrintStatus = ElegooPrintStatus(status)
        self.current_layer: int = current_layer
        self.total_layer: int = total_layer
        self.remaining_layers: int = total_layer - current_layer
        self.current_ticks: int = current_ticks
        self.total_ticks: int = total_ticks
        self.remaining_ticks: int = total_ticks - current_ticks
        self.error_number: ElegooPrintError = ElegooPrintError(error_number)
        self.filename: str = filename
        self.task_id: str = task_id


class Status:
    """Represent a printer status object."""

    def __init__(  # noqa: D107, PLR0913
        self,
        current_status: str,
        print_screen: str,
        release_film: str,
        temp_of_uvled: float,
        time_lapse_status: str,
        print_info: dict,
    ) -> None:
        self.current_status: ElegooMachineStatus = ElegooMachineStatus(current_status)
        self.print_screen: str = print_screen
        self.release_film: str = release_film
        self.temp_of_uvled: float = temp_of_uvled
        self.time_lapse_status: str = time_lapse_status
        self.print_info: PrintInfo = PrintInfo(
            print_info["Status"],
            print_info["CurrentLayer"],
            print_info["TotalLayer"],
            print_info["CurrentTicks"],
            print_info["TotalTicks"],
            print_info["ErrorNumber"],
            print_info["Filename"],
            print_info["TaskId"],
        )


class PrinterStatus:
    """Represent a printerstatus object."""

    def __init__(  # noqa: D107
        self,
        status: Status | None = None,
        mainboard_id: str = "",
        time_stamp: str = "",
        topic: str = "",
    ) -> None:
        if status is not None:
            self.status: Status = status
        self.mainboard_id: str = mainboard_id
        self.time_stamp: str = time_stamp
        self.topic: str = topic

    @classmethod
    def from_json(cls, json_str: str):  # noqa: ANN206
        """
        Creates a PrinterStatus object from a JSON string.

        Args:
            json_str: The JSON string representing the printer status.

        Returns:
            A PrinterStatus object.

        """  # noqa: D401
        data = json.loads(json_str)

        # Create Status object
        status_data = data["Status"]
        status = Status(
            status_data["CurrentStatus"],
            status_data["PrintScreen"],
            status_data["ReleaseFilm"],
            status_data["TempOfUVLED"],
            status_data["TimeLapseStatus"],
            status_data["PrintInfo"],
        )

        # Create PrinterStatus object
        return cls(status, data["MainboardID"], data["TimeStamp"], data["Topic"])

    def get_time_remaining_str(self) -> str:
        """
        Gets the estimated time remaining in a human-readable format
        (e.g., "2 hours 30 minutes").

        Returns:
            str: The estimated time remaining in a human-readable format
            (or "N/A" if unavailable).

        """  # noqa: D205, D401
        remaining_ms = self.status.print_info.remaining_ticks

        if remaining_ms is None:
            return "N/A"

        seconds = remaining_ms / 1000
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)

        time_str = ""
        if hours > 0:
            time_str += f"{int(hours)} hour{'s' if hours > 1 else ''}"
        if minutes > 0:
            if time_str:
                time_str += " "
            time_str += f"{int(minutes)} minute{'s' if minutes > 1 else ''}"
        if seconds > 0 and (not hours and not minutes):
            time_str += f"{int(seconds)} second{'s' if seconds > 1 else ''}"

        if not time_str:
            time_str = "Less than a minute"

        return time_str
