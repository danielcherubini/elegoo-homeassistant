"""Elegoo Printer enums."""

from enum import Enum
from typing import Optional


class ElegooMachineStatus(Enum):
    """
    Represents the different status states of an SDCP machine.

    Attributes:
        IDLE: The machine is idle and not performing any task.
        PRINTING: The machine is currently executing a print task.
        FILE_TRANSFERRING: A file transfer is in progress.
        EXPOSURE_TESTING: The machine is performing an exposure test.
        DEVICES_TESTING: The machine is running a device self-check.

    """

    IDLE = 0
    PRINTING = 1
    FILE_TRANSFERRING = 2
    EXPOSURE_TESTING = 3
    DEVICES_TESTING = 4

    @classmethod
    def from_int(cls, status_int: int) -> Optional["ElegooMachineStatus"] | None:
        """
        Converts an integer to an ElegooMachineStatus enum member.

        Args:
            status_int: The integer representing the print status.

        Returns:
            The corresponding ElegooMachineStatus enum member, or None if the
            integer is not a valid status value.

        """  # noqa: D401
        try:
            return cls(status_int)  # Use cls() to create enum members
        except ValueError:
            return None

    @classmethod
    def from_list(cls, status_list: list[int]) -> Optional["ElegooMachineStatus"]:
        """
        Convert a list of integers to an ElegooMachineStatus enum member.

        Args:
            status_list: A list of integers representing print statuses.
                         It is expected to contain only one element.

        Returns:
            The corresponding ElegooMachineStatus enum member, or None if:
            - The list is empty.
            - The list contains more than one element.
            - The integer in the list is not a valid status value.

        """
        if not status_list or len(status_list) != 1:
            return None  # Return None if the list is empty or has more than one element

        status_int = status_list[0]
        return cls.from_int(status_int)


class ElegooPrintStatus(Enum):
    """
    Represents the different status states of a print job.

    Attributes:
        IDLE: The print job is idle and not actively printing.
        HOMING: The printer is resetting or homing its axes.
        DROPPING: The print platform is descending.
        EXPOSURING: The printer is exposing the resin/material.
        LIFTING: The print platform is lifting.
        PAUSING: The printer is in the process of pausing the print job.
        PAUSED: The print job is currently paused.
        STOPPING: The printer is in the process of stopping the print job.
        STOPPED: The print job is stopped.
        COMPLETE: The print job has completed successfully.
        FILE_CHECKING: The printer is currently checking the print file.

    """

    IDLE = 0
    HOMING = 1
    DROPPING = 2
    EXPOSURING = 3
    LIFTING = 4
    PAUSING = 5
    PAUSED = 6
    STOPPING = 7
    STOPPED = 8
    COMPLETE = 9
    FILE_CHECKING = 10

    @classmethod
    def from_int(cls, status_int: int) -> Optional["ElegooPrintStatus"] | None:
        """
        Converts an integer to an ElegooPrintStatus enum member.

        Args:
            status_int: The integer representing the print status.

        Returns:
            The corresponding ElegooPrintStatus enum member, or None if the
            integer is not a valid status value.

        """  # noqa: D401
        try:
            return cls(status_int)  # Use cls() to create enum members
        except ValueError:
            return None


class ElegooPrintError(Enum):
    """
    Represents the different error states that can occur during printing.

    Attributes:
        NONE: No error has occurred. The print process is normal.
        CHECK: File MD5 checksum check failed, indicating potential file corruption.
        FILEIO: An error occurred while reading the print file.
        INVALID_RESOLUTION: The print file's resolution does not match the printer's capabilities.
        UNKNOWN_FORMAT: The printer does not recognize the format of the print file.
        UNKNOWN_MODEL: The print file is intended for a different machine model.

    """  # noqa: E501

    NONE = 0
    CHECK = 1
    FILEIO = 2
    INVALID_RESOLUTION = 3
    UNKNOWN_FORMAT = 4
    UNKNOWN_MODEL = 5

    @classmethod
    def from_int(cls, status_int: int) -> Optional["ElegooPrintError"] | None:
        """
        Convert an integer to the corresponding ElegooPrintError enum member.

        Returns:
            The matching ElegooPrintError member if the integer is valid, or None if it does not correspond to any defined error.
        """  # noqa: D401
        try:
            return cls(status_int)  # Use cls() to create enum members
        except ValueError:
            return None


class ElegooVideoStatus(Enum):
    """
    Represents a video status

    Attributes:
        0 - Success
        1 - Exceeded maximum streaming limit
        2 - Camera does not exist
        3 - Unknown error

    """

    SUCCESS = 0
    EXCEEDED_MAX_STREAMING_LIMIT = 1
    CAMERA_DOES_NOT_EXIST = 2
    UNKNOWN_ERROR = 3

    @classmethod
    def from_int(cls, status_int: int) -> Optional["ElegooVideoStatus"] | None:
        """
        Convert an integer to the corresponding ElegooVideoStatus enum member.

        Returns:
            ElegooVideoStatus: The matching enum member if the integer is valid, otherwise None.
        """
        try:
            return cls(status_int)
        except ValueError:
            return None


class ErrorStatusReason(Enum):
    """
    Represents the reason for a print job status or error.

    Attributes:
        OK: Normal operation.
        TEMP_ERROR: Over-temperature error for the nozzle or bed.
        FILAMENT_RUNOUT: Filament runout was detected.
        FILAMENT_JAM: A filament jam or clog was detected.
        LEVEL_FAILED: Auto-bed leveling process failed.
        UDISK_REMOVE: USB drive was removed during printing.
        HOME_FAILED_X: Homing failed on the X-axis, likely a motor or endstop issue.
        HOME_FAILED_Z: Homing failed on the Z-axis, likely a motor or endstop issue.
        HOME_FAILED: A general homing failure occurred.
        BED_ADHESION_FAILED: The print detached from the print bed.
        ERROR: A general, unspecified printing exception occurred.
        MOVE_ABNORMAL: An abnormality was detected in motor movement.
        HOME_FAILED_Y: Homing failed on the Y-axis, likely a motor or endstop issue.
        FILE_ERROR: An error occurred while reading the G-code file.
        CAMERA_ERROR: A camera connection error occurred.
        NETWORK_ERROR: A network connection error occurred.
        SERVER_CONNECT_FAILED: Failed to connect to the server.
        DISCONNECT_APP: The controlling application disconnected during the print.
        NOZZLE_TEMP_SENSOR_OFFLINE: The nozzle thermistor is offline or disconnected.
        BED_TEMP_SENSOR_OFFLINE: The bed thermistor is offline or disconnected.

    """  # noqa: E501

    OK = 0
    TEMP_ERROR = 1
    FILAMENT_RUNOUT = 3
    FILAMENT_JAM = 6
    LEVEL_FAILED = 7
    UDISK_REMOVE = 12
    HOME_FAILED_X = 13
    HOME_FAILED_Z = 14
    HOME_FAILED = 17
    BED_ADHESION_FAILED = 18
    ERROR = 19
    MOVE_ABNORMAL = 20
    HOME_FAILED_Y = 23
    FILE_ERROR = 24
    CAMERA_ERROR = 25
    NETWORK_ERROR = 26
    SERVER_CONNECT_FAILED = 27
    DISCONNECT_APP = 28
    NOZZLE_TEMP_SENSOR_OFFLINE = 33
    BED_TEMP_SENSOR_OFFLINE = 34

    @classmethod
    def from_int(cls, status_int: int) -> Optional["ErrorStatusReason"]:
        """
        Convert an integer to the corresponding ErrorStatusReason enum member.

        Returns:
            The matching ErrorStatusReason member if the integer is valid; otherwise, None.
        """
        try:
            return cls(status_int)
        except ValueError:
            return None


class PrinterType(Enum):
    """
    Represents the type of printer.

    Attributes:
        RESIN: A resin-based 3D printer.
        FDM: A fused deposition modeling (FDM) 3D printer.
    """

    RESIN = "resin"
    FDM = "fdm"

    @classmethod
    def from_model(cls, model: str) -> Optional["PrinterType"]:
        """
        Returns the printer type (RESIN or FDM) based on the provided model name.

        Parameters:
            model (str): The printer model name to evaluate.

        Returns:
            PrinterType or None: The corresponding printer type if the model matches a known FDM or resin printer, otherwise None.
        """
        if not model:
            return None

        fdm_printers = ["Centauri Carbon", "Centauri"]
        resin_printers = [
            "Mars 5",
            "Mars 5 Ultra",
            "Saturn 4",
            "Saturn 4 Ultra",
            "Saturn 4 Ultra 16k",
        ]

        if any(fdm_printer in model for fdm_printer in fdm_printers):
            return cls.FDM

        if any(resin_printer in model for resin_printer in resin_printers):
            return cls.RESIN

        return None
