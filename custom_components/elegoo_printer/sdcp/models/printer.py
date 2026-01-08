"""Elegoo SDCP Printer Model."""

from __future__ import annotations

import json
import re
import socket
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from custom_components.elegoo_printer.const import (
    CONF_CAMERA_ENABLED,
    CONF_EXTERNAL_IP,
    CONF_MQTT_BROKER_ENABLED,
    CONF_PROXY_ENABLED,
    DEFAULT_FALLBACK_IP,
    WEBSOCKET_PORT,
)
from custom_components.elegoo_printer.sdcp.models.enums import ElegooMachineStatus

from .attributes import PrinterAttributes
from .enums import PrinterType, ProtocolVersion, TransportType
from .status import PrinterStatus
from .video import ElegooVideo

if TYPE_CHECKING:
    from .print_history_detail import PrintHistoryDetail
from typing import TypedDict

# Printer models that support vat heating
PRINTERS_WITH_VAT_HEATER = [
    "Saturn 4 Ultra 16K",
    # Add future models here as needed
]


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
        protocol (str): The protocol version string (e.g., "V1.0.0", "V3.0.0").
        transport_type (TransportType): The transport layer (MQTT or WebSocket).
        protocol_version (ProtocolVersion): The SDCP protocol version (V1 or V3).
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
    transport_type: TransportType
    protocol_version: ProtocolVersion
    firmware: str | None
    id: str | None
    printer_type: PrinterType | None
    proxy_enabled: bool
    camera_enabled: bool
    proxy_websocket_port: int | None
    proxy_video_port: int | None
    is_proxy: bool
    mqtt_broker_enabled: bool
    external_ip: str | None
    open_centauri: bool

    def __init__(  # noqa: PLR0915
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
            self.protocol_version = ProtocolVersion.V3
            self.transport_type = TransportType.WEBSOCKET
            self.firmware = None
            self.id = None
            self.printer_type = None
            self.is_proxy = False
            self.open_centauri = False
        else:
            try:
                j: dict[str, Any] = json.loads(json_string)  # Decode the JSON string
                self.connection = j.get("Id")
                data_dict = j.get("Data", j)

                # Support both legacy Saturn (Attributes) and flat format
                attrs = data_dict.get("Attributes", data_dict)

                self.name = attrs.get("Name")
                self.model = attrs.get("MachineName")
                self.brand = attrs.get("BrandName")
                self.ip_address = attrs.get("MainboardIP") or attrs.get("ip_address")
                self.protocol = attrs.get("ProtocolVersion")
                self.protocol_version = ProtocolVersion.from_version_string(
                    self.protocol
                )
                self.transport_type = self.protocol_version.get_transport_type()
                self.firmware = attrs.get("FirmwareVersion")
                self.id = attrs.get("MainboardID")
                self.is_proxy = attrs.get("Proxy", False)

                self.printer_type = PrinterType.from_model(self.model)

                # Check if this is a Centauri printer with Open Centauri firmware
                self.open_centauri = self._is_open_centauri(self.model, self.firmware)

                # Check if this printer has vat heating capability
                self.has_vat_heater = self._has_vat_heater(self.model)
            except json.JSONDecodeError:
                # Handle the error appropriately (e.g., log it, raise an exception)
                self.connection = None
                self.name = ""
                self.model = None
                self.brand = None
                self.ip_address = None
                self.protocol = None
                self.protocol_version = ProtocolVersion.V3
                self.transport_type = TransportType.WEBSOCKET
                self.firmware = None
                self.id = None
                self.printer_type = None
                self.is_proxy = False
                self.open_centauri = False
                self.has_vat_heater = False

        # Initialize config-based attributes for all instances
        self.proxy_enabled = config.get(CONF_PROXY_ENABLED, False)
        self.camera_enabled = config.get(CONF_CAMERA_ENABLED, False)
        self.mqtt_broker_enabled = config.get(CONF_MQTT_BROKER_ENABLED, False)
        self.external_ip = config.get(CONF_EXTERNAL_IP)
        self.proxy_websocket_port = None
        self.proxy_video_port = None

    @staticmethod
    def _is_open_centauri(model: str | None, firmware: str | None) -> bool:
        """
        Check if this is a Centauri printer with Open Centauri firmware.

        Args:
            model: The printer model name
            firmware: The firmware version string

        Returns:
            True if model contains "centauri" and firmware contains "OC" anywhere
            or contains a standalone "O" marker (word boundary), False otherwise.

        Examples:
            - "V0.1.0 O" -> matches (standalone O)
            - "V0.2.0OC" -> matches (contains OC)
            - "V0.1.0 OCEAN" -> does not match (O is not standalone)

        """
        if not model or not firmware:
            return False

        model_lower = model.lower()
        firmware_upper = firmware.upper()

        # Check if it's a Centauri printer and has Open Centauri firmware
        # Match "OC" or "O" as standalone markers (word boundaries or end of string)
        # This matches: "V0.1.0 O", "V0.1.0O", "V0.2.0OC", "V0.2.0 OC"
        # But not: "OCEAN", "OFFICIAL" (OC/O must be standalone or at end)
        has_oc_marker = bool(
            re.search(r"\bOC\b", firmware_upper)
            or re.search(r"\bO\b", firmware_upper)
            or re.search(r"OC$", firmware_upper)
            or re.search(r"O$", firmware_upper)
        )

        return "centauri" in model_lower and has_oc_marker

    @staticmethod
    def _has_vat_heater(model: str | None) -> bool:
        """
        Check if this printer model has vat heating capability.

        Args:
            model: The printer model name

        Returns:
            True if model is in the list of printers with vat heaters, False otherwise.

        """
        if not model:
            return False
        return model in PRINTERS_WITH_VAT_HEATER

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary containing all attributes of the Printer instance."""
        return {
            "connection": self.connection,
            "name": self.name,
            "model": self.model,
            "brand": self.brand,
            "ip_address": self.ip_address,
            "protocol": self.protocol,
            "transport_type": self.transport_type.value,
            "protocol_version": self.protocol_version.value,
            "firmware": self.firmware,
            "id": self.id,
            "printer_type": self.printer_type.value if self.printer_type else None,
            "proxy_enabled": self.proxy_enabled,
            "camera_enabled": self.camera_enabled,
            "proxy_websocket_port": self.proxy_websocket_port,
            "proxy_video_port": self.proxy_video_port,
            "is_proxy": self.is_proxy,
            "mqtt_broker_enabled": self.mqtt_broker_enabled,
            "external_ip": self.external_ip,
            "open_centauri": self.open_centauri,
        }

    def to_dict_safe(self) -> dict[str, Any]:
        """
        Return a dictionary representation safe for logging (excludes passwords).

        Returns:
            dict: Dictionary representation with sensitive fields redacted.

        """
        # No sensitive fields to redact anymore since we removed MQTT auth
        return self.to_dict()

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

        # Support both legacy Saturn (Attributes) and flat format
        attrs = data_dict.get("Attributes", data_dict)

        printer.name = attrs.get("Name", attrs.get("name"))
        printer.model = attrs.get("MachineName", attrs.get("model"))
        printer.brand = attrs.get("BrandName", attrs.get("brand"))
        printer.ip_address = attrs.get("MainboardIP", attrs.get("ip_address"))
        printer.protocol = attrs.get("ProtocolVersion", attrs.get("protocol"))

        # Determine transport and version from stored values or protocol string
        transport_type_str = attrs.get("transport_type")
        protocol_version_str = attrs.get("protocol_version")

        if transport_type_str and protocol_version_str:
            # Use stored values if available (from to_dict)
            printer.transport_type = TransportType(transport_type_str)
            printer.protocol_version = ProtocolVersion(protocol_version_str)
        else:
            # Derive from protocol version string
            printer.protocol_version = ProtocolVersion.from_version_string(
                printer.protocol
            )
            printer.transport_type = printer.protocol_version.get_transport_type()

        printer.firmware = attrs.get("FirmwareVersion", attrs.get("firmware"))
        printer.id = attrs.get("MainboardID", attrs.get("id"))

        printer.printer_type = PrinterType.from_model(printer.model)
        printer.proxy_enabled = attrs.get(
            CONF_PROXY_ENABLED, attrs.get("proxy_enabled", False)
        )
        printer.camera_enabled = attrs.get(
            CONF_CAMERA_ENABLED, attrs.get("camera_enabled", False)
        )
        printer.mqtt_broker_enabled = attrs.get(
            CONF_MQTT_BROKER_ENABLED, attrs.get("mqtt_broker_enabled", False)
        )
        printer.proxy_websocket_port = attrs.get("proxy_websocket_port")
        printer.proxy_video_port = attrs.get("proxy_video_port")
        printer.is_proxy = attrs.get("Proxy", attrs.get("is_proxy", False))
        printer.external_ip = attrs.get(CONF_EXTERNAL_IP, attrs.get("external_ip"))

        # Calculate open_centauri based on model and firmware
        printer.open_centauri = Printer._is_open_centauri(
            printer.model, printer.firmware
        )

        # Check if this printer has vat heating capability
        printer.has_vat_heater = Printer._has_vat_heater(printer.model)

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

    @staticmethod
    def get_local_ip(target_ip: str, external_ip: str | None = None) -> str:
        """
        Determine the local IP address used for outbound communication.

        Args:
            target_ip: The target IP to determine the route to.
            external_ip: Optional external IP override (for Kubernetes/Docker setups).

        Returns:
            The external IP if provided, otherwise the detected local IP address,
            or "127.0.0.1" if detection fails.

        """
        if external_ip:
            return external_ip

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                # Doesn't have to be reachable
                s.connect((target_ip or DEFAULT_FALLBACK_IP, 1))
                return s.getsockname()[0]
        except (socket.gaierror, OSError):
            return "127.0.0.1"

    @property
    def printer_url(self) -> str | None:
        """Get the printer URL based on proxy configuration."""
        if not self.printer or not self.printer.ip_address:
            return None

        if self.printer.proxy_enabled:
            # Use centralized proxy on port 3030 (MainboardID routing handles the rest)
            external_ip = getattr(self.printer, "external_ip", None)
            proxy_ip = PrinterData.get_local_ip(self.printer.ip_address, external_ip)
            return f"http://{proxy_ip}:{WEBSOCKET_PORT}"

        # Use direct printer URL
        return f"http://{self.printer.ip_address}:{WEBSOCKET_PORT}"

    def _get_assigned_proxy_port(self) -> int | None:
        """Get the assigned proxy port for this printer (fallback method)."""
        if not self.printer or not self.printer.ip_address:
            return None

        return WEBSOCKET_PORT
