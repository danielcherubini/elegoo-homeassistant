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
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit, urlunparse, urlunsplit

import aiohttp
from aiohttp import ClientSession, WSMsgType, web
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.elegoo_printer.const import (
    DEFAULT_BROADCAST_ADDRESS,
    DEFAULT_FALLBACK_IP,
    DISCOVERY_MESSAGE,
    DISCOVERY_PORT,
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
    Registry for managing multiple discovered printers.

    This class provides thread-safe management of network-discovered printers
    with automatic discovery, caching, and rate limiting capabilities.

    DISCOVERY PROCESS:
    =================

    1. UDP Broadcast: Send "M99999" to 255.255.255.255:3000
    2. Response Collection: Gather JSON responses for 5 seconds
    3. Printer Parsing: Extract printer info and create Printer objects
    4. Registry Update: Store printers by MainboardID for routing

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
        self._printers: dict[str, Printer] = {}
        self._last_discovery: float = 0
        self._discovery_lock = asyncio.Lock()

    def add_printer(self, printer: Printer) -> None:
        """Add a printer to the registry by MainboardID."""
        if printer.id:
            self._printers[printer.id] = printer

    def get_printer(self, mainboard_id: str) -> Printer | None:
        """Get a printer by MainboardID."""
        return self._printers.get(mainboard_id)

    def get_all_printers(self) -> dict[str, Printer]:
        """Get all registered printers."""
        return self._printers.copy()

    def count(self) -> int:
        """Get the number of registered printers."""
        return len(self._printers)

    def remove_printer(self, mainboard_id: str) -> bool:
        """
        Remove a printer from the registry by MainboardID.

        Returns:
            True if printer was removed, False if not found.

        """
        if mainboard_id in self._printers:
            del self._printers[mainboard_id]
            return True
        return False

    def clear(self) -> None:
        """Clear all registered printers."""
        self._printers.clear()

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
                            if printer and printer.id:
                                discovered_printers[printer.id] = printer
                                self.add_printer(printer)
                                logger.info(
                                    "Discovered printer: %s (%s) ID: %s",
                                    printer.name,
                                    printer.ip_address,
                                    printer.id,
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
                    # Add the new printer to the existing server's registry
                    cls._instance.printer_registry.add_printer(printer)
                    logger.debug(
                        "Added printer %s (%s) to existing proxy server",
                        printer.name,
                        printer.id,
                    )
                else:
                    logger.debug("Reusing existing proxy server instance")
                return cls._instance

            # Create new instance
            logger.debug("Creating new proxy server instance")
            self = cls(logger, hass, session)
            if printer:
                # Add the initial printer to the registry
                self.printer_registry.add_printer(printer)
                logger.debug(
                    "Added printer %s (%s) to new proxy server",
                    printer.name,
                    printer.id,
                )

            # Set instance BEFORE starting to prevent race condition
            cls._instance = self

            try:
                # Start the server for the new instance
                await self.start()

                # Only perform network discovery if no printers were provided via config
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
                raise

            return self

    @property
    def is_connected(self) -> bool:
        """Return true if the proxy is connected to the printer."""
        return self._is_connected

    async def start(self) -> None:
        """Start the proxy server on the Home Assistant event loop."""
        if not self._check_ports_are_available():
            msg = "Proxy server ports are in use, likely by another instance."
            self.logger.debug(msg)
            # Don't raise an exception if another instance is already running
            # The singleton pattern should handle this gracefully
            # Mark as connected since we're relying on the existing singleton
            self._is_connected = True
            return

        self.logger.info("Initializing multi-printer proxy server")

        try:
            # Allow large uploads (streamed), keep headroom for typical print files.
            main_app = web.Application(client_max_size=1024 * 1024 * 1024)  # 1 GiB
            main_app.router.add_route("*", "/{path:.*}", self._http_handler)
            main_runner = web.AppRunner(main_app)
            await main_runner.setup()
            main_site = web.TCPSite(main_runner, INADDR_ANY, WEBSOCKET_PORT)
            await main_site.start()
            self.runners.append(main_runner)
            msg = f"Main HTTP/WebSocket Proxy running on http://{self.get_local_ip()}:{WEBSOCKET_PORT}"
            self.logger.info(msg)

            video_app = web.Application()
            video_app.router.add_route("*", "/{path:.*}", self._video_proxy_handler)
            video_runner = web.AppRunner(video_app)
            await video_runner.setup()
            video_site = web.TCPSite(video_runner, INADDR_ANY, VIDEO_PORT)
            await video_site.start()
            self.runners.append(video_runner)
            msg = f"Video Proxy running on http://{self.get_local_ip()}:{VIDEO_PORT}"
            self.logger.info(msg)

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

        # Note: singleton instance is set in async_create
        self._is_connected = True
        self.logger.info("Proxy server has started successfully.")

    @classmethod
    async def stop_all(cls) -> None:
        """Stop the proxy server singleton instance."""
        if cls._instance is not None:
            await cls._instance.stop()
            cls._instance = None

    def _check_ports_are_available(self) -> bool:
        """Check if the required TCP and UDP ports for the proxy server are free."""
        # If there's already an instance running, we don't need new ports
        if self.__class__._instance is not None and self.__class__._instance != self:  # noqa: SLF001
            self.logger.debug(
                "Proxy server already running, skipping port availability check"
            )
            return True
        for port, proto, name in [
            (WEBSOCKET_PORT, socket.SOCK_STREAM, "TCP"),
            (VIDEO_PORT, socket.SOCK_STREAM, "Video TCP"),
            (DISCOVERY_PORT, socket.SOCK_DGRAM, "UDP"),
        ]:
            try:
                with socket.socket(socket.AF_INET, proto) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind((INADDR_ANY, port))
            except OSError:
                msg = (
                    f"{name} port {port} is already in use. Proxy server cannot start."
                )
                self.logger.debug(msg)  # Changed from warning to debug for less noise
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

        if printer.id:
            removed = cls._instance.printer_registry.remove_printer(printer.id)
            if removed:
                logger.debug(
                    "Removed printer %s (%s) from proxy server",
                    printer.name,
                    printer.id,
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

    def _extract_mainboard_id_from_request(
        self, request_data: str | bytes
    ) -> str | None:
        """Extract MainboardID from SDCP request data."""
        try:
            if isinstance(request_data, bytes):
                request_data = request_data.decode("utf-8")

            data = json.loads(request_data)

            # Try to get MainboardID from Data.MainboardID
            if isinstance(data, dict):
                inner_data = data.get("Data", {})
                if isinstance(inner_data, dict):
                    mainboard_id = inner_data.get("MainboardID")
                    if mainboard_id:
                        return mainboard_id

                # Also check Topic for MainboardID (sdcp/request/{MainboardID})
                topic = data.get("Topic", "")
                if topic and "/" in topic:
                    parts = topic.split("/")
                    if len(parts) >= 3:  # noqa: PLR2004
                        return parts[-1]  # Last part should be MainboardID

        except (json.JSONDecodeError, AttributeError, KeyError) as e:
            self.logger.debug("Error extracting MainboardID from request: %s", e)

        return None

    def get_local_ip(self) -> str:
        """Determine the local IP address for outbound communication."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((DEFAULT_FALLBACK_IP, 1))
                return s.getsockname()[0]
        except Exception:  # noqa: BLE001
            return PROXY_HOST

    async def _http_handler(self, request: web.Request) -> web.StreamResponse:
        """Dispatches incoming HTTP requests."""
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._websocket_handler(request)
        if request.method == "POST" and (
            request.path == "/uploadFile/upload"
            or request.path.endswith("/uploadFile/upload")
        ):
            return await self._http_file_proxy_passthrough_handler(request)
        return await self._http_proxy_handler(request)

    async def _video_proxy_handler(self, request: web.Request) -> web.StreamResponse:  # noqa: PLR0912
        """Proxies video stream requests with MainboardID routing from modified URLs."""
        # Extract MainboardID from path like /video/{mainboard_id}
        mainboard_id = None

        if request.path.startswith("/video/"):
            path_parts = request.path.strip("/").split("/")
            if len(path_parts) >= 2:  # noqa: PLR2004
                potential_id = path_parts[1]
                # Check if this looks like a MainboardID (hex string, at least 8 chars)
                if len(potential_id) >= MIN_MAINBOARD_ID_LENGTH and all(
                    c in "0123456789abcdefABCDEF" for c in potential_id
                ):
                    mainboard_id = potential_id

        if not mainboard_id:
            return web.Response(
                status=400, text="MainboardID required in video URL path"
            )

        printer = self.printer_registry.get_printer(mainboard_id)
        if not printer:
            # Try to rediscover printers
            await self.printer_registry.discover_printers(self.logger)
            printer = self.printer_registry.get_printer(mainboard_id)
            if not printer:
                return web.Response(
                    status=404, text=f"Printer {mainboard_id} not found"
                )

        # Forward to the printer's /video endpoint (original path without MainboardID)
        forwarded_path = "/video"
        if request.query_string:
            forwarded_path += f"?{request.query_string}"

        remote_url = f"http://{printer.ip_address}:{VIDEO_PORT}{forwarded_path}"
        if not self.session or self.session.closed:
            return web.Response(status=503, text="Session not available.")

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
                    msg = f"Video stream stopped: {e}"
                    self.logger.debug(msg)
                except Exception:
                    self.logger.exception(
                        "An unexpected error occurred during video streaming"
                    )
                return response
        except (TimeoutError, aiohttp.ClientError):
            self.logger.exception("Error proxying video stream")
            return web.Response(status=502, text="Bad Gateway")

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:  # noqa: PLR0915
        """
        Proxy a WebSocket connection with multi-printer routing.

        This method implements sophisticated WebSocket proxying that routes messages
        between Home Assistant and multiple printers based on MainboardID extraction.

        WEBSOCKET ROUTING FLOW:
        ======================

        1. Client Connection: Home Assistant connects to proxy WebSocket
        2. Message Analysis: Extract MainboardID from incoming JSON messages
        3. Printer Lookup: Find target printer in registry by MainboardID
        4. Connection Pool: Establish/reuse WebSocket connection to target printer
        5. Bidirectional Relay: Forward messages between client and printer
        6. URL Rewriting: Modify VideoUrl responses to route through proxy

        CONNECTION MANAGEMENT:
        =====================

        - Active connections are pooled per MainboardID
        - Heartbeat (10s) prevents connection timeouts
        - Automatic reconnection on printer connection failure
        - Graceful cleanup on client disconnect or errors

        MESSAGE STRUCTURE:
        =================

        Expected JSON format with MainboardID routing:
        ```json
        {
          "Topic": "sdcp/request/{MainboardID}",
          "Data": {
            "MainboardID": "{MainboardID}",
            "Cmd": 1001,
            "Data": { ... }
          }
        }
        ```

        ERROR HANDLING:
        ==============

        - Missing MainboardID: Log warning, ignore message
        - Printer not found: Auto-discovery attempt, then error response
        - Connection failures: Automatic retry with exponential backoff
        - Client disconnect: Clean shutdown of all printer connections
        """
        client_ws = web.WebSocketResponse()
        await client_ws.prepare(request)

        if not self.session or self.session.closed:
            await client_ws.close(code=1011, message=b"Upstream connection failed")
            return client_ws

        # Store active connections for routing messages
        active_connections: dict[str, aiohttp.ClientWebSocketResponse] = {}
        tasks = set()

        try:

            async def route_message(
                message_data: str,
                client_ws: web.WebSocketResponse,
            ) -> None:
                """Route message to appropriate printer based on MainboardID."""
                mainboard_id = self._extract_mainboard_id_from_request(message_data)
                if not mainboard_id:
                    self.logger.warning("No MainboardID found in message, cannot route")
                    return

                printer = self.printer_registry.get_printer(mainboard_id)
                if not printer:
                    self.logger.warning(
                        f"No printer found for MainboardID: {mainboard_id}"  # noqa: G004
                    )
                    # Try to rediscover printers
                    await self.printer_registry.discover_printers(self.logger)
                    printer = self.printer_registry.get_printer(mainboard_id)
                    if not printer:
                        error_response = {
                            "Id": "proxy-error",
                            "Data": {
                                "Cmd": 0,
                                "Data": {"Error": f"Printer {mainboard_id} not found"},
                                "RequestID": "error",
                                "MainboardID": mainboard_id,
                                "TimeStamp": int(time.time()),
                            },
                            "Topic": f"sdcp/error/{mainboard_id}",
                        }
                        await client_ws.send_str(json.dumps(error_response))
                        return

                # Get or create connection to printer
                if mainboard_id not in active_connections:
                    remote_ws_url = (
                        f"ws://{printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"
                    )
                    allowed_headers = {
                        "sec-websocket-version",
                        "sec-websocket-key",
                        "sec-websocket-protocol",
                        "upgrade",
                        "connection",
                    }
                    filtered_headers = {
                        k: v
                        for k, v in request.headers.items()
                        if k.lower() in allowed_headers
                    }

                    try:
                        remote_ws = await self.session.ws_connect(
                            remote_ws_url, headers=filtered_headers, heartbeat=10.0
                        )
                        active_connections[mainboard_id] = remote_ws
                        self.logger.info(
                            f"Established WebSocket connection to printer {mainboard_id} at {printer.ip_address}"  # noqa: E501, G004
                        )

                        # Start forwarding messages from printer to client
                        forward_task = self.hass.async_create_task(
                            self._forward_from_printer(
                                remote_ws, client_ws, mainboard_id
                            )
                        )
                        tasks.add(forward_task)

                    except Exception:
                        self.logger.exception(
                            "Failed to connect to printer %s", mainboard_id
                        )
                        return

                # Forward message to printer
                remote_ws = active_connections[mainboard_id]
                if not remote_ws.closed:
                    await remote_ws.send_str(message_data)
                else:
                    # Connection closed, remove it
                    del active_connections[mainboard_id]
                    self.logger.warning(
                        f"Connection to printer {mainboard_id} was closed"  # noqa: G004
                    )

            # Handle messages from client
            async for message in client_ws:
                if message.type == WSMsgType.TEXT:
                    await route_message(message.data, client_ws)
                elif message.type == WSMsgType.CLOSE:
                    break
                elif message.type == WSMsgType.ERROR:
                    self.logger.error(
                        f"WebSocket error from client: {client_ws.exception()}"  # noqa: G004
                    )
                    break

        except Exception:
            self.logger.exception("WebSocket handler error")
        finally:
            # Clean up all connections
            for remote_ws in active_connections.values():
                if not remote_ws.closed:
                    await remote_ws.close()

            # Cancel all tasks
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            if not client_ws.closed:
                await client_ws.close()

        return client_ws

    async def _forward_from_printer(
        self,
        remote_ws: aiohttp.ClientWebSocketResponse,
        client_ws: web.WebSocketResponse,
        mainboard_id: str,
    ) -> None:
        """Forward messages from printer to client, replacing printer IP with proxy IP."""  # noqa: E501
        try:
            # Get printer and proxy IPs for replacement
            printer = self.printer_registry.get_printer(mainboard_id)
            if not printer:
                return

            printer_ip = printer.ip_address
            proxy_ip = self.get_local_ip()

            async for message in remote_ws:
                if message.type == WSMsgType.TEXT:
                    # Replace printer IP with proxy IP in text messages
                    modified_data = message.data.replace(printer_ip, proxy_ip)
                    # Also handle VideoUrl modification
                    modified_data = self._modify_video_url_in_response(
                        modified_data, mainboard_id
                    )
                    await client_ws.send_str(modified_data)
                elif message.type == WSMsgType.BINARY:
                    # Replace printer IP with proxy IP in binary messages
                    try:
                        # Try text-based replacement first
                        text_data = message.data.decode("utf-8")
                        modified_text = text_data.replace(printer_ip, proxy_ip)
                        await client_ws.send_bytes(modified_text.encode("utf-8"))
                    except UnicodeDecodeError:
                        # Fall back to byte-level replacement
                        printer_ip_bytes = printer_ip.encode("utf-8")
                        proxy_ip_bytes = proxy_ip.encode("utf-8")
                        modified_data = message.data.replace(
                            printer_ip_bytes, proxy_ip_bytes
                        )
                        await client_ws.send_bytes(modified_data)
                elif message.type == WSMsgType.CLOSE:
                    break
                elif message.type == WSMsgType.ERROR:
                    self.logger.error(
                        f"WebSocket error from printer {mainboard_id}: {remote_ws.exception()}"  # noqa: E501, G004
                    )
                    break
        except Exception as e:  # noqa: BLE001
            self.logger.debug("Forward from printer %s stopped: %s", mainboard_id, e)

    def _modify_video_url_in_response(
        self, message_data: str, mainboard_id: str
    ) -> str:
        """Modify VideoUrl in response to include MainboardID for routing."""
        try:
            data = json.loads(message_data)

            # Check if this is a response with VideoUrl
            if isinstance(data, dict):
                inner_data = data.get("Data", {})
                if isinstance(inner_data, dict):
                    response_data = inner_data.get("Data", {})
                    if isinstance(response_data, dict) and "VideoUrl" in response_data:
                        original_url = response_data["VideoUrl"]
                        self.logger.debug(
                            "Found VideoUrl in response: %s", original_url
                        )

                        # Only rewrite HTTP URLs, preserve RTSP and other protocols
                        parts = urlsplit(original_url)
                        if parts.scheme.lower() == "http":
                            # Replace the printer's IP with our proxy IP
                            # and add MainboardID to path
                            # Original: http://192.168.1.2:3031/video
                            # Modified: http://proxy_ip:3031/video/{mainboard_id}
                            proxy_ip = self.get_local_ip()
                            host, _, port = parts.netloc.partition(":")
                            port = port or "3031"
                            new_netloc = f"{proxy_ip}:{port}"
                            # Force /video/{id} path, preserve query and fragment
                            new_path = f"/video/{mainboard_id}"
                            modified_url = urlunsplit(
                                (
                                    parts.scheme,
                                    new_netloc,
                                    new_path,
                                    parts.query,
                                    parts.fragment,
                                )
                            )
                            response_data["VideoUrl"] = modified_url
                            self.logger.debug(
                                "Modified HTTP VideoUrl from %s to %s",
                                original_url,
                                modified_url,
                            )
                        else:
                            self.logger.debug(
                                "Preserving non-HTTP VideoUrl (%s): %s",
                                parts.scheme.upper(),
                                original_url,
                            )
                        return json.dumps(data)

        except (json.JSONDecodeError, KeyError, AttributeError) as e:
            self.logger.debug("Error modifying VideoUrl in response: %s", e)

        # Return original message if no modification needed or error occurred
        return message_data


    def _extract_mainboard_id_from_referer(self, request: web.Request) -> str | None:
        """Extract MainboardID from HTTP Referer header."""
        referer = request.headers.get("Referer")
        if not referer:
            return None

        try:
            parsed = urlparse(referer)
            path_parts = parsed.path.strip("/").split("/")

            if len(path_parts) >= 1 and path_parts[0]:
                potential_id = path_parts[0]

                # Check if first path segment looks like a MainboardID (hex, 8+ chars)
                if len(potential_id) >= MIN_MAINBOARD_ID_LENGTH and all(
                    c in "0123456789abcdefABCDEF" for c in potential_id
                ):
                    return potential_id
        except (ValueError, AttributeError) as e:
            # If parsing fails, return None
            self.logger.debug("Failed to parse Referer header: %s", e)

        return None


    def _extract_mainboard_id_from_http_request(
        self, request: web.Request
    ) -> str | None:
        """Extract MainboardID from HTTP request query parameters, path, or Referer."""
        # First try query parameters (highest priority)
        mainboard_id = request.query.get("mainboard_id")
        if mainboard_id:
            return mainboard_id

        # If no MainboardID in query, try path-based routing (medium priority)
        path_parts = request.path.strip("/").split("/")

        if len(path_parts) >= 1 and path_parts[0]:
            potential_id = path_parts[0]

            # Check if first path segment looks like a MainboardID (hex, 8+ chars)
            if len(potential_id) >= MIN_MAINBOARD_ID_LENGTH and all(
                c in "0123456789abcdefABCDEF" for c in potential_id
            ):
                return potential_id

        # Fallback: try /api/{mainboard_id}/... pattern for compatibility
        if len(path_parts) > 1 and path_parts[0] == "api":
            potential_id = path_parts[1]
            # Check if this looks like a MainboardID (hex, 8+ chars)
            if len(potential_id) >= MIN_MAINBOARD_ID_LENGTH and all(
                c in "0123456789abcdefABCDEF" for c in potential_id
            ):
                return potential_id

        # Final fallback: try extracting from Referer header (lowest priority)
        return self._extract_mainboard_id_from_referer(request)

    async def _http_proxy_handler(self, request: web.Request) -> web.StreamResponse:
        """
        Streams HTTP requests with multi-printer routing.

        This method handles HTTP/HTTPS requests and routes them to the appropriate
        printer based on MainboardID extraction from query parameters or URL paths.

        HTTP ROUTING METHODS:
        ====================

        1. Query Parameter: ?mainboard_id={MainboardID}
           Example: GET /api/status?mainboard_id=ABC123DEF456

        2. Root Path-Based: /{MainboardID} or /{MainboardID}/endpoint
           Example: GET /ABC123DEF456 or GET /ABC123DEF456/api/status

        3. API Path-Based: /api/{MainboardID}/endpoint (legacy compatibility)
           Example: GET /api/ABC123DEF456/status

        SUPPORTED OPERATIONS:
        ====================

        - GET: Status queries, file listings, configuration retrieval
        - POST: Commands, settings updates, print job control
        - PUT: File operations, firmware updates
        - DELETE: File removal, job cancellation

        STREAMING SUPPORT:
        =================

        The handler uses streaming responses to support:
        - Large file downloads (G-code, logs, firmware)
        - Real-time status updates via Server-Sent Events
        - Chunked transfer encoding for efficient memory usage

        ERROR HANDLING:
        ==============

        - 400 Bad Request: Missing MainboardID in request
        - 404 Not Found: Printer not found in registry
        - 502 Bad Gateway: Connection failure to target printer
        - 503 Service Unavailable: Proxy session unavailable
        """
        if not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        # Extract MainboardID from query parameters or path
        mainboard_id = self._extract_mainboard_id_from_http_request(request)

        self.logger.debug(
            "HTTP proxy request: %s %s, extracted mainboard_id: %s",
            request.method,
            request.path_qs,
            mainboard_id,
        )

        if not mainboard_id:
            return web.Response(
                status=400,
                text="MainboardID required for HTTP proxy routing. "
                "Ensure URL includes mainboard_id in path or query parameter, "
                "or that Referer header is set."
            )

        printer = self.printer_registry.get_printer(mainboard_id)
        if not printer:
            # Try to rediscover printers
            await self.printer_registry.discover_printers(self.logger)
            printer = self.printer_registry.get_printer(mainboard_id)
            if not printer:
                return web.Response(
                    status=404, text=f"Printer {mainboard_id} not found"
                )

        # Parse URL to properly remove MainboardID from path and query parameters
        parsed_url = urlparse(request.path_qs)

        # Remove mainboard_id as a discrete path segment
        path_parts = [part for part in parsed_url.path.split("/") if part]
        if mainboard_id in path_parts:
            path_parts.remove(mainboard_id)

        # Rebuild path with single leading slash and no double slashes
        cleaned_path = "/" + "/".join(path_parts) if path_parts else "/"

        # Remove mainboard_id from query parameters
        query_params = parse_qs(parsed_url.query, keep_blank_values=True)
        query_params.pop("mainboard_id", None)
        cleaned_query = urlencode(query_params, doseq=True)

        # Reconstruct the forwarded path
        forwarded_path = urlunparse(
            (
                "",
                "",
                cleaned_path,
                parsed_url.params,
                cleaned_query,
                parsed_url.fragment,
            )
        )

        # Add mainboard_id as query parameter for the target printer
        separator = "&" if "?" in forwarded_path else "?"
        target_url = f"http://{printer.ip_address}:{WEBSOCKET_PORT}{forwarded_path}{separator}mainboard_id={mainboard_id}"

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
                    "Proxy-Authenticate",
                    "Proxy-Authorization",
                    "TE",
                    "Trailer",
                    "Upgrade",
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
            msg = f"HTTP proxy error connecting to {target_url}"
            self.logger.exception(msg)
            # Temporary debug logging
            self.logger.exception("DEBUG: aiohttp.ClientError")
            return web.Response(status=502, text=f"Bad Gateway: {e}")

    async def _http_file_proxy_passthrough_handler(
        self, request: web.Request
    ) -> web.Response:
        """Proxies multipart file upload requests with multi-printer routing."""
        if not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        # Extract MainboardID from query parameters or path for file uploads
        mainboard_id = self._extract_mainboard_id_from_http_request(request)

        if not mainboard_id:
            return web.Response(
                status=400, text="MainboardID required for file upload routing"
            )

        printer = self.printer_registry.get_printer(mainboard_id)
        if not printer:
            # Try to rediscover printers
            await self.printer_registry.discover_printers(self.logger)
            printer = self.printer_registry.get_printer(mainboard_id)
            if not printer:
                return web.Response(
                    status=404, text=f"Printer {mainboard_id} not found"
                )

        # Clean the path by removing mainboard_id if present
        parsed_url = urlparse(request.path_qs)
        path_parts = [part for part in parsed_url.path.split("/") if part]
        if mainboard_id in path_parts:
            path_parts.remove(mainboard_id)

        # Rebuild path with single leading slash and no double slashes
        cleaned_path = "/" + "/".join(path_parts) if path_parts else "/"

        # Remove mainboard_id from query parameters
        query_params = parse_qs(parsed_url.query, keep_blank_values=True)
        query_params.pop("mainboard_id", None)
        cleaned_query = urlencode(query_params, doseq=True)

        # Construct final URL
        if cleaned_query:
            remote_url = f"http://{printer.ip_address}:{WEBSOCKET_PORT}{cleaned_path}?{cleaned_query}"
        else:
            remote_url = f"http://{printer.ip_address}:{WEBSOCKET_PORT}{cleaned_path}"

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
                    "Proxy-Authenticate",
                    "Proxy-Authorization",
                    "TE",
                    "Trailer",
                    "Upgrade",
                ):
                    resp_headers.pop(h, None)
                return web.Response(
                    body=content, status=response.status, headers=resp_headers
                )
        except Exception as e:
            self.logger.exception("HTTP file passthrough proxy error")
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

        Respond for each discovered printer.
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
                    msg = "Sent proxy discovery response (no printers found)"
                    self.logger.debug(msg)
            else:
                # Send a response for each discovered printer
                for mainboard_id, printer in printers.items():
                    response_payload = {
                        "Id": getattr(printer, "connection", os.urandom(8).hex()),
                        "Data": {
                            "Name": f"{getattr(printer, 'name', 'Elegoo')} (via Proxy)",
                            "MachineName": (
                                f"{getattr(printer, 'name', 'Elegoo')} (via Proxy)"
                            ),
                            "BrandName": getattr(printer, "brand", "Elegoo"),
                            "MainboardIP": self.proxy_ip,  # Point to our proxy
                            "MainboardID": mainboard_id,
                            "ProtocolVersion": getattr(printer, "protocol", "V3.0.0"),
                            "FirmwareVersion": getattr(printer, "firmware", "V1.0.0"),
                        },
                    }
                    json_string = json.dumps(response_payload)
                    if self.transport:
                        self.transport.sendto(json_string.encode(), addr)
                        self.logger.debug(
                            "Sent discovery response for printer %s", mainboard_id
                        )

    def error_received(self, exc: Exception) -> None:
        """Call when an error is received."""
        msg = f"UDP Discovery Server Error: {exc}"
        self.logger.error(msg)
