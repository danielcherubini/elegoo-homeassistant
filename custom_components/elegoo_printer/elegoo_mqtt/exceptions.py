"""Custom exceptions for Elegoo MQTT."""


class ElegooMqttError(Exception):
    """Base class for other exceptions"""

    pass


class ElegooPrinterConnectionError(ElegooMqttError):
    """Exception raised when connection to printer fails."""

    pass
