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
        Converts an integer to an ElegooPrintError enum member.

        Args:
            status_int: The integer representing the print status.

        Returns:
            The corresponding ElegooPrintError enum member, or None if the
            integer is not a valid status value.

        """  # noqa: D401
        try:
            return cls(status_int)  # Use cls() to create enum members
        except ValueError:
            return None
