import json


class Status:
    def __init__(
        self,
        current_status,
        print_screen,
        release_film,
        temp_of_uvled,
        time_lapse_status,
        print_info,
    ):
        self.current_status = current_status
        self.print_screen = print_screen
        self.release_film = release_film
        self.temp_of_uvled: float = temp_of_uvled
        self.time_lapse_status = time_lapse_status
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
        status,
        current_layer,
        total_layer,
        current_ticks,
        total_ticks,
        error_number,
        filename,
        task_id,
    ):
        self.status = status
        self.current_layer = current_layer
        self.total_layer = total_layer
        self.current_ticks = current_ticks
        self.total_ticks = total_ticks
        self.error_number = error_number
        self.filename = filename
        self.task_id = task_id


class PrinterStatus:
    def __init__(self, status, mainboard_id, time_stamp, topic):
        self.status: Status = status
        self.mainboard_id = mainboard_id
        self.time_stamp = time_stamp
        self.topic = topic

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

    def calculate_time_remaining(self):
        """
        Calculates the estimated time remaining in ticks.

        Returns:
            int: The estimated time remaining in ticks.

        """
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

        """
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
         return self.status.print_info.total_layer - self.status.print_info.current_layer

