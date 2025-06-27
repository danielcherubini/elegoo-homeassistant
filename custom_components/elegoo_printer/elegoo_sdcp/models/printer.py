"""Printer object for the Elegoo Printers."""

import json
from types import MappingProxyType
from typing import Any

from custom_components.elegoo_printer.const import (
    CONF_CENTAURI_CARBON,
    CONF_PROXY_ENABLED,
)

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
        Initialize a Printer instance from a JSON string and configuration mapping.
        
        If a JSON string is provided, parses printer details such as connection ID, name, model, brand, IP address, protocol version, firmware version, and mainboard ID. If no JSON string is given, initializes all attributes to None or empty values. Configuration flags for centauri_carbon and proxy_enabled are set from the provided config mapping.
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
        Return a dictionary containing all attributes of the Printer instance.
        
        Returns:
            dict: Dictionary with keys for connection, name, model, brand, ip_address, protocol, firmware, id, centauri_carbon, and proxy_enabled.
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
        print_history: list[PrintHistoryDetail] | None = None,
    ) -> None:
        """
        Initialize a new PrinterData object with optional data.

        If any of the data arguments (status, attributes, printer)
        are not provided, the corresponding attribute is initialized
        with a blank/default instance of the respective model class.
        """
        self.status: PrinterStatus = status or PrinterStatus()
        self.attributes: PrinterAttributes = attributes or PrinterAttributes()
        self.printer: Printer = printer or Printer()
        self.print_history: list[PrintHistoryDetail] = print_history or []
