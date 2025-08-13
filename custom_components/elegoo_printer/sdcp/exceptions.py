"""Custom exceptions for Elegoo SDCP."""


class ElegooSDCPError(Exception):
    """Base class for other exceptions"""

    pass


class ElegooConfigFlowGeneralError(ElegooSDCPError):
    """Exception raised for general configuration flow errors."""


class ElegooConfigFlowConnectionError(ElegooSDCPError):
    """Exception raised when connection to printer fails during configuration."""


class ElegooPrinterConfigurationError(ElegooSDCPError):
    """Exception raised when printer configuration fails."""


class ElegooPrinterConnectionError(ElegooSDCPError):
    """Exception to indicate a connection error with the Elegoo printer."""


class ElegooPrinterNotConnectedError(ElegooSDCPError):
    """Exception to indicate that the Elegoo printer is not connected."""
