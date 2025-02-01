"""Models for the Elegoo printer."""  # noqa: INP001

import json

from custom_components.elegoo_printer.elegoo.enums import (
    ElegooMachineStatus,
    ElegooPrintError,
    ElegooPrintStatus,
)

from .const import LOGGER


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
    ...     }    ... }
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
            self.ip_address: str | None = None
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
            self.ip_address = data.get("MainboardIP")
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
        self.status: ElegooPrintStatus | None = ElegooPrintStatus.from_int(status)
        self.current_layer: int = current_layer
        self.total_layers: int = total_layer
        self.remaining_layers: int = total_layer - current_layer
        self.percent_complete: float = round(
            (self.current_layer / self.total_layers) * 100, 2
        )
        self.current_ticks: int = current_ticks
        self.total_ticks: int = total_ticks
        remaining_ticks = total_ticks - current_ticks
        if remaining_ticks < 0:
            self.remaining_ticks: int = 0
        else:
            self.remaining_ticks: int = total_ticks - current_ticks
        self.error_number: ElegooPrintError | None = ElegooPrintError.from_int(
            error_number
        )
        self.filename: str = filename
        self.task_id: str = task_id


class Status:
    """Represent a printer status object."""

    def __init__(  # noqa: D107, PLR0913
        self,
        current_status: list[int],
        print_screen: str,
        release_film: str,
        temp_of_uvled: float,
        time_lapse_status: str,
        print_info: dict,
    ) -> None:
        self.current_status: ElegooMachineStatus | None = ElegooMachineStatus.from_int(
            current_status[0]
        )
        self.print_screen: str = print_screen
        self.release_film: str = release_film
        self.temp_of_uvled: float = round(temp_of_uvled, 2)
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
