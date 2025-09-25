"""
Elegoo Printer Proxy Server - Multi-Printer Network Gateway.

This module implements a sophisticated proxy server that acts as a centralized gateway
for multiple Elegoo 3D printers on a network.
It provides transparent protocol translation,
intelligent message routing, and seamless integration with Home Assistant.

ARCHITECTURE OVERVIEW:
====================

The proxy server operates as a singleton service with three main components:

1. PrinterRegistry: Manages discovery and registration of multiple printers
2. ElegooPrinterServer: Core proxy server with multi-protocol support
3. DiscoveryProtocol: UDP-based printer discovery and advertisement

NETWORK FLOW DIAGRAM:
====================

    [Home Assistant]
           |
           v
    [Proxy Server] <-- Single Entry Point
      |    |    |
      v    v    v
   [P1] [P2] [P3] <-- Multiple Printers

SUPPORTED PROTOCOLS:
===================

- WebSocket (Port 3030): SDCP protocol messages with topic-based routing
- HTTP/HTTPS (Port 3030): REST API calls and file uploads
- UDP Discovery (Port 3000): Network printer discovery and advertisement

MESSAGE ROUTING:
===============

WebSocket messages are routed based on SDCP topic structure:
- Topic format: "sdcp/{message_type}/{MainboardID}"
- Primary routing: Extract MainboardID from Topic field
- Fallback routing: Extract MainboardID from message payload
- HTTP requests: Currently routed to first available printer

PROXY FEATURES:
==============

- Automatic printer discovery with rate limiting
- Connection pooling and management
- URL rewriting for video streams
- Error handling and fallback mechanisms
- Singleton architecture preventing port conflicts
- Thread-safe printer registry with async locks
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import time
from math import floor
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import aiohttp
from aiohttp import ClientResponse, ClientSession, WSMsgType, web
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.elegoo_printer.const import (
    CONF_PROXY_VIDEO_PORT,
    CONF_PROXY_WEBSOCKET_PORT,
    DEFAULT_BROADCAST_ADDRESS,
    DEFAULT_FALLBACK_IP,
    DISCOVERY_MESSAGE,
    DISCOVERY_PORT,
    DOMAIN,
    LOGGER,
    PROXY_HOST,
    VIDEO_PORT,
    WEBSOCKET_PORT,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from multidict import CIMultiDictProxy

INADDR_ANY = "0.0.0.0"  # noqa: S104
DISCOVERY_TIMEOUT = 5
DISCOVERY_RATE_LIMIT_SECONDS = 30
MIN_MAINBOARD_ID_LENGTH = 8
TOPIC_PARTS_COUNT = 3  # Expected parts in SDCP topic: sdcp/{type}/{MainboardID}
MIN_PATH_PARTS_FOR_FALLBACK = 2  # Minimum path parts needed for MainboardID fallback
MIN_API_PATH_PARTS = 3  # Minimum parts for /api/{MainboardID}/... pattern
MIN_VIDEO_PATH_PARTS = 2  # Minimum parts for /video/{MainboardID} pattern
MAX_LOG_LENGTH = 50  # Maximum length for log message truncation

ALLOWED_REQUEST_HEADERS = {
    "GET": [
        "accept",
        "accept-language",
        "accept-encoding",
        "priority",
        "user-agent",
        "range",
        "if-none-match",
        "if-modified-since",
    ],
    "HEAD": [
        "accept",
        "accept-language",
        "accept-encoding",
        "priority",
        "user-agent",
        "range",
        "if-none-match",
        "if-modified-since",
    ],
    "OPTIONS": [
        "origin",
        "access-control-request-method",
        "access-control-request-headers",
    ],
    "POST": [
        "user-agent",
        "accept",
        "accept-language",
        "accept-encoding",
        "content-length",
        "content-type",
        "origin",
    ],
    "WS": [
        "connection",
        "upgrade",
        "origin",
        "sec-websocket-extensions",
        "sec-websocket-key",
        "sec-websocket-protocol",
        "sec-websocket-version",
    ],
}

ALLOWED_RESPONSE_HEADERS = {
    "GET": [
        "content-length",
        "content-type",
        "content-encoding",
        "etag",
        "cache-control",
        "last-modified",
        "accept-ranges",
    ],
    "HEAD": [
        "content-length",
        "content-type",
        "content-encoding",
        "etag",
        "cache-control",
        "last-modified",
        "accept-ranges",
    ],
    "OPTIONS": [
        "access-control-allow-origin",
        "access-control-allow-methods",
        "access-control-allow-headers",
        "access-control-max-age",
        "content-length",
    ],
    "POST": ["content-length", "content-type", "content-encoding"],
}

TRANSFORMABLE_MIME_TYPES = [
    "text/plain",
    "text/css",
    "text/html",
    "text/javascript",
    "application/json",
]

CACHEABLE_MIME_TYPES = [
    "text/plain",
    "text/css",
    "text/html",
    "text/javascript",
    "application/json",
    "image/apng",
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/svg+xml",
    "image/webp",
]


def extract_mainboard_id_from_topic(topic: str) -> str | None:
    """
    Extract MainboardID from SDCP topic string.

    Topics follow the pattern: "sdcp/{message_type}/{MainboardID}"
    Examples:
    - "sdcp/request/ABC123DEF456" -> "ABC123DEF456"
    - "sdcp/status/abc123def456" -> "abc123def456"
    - "sdcp/attributes/12345678" -> "12345678"

    Args:
        topic: The SDCP topic string

    Returns:
        MainboardID if found and valid, None otherwise

    """
    if not topic or not isinstance(topic, str):
        return None

    parts = topic.strip().split("/")
    if len(parts) != TOPIC_PARTS_COUNT or parts[0] != "sdcp":
        return None

    mainboard_id = parts[2]

    # Validate MainboardID (hex characters, minimum length)
    if len(mainboard_id) >= MIN_MAINBOARD_ID_LENGTH and all(
        c in "0123456789abcdefABCDEF" for c in mainboard_id
    ):
        return mainboard_id

    return None


def extract_mainboard_id_from_message(message_data: str) -> str | None:
    """
    Extract MainboardID from SDCP JSON message.

    First tries to extract from Topic field, then falls back to MainboardID field.

    Args:
        message_data: JSON message string

    Returns:
        MainboardID if found, None otherwise

    """
    try:
        message = json.loads(message_data)
        if not isinstance(message, dict):
            return None

        # First priority: Extract from Topic field
        topic = message.get("Topic")
        if topic:
            mainboard_id = extract_mainboard_id_from_topic(topic)
            if mainboard_id:
                return mainboard_id

        # Second priority: Direct MainboardID field
        mainboard_id = message.get("MainboardID")
        if (
            mainboard_id
            and isinstance(mainboard_id, str)
            and len(mainboard_id) >= MIN_MAINBOARD_ID_LENGTH
            and all(c in "0123456789abcdefABCDEF" for c in mainboard_id)
        ):
            return mainboard_id

        # Check nested Data.MainboardID (some messages have this structure)
        data = message.get("Data")
        if isinstance(data, dict):
            mainboard_id = data.get("MainboardID")
            if (
                mainboard_id
                and isinstance(mainboard_id, str)
                and len(mainboard_id) >= MIN_MAINBOARD_ID_LENGTH
                and all(c in "0123456789abcdefABCDEF" for c in mainboard_id)
            ):
                return mainboard_id

    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        # Invalid JSON or wrong structure
        pass

    return None


def extract_mainboard_id_from_header(header: str) -> str | None:
    """
    Extract MainboardID from a HTTP Header (like Referer).

    Args:
        header: Header string (e.g., Referer URL)

    Returns:
        MainboardID if found, None otherwise

    """
    if not header:
        return None

    try:
        parsed_header = urlparse(header)
        query_params = parse_qs(parsed_header.query)

        # Try "id" parameter first, then "mainboard_id" as fallback
        if query_params.get("id"):
            mainboard_id = query_params["id"][0]
            if mainboard_id and len(mainboard_id) >= MIN_MAINBOARD_ID_LENGTH:
                return mainboard_id

        if query_params.get("mainboard_id"):
            mainboard_id = query_params["mainboard_id"][0]
            if mainboard_id and len(mainboard_id) >= MIN_MAINBOARD_ID_LENGTH:
                return mainboard_id

    except (ValueError, TypeError, IndexError):
        # Invalid URL or parsing error
        pass

    return None


class PrinterRegistry:
    """
    Registry for managing multiple discovered printers with topic-based routing.

    This class provides thread-safe management of network-discovered printers
    with automatic discovery, caching, rate limiting, and MainboardID-based lookup.

    TOPIC-BASED ROUTING:
    ===================

    Printers are identified by their MainboardID and accessed via topic-based routing:
    - All printers accessible through single proxy port (3030)
    - WebSocket messages routed by SDCP topic: "sdcp/{type}/{MainboardID}"
    - Maintains backward compatibility with port-based configuration

    DISCOVERY PROCESS:
    =================

    1. UDP Broadcast: Send "M99999" to 255.255.255.255:3000
    2. Response Collection: Gather JSON responses for 5 seconds
    3. Printer Parsing: Extract printer info and create Printer objects
    4. Registry Update: Store printers by IP address and MainboardID

    RATE LIMITING:
    =============

    Discovery is limited to once every 30 seconds to prevent network flooding
    and reduce resource usage. Cached results are returned for subsequent calls
    within the rate limit window.

    THREAD SAFETY:
    =============

    All operations are protected by an async lock to ensure safe concurrent
    access from multiple Home Assistant integration instances.
    """

    def __init__(self) -> None:
        """Initialize the printer registry."""
        self._printers: dict[str, Printer] = {}  # IP -> Printer mapping
        self._printer_ports: dict[
            str, tuple[int, int]
        ] = {}  # IP -> (ws_port, video_port)
        self._next_index: int = 0
        self._last_discovery: float = 0
        self._discovery_lock = asyncio.Lock()

    def add_printer(self, printer: Printer) -> tuple[int, int]:
        """
        Add a printer to the registry by IP address and assign ports.

        Returns:
            Tuple of (websocket_port, video_port) assigned to this printer.

        """
        if not printer.ip_address:
            msg = "Printer must have an IP address"
            raise ValueError(msg)

        # If printer already exists, return existing ports
        if printer.ip_address in self._printers:
            return self._printer_ports[printer.ip_address]

        # Store printer in registry (ports no longer needed for MainboardID routing)
        self._printers[printer.ip_address] = printer

        # Keep port tracking for legacy compatibility, but use defaults
        ws_port = WEBSOCKET_PORT  # Centralized proxy port
        video_port = VIDEO_PORT  # Default video port
        self._printer_ports[printer.ip_address] = (ws_port, video_port)

        return (ws_port, video_port)

    @staticmethod
    async def ensure_printer_ports_assigned(
        printer: Printer, hass: HomeAssistant, logger: Any
    ) -> None:
        """
        Ensure printer has assigned ports, updating config entry if needed.

        This method checks if a printer with proxy enabled is missing port assignments
        and updates the Home Assistant configuration entry with newly assigned ports.
        """
        if printer.proxy_enabled and (
            not printer.proxy_websocket_port or not printer.proxy_video_port
        ):
            logger.debug(
                "Printer %s proxy enabled but ports missing. "
                "Assigning and updating config.",
                printer.ip_address,
            )

            # Get next available ports
            ws_port, video_port = ElegooPrinterServer.get_next_available_ports()
            printer.proxy_websocket_port = ws_port
            printer.proxy_video_port = video_port

            # Find and update the config entry
            try:
                # Find config entry for this printer by mainboard ID
                for entry in hass.config_entries.async_entries(DOMAIN):
                    if entry.data.get("id") == printer.id:
                        new_data = dict(entry.data)
                        new_data[CONF_PROXY_WEBSOCKET_PORT] = ws_port
                        new_data[CONF_PROXY_VIDEO_PORT] = video_port

                        hass.config_entries.async_update_entry(entry, data=new_data)
                        logger.debug(
                            "Updated config entry for printer %s with ports "
                            "WS:%d Video:%d",
                            printer.id,
                            ws_port,
                            video_port,
                        )
                        break
                else:
                    logger.warning(
                        "Could not find config entry for printer %s to update ports",
                        printer.id,
                    )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to update config entry for printer %s",
                    printer.id,
                )

    def get_printer_by_ip(self, ip_address: str) -> Printer | None:
        """Get a printer by IP address."""
        return self._printers.get(ip_address)

    def get_printer_by_port(self, port: int) -> Printer | None:
        """Get a printer by its assigned websocket or video port."""
        for ip, (ws_port, video_port) in self._printer_ports.items():
            if port in (ws_port, video_port):
                return self._printers.get(ip)
        return None

    def get_printer_ports(self, ip_address: str) -> tuple[int, int] | None:
        """Get the assigned ports for a printer by IP address."""
        return self._printer_ports.get(ip_address)

    def get_all_printers(self) -> dict[str, Printer]:
        """Get all registered printers mapped by IP address."""
        return self._printers.copy()

    def get_all_printer_ports(self) -> dict[str, tuple[int, int]]:
        """Get all printer port assignments mapped by IP address."""
        return self._printer_ports.copy()

    def get_printer_by_mainboard_id(self, mainboard_id: str) -> Printer | None:
        """Get a printer by its MainboardID."""
        if not mainboard_id:
            return None

        for printer in self._printers.values():
            if printer.id and printer.id.lower() == mainboard_id.lower():
                return printer
        return None

    def get_all_printers_by_mainboard_id(self) -> dict[str, Printer]:
        """Get all registered printers mapped by MainboardID."""
        result = {}
        for printer in self._printers.values():
            if printer.id:
                result[printer.id] = printer
        return result

    def count(self) -> int:
        """Get the number of registered printers."""
        return len(self._printers)

    def remove_printer(self, ip_address: str) -> bool:
        """
        Remove a printer from the registry by IP address.

        Returns:
            True if printer was removed, False if not found.

        """
        if ip_address in self._printers:
            del self._printers[ip_address]
            del self._printer_ports[ip_address]
            return True
        return False

    def clear(self) -> None:
        """Clear all registered printers."""
        self._printers.clear()
        self._printer_ports.clear()
        self._next_index = 0

    async def discover_printers(
        self,
        logger: Any,
        broadcast_address: str = DEFAULT_BROADCAST_ADDRESS,
    ) -> dict[str, Printer]:
        """Discover all printers on the network via UDP broadcast."""
        async with self._discovery_lock:
            # Rate limit discovery to once every 30 seconds
            current_time = time.monotonic()
            if current_time - self._last_discovery < DISCOVERY_RATE_LIMIT_SECONDS:
                logger.debug("Discovery rate limited, returning cached printers")
                return self.get_all_printers()

            self._last_discovery = current_time
            self.clear()

            logger.info("Broadcasting for printer discovery...")
            discovered_printers: dict[str, Printer] = {}

            try:
                with socket.socket(
                    socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP
                ) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.settimeout(DISCOVERY_TIMEOUT)

                    # Send discovery message
                    msg = DISCOVERY_MESSAGE.encode()
                    sock.sendto(msg, (broadcast_address, DISCOVERY_PORT))

                    # Collect responses
                    start_time = time.monotonic()
                    while time.monotonic() - start_time < DISCOVERY_TIMEOUT:
                        try:
                            data, addr = sock.recvfrom(8192)
                            printer = self._parse_discovery_response(data, logger)
                            if printer and printer.ip_address:
                                discovered_printers[printer.ip_address] = printer
                                ws_port, video_port = self.add_printer(printer)
                                logger.info(
                                    "Discovered %s (%s) assigned WS:%d Video:%d",
                                    printer.name,
                                    printer.ip_address,
                                    ws_port,
                                    video_port,
                                )
                        except TimeoutError:
                            # Socket timeout is expected, continue polling
                            continue
                        except OSError as e:
                            # Handle socket-level errors (connection reset, etc.)
                            logger.debug("Socket error during discovery receive: %s", e)
                            continue
                        except (
                            UnicodeDecodeError,
                            json.JSONDecodeError,
                            ValueError,
                        ) as e:
                            # Handle data parsing errors
                            logger.debug(
                                "Error parsing discovery response from %s: %s", addr, e
                            )
                            continue
                        except Exception:  # noqa: BLE001
                            # Catch any other unexpected errors
                            logger.debug("Unexpected error during discovery")
                            continue

            except OSError:
                logger.exception("Socket error during discovery")

            logger.info(
                "Discovery complete. Found %d printer(s)", len(discovered_printers)
            )
            return discovered_printers

    def _parse_discovery_response(self, data: bytes, logger: Any) -> Printer | None:
        """Parse discovery response bytes and create a Printer object if valid."""
        try:
            response = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug("Error parsing discovery response: %s", e)
            return None
        else:
            # Construct Printer from the parsed dict to ensure proper field mapping
            return Printer.from_dict(response)


class ElegooPrinterServer:
    """
    Centralized proxy server for multiple Elegoo printers with topic-based routing.

    This server provides a single entry point for multiple printers, routing
    WebSocket messages based on SDCP topic structure and MainboardID extraction.
    Runs on the main Home Assistant event loop with singleton architecture.

    ARCHITECTURE:
    - Single WebSocket/HTTP server on port 3030
    - Topic-based message routing for WebSocket connections
    - MainboardID extraction from SDCP message topics
    - UDP discovery server for network printer detection
    """

    _instance: ElegooPrinterServer | None = None
    _creation_lock = asyncio.Lock()
    _reference_count: int = 0  # Track how many integrations are using the proxy

    def __init__(
        self,
        logger: Any,
        hass: HomeAssistant,
        session: ClientSession,
    ) -> None:
        """Initialize the Elegoo printer proxy server for multiple printers."""
        self.logger = logger
        self.hass = hass
        self.session = session
        self.runners: list[web.AppRunner] = []
        self._is_connected = False
        self.datagram_transport: asyncio.DatagramTransport | None = None
        self.printer_registry = PrinterRegistry()

    @classmethod
    def get_next_available_ports(cls) -> tuple[int, int]:
        """
        Get the next available port pair for a new printer.

        This is used during config flow to assign ports before the server starts.

        Returns:
            Tuple of (websocket_port, video_port) for the new printer.

        """
        if cls._instance and cls._instance.printer_registry:
            # Get current max index from existing ports
            max_index = -1
            for (
                ws_port,
                _,
            ) in cls._instance.printer_registry.get_all_printer_ports().values():
                index = (ws_port - WEBSOCKET_PORT) // 2
                max_index = max(max_index, index)
            next_index = max_index + 1
        else:
            # No server running, start from 0
            next_index = 0

        return (WEBSOCKET_PORT + (next_index * 2), VIDEO_PORT + (next_index * 2))

    @classmethod
    async def async_create(
        cls,
        logger: Any,
        hass: HomeAssistant,
        session: ClientSession,
        printer: Printer | None = None,
    ) -> ElegooPrinterServer:
        """Asynchronously creates and starts the multi-printer server (singleton)."""
        async with cls._creation_lock:
            # Return existing instance if already created (check again inside the lock)
            if cls._instance is not None:
                cls._reference_count += 1
                logger.debug(
                    "Reusing existing proxy server instance (reference count: %d)",
                    cls._reference_count,
                )
                if printer:
                    # Add printer to existing server's registry
                    cls._instance.printer_registry.add_printer(printer)
                    logger.debug(
                        "Added printer %s (%s) with MainboardID %s to existing server",
                        printer.name,
                        printer.ip_address,
                        printer.id,
                    )
                return cls._instance

            # Create new instance
            logger.debug("Creating new proxy server instance")
            self = cls(logger, hass, session)
            if printer:
                # Add the initial printer to the registry
                self.printer_registry.add_printer(printer)
                logger.debug(
                    "Added printer %s (%s) with MainboardID %s to new server",
                    printer.name,
                    printer.ip_address,
                    printer.id,
                )

            # Set instance BEFORE starting to prevent race condition
            cls._instance = self
            cls._reference_count = 1  # Initialize reference count for first integration
            logger.debug(
                "Created new proxy server instance (reference count: %d)",
                cls._reference_count,
            )

            try:
                # Start the centralized proxy server
                await self.start()

                # Perform network discovery for printers if none configured
                if self.printer_registry.count() == 0:
                    logger.debug("No configured printers, performing network discovery")
                    await self.printer_registry.discover_printers(logger)
                else:
                    logger.debug(
                        "Using %d printer(s) from Home Assistant config",
                        self.printer_registry.count(),
                    )
            except Exception:
                # Clear instance if startup fails
                cls._instance = None
                cls._reference_count = 0
                raise

            return self

    @property
    def is_connected(self) -> bool:
        """Return true if the proxy is connected to the printer."""
        return self._is_connected

    async def start(self) -> None:
        """Start the centralized proxy server with topic-based routing."""
        self.logger.info(
            "Initializing multi-printer proxy server with topic-based routing"
        )

        try:
            # Start centralized HTTP/WebSocket server
            main_app = web.Application(client_max_size=1024 * 1024 * 1024)  # 1 GiB
            main_app.router.add_route("*", "/{path:.*}", self._centralized_http_handler)
            main_runner = web.AppRunner(main_app)
            await main_runner.setup()
            main_site = web.TCPSite(main_runner, INADDR_ANY, WEBSOCKET_PORT)
            await main_site.start()
            self.runners.append(main_runner)

            msg = f"Centralized HTTP/WebSocket Proxy running on http://{self.get_local_ip()}:{WEBSOCKET_PORT}"
            self.logger.info(msg)

            # Start dedicated video server on port 3031
            video_app = web.Application(client_max_size=1024 * 1024 * 1024)  # 1 GiB
            video_app.router.add_route(
                "*", "/{path:.*}", self._centralized_http_handler
            )
            video_runner = web.AppRunner(video_app)
            await video_runner.setup()
            video_site = web.TCPSite(video_runner, INADDR_ANY, VIDEO_PORT)
            await video_site.start()
            self.runners.append(video_runner)

            msg = f"Centralized Video Proxy running on http://{self.get_local_ip()}:{VIDEO_PORT}"
            self.logger.info(msg)

            # Start UDP discovery server
            def discovery_factory() -> DiscoveryProtocol:
                return DiscoveryProtocol(
                    self.logger, self.printer_registry, self.get_local_ip()
                )

            transport, _ = await self.hass.loop.create_datagram_endpoint(
                discovery_factory, local_addr=(INADDR_ANY, DISCOVERY_PORT)
            )
            self.datagram_transport = transport
            msg = f"Discovery Proxy listening on UDP port {DISCOVERY_PORT}"
            self.logger.info(msg)

        except OSError as e:
            msg = f"Failed to start proxy server: {e}"
            self.logger.exception(msg)
            await self.stop()
            raise ConfigEntryNotReady(msg) from e

        self._is_connected = True
        self.logger.info("Centralized proxy server started successfully.")

    async def _start_servers_for_all_printers(self) -> None:
        """Start HTTP/WebSocket servers for all registered printers."""
        for ip, printer in self.printer_registry.get_all_printers().items():
            ports = self.printer_registry.get_printer_ports(ip)
            if ports:
                ws_port, video_port = ports
                await self.start_printer_servers(printer, ws_port, video_port)

    async def start_printer_servers(
        self, printer: Printer, ws_port: int, video_port: int
    ) -> None:
        """Start HTTP/WebSocket and video servers for a specific printer."""
        try:
            # Check if ports are available
            if not self._check_printer_ports_available(ws_port, video_port):
                self.logger.warning(
                    "Ports %d/%d already in use for printer %s, skipping server start",
                    ws_port,
                    video_port,
                    printer.ip_address,
                )
                return

            # Start WebSocket/HTTP server for this printer
            ws_app = web.Application(client_max_size=1024 * 1024 * 1024)  # 1 GiB
            ws_app.router.add_route(
                "*", "/{path:.*}", lambda req: self._printer_http_handler(req, printer)
            )
            ws_runner = web.AppRunner(ws_app)
            await ws_runner.setup()
            ws_site = web.TCPSite(ws_runner, INADDR_ANY, ws_port)
            await ws_site.start()
            self.runners.append(ws_runner)

            # Start Video server for this printer
            video_app = web.Application()
            video_app.router.add_route(
                "*", "/{path:.*}", lambda req: self._printer_video_handler(req, printer)
            )
            video_runner = web.AppRunner(video_app)
            await video_runner.setup()
            video_site = web.TCPSite(video_runner, INADDR_ANY, video_port)
            await video_site.start()
            self.runners.append(video_runner)

            self.logger.info(
                "Started servers for printer %s (%s) on ports WS:%d Video:%d",
                printer.name,
                printer.ip_address,
                ws_port,
                video_port,
            )

        except OSError:
            self.logger.exception(
                "Failed to start servers for printer %s on ports WS:%d Video:%d",
                printer.ip_address,
                ws_port,
                video_port,
            )

    @classmethod
    async def release_reference(cls) -> None:
        """
        Release a reference to the proxy server.

        Only stops when all references are released.

        This should be called when an integration is being unloaded.
        """
        async with cls._creation_lock:
            if cls._instance is not None:
                cls._reference_count = max(0, cls._reference_count - 1)
                LOGGER.debug(
                    "Released proxy server reference (remaining references: %d)",
                    cls._reference_count,
                )

                if cls._reference_count <= 0:
                    LOGGER.debug(
                        "No more references, stopping centralized proxy server instance"
                    )
                    await cls._instance.stop()
                    cls._instance = None
                    cls._reference_count = 0

                    # Give time for ports to actually be released by the OS
                    await asyncio.sleep(0.5)

                    # Force cleanup any lingering connections
                    await cls._force_cleanup_ports(LOGGER)
                    LOGGER.debug("Proxy server completely stopped")
                else:
                    LOGGER.debug(
                        "Proxy server still has %d active reference(s), keeping alive",
                        cls._reference_count,
                    )

    @classmethod
    async def stop_all(cls) -> None:
        """
        Force stop the proxy server singleton instance (emergency cleanup).

        WARNING: This bypasses reference counting and should only be used for
        emergency cleanup.
        Normal shutdown should use release_reference() instead.
        """
        async with cls._creation_lock:
            if cls._instance is not None:
                LOGGER.warning("Force stopping centralized proxy server instance")
                await cls._instance.stop()
                cls._instance = None
                cls._reference_count = 0

                # Give time for ports to actually be released by the OS
                await asyncio.sleep(0.5)

                # Force cleanup any lingering connections
                await cls._force_cleanup_ports(LOGGER)
                LOGGER.debug("Force stop completed")

    @classmethod
    async def _force_cleanup_ports(cls, logger: Any) -> None:
        """Force cleanup of any lingering socket connections on our ports."""
        ports_to_cleanup = [
            (WEBSOCKET_PORT, socket.SOCK_STREAM),
            (VIDEO_PORT, socket.SOCK_STREAM),
            (DISCOVERY_PORT, socket.SOCK_DGRAM),
        ]

        for port, proto in ports_to_cleanup:
            try:
                # Create and immediately close a socket to force cleanup
                with socket.socket(socket.AF_INET, proto) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    if proto == socket.SOCK_STREAM and hasattr(socket, "SO_REUSEPORT"):
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                    try:
                        s.bind(("localhost", port))  # Bind to localhost only
                        logger.debug("Port %s is now available", port)
                    except OSError:
                        logger.debug("Port %s still in use after cleanup", port)
            except OSError as e:
                logger.debug("Error during port %s cleanup: %s", port, e)

    def _check_printer_ports_available(self, ws_port: int, video_port: int) -> bool:
        """Check if the specified ports are available for a printer."""
        for port, name in [(ws_port, "WebSocket"), (video_port, "Video")]:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind((INADDR_ANY, port))
            except OSError:
                self.logger.debug("%s port %d is already in use", name, port)
                return False
        return True

    @classmethod
    async def remove_printer_from_server(cls, printer: Printer, logger: Any) -> bool:
        """
        Remove a printer from the server registry.

        Returns:
            True if server should be stopped (no more printers), False if server
            should continue.

        """
        if cls._instance is None:
            return False  # No server to remove from

        if printer.ip_address:
            removed = cls._instance.printer_registry.remove_printer(printer.ip_address)
            if removed:
                logger.debug(
                    "Removed printer %s (%s) from proxy server",
                    printer.name,
                    printer.ip_address,
                )

            # Check if any printers remain
            if cls._instance.printer_registry.count() == 0:
                logger.info("No printers remain in proxy server, stopping server")
                await cls._instance.stop()
                return True
            logger.debug(
                "Proxy server continues with %d printers remaining",
                cls._instance.printer_registry.count(),
            )
            return False

        return False

    async def stop(self) -> None:
        """Stop the proxy server and cleans up all associated resources."""
        self.logger.info("Stopping proxy server...")
        self._is_connected = False

        # Close UDP transport first
        if self.datagram_transport:
            try:
                self.datagram_transport.close()
            except OSError as e:
                self.logger.warning("Error closing datagram transport: %s", e)
            finally:
                self.datagram_transport = None

        # Clean up web runners
        for runner in self.runners:
            try:
                await runner.cleanup()
            except (RuntimeError, OSError) as e:
                self.logger.warning("Error cleaning up runner: %s", e)
        self.runners.clear()

        # Clear singleton instance
        if self.__class__._instance is self:  # noqa: SLF001
            self.__class__._instance = None  # noqa: SLF001

        # Small delay to ensure ports are fully released
        await asyncio.sleep(0.1)

        self.logger.info("Proxy server stopped.")

    def get_printer(self, specific_printer: Printer | None = None) -> Printer:
        """Return a proxy printer object that points clients to the proxy server."""
        # For topic-based routing, return a proxy printer that directs clients
        # to connect to the centralized proxy server on the local IP
        all_printers = self.printer_registry.get_all_printers()
        if not all_printers:
            msg = "No printers in registry"
            raise RuntimeError(msg)

        # Use the specific printer if provided, otherwise default to first printer
        if specific_printer and specific_printer.id:
            # Find the printer with matching MainboardID in the registry
            target_printer = self.printer_registry.get_printer_by_mainboard_id(
                specific_printer.id
            )
            if target_printer:
                self.logger.debug(
                    "get_printer: Using specific printer %s with MainboardID %s",
                    target_printer.name,
                    target_printer.id,
                )
            else:
                self.logger.warning(
                    "get_printer: Printer with MainboardID %s not found in registry",
                    specific_printer.id,
                )
                target_printer = next(iter(all_printers.values()))
        else:
            target_printer = next(iter(all_printers.values()))
            self.logger.debug(
                "get_printer: No specific printer, using 1st printer %s with id %s",
                target_printer.name,
                target_printer.id,
            )

        # Create a proxy printer that clients connect to,
        # preserving the specific printer's MainboardID
        proxy_printer_dict = target_printer.to_dict()
        proxy_printer_dict["ip_address"] = self.get_local_ip()
        proxy_printer_dict["name"] = f"{target_printer.name} Proxy"
        return Printer.from_dict(proxy_printer_dict)

    def _check_ports_are_available(self) -> bool:
        """Check if the required TCP and UDP ports for the proxy server are free."""
        for port, proto, name in [
            (WEBSOCKET_PORT, socket.SOCK_STREAM, "WebSocket/HTTP TCP"),
            (VIDEO_PORT, socket.SOCK_STREAM, "Video TCP"),
            (DISCOVERY_PORT, socket.SOCK_DGRAM, "UDP"),
        ]:
            try:
                with socket.socket(socket.AF_INET, proto) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind((INADDR_ANY, port))
            except OSError:
                self.logger.exception(
                    "%s port %s is already in use. Proxy server cannot start.",
                    name,
                    port,
                )
                return False
        return True

    def _get_request_headers(
        self, method: str, headers: CIMultiDictProxy[str]
    ) -> dict[str, str]:
        allowed_headers = ALLOWED_REQUEST_HEADERS.get(method.upper(), [])
        request_headers = {}
        request_headers["connection"] = "keep-alive"
        request_headers.update(self._get_filtered_headers(allowed_headers, headers))
        return request_headers

    def _get_response_headers(
        self, method: str, headers: CIMultiDictProxy[str]
    ) -> dict[str, str]:
        allowed_headers = ALLOWED_RESPONSE_HEADERS.get(method.upper(), [])
        filtered_headers = self._get_filtered_headers(allowed_headers, headers)
        if method.upper() in ("GET", "HEAD"):
            return self._set_caching_headers(filtered_headers)
        return filtered_headers

    def _set_caching_headers(self, headers: dict[str, str]) -> dict[str, str]:
        content_type = headers.get("content-type", "").split(";")[0]
        if content_type in CACHEABLE_MIME_TYPES and "cache-control" not in headers:
            headers["cache-control"] = "public, max-age=31536000"
        return headers

    def _get_filtered_headers(
        self, allowed_headers: list[str], headers: CIMultiDictProxy[str]
    ) -> dict[str, str]:
        """Build a header dict that is filtered to just the allowed headers."""
        filtered_headers = {}
        for h in allowed_headers:
            if h in headers:
                filtered_headers[h] = headers[h]
        return filtered_headers

    async def _printer_http_handler(
        self, request: web.Request, printer: Printer
    ) -> web.StreamResponse:
        """Handle HTTP requests for a specific printer (direct pass-through)."""
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._printer_websocket_handler(request, printer)

        if request.method == "POST" and (
            request.path == "/uploadFile/upload"
            or request.path.endswith("/uploadFile/upload")
        ):
            return await self._printer_file_handler(request, printer)
        return await self._printer_http_proxy_handler(request, printer)

    async def _printer_video_handler(
        self, request: web.Request, printer: Printer
    ) -> web.StreamResponse:
        """Handle video requests for a specific printer (direct pass-through)."""
        if not self.session or self.session.closed:
            return web.Response(status=503, text="Session not available.")

        # Forward directly to printer's video endpoint
        remote_url = f"http://{printer.ip_address}:{VIDEO_PORT}{request.path_qs}"
        try:
            async with self.session.get(
                remote_url,
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=10, sock_read=None
                ),
                headers=self._get_request_headers("GET", request.headers),
            ) as proxy_response:
                resp_headers = self._get_response_headers("GET", proxy_response.headers)
                resp_headers.pop("content-length", None)
                response = web.StreamResponse(
                    status=proxy_response.status,
                    reason=proxy_response.reason,
                    headers=resp_headers,
                )
                await response.prepare(request)
                try:
                    async for chunk in proxy_response.content.iter_chunked(8192):
                        if request.transport is None or request.transport.is_closing():
                            self.logger.debug(
                                "Client disconnected, stopping video stream."
                            )
                            break
                        await response.write(chunk)
                    await response.write_eof()
                except (ConnectionResetError, asyncio.CancelledError) as e:
                    self.logger.debug("Video stream stopped: %s", e)
                except Exception:
                    self.logger.exception(
                        "An unexpected error occurred during video streaming"
                    )
                return response
        except TimeoutError as e:
            self.logger.debug("Video stream timeout from %s: %s", remote_url, e)
            return web.Response(status=504, text="Video stream not available")
        except aiohttp.ClientError as e:
            self.logger.debug("Video stream not available from %s: %s", remote_url, e)
            return web.Response(status=502, text="Video stream not available")

    def get_local_ip(self) -> str:
        """Determine the local IP address for outbound communication."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((DEFAULT_FALLBACK_IP, 1))
                return s.getsockname()[0]
        except Exception:  # noqa: BLE001
            return PROXY_HOST

    async def _connect_to_printer(
        self, request: web.Request, printer: Printer
    ) -> aiohttp.ClientWebSocketResponse | None:
        """Connect to a specific printer's WebSocket."""
        try:
            remote_ws_url = (
                f"ws://{printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"
            )
            remote_ws = await self.session.ws_connect(
                remote_ws_url,
                headers=self._get_request_headers("WS", request.headers),
                heartbeat=10.0,
            )
            self.logger.debug(
                "Connected to printer %s (%s)", printer.name, printer.ip_address
            )
        except aiohttp.ClientError:
            self.logger.warning(
                "Failed to connect to printer %s (%s)",
                printer.name,
                printer.ip_address,
            )
            return None
        else:
            return remote_ws

    def _find_video_url_in_data(self, data: dict) -> tuple[str | None, dict | None]:
        """Find VideoUrl in nested data structures and return it with its parent."""
        if not isinstance(data, dict):
            return None, None

        # Check top-level VideoUrl
        if "VideoUrl" in data:
            return data["VideoUrl"], data

        # Check Data.VideoUrl
        if isinstance(data.get("Data"), dict) and "VideoUrl" in data["Data"]:
            return data["Data"]["VideoUrl"], data["Data"]

        # Check Data.Data.VideoUrl
        if (
            isinstance(data.get("Data"), dict)
            and isinstance(data["Data"].get("Data"), dict)
            and "VideoUrl" in data["Data"]["Data"]
        ):
            return data["Data"]["Data"]["VideoUrl"], data["Data"]["Data"]

        return None, None

    async def _route_printer_to_client(
        self,
        mainboard_id: str,
        remote_ws: aiohttp.ClientWebSocketResponse,
        client_ws: web.WebSocketResponse,
        printer_connections: dict[str, aiohttp.ClientWebSocketResponse],
    ) -> None:
        """Route messages from specific printer to client."""
        try:
            async for message in remote_ws:
                if message.type == WSMsgType.TEXT:
                    payload = message.data
                    try:
                        data = json.loads(payload)
                        # Find and rewrite VideoUrl in nested data structures
                        video_url, target = self._find_video_url_in_data(data)
                        if video_url:
                            video_url_str = str(video_url)
                            proxy_ip = self.get_local_ip()

                            # Handle URLs without scheme (e.g., "10.0.0.184:3031/video")
                            if not video_url_str.startswith(("http://", "https://")):
                                video_url_str = f"http://{video_url_str}"

                            # Build URL without scheme to match original format
                            modified_url = (
                                f"{proxy_ip}:{VIDEO_PORT}/video?id={mainboard_id}"
                            )
                            target["VideoUrl"] = modified_url
                            payload = json.dumps(data)
                            self.logger.debug(
                                "Rewrote VideoUrl from %s -> %s",
                                video_url,
                                modified_url,
                            )
                    except (
                        json.JSONDecodeError,
                        ValueError,
                        TypeError,
                        KeyError,
                        AttributeError,
                    ):
                        # Not JSON or malformed: forward original payload
                        self.logger.debug("Could not parse or rewrite VideoUrl")
                    await client_ws.send_str(payload)
                elif message.type == WSMsgType.BINARY:
                    await client_ws.send_bytes(message.data)
                elif message.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        except aiohttp.ClientError:
            self.logger.exception(
                "Error in printer-to-client routing for %s", mainboard_id
            )
        finally:
            # Clean up connection
            printer_connections.pop(mainboard_id, None)

    async def _centralized_websocket_handler(
        self, request: web.Request
    ) -> web.WebSocketResponse:
        """Handle WebSocket connections with topic-based message routing."""
        client_ws = web.WebSocketResponse()
        await client_ws.prepare(request)

        if not self.session or self.session.closed:
            await client_ws.close(code=1011, message=b"Upstream connection failed")
            return client_ws

        # Keep connections to all printers
        printer_connections: dict[str, aiohttp.ClientWebSocketResponse] = {}
        tasks: set[asyncio.Task] = set()

        try:
            # Route messages from client to printers
            async for message in client_ws:
                if message.type == WSMsgType.TEXT:
                    await self._handle_client_text_message(
                        message, printer_connections, tasks, request, client_ws
                    )
                elif message.type == WSMsgType.BINARY:
                    self.logger.debug("Binary message received but not routed")
                elif message.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break

        except aiohttp.ClientError:
            self.logger.exception("Error in centralized WebSocket handler")
        finally:
            await self._cleanup_websocket_connections(
                tasks, printer_connections, client_ws
            )

        return client_ws

    async def _handle_client_text_message(
        self,
        message: aiohttp.WSMessage,
        printer_connections: dict[str, aiohttp.ClientWebSocketResponse],
        tasks: set[asyncio.Task],
        request: web.Request,
        client_ws: web.WebSocketResponse,
    ) -> None:
        """Handle a text message from client and route to appropriate printer."""
        mainboard_id = extract_mainboard_id_from_message(message.data)

        if not mainboard_id:
            # Fallbacks: query param and path segment
            mainboard_id = request.query.get("id") or request.query.get("mainboard_id")
            if not mainboard_id:
                parts = request.path.strip("/").split("/")
                if len(parts) >= MIN_PATH_PARTS_FOR_FALLBACK and parts[0] in (
                    "api",
                    "video",
                ):
                    mainboard_id = parts[1]
        if not mainboard_id:
            self.logger.debug("No MainboardID found in message: %s", message.data[:200])
            return

        # Find target printer
        target_printer = self.printer_registry.get_printer_by_mainboard_id(mainboard_id)
        if not target_printer:
            # Debug: Show what printers are available
            available_printers = (
                self.printer_registry.get_all_printers_by_mainboard_id()
            )
            self.logger.warning(
                "No printer found for MainboardID: %s. Available printers: %s",
                mainboard_id,
                list(available_printers.keys()),
            )
            return

        # Connect to printer if not already connected
        if mainboard_id not in printer_connections:
            remote_ws = await self._connect_to_printer(request, target_printer)
            if not remote_ws:
                return
            printer_connections[mainboard_id] = remote_ws

            # Start printer-to-client forwarding task
            task = asyncio.create_task(
                self._route_printer_to_client(
                    mainboard_id, remote_ws, client_ws, printer_connections
                ),
                name=f"elegoo_ws:printer_{mainboard_id}_to_client",
            )
            tasks.add(task)

        # Forward message to printer, injecting MainboardID if missing
        remote_ws = printer_connections[mainboard_id]
        try:
            # Check if message needs MainboardID injection
            message_data = message.data
            try:
                data = json.loads(message_data)
                if (
                    isinstance(data, dict)
                    and isinstance(data.get("Data"), dict)
                    and not data["Data"].get("MainboardID")
                ):
                    # Inject MainboardID from WebSocket query parameter
                    data["Data"]["MainboardID"] = mainboard_id
                    message_data = json.dumps(data)
                    self.logger.debug(
                        "Injected MainboardID %s into outgoing message", mainboard_id
                    )
            except (json.JSONDecodeError, KeyError, TypeError):
                # If we can't parse/modify the message, send it as-is
                pass

            await remote_ws.send_str(message_data)
        except aiohttp.ClientError:
            self.logger.exception("Failed to send message to printer %s", mainboard_id)
            printer_connections.pop(mainboard_id, None)

    async def _cleanup_websocket_connections(
        self,
        tasks: set[asyncio.Task],
        printer_connections: dict[str, aiohttp.ClientWebSocketResponse],
        client_ws: web.WebSocketResponse,
    ) -> None:
        """Clean up WebSocket connections and tasks."""
        # Cancel all tasks
        for task in tasks:
            if not task.done():
                task.cancel()

        # Close all printer connections
        for remote_ws in printer_connections.values():
            if not remote_ws.closed:
                await remote_ws.close()

        # Close client connection
        if not client_ws.closed:
            await client_ws.close()

        # Wait for all tasks to complete
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _get_target_printer_from_request(self, request: web.Request) -> Printer | None:
        """
        Extract target printer from HTTP request using multiple methods.

        Tries in order:
        1. Query parameters: ?id=mainboardid or ?mainboard_id=mainboardid (preferred)
        2. Referer header: Extracts MainboardID from referring page URL
        3. X-MainboardID header: Direct header specification (fallback)

        This multi-method approach ensures routing works for:
        - Direct API calls with query params
        - Web interface navigation (uses referer)
        - Custom client implementations (uses headers)

        Returns:
            Target printer if found, None otherwise

        """
        mainboard_id = None

        self.logger.debug(
            "HTTP request path: %s, query: %s", request.path, dict(request.query)
        )

        # Method 1: Query parameter routing (preferred for all requests)
        mainboard_id = request.query.get("id") or request.query.get("mainboard_id")
        if mainboard_id:
            # Reduced logging: Only log when needed for debugging
            pass

        # Method 2: Referer header fallback (for web interface navigation)
        if not mainboard_id:
            referer = request.headers.get("Referer", "")
            if referer:
                mainboard_id = extract_mainboard_id_from_header(referer)
                if mainboard_id:
                    # Reduced logging: Only log when needed for debugging
                    pass

        # Method 3: X-MainboardID header fallback
        if not mainboard_id:
            mainboard_id = request.headers.get("X-MainboardID")
            if mainboard_id:
                # Reduced logging: Only log when needed for debugging
                pass

        # Find printer by MainboardID
        if mainboard_id:
            self.logger.debug("Looking up printer for MainboardID: %s", mainboard_id)
            available_printers = self.printer_registry.get_all_printers()
            available_by_mainboard = (
                self.printer_registry.get_all_printers_by_mainboard_id()
            )
            self.logger.debug(
                "Available printers by IP: %s", list(available_printers.keys())
            )
            self.logger.debug(
                "Available printers by MainboardID: %s",
                list(available_by_mainboard.keys()),
            )

            target_printer = self.printer_registry.get_printer_by_mainboard_id(
                mainboard_id
            )
            if target_printer:
                self.logger.debug(
                    "HTTP request routed to printer %s (MainboardID: %s)",
                    target_printer.name,
                    mainboard_id,
                )
                return target_printer
            self.logger.warning(
                "No printer found for MainboardID: %s in HTTP request", mainboard_id
            )

        # Fallback: Use first available printer if no MainboardID specified
        printers = self.printer_registry.get_all_printers()
        if printers:
            fallback_printer = next(iter(printers.values()))
            self.logger.debug(
                "No MainboardID in HTTP request, using fallback printer: %s",
                fallback_printer.name,
            )
            return fallback_printer

        self.logger.warning("No printers available for HTTP request")
        return None

    def _get_cleaned_path_for_printer(self, request_path: str) -> str:
        """
        Return the request path as-is since we use query parameter routing.

        With query parameter routing (?id=mainboardid), the path doesn't contain
        MainboardID information that needs to be removed.

        Examples:
            /api/status?id=abc123 -> /api/status (path unchanged)
            /video?id=abc123 -> /video (path unchanged)
            /status?id=abc123 -> /status (path unchanged)

        """
        return request_path

    async def _centralized_http_handler(
        self, request: web.Request
    ) -> web.StreamResponse:
        """Central HTTP handler that routes all requests."""
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._centralized_websocket_handler(request)

        # Extract MainboardID from URL path, query param, or header
        target_printer = self._get_target_printer_from_request(request)
        if not target_printer:
            return web.Response(status=404, text="Printer not found or not specified")

        # Use the identified printer for HTTP requests
        first_printer = target_printer

        if request.method == "POST" and (
            request.path == "/uploadFile/upload"
            or request.path.endswith("/uploadFile/upload")
        ):
            return await self._centralized_file_handler(request, first_printer)

        return await self._centralized_http_proxy_handler(request, first_printer)

    async def _centralized_http_proxy_handler(
        self, request: web.Request, printer: Printer
    ) -> web.StreamResponse:
        """Handle HTTP requests by forwarding to the specified printer."""
        if not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        # Clean path by removing MainboardID before forwarding to printer
        cleaned_path = self._get_cleaned_path_for_printer(request.path)
        query_string = f"?{request.query_string}" if request.query_string else ""

        # Use appropriate port based on request type
        if cleaned_path.startswith("/video"):
            # Video requests always go to VIDEO_PORT (3031)
            target_port = VIDEO_PORT
        else:
            # Other requests go to WEBSOCKET_PORT (3030)
            target_port = WEBSOCKET_PORT

        target_url = (
            f"http://{printer.ip_address}:{target_port}{cleaned_path}{query_string}"
        )

        self.logger.debug(
            "HTTP proxy forwarding: %s %s -> %s (cleaned path: %s)",
            request.method,
            request.path,
            target_url,
            cleaned_path,
        )

        try:
            async with self.session.request(
                request.method,
                target_url,
                headers=self._get_request_headers(request.method, request.headers),
                data=request.content,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=10, sock_read=None
                ),
            ) as upstream_response:
                response_headers = self._get_response_headers(
                    request.method, upstream_response.headers
                )
                client_response = web.StreamResponse(
                    status=upstream_response.status,
                    headers=response_headers,
                )
                content_type = response_headers.get("content-type", "").split(";")[0]
                if content_type in TRANSFORMABLE_MIME_TYPES:
                    return await self._transformed_streamed_response(
                        request, client_response, upstream_response, printer
                    )
                return await self._streamed_response(
                    request, client_response, upstream_response
                )
        except aiohttp.ClientError as e:
            msg = f"HTTP proxy error connecting to {target_url}"
            self.logger.exception(msg)
            return web.Response(status=502, text=f"Bad Gateway: {e}")

    async def _transformed_streamed_response(
        self,
        request: web.Request,
        client_response: web.StreamResponse,
        upstream_response: ClientResponse,
        printer: Printer,
    ) -> web.StreamResponse:
        client_response.headers.pop("content-length", None)
        await client_response.prepare(request)
        encoding = "utf-8"
        content_type = client_response.headers.get("content-type")
        if content_type:
            matches = re.search(r"charset=(.+?)(;|$)", content_type)
            if matches and matches[1]:
                encoding = matches[1]
        previous = ""
        async for chunk in upstream_response.content.iter_any():
            current = chunk.decode(encoding)
            previous_length = len(previous)
            if previous_length > 0:
                combined = previous + current
                replaced = self._process_replacements(combined, printer)
                half_len = floor(len(replaced) / 2)
                replaced_previous = replaced[:half_len]
                await client_response.write(replaced_previous.encode(encoding))
                previous = replaced[half_len:]
            else:
                previous = current

        await client_response.write(previous.encode(encoding))
        await client_response.write_eof()
        return client_response

    async def _streamed_response(
        self,
        request: web.Request,
        client_response: web.StreamResponse,
        upstream_response: ClientResponse,
    ) -> web.StreamResponse:
        await client_response.prepare(request)
        async for chunk in upstream_response.content.iter_any():
            await client_response.write(chunk)
        await client_response.write_eof()
        return client_response

    def _process_replacements(self, content: str, printer: Printer) -> str:
        # Apply existing IP address and port replacements
        processed_content = (
            content.replace(
                printer.ip_address or DEFAULT_FALLBACK_IP, self.get_local_ip()
            )
            .replace(
                f"{self.get_local_ip()}/",
                f"{self.get_local_ip()}:{WEBSOCKET_PORT}/",
            )
            .replace(
                "${this.webSocketService.hostName}:80",
                "${this.webSocketService.hostName}:" + f"{WEBSOCKET_PORT}",
            )
        )

        # Apply JavaScript WebSocket URL transformations (for MainboardID routing)
        if printer.id and f"?id={printer.id}" not in processed_content:
            # Template literal syntax (ES6) - main pattern for WebSocket connections
            processed_content = processed_content.replace(
                "ws://${this.hostName}:3030/websocket",
                f"ws://${{this.hostName}}:3030/websocket?id={printer.id}",
            )

            # Template literal for HTTP URLs
            processed_content = processed_content.replace(
                "http://${this.hostName}:3030/",
                f"http://${{this.hostName}}:3030/?id={printer.id}&",
            )

            # String concatenation patterns (in case they exist)
            processed_content = processed_content.replace(
                'ws://" + this.hostName + ":3030/websocket',
                f'ws://" + this.hostName + ":3030/websocket?id={printer.id}',
            )

            # Generic patterns without host variables
            processed_content = processed_content.replace(
                "ws://localhost:3030/websocket",
                f"ws://localhost:3030/websocket?id={printer.id}",
            )

        return processed_content

    async def _centralized_file_handler(
        self, request: web.Request, printer: Printer
    ) -> web.Response:
        """Handle file upload requests by forwarding to the specified printer."""
        if not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        # Clean path by removing MainboardID before forwarding to printer
        cleaned_path = self._get_cleaned_path_for_printer(request.path)
        query_string = f"?{request.query_string}" if request.query_string else ""
        remote_url = (
            f"http://{printer.ip_address}:{WEBSOCKET_PORT}{cleaned_path}{query_string}"
        )

        try:
            async with self.session.post(
                remote_url,
                headers=self._get_request_headers("POST", request.headers),
                data=request.content,
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=10, sock_read=None
                ),
            ) as response:
                content = await response.read()
                return web.Response(
                    body=content,
                    status=response.status,
                    headers=self._get_response_headers("POST", response.headers),
                )
        except Exception:
            self.logger.exception(
                "HTTP file proxy error for printer %s", printer.ip_address
            )
            return web.Response(status=502, text="Bad Gateway")

    async def _intercept_and_modify_main_js(
        self, upstream_response: aiohttp.ClientResponse, printer: Printer
    ) -> web.Response:
        """
        Intercept and modify JavaScript files to inject MainboardID routing.

        This method modifies WebSocket connection URLs in the JavaScript to include
        the MainboardID parameter for proper multi-printer routing.
        """
        self.logger.info(
            "[!] Intercepting and modifying JavaScript file for printer %s", printer.id
        )

        # 1. Read the entire original body to perform replacement
        original_body = await upstream_response.text()
        modified_body = original_body

        # 2. Define replacement patterns for WebSocket URLs
        replacements = [
            # Template literal syntax (ES6) - this is what you're actually looking for
            {
                "find": "ws://${this.hostName}:3030/websocket",
                "replace": f"ws://${{this.hostName}}:3030/websocket?id={printer.id}",
            },
            # Template literal for HTTP URLs
            {
                "find": "http://${this.hostName}:3030/",
                "replace": f"http://${{this.hostName}}:3030/?id={printer.id}&",
            },
            # String concatenation patterns (in case they exist)
            {
                "find": 'ws://" + this.hostName + ":3030/websocket',
                "replace": f'ws://" + this.hostName + ":3030/websocket?id={printer.id}',
            },
            # Generic patterns without host variables
            {
                "find": "ws://localhost:3030/websocket",
                "replace": f"ws://localhost:3030/websocket?id={printer.id}",
            },
        ]

        # 3. Apply all replacements
        replacements_made = 0
        for replacement in replacements:
            if replacement["find"] in modified_body:
                modified_body = modified_body.replace(
                    replacement["find"], replacement["replace"]
                )
                replacements_made += 1
                self.logger.debug(
                    "Replaced '%s' with '%s'",
                    replacement["find"][:MAX_LOG_LENGTH] + "..."
                    if len(replacement["find"]) > MAX_LOG_LENGTH
                    else replacement["find"],
                    replacement["replace"][:MAX_LOG_LENGTH] + "..."
                    if len(replacement["replace"]) > MAX_LOG_LENGTH
                    else replacement["replace"],
                )

        if replacements_made > 0:
            self.logger.info(
                "Made %d replacements in JavaScript file for printer %s",
                replacements_made,
                printer.id,
            )
        else:
            self.logger.warning(
                "No WebSocket patterns found in JS file for printer %s",
                printer.id,
            )

        # 4. Prepare response headers, removing recalculated ones
        response_headers = upstream_response.headers.copy()
        for h in (
            "Content-Length",
            "Transfer-Encoding",
            "Connection",
            "Content-Encoding",
        ):
            response_headers.pop(h, None)

        # 5. Return a new, non-streaming response with the modified text
        # aiohttp will automatically set the correct Content-Length.
        return web.Response(
            text=modified_body,
            status=upstream_response.status,
            headers=response_headers,
        )

    async def _printer_http_proxy_handler(
        self, request: web.Request, printer: Printer
    ) -> web.StreamResponse:
        """Handle HTTP requests, to an interception method for specific files."""
        if not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        target_url = f"http://{printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"

        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower()
            not in (
                "host",
                "content-length",
                "transfer-encoding",
                "connection",
                "keep-alive",
                "proxy-authenticate",
                "proxy-authorization",
                "te",
                "trailer",
                "upgrade",
            )
        }

        try:
            async with self.session.request(
                request.method,
                target_url,
                headers=headers,
                data=request.content,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=10, sock_read=None
                ),
            ) as upstream_response:
                # --- Main Logic: Check and Delegate ---
                # Intercept JavaScript files with WebSocket connection code
                if request.path.endswith(".js") and (
                    request.path.startswith("/main.")
                    or request.path.startswith("/app.")
                    or "main" in request.path.lower()
                ):
                    # Delegate the special handling to our new method
                    return await self._intercept_and_modify_main_js(
                        upstream_response, printer
                    )

                # --- Default Case: Stream all other responses ---
                response_headers = upstream_response.headers.copy()
                for h in (
                    "Content-Length",
                    "Transfer-Encoding",
                    "Connection",
                    "Keep-Alive",
                ):
                    response_headers.pop(h, None)

                client_response = web.StreamResponse(
                    status=upstream_response.status, headers=response_headers
                )
                await client_response.prepare(request)
                async for chunk in upstream_response.content.iter_any():
                    await client_response.write(chunk)
                await client_response.write_eof()
                return client_response

        except aiohttp.ClientError:
            self.logger.exception(
                "HTTP proxy error connecting to printer %s", printer.ip_address
            )
            return web.Response(status=502, text="Bad Gateway")

    async def _printer_file_handler(
        self, request: web.Request, printer: Printer
    ) -> web.Response:
        """Handle file upload requests for a specific printer (direct pass-through)."""
        if not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        # Forward directly to printer's file upload endpoint
        remote_url = f"http://{printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"

        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower()
            not in (
                "host",
                "content-length",
                "transfer-encoding",
                "connection",
                "keep-alive",
                "proxy-authenticate",
                "proxy-authorization",
                "te",
                "trailer",
                "upgrade",
            )
        }

        try:
            async with self.session.post(
                remote_url,
                headers=headers,
                data=request.content,
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=10, sock_read=None
                ),
            ) as response:
                content = await response.read()
                resp_headers = response.headers.copy()
                for h in (
                    "Content-Length",
                    "Transfer-Encoding",
                    "Connection",
                    "Keep-Alive",
                ):
                    resp_headers.pop(h, None)
                return web.Response(
                    body=content, status=response.status, headers=resp_headers
                )
        except Exception:
            self.logger.exception(
                "HTTP file proxy error for printer %s", printer.ip_address
            )
            return web.Response(status=502, text="Bad Gateway")


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol to handle UDP discovery broadcasts for multiple printers."""

    def __init__(
        self, logger: Any, printer_registry: PrinterRegistry, proxy_ip: str
    ) -> None:
        """Initialize the discovery protocol."""
        super().__init__()
        self.logger = logger
        self.printer_registry = printer_registry
        self.proxy_ip = proxy_ip
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        """Call when a connection is made."""
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """
        Handle incoming UDP datagrams for discovery.

        Respond for each discovered printer with port-based routing information.
        """
        try:
            message = data.decode("utf-8", errors="ignore").strip()
        except Exception:  # noqa: BLE001
            msg = f"Ignoring undecodable discovery datagram from {addr}"
            self.logger.debug(msg)
            return

        if message == DISCOVERY_MESSAGE:
            self.logger.debug(
                "Discovery request received from %s, responding for all printers.", addr
            )

            # Get all discovered printers
            printers = self.printer_registry.get_all_printers()
            printer_ports = self.printer_registry.get_all_printer_ports()

            if not printers:
                # If no printers discovered, send a generic proxy response
                response_payload = {
                    "Id": os.urandom(8).hex(),
                    "Data": {
                        "Name": "Elegoo Multi-Printer Proxy",
                        "MachineName": "Elegoo Multi-Printer Proxy",
                        "BrandName": "Elegoo",
                        "MainboardIP": self.proxy_ip,
                        "MainboardID": "proxy",
                        "ProtocolVersion": "V3.0.0",
                        "FirmwareVersion": "V1.0.0",
                    },
                }
                json_string = json.dumps(response_payload)
                if self.transport:
                    self.transport.sendto(json_string.encode(), addr)
                    self.logger.debug(
                        "Sent proxy discovery response (no printers found)"
                    )
            else:
                # Send a response for each discovered printer with port information
                for ip, printer in printers.items():
                    ports = printer_ports.get(ip)
                    if not ports:
                        continue

                    ws_port, video_port = ports
                    printer_name = getattr(printer, "name", "Elegoo")
                    display_name = f"{printer_name} (Port {ws_port})"
                    response_payload = {
                        "Id": getattr(printer, "connection", os.urandom(8).hex()),
                        "Data": {
                            "Name": display_name,
                            "MachineName": display_name,
                            "BrandName": getattr(printer, "brand", "Elegoo"),
                            "MainboardIP": self.proxy_ip,  # Point to our proxy
                            "MainboardID": getattr(printer, "id", None) or ip,
                            "ProtocolVersion": getattr(printer, "protocol", "V3.0.0"),
                            "FirmwareVersion": getattr(printer, "firmware", "V1.0.0"),
                            # Add custom fields for port-based routing
                            "ProxyWebSocketPort": ws_port,
                            "ProxyVideoPort": video_port,
                        },
                    }
                    json_string = json.dumps(response_payload)
                    if self.transport:
                        self.transport.sendto(json_string.encode(), addr)
                        self.logger.debug(
                            "Sent discovery response for %s on ports WS:%d Video:%d",
                            ip,
                            ws_port,
                            video_port,
                        )

    def error_received(self, exc: Exception) -> None:
        """Call when an error is received."""
        msg = f"UDP Discovery Server Error: {exc}"
        self.logger.error(msg)
