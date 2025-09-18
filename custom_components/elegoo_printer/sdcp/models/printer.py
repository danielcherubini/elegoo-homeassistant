"""Elegoo SDCP Printer Model."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from custom_components.elegoo_printer.const import (
    CONF_CAMERA_ENABLED,
    CONF_PROXY_ENABLED,
)
from custom_components.elegoo_printer.sdcp.models.enums import ElegooMachineStatus

from .attributes import PrinterAttributes
from .enums import PrinterType
from .status import PrinterStatus
from .video import ElegooVideo

if TYPE_CHECKING:
    from .print_history_detail import PrintHistoryDetail
from typing import TypedDict


class FirmwareUpdateInfo(TypedDict, total=False):
    """Represent a Firmware Update Object."""

    update_available: bool
    current_version: str | None
    latest_version: str | None
    package_url: str | None
    changelog: str | None


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

    connection: str | None
    name: str
    model: str | None
    brand: str | None
    ip_address: str | None
    protocol: str | None
    firmware: str | None
    id: str | None
    printer_type: PrinterType | None
    proxy_enabled: bool
    camera_enabled: bool

    def __init__(
        self,
        json_string: str | None = None,
        config: MappingProxyType[str, Any] = MappingProxyType({}),
    ) -> None:
        """Initialize a Printer instance from a JSON string and config mapping."""
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
                j: dict[str, Any] = json.loads(json_string)  # Decode the JSON string
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
        self.camera_enabled = config.get(CONF_CAMERA_ENABLED, False)

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary containing all attributes of the Printer instance."""
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
            "camera_enabled": self.camera_enabled,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        config: MappingProxyType[str, Any] = MappingProxyType({}),
    ) -> Printer:
        """Create a Printer instance from a dictionary."""
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
        printer.proxy_enabled = data_dict.get(
            CONF_PROXY_ENABLED, data_dict.get("proxy_enabled", False)
        )
        printer.camera_enabled = data_dict.get(
            CONF_CAMERA_ENABLED, data_dict.get("camera_enabled", False)
        )
        return printer


class PrinterData:
    """
    Data object for printer information.

    Attributes:
        status (PrinterStatus): The status of the printer.
        attributes (PrinterAttributes): The attributes of the printer.
        printer (Printer): The printer object.
        print_history (dict[str, PrintHistoryDetail | None]): The print history of the
            printer.
        current_job (PrintHistoryDetail | None): The current print job of the printer.
        video (ElegooVideo): The video object of the printer.
        firmware_update_info (dict): Firmware update state and metadata
            (update_available, current_version, latest_version, package_url, changelog).

    """

    print_history: dict[str, PrintHistoryDetail | None]
    current_job: PrintHistoryDetail | None
    video: ElegooVideo
    firmware_update_info: FirmwareUpdateInfo

    def __init__(
        self,
        status: PrinterStatus | None = None,
        attributes: PrinterAttributes | None = None,
        printer: Printer | None = None,
        print_history: dict[str, PrintHistoryDetail | None] | None = None,
    ) -> None:
        """Initialize a PrinterData instance with optional printer-related data."""
        self.status: PrinterStatus = status or PrinterStatus()
        self.attributes: PrinterAttributes = attributes or PrinterAttributes()
        self.printer: Printer = printer or Printer()
        self.print_history: dict[str, PrintHistoryDetail | None] = print_history or {}
        self.current_job: PrintHistoryDetail | None = None
        self.video: ElegooVideo = ElegooVideo()
        self.firmware_update_info: FirmwareUpdateInfo = {
            "update_available": False,
            "current_version": None,
            "latest_version": None,
            "package_url": None,
            "changelog": None,
        }

    def round_minute(self, date: datetime | None = None, round_to: int = 1) -> datetime:
        """Round datetime object to minutes."""
        if date is None:
            date = datetime.now(UTC)

        if not isinstance(round_to, int) or round_to <= 0:
            msg = "round_to must be a positive integer"
            raise ValueError(msg)

        date = date.replace(second=0, microsecond=0)
        delta = date.minute % round_to
        return date.replace(minute=date.minute - delta)

    def calculate_current_job_end_time(self) -> None:
        """Calculate the estimated end time of the print job."""
        if (
            self.status.current_status == ElegooMachineStatus.PRINTING
            and self.status.print_info.remaining_ticks is not None
            and self.status.print_info.remaining_ticks > 0
            and self.current_job
        ):
            now = datetime.now(UTC)
            total_seconds_remaining = self.status.print_info.remaining_ticks / 1000
            target_datetime = now + timedelta(seconds=total_seconds_remaining)
            # Round to nearest minute by adding a 30s bias before flooring
            self.current_job.end_time = self.round_minute(
                target_datetime + timedelta(seconds=30), 1
            )
