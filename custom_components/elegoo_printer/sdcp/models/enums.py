"""Elegoo Printer enums."""

from enum import Enum


class ElegooMachineStatus(Enum):
    """
    Represents the different status states of an SDCP machine.

    Attributes:
        IDLE: The machine is idle and not performing any task.
        PRINTING: The machine is currently executing a print task.
        FILE_TRANSFERRING: A file transfer is in progress.
        EXPOSURE_TESTING: The machine is performing an exposure test.
        DEVICES_TESTING: The machine is running a device self-check.
        LEVELING: The machine is performing a leveling operation.

    Example:
        >>> ElegooMachineStatus(0)
        <ElegooMachineStatus.IDLE: 0>
        >>> ElegooMachineStatus.from_int(1)
        <ElegooMachineStatus.PRINTING: 1>

    """

    IDLE = 0
    PRINTING = 1
    FILE_TRANSFERRING = 2
    EXPOSURE_TESTING = 3
    DEVICES_TESTING = 4
    LEVELING = 5
    HOMING = 9
    LOADING_UNLOADING = 10

    @classmethod
    def from_int(cls, status_int: int) -> "ElegooMachineStatus | None":
        """
        Converts an integer to an ElegooMachineStatus enum member.

        Arguments:
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
    def from_list(cls, status_list: list[int]) -> "ElegooMachineStatus | None":
        """
        Convert a list of integers to an ElegooMachineStatus enum member.

        Arguments:
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
        PRINTING: The printer is currently printing.
        LIFTING: The print platform is lifting.
        PAUSING: The printer is in the process of pausing the print job.
        PAUSED: The print job is currently paused.
        STOPPING: The printer is in the process of stopping the print job.
        STOPPED: The print job is stopped.
        COMPLETE: The print job has completed successfully.
        FILE_CHECKING: The printer is currently checking the print file.
        LOADING: The printer is loading filament.
        PREHEATING: The printer is preheating.
        LEVELING: The printer is leveling.

    Example:
        >>> ElegooPrintStatus(0)
        <ElegooPrintStatus.IDLE: 0>
        >>> ElegooPrintStatus.from_int(3)
        <ElegooPrintStatus.PRINTING: 3>

    """

    IDLE = 0
    HOMING = 1
    DROPPING = 2
    PRINTING = 3
    LIFTING = 4
    PAUSING = 5
    PAUSED = 6
    STOPPING = 7
    STOPPED = 8
    COMPLETE = 9
    FILE_CHECKING = 10
    RECOVERY = 12
    PRINTING_RECOVERY = 13
    LOADING = 15
    PREHEATING = 16
    LEVELING = 20

    @classmethod
    def from_int(cls, status_int: int) -> "ElegooPrintStatus | None":
        """
        Converts an integer to an ElegooPrintStatus enum member.

        Arguments:
            status_int: The integer representing the print status.

        Returns:
            The corresponding ElegooPrintStatus enum member, or None if the
            integer is not a valid status value.

        """  # noqa: D401
        if status_int in [18, 19, 21]:
            return cls.LOADING
        if status_int == cls.PRINTING_RECOVERY.value:
            return cls.PRINTING
        try:
            return cls(status_int)
        except ValueError:
            return None


class ElegooPrintError(Enum):
    """
    Represents the different error states that can occur during printing.

    Attributes:
        NONE: No error has occurred. The print process is normal.
        CHECK: File MD5 checksum check failed, indicating potential file corruption.
        FILEIO: An error occurred while reading the print file.
        INVALID_RESOLUTION: The print file's resolution does not match the printer's
            capabilities.
        UNKNOWN_FORMAT: The printer does not recognize the format of the print file.
        UNKNOWN_MODEL: The print file is intended for a different machine model.

    Example:
        >>> ElegooPrintError(0)
        <ElegooPrintError.NONE: 0>
        >>> ElegooPrintError.from_int(1)
        <ElegooPrintError.CHECK: 1>

    """

    NONE = 0
    CHECK = 1
    FILEIO = 2
    INVALID_RESOLUTION = 3
    UNKNOWN_FORMAT = 4
    UNKNOWN_MODEL = 5

    @classmethod
    def from_int(cls, status_int: int) -> "ElegooPrintError | None":
        """
        Convert an integer to the corresponding ElegooPrintError enum member.

        Returns:
            The matching ElegooPrintError member if the integer is valid, or None if it
            does not correspond to any defined error.

        """
        try:
            return cls(status_int)  # Use cls() to create enum members
        except ValueError:
            return None


class ElegooVideoStatus(Enum):
    """
    Represents a video status.

    Attributes:
        0 - Success
        1 - Exceeded maximum streaming limit
        2 - Camera does not exist
        3 - Unknown error

    Example:
        >>> ElegooVideoStatus(0)
        <ElegooVideoStatus.SUCCESS: 0>
        >>> ElegooVideoStatus.from_int(1)
        <ElegooVideoStatus.EXCEEDED_MAX_STREAMING_LIMIT: 1>

    """

    SUCCESS = 0
    EXCEEDED_MAX_STREAMING_LIMIT = 1
    CAMERA_DOES_NOT_EXIST = 2
    UNKNOWN_ERROR = 3

    @classmethod
    def from_int(cls, status_int: int) -> "ElegooVideoStatus | None":
        """
        Convert an integer to the corresponding ElegooVideoStatus enum member.

        Returns:
            ElegooVideoStatus: The matching enum member if the integer is valid,
            otherwise None.

        """
        try:
            return cls(status_int)
        except ValueError:
            return None


class ElegooErrorStatusReason(Enum):
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
        FILAMENT_ABOUT_TO_RUNOUT: The filament is about to runout but hasn't

    Example:
        >>> ElegooErrorStatusReason(0)
        <ElegooErrorStatusReason.OK: 0>
        >>> ElegooErrorStatusReason.from_int(1)
        <ElegooErrorStatusReason.TEMP_ERROR: 1>

    """

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
    FILAMENT_ABOUT_TO_RUNOUT = 45

    @classmethod
    def from_int(cls, status_int: int) -> "ElegooErrorStatusReason | None":
        """
        Convert an integer to the corresponding ElegooErrorStatusReason enum member.

        Returns:
            The matching ElegooErrorStatusReason member if the integer is valid;
            otherwise, None.

        """
        try:
            return cls(status_int)
        except ValueError:
            return None


class ElegooFan(Enum):
    """
    Represents the different fan names in the Elegoo printer API.

    Attributes:
        MODEL_FAN: The fan that cools the model.
        AUXILIARY_FAN: The auxiliary fan.
        BOX_FAN: The fan that cools the enclosure.

    """

    MODEL_FAN = "ModelFan"
    AUXILIARY_FAN = "AuxiliaryFan"
    BOX_FAN = "BoxFan"

    @classmethod
    def from_key(cls, key: str) -> "ElegooFan | None":
        """
        Convert a key to the corresponding ElegooFan enum member.

        Returns:
            ElegooFan: The matching enum member if the key is valid, otherwise None.

        """
        pascal_case_string = key.replace("_", " ").title().replace(" ", "")
        for fan_name in cls:
            if fan_name.value == pascal_case_string:
                return fan_name
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
    def from_model(cls, model: str | None) -> "PrinterType | None":
        """
        Return the printer type (RESIN or FDM) based on the provided model name.

        Arguments:
            model (str): The printer model name to evaluate.

        Returns:
            PrinterType or None: The corresponding printer type if the model matches a
            known FDM or resin printer, otherwise None.

        """
        if model is None:
            return None

        fdm_printers = ["centauri carbon", "centauri"]
        resin_printers = [
            "mars 5",
            "mars 5 ultra",
            "saturn 4",
            "saturn 4 ultra",
            "saturn 4 ultra 16k",
        ]

        if model.lower() in fdm_printers:
            return cls.FDM

        if model.lower() in resin_printers:
            return cls.RESIN

        return None
