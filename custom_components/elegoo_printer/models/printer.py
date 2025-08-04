"""Printer object for the Elegoo Printers."""

import json
from types import MappingProxyType
from typing import Any, Dict, Optional

from ..const import CONF_PROXY_ENABLED, LOGGER
from .video import ElegooVideo

from .attributes import PrinterAttributes
from .enums import PrinterType
from .print_history_detail import PrintHistoryDetail
from .status import PrinterStatus
from .enums import ProtocolType


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
    protocol_type: Optional[ProtocolType]
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
            self.protocol_type = None
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
                protocol_type_str = data_dict.get("protocol_type")
                if protocol_type_str:
                    self.protocol_type = ProtocolType(protocol_type_str)
                elif self.protocol and self.protocol.startswith("V1"):
                    self.protocol_type = ProtocolType.MQTT
                else:
                    self.protocol_type = ProtocolType.SDCP
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
                self.protocol_type = None

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
            "protocol_type": self.protocol_type.value if self.protocol_type else None,
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
        protocol_type_str = data_dict.get("protocol_type")
        if protocol_type_str:
            printer.protocol_type = ProtocolType(protocol_type_str)
        elif printer.protocol and printer.protocol.startswith("V1"):
            printer.protocol_type = ProtocolType.MQTT
        else:
            printer.protocol_type = ProtocolType.SDCP
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

    def update_from_websocket(self, response: str) -> None:
        """Parse and route an incoming JSON response message from the printer.

        Attempts to decode the response as JSON and dispatches it to the appropriate
        handler based on the message topic. Logs unknown topics, missing topics, and
        JSON decoding errors.

        Args:
            response: The JSON response message to parse.
        """
        try:
            data = json.loads(response)
            topic = data.get("Topic")
            if topic:
                topic_parts = topic.split("/")
                if len(topic_parts) > 1:
                    match topic_parts[1]:
                        case "response":
                            self._response_handler(data)
                        case "status":
                            self._status_handler(data)
                        case "attributes":
                            self._attributes_handler(data)
                        case "notice":
                            LOGGER.debug(f"notice >> \n{json.dumps(data, indent=5)}")
                        case "error":
                            LOGGER.error(f"error >> \n{json.dumps(data, indent=5)}")
                        case _:
                            LOGGER.warning("--- UNKNOWN MESSAGE ---")
                            LOGGER.debug(data)
                            LOGGER.warning("--- UNKNOWN MESSAGE ---")
                else:
                    LOGGER.warning(f"Received message with malformed topic: {topic}")
            else:
                LOGGER.warning("Received message without 'Topic'")
                LOGGER.debug(f"Message content: {response}")
        except json.JSONDecodeError as e:
            LOGGER.exception(f"Invalid JSON received: {e}")

    def _response_handler(self, data: dict[str, Any]) -> None:
        """Handles response messages by dispatching to the appropriate handler based on the command type.

        Routes print history and video stream response data to their respective
        handlers according to the command ID in the response.

        Args:
            data: The response data.
        """
        try:
            inner_data = data.get("Data")
            if inner_data:
                data_data = inner_data.get("Data", {})
                cmd: int = inner_data.get("Cmd", 0)
                if cmd == 320:
                    self._print_history_handler(data_data)
                elif cmd == 321:
                    self._print_history_detail_handler(data_data)
                elif cmd == 386:
                    self._print_video_handler(data_data)
        except json.JSONDecodeError:
            pass  # self.logger.exception("Invalid JSON")

    def _status_handler(self, data: dict[str, Any]) -> None:
        """Parses and updates the printer's status information from the provided data.

        Args:
            data: Dictionary containing the printer status information in JSON-compatible format.
        """
        printer_status = PrinterStatus.from_json(json.dumps(data))
        self.status = printer_status

    def _attributes_handler(self, data: dict[str, Any]) -> None:
        """Parses and updates the printer's attribute data from a JSON dictionary.

        Args:
            data: Dictionary containing printer attribute information.
        """
        printer_attributes = PrinterAttributes.from_json(json.dumps(data))
        self.attributes = printer_attributes

    def _print_history_handler(self, data_data: dict[str, Any]) -> None:
        """Parses and updates the printer's print history details from the provided data."""
        history_data_list = data_data.get("HistoryData")
        if history_data_list:
            for task_id in history_data_list:
                if task_id not in self.print_history:
                    self.print_history[task_id] = None

    def _print_history_detail_handler(self, data_data: dict[str, Any]) -> None:
        """Parses and updates the printer's print history details from the provided data.

        If a list of print history details is present in the input, updates the
        printer data with a list of `PrintHistoryDetail` objects.

        Args:
            data_data: The data containing the print history details.
        """
        history_data_list = data_data.get("HistoryDetailList")
        if history_data_list:
            for history_data in history_data_list:
                detail = PrintHistoryDetail(history_data)
                if detail.task_id is not None:
                    self.print_history[detail.task_id] = detail

    def _print_video_handler(self, data_data: dict[str, Any]) -> None:
        """Parse video stream data and update the printer's video attribute.

        Args:
            data_data: Dictionary containing video stream information.
        """
        LOGGER.debug(f"_print_video_handler: Received data_data: {data_data}")
        self.video = ElegooVideo(data_data)
        LOGGER.debug(f"_print_video_handler: Updated video object: {self.video.to_dict()}")
