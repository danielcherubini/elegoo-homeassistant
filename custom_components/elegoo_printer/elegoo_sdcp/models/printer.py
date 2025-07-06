"""Printer object for the Elegoo Printers."""

import json
from types import MappingProxyType
from typing import Any, Dict, Optional

from custom_components.elegoo_printer.const import CONF_PROXY_ENABLED
from custom_components.elegoo_printer.elegoo_sdcp.models.video import ElegooVideo

from .attributes import PrinterAttributes
from .enums import PrinterType
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
        printer_type (PrinterType): The type of printer (RESIN or FDM).

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

    connection: Optional[str]
    name: str
    model: Optional[str]
    brand: Optional[str]
    ip_address: Optional[str]
    protocol: Optional[str]
    firmware: Optional[str]
    id: Optional[str]
    printer_type: Optional[PrinterType]
    proxy_enabled: bool

    def __init__(
        self,
        json_string: Optional[str] = None,
        config: MappingProxyType[str, Any] = MappingProxyType({}),
    ) -> None:
        """
        Initialize a Printer instance from a JSON string and configuration mapping.

        If a valid JSON string is provided, extracts printer attributes from the JSON data. If parsing fails or no data is given, initializes all attributes to default "nulled" values. The printer type is determined from the model name, and the proxy_enabled flag is set based on the configuration.
        """
        if json_string is None:
            self.connection = None
            self.name = ""
            self.model = None
            self.brand = None
            self.ip_address = None
            self.protocol = None
            self.firmware = None
            self.id = None
            self.printer_type = None
        else:
            try:
                j: Dict[str, Any] = json.loads(json_string)  # Decode the JSON string
                self.connection = j.get("Id")
                data_dict = j.get("Data", j)
                self.name = data_dict.get("Name")
                self.model = data_dict.get("MachineName")
                self.brand = data_dict.get("BrandName")
                self.ip_address = data_dict.get("MainboardIP") or data_dict.get(
                    "ip_address"
                )
                self.protocol = data_dict.get("ProtocolVersion")
                self.firmware = data_dict.get("FirmwareVersion")
                self.id = data_dict.get("MainboardID")
                self.printer_type = PrinterType.from_model(self.model)
            except json.JSONDecodeError:
                # Handle the error appropriately (e.g., log it, raise an exception)
                self.connection = None
                self.name = ""
                self.model = None
                self.brand = None
                self.ip_address = None
                self.protocol = None
                self.firmware = None
                self.id = None
                self.printer_type = None

        # Initialize config-based attributes for all instances
        self.proxy_enabled = config.get(CONF_PROXY_ENABLED, False)

    def to_dict(self) -> Dict[str, Any]:
        """
        Return a dictionary containing all attributes of the Printer instance.

        The resulting dictionary includes connection details, identification, model information, printer type (as a string value or None), and proxy status.

        Returns:
            dict: Dictionary with printer attributes and metadata.
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
            "printer_type": self.printer_type.value if self.printer_type else None,
            "proxy_enabled": self.proxy_enabled,
        }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        config: MappingProxyType[str, Any] = MappingProxyType({}),
    ) -> "Printer":
        """
        Create a Printer instance from a dictionary.
        """
        printer = cls(config=config)
        printer.connection = data.get("Id", data.get("connection"))
        data_dict = data.get("Data", data)
        printer.name = data_dict.get("Name", data_dict.get("name"))
        printer.model = data_dict.get("MachineName", data_dict.get("model"))
        printer.brand = data_dict.get("BrandName", data_dict.get("brand"))
        printer.ip_address = data_dict.get("MainboardIP", data_dict.get("ip_address"))
        printer.protocol = data_dict.get("ProtocolVersion", data_dict.get("protocol"))
        printer.firmware = data_dict.get("FirmwareVersion", data_dict.get("firmware"))
        printer.id = data_dict.get("MainboardID", data_dict.get("id"))
        printer.printer_type = PrinterType.from_model(printer.model)
        printer.proxy_enabled = data_dict.get(CONF_PROXY_ENABLED, False)
        return printer


class PrinterData:
    """Data object for printer information."""

    status: PrinterStatus
    attributes: PrinterAttributes
    printer: Printer
    print_history: dict[str, PrintHistoryDetail | None]
    video: ElegooVideo

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
        self.print_history: dict[str, PrintHistoryDetail | None] = print_history or {}
        self.video: ElegooVideo = ElegooVideo()
