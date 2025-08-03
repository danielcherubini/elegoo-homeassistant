"""Custom exceptions for Elegoo MQTT."""


class ElegooMqttError(Exception):
    """Base class for other exceptions"""

    pass


class ElegooMqttConnectionError(ElegooMqttError):
    """Exception raised when connection to printer fails."""

    pass
