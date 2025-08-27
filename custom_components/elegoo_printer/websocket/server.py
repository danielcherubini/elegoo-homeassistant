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

- WebSocket (Port 3030): SDCP protocol messages with MainboardID routing
- HTTP/HTTPS (Port 3030): REST API calls and file uploads
- Video Streaming (Port 3031): Real-time camera feeds with URL rewriting
- UDP Discovery (Port 3000): Network printer discovery and advertisement

MESSAGE ROUTING:
===============

All messages include a MainboardID that identifies the target printer:
- WebSocket: Extracted from JSON message payload
- HTTP: Via query parameter (?mainboard_id=xxx) or path (/api/{id}/...)
- Video: Via URL path (/video/{mainboard_id})

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
import socket
import time
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import ClientSession, WSMsgType, web
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.elegoo_printer.const import (
    CONF_PROXY_VIDEO_PORT,
    CONF_PROXY_WEBSOCKET_PORT,
    DEFAULT_BROADCAST_ADDRESS,
    DEFAULT_FALLBACK_IP,
    DISCOVERY_MESSAGE,
    DISCOVERY_PORT,
    DOMAIN,
    PROXY_HOST,
    VIDEO_PORT,
    WEBSOCKET_PORT,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

INADDR_ANY = "0.0.0.0"  # noqa: S104
DISCOVERY_TIMEOUT = 5
DISCOVERY_RATE_LIMIT_SECONDS = 30
MIN_MAINBOARD_ID_LENGTH = 8


class PrinterRegistry:
    """
    Registry for managing multiple discovered printers with port-based routing.

    This class provides thread-safe management of network-discovered printers
    with automatic discovery, caching, rate limiting, and port assignment.

    PORT ALLOCATION STRATEGY:
    ========================

    Printers are assigned unique port pairs based on registration order:
    - Printer 1: WebSocket=3030, Video=3031
    - Printer 2: WebSocket=3032, Video=3033
    - Printer 3: WebSocket=3034, Video=3035
    - Formula: WebSocket = 3030 + (index)*2, Video = 3031 + (index)*2

    DISCOVERY PROCESS:
    =================

    1. UDP Broadcast: Send "M99999" to 255.255.255.255:3000
    2. Response Collection: Gather JSON responses for 5 seconds
    3. Printer Parsing: Extract printer info and create Printer objects
    4. Registry Update: Store printers by IP address with port assignment

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

        # Use stored ports if available (from config)
        if printer.proxy_websocket_port and printer.proxy_video_port:
            ws_port = printer.proxy_websocket_port
            video_port = printer.proxy_video_port
        else:
            # Fallback to auto-assignment for legacy configs
            ws_port = WEBSOCKET_PORT + (self._next_index * 2)
            video_port = VIDEO_PORT + (self._next_index * 2)
            self._next_index += 1

        self._printers[printer.ip_address] = printer
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
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Failed to update config entry for printer %s: %s",
                    printer.id,
                    e,
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
                        except Exception as e:  # noqa: BLE001
                            # Catch any other unexpected errors
                            logger.debug("Unexpected error during discovery: %s", e)
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
    Manages local proxy servers for multiple Elegoo printers.

    This includes WebSocket, UDP discovery, and a full HTTP reverse proxy.
    This server runs on the main Home Assistant event loop and routes
    requests to appropriate printers based on MainboardID.
    """

    _instance: ElegooPrinterServer | None = None
    _creation_lock = asyncio.Lock()

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
                if printer:
                    # Check and assign ports if needed, update config
                    await PrinterRegistry.ensure_printer_ports_assigned(
                        printer, hass, logger
                    )
                    # Add printer to existing server's registry and start server for it
                    ws_port, video_port = cls._instance.printer_registry.add_printer(
                        printer
                    )
                    # Start printer servers - accessing private method
                    # for singleton coordination
                    await cls._instance._start_printer_servers(  # noqa: SLF001
                        printer, ws_port, video_port
                    )
                    logger.debug(
                        "Added printer %s (%s) to proxy server on ports WS:%d Video:%d",
                        printer.name,
                        printer.ip_address,
                        ws_port,
                        video_port,
                    )
                else:
                    logger.debug("Reusing existing proxy server instance")
                return cls._instance

            # Create new instance
            logger.debug("Creating new proxy server instance")
            self = cls(logger, hass, session)
            if printer:
                # Check and assign ports if needed, update config
                await PrinterRegistry.ensure_printer_ports_assigned(
                    printer, hass, logger
                )
                # Add the initial printer to the registry
                ws_port, video_port = self.printer_registry.add_printer(printer)
                logger.debug(
                    "Added printer %s (%s) to new proxy server on ports WS:%d Video:%d",
                    printer.name,
                    printer.ip_address,
                    ws_port,
                    video_port,
                )

            # Set instance BEFORE starting to prevent race condition
            cls._instance = self

            try:
                # Start the discovery server and printer-specific servers
                await self.start()

                # Only perform network discovery if no printers were provided via config
                if self.printer_registry.count() == 0:
                    logger.debug("No configured printers, performing network discovery")
                    await self.printer_registry.discover_printers(logger)
                    # Start servers for discovered printers
                    await self._start_servers_for_all_printers()
                else:
                    logger.debug(
                        "Using %d printer(s) from Home Assistant config",
                        self.printer_registry.count(),
                    )
                    # Start servers for configured printers
                    await self._start_servers_for_all_printers()
            except Exception:
                # Clear instance if startup fails
                cls._instance = None
                raise

            return self

    @property
    def is_connected(self) -> bool:
        """Return true if the proxy is connected to the printer."""
        return self._is_connected

    async def start(self) -> None:
        """Start the discovery server (UDP only for printer discovery)."""
        self.logger.info(
            "Initializing multi-printer proxy server with port-based routing"
        )

        try:
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
            msg = f"Failed to start discovery server: {e}"
            self.logger.exception(msg)
            await self.stop()
            raise ConfigEntryNotReady(msg) from e

        self._is_connected = True
        self.logger.info("Discovery server has started successfully.")

    async def _start_servers_for_all_printers(self) -> None:
        """Start HTTP/WebSocket servers for all registered printers."""
        for ip, printer in self.printer_registry.get_all_printers().items():
            ports = self.printer_registry.get_printer_ports(ip)
            if ports:
                ws_port, video_port = ports
                await self._start_printer_servers(printer, ws_port, video_port)

    async def _start_printer_servers(
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
    async def stop_all(cls) -> None:
        """Stop the proxy server singleton instance."""
        if cls._instance is not None:
            await cls._instance.stop()
            cls._instance = None

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

        if self.datagram_transport:
            self.datagram_transport.close()
            self.datagram_transport = None

        for runner in self.runners:
            await runner.cleanup()
        self.runners.clear()

        # Clear singleton instance
        if self.__class__._instance is self:  # noqa: SLF001
            self.__class__._instance = None  # noqa: SLF001

        self.logger.info("Proxy server stopped.")

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

        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        try:
            async with self.session.get(
                remote_url,
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=10, sock_read=None
                ),
                headers=headers,
            ) as proxy_response:
                response_headers = proxy_response.headers.copy()
                for h in ("Content-Length", "Transfer-Encoding", "Connection"):
                    response_headers.pop(h, None)
                response = web.StreamResponse(
                    status=proxy_response.status,
                    reason=proxy_response.reason,
                    headers=response_headers,
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
        except (TimeoutError, aiohttp.ClientError):
            self.logger.exception(
                "Error proxying video stream to %s", printer.ip_address
            )
            return web.Response(status=502, text="Bad Gateway")

    def get_local_ip(self) -> str:
        """Determine the local IP address for outbound communication."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((DEFAULT_FALLBACK_IP, 1))
                return s.getsockname()[0]
        except Exception:  # noqa: BLE001
            return PROXY_HOST

    async def _printer_websocket_handler(
        self, request: web.Request, printer: Printer
    ) -> web.WebSocketResponse:
        """Handle WebSocket connections for a specific printer (direct pass-through)."""
        client_ws = web.WebSocketResponse()
        await client_ws.prepare(request)

        if not self.session or self.session.closed:
            await client_ws.close(code=1011, message=b"Upstream connection failed")
            return client_ws

        # Connect directly to printer's WebSocket
        remote_ws_url = f"ws://{printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"
        allowed_headers = {
            "sec-websocket-version",
            "sec-websocket-key",
            "sec-websocket-protocol",
            "upgrade",
            "connection",
        }
        filtered_headers = {
            k: v for k, v in request.headers.items() if k.lower() in allowed_headers
        }

        try:
            remote_ws = await self.session.ws_connect(
                remote_ws_url, headers=filtered_headers, heartbeat=10.0
            )

            # Bidirectional message forwarding
            async def forward_to_remote() -> None:
                async for message in client_ws:
                    if message.type == WSMsgType.TEXT:
                        await remote_ws.send_str(message.data)
                    elif message.type == WSMsgType.BINARY:
                        await remote_ws.send_bytes(message.data)
                    elif message.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break

            async def forward_to_client() -> None:
                async for message in remote_ws:
                    if message.type == WSMsgType.TEXT:
                        await client_ws.send_str(message.data)
                    elif message.type == WSMsgType.BINARY:
                        await client_ws.send_bytes(message.data)
                    elif message.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break

            # Run both forwarding tasks concurrently
            await asyncio.gather(
                forward_to_remote(), forward_to_client(), return_exceptions=True
            )

        except Exception:
            self.logger.exception(
                "Failed to establish WebSocket connection to printer %s",
                printer.ip_address,
            )
        finally:
            if not remote_ws.closed:
                await remote_ws.close()
            if not client_ws.closed:
                await client_ws.close()

        return client_ws

    async def _printer_http_proxy_handler(
        self, request: web.Request, printer: Printer
    ) -> web.StreamResponse:
        """Handle HTTP requests for a specific printer (direct pass-through)."""
        if not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        # Forward directly to printer
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

        except aiohttp.ClientError as e:
            self.logger.exception(
                "HTTP proxy error connecting to printer %s", printer.ip_address
            )
            return web.Response(status=502, text=f"Bad Gateway: {e}")

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
        except Exception as e:
            self.logger.exception(
                "HTTP file proxy error for printer %s", printer.ip_address
            )
            return web.Response(status=502, text=f"Bad Gateway: {e}")


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
        except Exception as e:  # noqa: BLE001
            msg = f"Ignoring undecodable discovery datagram from {addr}: {e}"
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
                            "MainboardID": f"{ip}:{ws_port}",  # Use IP:port as ID
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
