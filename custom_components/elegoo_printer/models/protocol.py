"""Protocol enum for Elegoo printers."""

from enum import Enum


class ProtocolType(Enum):
    """Enum for the protocol type."""

    SDCP = "sdcp"
    MQTT = "mqtt"
