"""
UDP Discovery Protocol for the Elegoo Printer Proxy Server.

This module implements the UDP-based discovery protocol that allows
clients to discover printers through the centralized proxy.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import TYPE_CHECKING, Any

from custom_components.elegoo_printer.const import DISCOVERY_MESSAGE

from .utils import DISCOVERY_RATE_LIMIT_SECONDS

if TYPE_CHECKING:
    from .registry import PrinterRegistry


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """
    UDP Discovery Protocol handler.

    Listens for discovery requests and responds with information about
    available printers accessible through the centralized proxy.
    """

    def __init__(
        self, logger: Any, printer_registry: PrinterRegistry, proxy_ip: str
    ) -> None:
        """Initialize the discovery protocol."""
        self.logger = logger
        self.printer_registry = printer_registry
        self.proxy_ip = proxy_ip
        self.transport: asyncio.DatagramTransport | None = None
        self._last_discovery_time = 0.0

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        """Handle UDP transport ready event."""
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle incoming UDP discovery requests."""
        try:
            message = data.decode().strip()
            if message != DISCOVERY_MESSAGE:
                return

            # Rate limiting to prevent spam
            current_time = time.time()
            if current_time - self._last_discovery_time < DISCOVERY_RATE_LIMIT_SECONDS:
                self.logger.debug(
                    "Rate limiting discovery request from %s (last: %.1fs ago)",
                    addr,
                    current_time - self._last_discovery_time,
                )
                return

            self._last_discovery_time = current_time

            self.logger.debug(
                "Discovery request received from %s, responding for all printers.", addr
            )

            # Get all discovered printers
            printers = self.printer_registry.get_all_printers()

            if not printers:
                # If no printers discovered, send a generic proxy response
                # Using legacy Saturn format for compatibility with tools like Cassini
                response_payload = {
                    "Id": os.urandom(8).hex(),
                    "Data": {
                        "Attributes": {
                            "Name": "Elegoo Proxy Server",
                            "MachineName": "Elegoo Proxy Server",
                            "BrandName": "Elegoo",
                            "MainboardIP": self.proxy_ip,
                            "MainboardID": "proxy",
                            "ProtocolVersion": "V3.0.0",
                            "FirmwareVersion": "V1.0.0",
                            "Proxy": True,
                        },
                        "Status": {
                            "CurrentStatus": 0,
                        },
                    },
                }
                json_string = json.dumps(response_payload)
                if self.transport:
                    self.transport.sendto(json_string.encode(), addr)
                    self.logger.debug(
                        "Sent proxy discovery response (no printers found)"
                    )
            else:
                # Send a response for each discovered printer via centralized proxy
                # Using legacy Saturn format for compatibility with tools like Cassini
                for ip, printer in printers.items():
                    printer_name = getattr(printer, "name", "Elegoo")
                    display_name = printer_name
                    response_payload = {
                        "Id": getattr(printer, "connection", os.urandom(8).hex()),
                        "Data": {
                            "Attributes": {
                                "Name": display_name,
                                "MachineName": display_name,
                                "BrandName": getattr(printer, "brand", "Elegoo"),
                                "MainboardIP": self.proxy_ip,  # Point to our proxy
                                "MainboardID": getattr(printer, "id", None) or ip,
                                "ProtocolVersion": getattr(printer, "protocol", "V3.0.0"),
                                "FirmwareVersion": getattr(printer, "firmware", "V1.0.0"),
                                "Proxy": True,
                            },
                            "Status": {
                                "CurrentStatus": 0,
                            },
                        },
                    }
                    json_string = json.dumps(response_payload)
                    if self.transport:
                        self.transport.sendto(json_string.encode(), addr)
                        self.logger.debug(
                            "Sent discovery response for %s via centralized proxy",
                            ip,
                        )

        except UnicodeDecodeError:
            self.logger.debug("Non-UTF8 discovery packet from %s", addr)
        except Exception:
            self.logger.exception("Error handling discovery request from %s", addr)

    def error_received(self, exc: Exception) -> None:
        """Call when an error is received."""
        msg = f"UDP Discovery Server Error: {exc}"
        self.logger.warning(msg)
