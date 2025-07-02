"""Printer object for the Elegoo Printers."""

import json
from types import MappingProxyType
from typing import Any

from custom_components.elegoo_printer.const import (
    CONF_CENTAURI_CARBON,
    CONF_PROXY_ENABLED,
)
from custom_components.elegoo_printer.elegoo_sdcp.models.video import ElegooVideo

from .attributes import PrinterAttributes
from .print_history_detail import PrintHistoryDetail
from .status import PrinterStatus


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

    >>> printer_json = '''
    ... {
    ...     "Id": "12345",
    ...     "Data": {
    ...         "Name": "My Printer",
    ...         "MachineName": "Model XYZ",
    ...         "BrandName": "Acme",
    ...         "MainboardIP": "192.168.1.100",
    ...         "ProtocolVersion": "2.0",
    ...         "FirmwareVersion": "1.5",
    ...         "MainboardID": "ABCDEF"
    ...     }    ... }
    ... '''
    >>> my_printer = Printer(printer_json)
    >>> print(my_printer.name)
    My Printer

    """

    def __init__(
        self,
        json_string: str | None = None,
        config: MappingProxyType[str, Any] = MappingProxyType({}),
    ) -> None:
        """
        Initialize a new Printer object from a JSON string.

        Args:
            json_string (str, optional): A JSON string containing printer data.
                                         Defaults to None, creating a "nulled" printer.

        """
        if json_string is None:
            self.connection: str | None = None
            self.name: str = ""
            self.model: str | None = None
            self.brand: str | None = None
            self.ip_address: str | None = None
            self.protocol: str | None = None
            self.firmware: str | None = None
            self.id: str | None = None
        else:
            try:
                j: dict = json.loads(json_string)  # Decode the JSON string
            except json.JSONDecodeError:
                # Handle the error appropriately (e.g., log it, raise an exception)
                return

            self.connection = j.get("Id")

            data = j.get("Data", {})
            self.name = data.get("Name")
            self.model = data.get("MachineName")
            self.brand = data.get("BrandName")
            self.ip_address = data.get("MainboardIP")
            self.protocol = data.get("ProtocolVersion")
            self.firmware = data.get("FirmwareVersion")
            self.id = data.get("MainboardID")

            self.protocol = data.get("ProtocolVersion")
            self.firmware = data.get("FirmwareVersion")
            self.id = data.get("MainboardID")

        # Initialize config-based attributes for all instances
        self.centauri_carbon = config.get(CONF_CENTAURI_CARBON, False)
        self.proxy_enabled = config.get(CONF_PROXY_ENABLED, False)

    def to_dict(self) -> dict:
        """
        Return a dictionary representation of the Printer object.

        The dictionary keys directly match the attribute names of the class model.

        Returns:
            dict: A dictionary containing the printer's data.
        """
        return {
            "connection": self.connection,
            "name": self.name,
            "model": self.model,
            "brand": self.brand,
            "ip_address": self.ip_address,
            "protocol": self.protocol,
            "firmware": self.firmware,
            "id": self.id,
            "centauri_carbon": self.centauri_carbon,
            "proxy_enabled": self.proxy_enabled,
        }


class PrinterData:
    """Data object for printer information."""

    def __init__(
        self,
        status: PrinterStatus | None = None,
        attributes: PrinterAttributes | None = None,
        printer: Printer | None = None,
        print_history: dict[str, PrintHistoryDetail | None] | None = None,
    ) -> None:
        """
        Initialize a PrinterData instance with optional printer-related data.

        If any argument is omitted or None, the corresponding attribute is set to a default instance of its class. The `video` attribute is always initialized as a new ElegooVideo instance.
        """
        self.status: PrinterStatus = status or PrinterStatus()
        self.attributes: PrinterAttributes = attributes or PrinterAttributes()
        self.printer: Printer = printer or Printer()
        self.print_history: dict[
            str, PrintHistoryDetail | None
        ] = print_history or {}
        self.video: ElegooVideo = ElegooVideo()
