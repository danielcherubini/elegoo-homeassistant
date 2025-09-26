"""
Printer Registry for the Elegoo Printer Proxy Server.

This module manages the registration and tracking of multiple printers
in the proxy system, including discovery response parsing.
"""

from __future__ import annotations

import json
from typing import Any

from custom_components.elegoo_printer.const import (
    VIDEO_PORT,
    WEBSOCKET_PORT,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer


class PrinterRegistry:
    """
    Registry for managing multiple printer connections.

    This class maintains a registry of discovered printers and their proxy port
    assignments, enabling the proxy server to route requests to the correct
    printer based on MainboardID or other identifiers.
    """

    def __init__(self) -> None:
        """Initialize an empty printer registry."""
        self._printers: dict[str, Printer] = {}
        self._printer_ports: dict[
            str, tuple[int, int]
        ] = {}  # IP -> (websocket_port, video_port)

    def add_printer(self, printer: Printer) -> tuple[int, int]:
        """
        Add a printer to the registry.

        Args:
            printer: The printer instance to add

        Returns:
            Tuple of (websocket_port, video_port) assigned to this printer

        """
        self._printers[printer.ip_address] = printer

        # For centralized routing, all printers use the same proxy ports
        ws_port = WEBSOCKET_PORT  # Centralized proxy port
        video_port = VIDEO_PORT  # Default video port
        self._printer_ports[printer.ip_address] = (ws_port, video_port)

        return (ws_port, video_port)

    def get_printer_by_ip(self, ip_address: str) -> Printer | None:
        """Get a printer by IP address."""
        return self._printers.get(ip_address)

    def get_all_printers(self) -> dict[str, Printer]:
        """Get all registered printers mapped by IP address."""
        return self._printers.copy()

    def get_printer_by_mainboard_id(self, mainboard_id: str) -> Printer | None:
        """Get a printer by its MainboardID."""
        if not mainboard_id:
            return None

        for printer in self._printers.values():
            if printer.id and printer.id.lower() == mainboard_id.lower():
                return printer
        return None

    def get_all_printers_by_mainboard_id(self) -> dict[str, Printer]:
        """Get all printers mapped by their MainboardID."""
        printers_by_id = {}
        for printer in self._printers.values():
            if printer.id:
                printers_by_id[printer.id.lower()] = printer
        return printers_by_id

    def count(self) -> int:
        """Return the number of registered printers."""
        return len(self._printers)

    def remove_printer(self, ip_address: str) -> bool:
        """
        Remove a printer from the registry.

        Args:
            ip_address: IP address of the printer to remove

        Returns:
            True if printer was removed, False if not found

        """
        removed = ip_address in self._printers
        if removed:
            del self._printers[ip_address]
            if ip_address in self._printer_ports:
                del self._printer_ports[ip_address]
        return removed

    def clear(self) -> None:
        """Clear all registered printers."""
        self._printers.clear()
        self._printer_ports.clear()

    def _parse_discovery_response(self, data: bytes, logger: Any) -> Printer | None:
        """
        Parse a UDP discovery response into a Printer object.

        Args:
            data: Raw UDP response data
            logger: Logger instance for debugging

        Returns:
            Parsed Printer object or None if parsing failed

        """
        try:
            response = json.loads(data.decode())
            printer_data = response.get("Data", {})

            # Extract printer information
            ip = printer_data.get("MainboardIP", "").strip()
            mainboard_id = printer_data.get("MainboardID", "").strip()
            name = printer_data.get("Name", "Unknown Printer").strip()
            brand = printer_data.get("BrandName", "Elegoo").strip()
            firmware = printer_data.get("FirmwareVersion", "Unknown").strip()
            protocol = printer_data.get("ProtocolVersion", "V3.0.0").strip()

            if not ip or not mainboard_id:
                logger.debug("Discovery response missing IP or MainboardID")
                return None

            # Create printer instance
            printer = Printer(
                name=name,
                ip_address=ip,
                firmware_version=firmware,
                id=mainboard_id,
            )
            printer.brand = brand
            printer.protocol = protocol

            logger.debug(
                "Parsed discovery response: %s (%s) at %s",
                name,
                mainboard_id,
                ip,
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug("Failed to parse discovery response: %s", e)
            return None
        else:
            return printer
