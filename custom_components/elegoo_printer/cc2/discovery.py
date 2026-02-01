"""
CC2 (Centauri Carbon 2) printer discovery.

CC2 printers use a different discovery protocol than other Elegoo printers:
- Port 52700 instead of 3000
- JSON message format instead of plain text
- Different response structure (result.* instead of Data.Attributes.*)
"""

from __future__ import annotations

import json
import socket
from typing import TYPE_CHECKING, Any

from custom_components.elegoo_printer.const import DEFAULT_BROADCAST_ADDRESS

from .const import (
    CC2_DISCOVERY_MESSAGE,
    CC2_DISCOVERY_PORT,
    CC2_DISCOVERY_TIMEOUT,
    LOGGER,
)

if TYPE_CHECKING:
    from custom_components.elegoo_printer.sdcp.models.printer import Printer


class CC2DiscoveredPrinter:
    """Represents a CC2 printer discovered via UDP broadcast."""

    def __init__(self, data: dict[str, Any], ip_address: str) -> None:
        """
        Initialize a CC2DiscoveredPrinter from discovery response.

        Arguments:
            data: The parsed JSON response from discovery.
            ip_address: The IP address the response came from.

        """
        result = data.get("result", {})
        self.ip_address = ip_address
        self.host_name = result.get("host_name", "")
        self.machine_model = result.get("machine_model", "")
        self.serial_number = result.get("sn", "")
        self.token_status = result.get("token_status", 0)
        self.lan_status = result.get("lan_status", 0)

    @property
    def requires_access_code(self) -> bool:
        """Return True if printer requires access code for authentication."""
        return self.token_status == 1

    @property
    def is_lan_mode(self) -> bool:
        """Return True if printer is in LAN-only mode (not cloud)."""
        return self.lan_status == 1

    def to_printer(self) -> Printer:
        """
        Convert to a Printer object for integration with existing code.

        Returns:
            A Printer object configured for CC2 protocol.

        """
        # Local import to avoid circular dependency
        from custom_components.elegoo_printer.sdcp.models.enums import (  # noqa: PLC0415
            PrinterType,
            ProtocolVersion,
            TransportType,
        )
        from custom_components.elegoo_printer.sdcp.models.printer import (  # noqa: PLC0415
            Printer,
        )

        # Create a Printer object manually (not from JSON)
        printer = Printer()
        printer.name = self.host_name
        printer.model = self.machine_model
        printer.ip_address = self.ip_address
        printer.id = self.serial_number
        printer.connection = self.serial_number  # Use serial number as connection ID
        printer.protocol_version = ProtocolVersion.CC2
        printer.transport_type = TransportType.CC2_MQTT
        printer.printer_type = PrinterType.from_model(self.machine_model)
        printer.brand = "ELEGOO"
        # No embedded broker needed for CC2 - printer runs its own
        printer.mqtt_broker_enabled = False
        # Preserve token status so config flow knows if access code is required
        printer.cc2_token_status = self.token_status

        return printer

    def __repr__(self) -> str:
        """Return string representation of the discovered printer."""
        return (
            f"CC2DiscoveredPrinter("
            f"name={self.host_name!r}, "
            f"model={self.machine_model!r}, "
            f"sn={self.serial_number!r}, "
            f"ip={self.ip_address!r}, "
            f"token_status={self.token_status})"
        )


class CC2Discovery:
    """Discovery service for CC2 printers."""

    @staticmethod
    def discover(
        broadcast_address: str = DEFAULT_BROADCAST_ADDRESS,
    ) -> list[CC2DiscoveredPrinter]:
        """
        Broadcast a UDP discovery message to locate CC2 printers.

        Sends a discovery request and collects responses within a timeout period,
        returning a list of discovered printers.

        Arguments:
            broadcast_address: The network address to send the discovery message to.
                Can be a broadcast address (255.255.255.255) or a specific IP.

        Returns:
            A list of discovered CC2 printers, or an empty list if none are found.

        """
        discovered_printers: list[CC2DiscoveredPrinter] = []
        LOGGER.info("CC2 discovery on port %s...", CC2_DISCOVERY_PORT)

        msg = json.dumps(CC2_DISCOVERY_MESSAGE).encode("utf-8")

        with socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP
        ) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(CC2_DISCOVERY_TIMEOUT)

            try:
                sock.sendto(msg, (broadcast_address, CC2_DISCOVERY_PORT))
                LOGGER.debug(
                    "Sent CC2 discovery message to %s:%s",
                    broadcast_address,
                    CC2_DISCOVERY_PORT,
                )

                while True:
                    try:
                        data, addr = sock.recvfrom(8192)
                        ip_address = addr[0]
                        LOGGER.info("CC2 response from %s", ip_address)

                        try:
                            response = json.loads(data.decode("utf-8"))
                            LOGGER.debug("CC2 discovery response: %s", response)

                            # Validate response has expected structure
                            if "result" in response:
                                printer = CC2DiscoveredPrinter(response, ip_address)
                                discovered_printers.append(printer)
                                LOGGER.debug(
                                    "Discovered CC2 printer: %s (%s)",
                                    printer.host_name,
                                    printer.machine_model,
                                )
                            else:
                                LOGGER.debug(
                                    "Response from %s is not a CC2 discovery response",
                                    ip_address,
                                )
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            LOGGER.debug(
                                "Failed to parse CC2 discovery response from %s",
                                ip_address,
                            )
                    except socket.timeout:
                        break  # Timeout, no more responses

            except OSError as e:
                LOGGER.debug("Socket error during CC2 discovery: %s", e)
                return []

        if not discovered_printers:
            LOGGER.debug("No CC2 printers found during discovery.")
        else:
            LOGGER.debug("Discovered %d CC2 printer(s).", len(discovered_printers))

        return discovered_printers

    @staticmethod
    def discover_as_printers(
        broadcast_address: str = DEFAULT_BROADCAST_ADDRESS,
    ) -> list[Printer]:
        """
        Discover CC2 printers and return them as Printer objects.

        This is a convenience method that converts CC2DiscoveredPrinter
        objects to Printer objects for use with the existing API.

        Arguments:
            broadcast_address: The network address to send the discovery message to.

        Returns:
            A list of Printer objects configured for CC2 protocol.

        """
        cc2_printers = CC2Discovery.discover(broadcast_address)
        return [p.to_printer() for p in cc2_printers]
