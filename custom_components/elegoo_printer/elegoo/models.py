import json


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

      >>> printer_data = {
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
      >>> my_printer = Printer(printer_data)
      >>> print(my_printer.name)
      My Printer

    """

    def __init__(self, j: dict[str, str | dict[str, str | int]] | None = None) -> None:
        """
        Initialize a new Printer object.

        Args:
            j (dict, optional): A dictionary containing printer data.
                               Defaults to None, creating a "nulled" printer.

        """
        if j is None:
            self.connection: str | None = None
            self.name: str | None = None
            self.model: str | None = None
            self.brand: str | None = None
            self.ip: str | None = None
            self.protocol: str | None = None
            self.firmware: str | None = None
            self.id: str | None = None
        else:
            self.connection = j.get("Id")

            data: dict[str, str | int] = j.get("Data", {})
            self.name = data.get("Name")
            self.model = data.get("MachineName")
            self.brand = data.get("BrandName")
            self.ip = data.get("MainboardIP")
            self.protocol = data.get("ProtocolVersion")
            self.firmware = data.get("FirmwareVersion")
            self.id = data.get("MainboardID")


class Status:
    """Represent a printer status object."""

    def __init__(
        self,
        current_status: str,
        print_screen: str,
        release_film: str,
        temp_of_uvled: float,
        time_lapse_status: str,
        print_info: PrintInfo,
    ):
        """
        Initalize a new Status object.

        Returns:
            Status object

        """
        self.current_status: str = current_status
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


class PrintInfo:
    def __init__(
        self,
        status: Status,
        current_layer: int,
        total_layer: int,
        current_ticks: int,
        total_ticks: int,
        error_number: int,
        filename: str,
        task_id: str,
    ):
        self.status: Status = status
        self.current_layer: int = current_layer
        self.total_layer: int = total_layer
        self.current_ticks: int = current_ticks
        self.total_ticks: int = total_ticks
        self.error_number: int = error_number
        self.filename: str = filename
        self.task_id: str = task_id


class PrinterStatus:
    def __init__(
        self,
        status=None,  # noqa: ANN001
        mainboard_id: str = "",
        time_stamp: str = "",
        topic: str = "",
    ):
        if status is not None:
            self.status: Status = status
        self.mainboard_id: str = mainboard_id
        self.time_stamp: str = time_stamp
        self.topic: str = topic

    @classmethod
    def from_json(cls, json_str):
        """
        Creates a PrinterStatus object from a JSON string.

        Args:
            json_str: The JSON string representing the printer status.

        Returns:
            A PrinterStatus object.

        """
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

    def calculate_time_remaining(self) -> int | None:
        """
        Calculates the estimated time remaining in ticks.

        Returns:
            int: The estimated time remaining in ticks.

        """  # noqa: D401
        if self.status and self.status.print_info:
            return (
                self.status.print_info.total_ticks
                - self.status.print_info.current_ticks
            )
        return None

    def get_time_remaining_str(self) -> str:
        """
        Gets the estimated time remaining in a human-readable format (e.g., "2 hours 30 minutes").

        Returns:
            str: The estimated time remaining in a human-readable format (or "N/A" if unavailable).

        """  # noqa: D401
        remaining_ms = self.calculate_time_remaining()

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

    def get_layers_remaining(self) -> int:
        """
        Gets the layers remaining.

        Returns:
            int: The layers remaining

        """  # noqa: D401
        return self.status.print_info.total_layer - self.status.print_info.current_layer
