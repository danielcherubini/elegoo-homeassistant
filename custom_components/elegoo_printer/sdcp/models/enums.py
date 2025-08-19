"""Enums for the Elegoo printer integration."""

from enum import Enum


class ElegooFan(Enum):
    """Enum for Elegoo printer fans."""

    MODEL_FAN = "ModelFan"
    AUXILIARY_FAN = "AuxiliaryFan"
    BOX_FAN = "BoxFan"

    @classmethod
    def from_key(cls, key: str) -> "ElegooFan":
        """Get a fan from a key."""
        for fan in cls:
            if fan.name.lower() == key:
                return fan
        raise ValueError(f"Invalid fan key: {key}")


class PrinterType(Enum):
    """Enum for Elegoo printer types."""

    RESIN = "resin"
    FDM = "fdm"

    @classmethod
    def from_model(cls, model: str | None) -> "PrinterType":
        """Get a printer type from a model name."""
        if model in ["Saturn 4 Ultra"]:
            return cls.RESIN
        return cls.FDM


class ElegooMachineStatus(Enum):
    """Enum for Elegoo printer machine statuses."""

    IDLE = 0
    PRINTING = 1
    PAUSED = 2
    COMPLETED = 3
    ERROR = 4
    UNKNOWN = 99

    @classmethod
    def from_list(cls, status_list: list[int]) -> "ElegooMachineStatus":
        """Get a machine status from a list of integers."""
        if not status_list:
            return cls.UNKNOWN
        return cls.from_int(status_list[0])

    @classmethod
    def from_int(cls, status: int) -> "ElegooMachineStatus":
        """Get a machine status from an integer."""
        try:
            return cls(status)
        except ValueError:
            return cls.UNKNOWN


class ElegooPrintStatus(Enum):
    """Enum for Elegoo printer print statuses."""

    IDLE = 0
    PRINTING = 1
    PAUSED = 2
    COMPLETED = 3
    ERROR = 4
    UNKNOWN = 99

    @classmethod
    def from_int(cls, status: int) -> "ElegooPrintStatus":
        """Get a print status from an integer."""
        try:
            return cls(status)
        except ValueError:
            return cls.UNKNOWN


class ElegooPrintError(Enum):
    """Enum for Elegoo printer print errors."""

    NONE = 0
    M01 = 1
    M02 = 2
    M03 = 3
    M04 = 4
    M05 = 5
    M06 = 6
    M07 = 7
    M08 = 8
    M09 = 9
    M10 = 10
    M11 = 11
    M12 = 12
    M13 = 13
    M14 = 14
    M15 = 15
    M16 = 16
    M17 = 17
    M18 = 18
    M19 = 19
    M20 = 20
    M21 = 21
    M22 = 22
    M23 = 23
    M24 = 24
    UNKNOWN = 99

    @classmethod
    def from_int(cls, error: int) -> "ElegooPrintError":
        """Get a print error from an integer."""
        try:
            return cls(error)
        except ValueError:
            return cls.UNKNOWN


class ElegooVideoStatus(Enum):
    """Enum for Elegoo printer video statuses."""

    SUCCESS = 0
    CONNECTION_FAILED = 1
    CAMERA_NOT_FOUND = 2
    UNKNOWN = 99

    @classmethod
    def from_int(cls, status: int) -> "ElegooVideoStatus":
        """Get a video status from an integer."""
        try:
            return cls(status)
        except ValueError:
            return cls.UNKNOWN


class ElegooErrorStatusReason(Enum):
    """Enum for Elegoo printer error status reasons."""

    NONE = 0
    UNKNOWN = 99

    @classmethod
    def from_int(cls, reason: int) -> "ElegooErrorStatusReason":
        """Get an error status reason from an integer."""
        try:
            return cls(reason)
        except ValueError:
            return cls.UNKNOWN