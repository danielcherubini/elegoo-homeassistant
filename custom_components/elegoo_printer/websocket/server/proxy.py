"""
Main Proxy Server for the Elegoo Printer Multi-Printer Gateway.

This module contains the core ElegooPrinterServer class and all HTTP/WebSocket
routing logic for the centralized proxy server.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
from math import floor
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp
from aiohttp import ClientResponse, ClientSession, WSMsgType, web
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.elegoo_printer.const import (
    DEFAULT_FALLBACK_IP,
    DISCOVERY_PORT,
    LOGGER,
    VIDEO_PORT,
    WEBSOCKET_PORT,
)

from .discovery import DiscoveryProtocol
from .registry import PrinterRegistry
from .utils import (
    CACHEABLE_MIME_TYPES,
    INADDR_ANY,
    MAX_LOG_LENGTH,
    MIN_API_PATH_PARTS,
    MIN_MAINBOARD_ID_LENGTH,
    MIN_PATH_PARTS_FOR_FALLBACK,
    MIN_VIDEO_PATH_PARTS,
    TRANSFORMABLE_MIME_TYPES,
    extract_mainboard_id_from_header,
    extract_mainboard_id_from_topic,
    get_local_ip,
    get_request_headers,
    get_response_headers,
    set_caching_headers,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.elegoo_printer.sdcp.models.printer import Printer


class ElegooPrinterServer:
    """
    Centralized proxy server for multiple Elegoo 3D printers.

    This server acts as a single entry point for multiple printers, routing
    requests based on MainboardID and providing seamless protocol translation.
    """

    _instance: ElegooPrinterServer | None = None
    _reference_count: int = 0
    _creation_lock = asyncio.Lock()

    def __init__(
        self,
        logger: Any,
        hass: HomeAssistant,
        printer: Printer | None = None,  # noqa: ARG002
    ) -> None:
        """Initialize the proxy server."""
        self.logger = logger
        self.hass = hass
        # Three dedicated sessions for different use cases
        self.api_session: ClientSession | None = None  # API calls & WebSocket
        self.video_session: ClientSession | None = None  # Video streaming
        self.file_session: ClientSession | None = None  # File transfers
        self.runners: list[web.AppRunner] = []
        self._is_connected = False
        self.datagram_transport: asyncio.DatagramTransport | None = None
        self.printer_registry = PrinterRegistry()

    @classmethod
    def get_next_available_ports(cls) -> tuple[int, int]:
        """
        Get the centralized proxy ports.

        Since we use centralized routing, all printers use the same proxy ports.

        Returns:
            Tuple of (websocket_port, video_port) for the centralized proxy.

        """
        return (WEBSOCKET_PORT, VIDEO_PORT)

    @classmethod
    async def async_create(
        cls,
        logger: Any,
        hass: HomeAssistant,
        printer: Printer | None = None,
    ) -> ElegooPrinterServer:
        """Asynchronously creates and starts the multi-printer server (singleton)."""
        async with cls._creation_lock:
            # Return existing instance if already created (check again inside the lock)
            if cls._instance is not None:
                cls._reference_count += 1
                logger.debug(
                    "Returning existing proxy server instance (references: %d)",
                    cls._reference_count,
                )

                # If we have a printer, register it with the existing server
                if printer:
                    cls._instance.printer_registry.add_printer(printer)
                    logger.debug(
                        "Registered printer %s with existing proxy server",
                        printer.ip_address,
                    )

                return cls._instance

            # Create new instance
            cls._instance = cls(logger, hass, printer)
            cls._reference_count = 1

            logger.debug(
                "Creating new proxy server instance (references: %d)",
                cls._reference_count,
            )

            if printer:
                cls._instance.printer_registry.add_printer(printer)
                logger.debug(
                    "Registered printer %s with new proxy server",
                    printer.ip_address,
                )

            # Start the server
            await cls._instance.start()
            return cls._instance

    @property
    def is_connected(self) -> bool:
        """Return True if the proxy server is connected and running."""
        return self._is_connected

    def get_local_ip(self) -> str:
        """Get the local IP address for the proxy server."""
        return get_local_ip()

    def get_printer(self, specific_printer: Printer | None = None) -> Printer:
        """
        Get a printer instance for API operations.

        Args:
            specific_printer: If provided, returns this printer if it's registered

        Returns:
            A registered printer instance

        Raises:
            ConfigEntryNotReady: If no printers are available

        """
        if specific_printer:
            registered = self.printer_registry.get_printer_by_ip(
                specific_printer.ip_address
            )
            if registered:
                return registered

        # Return any available printer
        printers = self.printer_registry.get_all_printers()
        if printers:
            return next(iter(printers.values()))

        msg = "No printers available in proxy server"
        raise ConfigEntryNotReady(msg)

    def _raise_port_error(self) -> None:
        """Raise an OSError for port availability issues."""
        msg = f"Ports {WEBSOCKET_PORT} or {VIDEO_PORT} are already in use"
        raise OSError(msg)

    async def start(self) -> None:
        """Start the centralized proxy server."""
        try:
            if not self._check_ports_are_available():
                self._raise_port_error()

            # Create three dedicated sessions optimized for different use cases

            # API Session: Quick API calls and WebSocket upgrades
            api_connector = aiohttp.TCPConnector(
                limit=50,  # Moderate connection pool
                limit_per_host=10,  # Conservative per-host limit
                ttl_dns_cache=300,
                use_dns_cache=True,
                enable_cleanup_closed=True,
            )
            api_timeout = aiohttp.ClientTimeout(total=30, sock_read=10)
            self.api_session = aiohttp.ClientSession(
                connector=api_connector,
                timeout=api_timeout,
                headers={"User-Agent": "ElegooProxy-API/1.0"},
            )

            # Video Session: Optimized for streaming
            video_connector = aiohttp.TCPConnector(
                limit=20,  # Fewer total connections
                limit_per_host=5,  # Limited concurrent streams per printer
                ttl_dns_cache=300,
                use_dns_cache=True,
                enable_cleanup_closed=True,
            )
            video_timeout = aiohttp.ClientTimeout(
                total=None,  # No total timeout for streams
                sock_connect=10,  # Quick connection
                sock_read=None,  # No read timeout for streaming
            )
            self.video_session = aiohttp.ClientSession(
                connector=video_connector,
                timeout=video_timeout,
                headers={"User-Agent": "ElegooProxy-Video/1.0"},
            )

            # File Session: Optimized for large transfers
            file_connector = aiohttp.TCPConnector(
                limit=10,  # Very few connections
                limit_per_host=2,  # Only 2 concurrent file ops per printer
                ttl_dns_cache=300,
                use_dns_cache=True,
                enable_cleanup_closed=True,
            )
            file_timeout = aiohttp.ClientTimeout(
                total=600,  # 10 minute total timeout
                sock_connect=30,  # Longer connection timeout
                sock_read=300,  # 5 minute read timeout for large files
            )
            self.file_session = aiohttp.ClientSession(
                connector=file_connector,
                timeout=file_timeout,
                headers={"User-Agent": "ElegooProxy-File/1.0"},
            )

            self.logger.debug("Created dedicated proxy sessions: API, Video, File")

            # Start centralized HTTP/WebSocket server
            http_app = web.Application(client_max_size=1024 * 1024 * 1024)  # 1 GiB
            http_app.router.add_route("*", "/{path:.*}", self._centralized_http_handler)
            http_runner = web.AppRunner(http_app)
            await http_runner.setup()
            http_site = web.TCPSite(http_runner, INADDR_ANY, WEBSOCKET_PORT)
            await http_site.start()
            self.runners.append(http_runner)

            msg = f"Centralized HTTP/WebSocket Proxy running on http://{get_local_ip()}:{WEBSOCKET_PORT}"
            self.logger.info(msg)

            # Start centralized video server
            video_app = web.Application()
            video_app.router.add_route(
                "*", "/{path:.*}", self._centralized_video_handler
            )
            video_runner = web.AppRunner(video_app)
            await video_runner.setup()
            video_site = web.TCPSite(video_runner, INADDR_ANY, VIDEO_PORT)
            await video_site.start()
            self.runners.append(video_runner)

            msg = f"Centralized Video Proxy running on http://{get_local_ip()}:{VIDEO_PORT}"
            self.logger.info(msg)

            # Start UDP discovery server
            def discovery_factory() -> DiscoveryProtocol:
                return DiscoveryProtocol(
                    self.logger, self.printer_registry, get_local_ip()
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
            raise

        self._is_connected = True
        self.logger.info("Centralized proxy server started successfully.")

    def _check_ports_are_available(self) -> bool:
        """Check if the required ports are available."""
        for port, name in [(WEBSOCKET_PORT, "HTTP/WebSocket"), (VIDEO_PORT, "Video")]:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind((INADDR_ANY, port))
            except OSError:
                self.logger.exception("%s port %d is already in use", name, port)
                return False
        return True

    async def stop(self) -> None:
        """Stop the proxy server and cleanup resources."""
        self._is_connected = False

        # Stop all HTTP runners
        for runner in self.runners:
            try:
                await runner.cleanup()
            except Exception as e:  # noqa: BLE001
                self.logger.debug("Error cleaning up runner: %s", e)
        self.runners.clear()

        # Stop UDP discovery server
        if self.datagram_transport:
            self.datagram_transport.close()
            self.datagram_transport = None

        # Close all dedicated sessions
        sessions_to_close = [
            ("API", self.api_session),
            ("Video", self.video_session),
            ("File", self.file_session),
        ]

        for session_name, session in sessions_to_close:
            if session and not session.closed:
                try:
                    await session.close()
                    self.logger.debug("Closed dedicated %s session", session_name)
                except (RuntimeError, OSError) as e:
                    self.logger.warning("Error closing %s session: %s", session_name, e)

        self.api_session = None
        self.video_session = None
        self.file_session = None

        # Give time for ports to actually be released by the OS
        await asyncio.sleep(0.5)

        # Force cleanup any lingering connections
        await self._force_cleanup_ports(self.logger)
        self.logger.debug("Proxy server stopped")

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
                    s.bind((INADDR_ANY, port))
                    logger.debug("Port %s successfully cleaned up", port)
            except OSError:
                try:
                    # Try connecting to see if something is actually there
                    with socket.socket(socket.AF_INET, proto) as test_s:
                        if proto == socket.SOCK_STREAM:
                            test_s.connect((INADDR_ANY, port))
                        logger.debug("Port %s still in use after cleanup", port)
                except OSError as e:
                    logger.debug("Error during port %s cleanup: %s", port, e)

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
                    LOGGER.debug("No more references, stopping proxy server")
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
    async def remove_printer_from_server(cls, printer: Printer, logger: Any) -> bool:
        """
        Remove a printer from the server registry.

        Returns:
            True if server should be stopped (no more printers), False if server
            should continue.

        """
        if cls._instance is None:
            return False  # No server to remove from

        removed = cls._instance.printer_registry.remove_printer(printer.ip_address)
        if removed:
            logger.debug(
                "Removed printer %s from proxy server registry", printer.ip_address
            )
        else:
            logger.warning(
                "Printer %s not found in proxy server registry", printer.ip_address
            )

        # Check if we should stop the server (no more printers)
        remaining_count = cls._instance.printer_registry.count()
        if remaining_count == 0:
            logger.debug("No more printers in registry, server should be stopped")
            return True

        logger.debug(
            "Proxy server still managing %d printer(s), keeping alive", remaining_count
        )
        return False

    async def _centralized_video_handler(self, request: web.Request) -> web.Response:
        """Handle video requests by routing to appropriate printer via proxy."""
        if not self.video_session or self.video_session.closed:
            return web.Response(status=503, text="Video session not available.")

        # Find target printer using MainboardID routing
        printer = self._get_target_printer_from_request(request)
        if not printer:
            return web.Response(status=404, text="Printer not found")

        # Clean and forward to printer's video endpoint
        cleaned_path = self._get_cleaned_path_for_printer(request.path)
        q = [
            (k, v)
            for k, v in parse_qsl(request.query_string, keep_blank_values=True)
            if k.lower() not in ("id", "mainboard_id")
        ]
        query_string = f"?{urlencode(q, doseq=True)}" if q else ""
        remote_url = (
            f"http://{printer.ip_address}:{VIDEO_PORT}{cleaned_path}{query_string}"
        )

        try:
            async with self.video_session.get(
                remote_url,
                headers=get_request_headers("GET", request.headers),
            ) as proxy_response:
                resp_headers = get_response_headers("GET", proxy_response.headers)
                resp_headers.pop("content-length", None)
                response = web.StreamResponse(
                    status=proxy_response.status,
                    reason=proxy_response.reason,
                    headers=resp_headers,
                )
                await response.prepare(request)
                try:
                    # For MJPEG streams, use iter_any() to avoid breaking boundaries
                    content_type = proxy_response.headers.get("content-type", "")
                    if (
                        "multipart" in content_type.lower()
                        or "mjpeg" in content_type.lower()
                    ):
                        # Use iter_any() for MJPEG to preserve multipart boundaries
                        async for chunk in proxy_response.content.iter_any():
                            if (
                                request.transport is None
                                or request.transport.is_closing()
                            ):
                                self.logger.debug(
                                    "Client disconnected, stopping video stream."
                                )
                                break
                            await response.write(chunk)
                    else:
                        # Use chunked reading for other content types
                        async for chunk in proxy_response.content.iter_chunked(8192):
                            if (
                                request.transport is None
                                or request.transport.is_closing()
                            ):
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

    async def _connect_to_printer(
        self, request: web.Request, printer: Printer
    ) -> aiohttp.ClientWebSocketResponse | None:
        """Connect to a specific printer's WebSocket."""
        try:
            remote_ws_url = (
                f"ws://{printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"
            )
            remote_ws = await self.api_session.ws_connect(
                remote_ws_url,
                headers=get_request_headers("WS", request.headers),
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
                            proxy_ip = get_local_ip()

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
            # Clean up this connection from the dict when done
            printer_connections.pop(mainboard_id, None)

    async def _route_client_to_printers(
        self,
        client_ws: web.WebSocketResponse,
        printer_connections: dict[str, aiohttp.ClientWebSocketResponse],
    ) -> None:
        """Route messages from client to appropriate printers."""
        try:
            async for message in client_ws:
                if message.type == WSMsgType.TEXT:
                    message_data = message.data
                    try:
                        data = json.loads(message_data)
                        topic = data.get("Topic", "")
                        mainboard_id = extract_mainboard_id_from_topic(topic)

                        if mainboard_id and mainboard_id in printer_connections:
                            # Inject MainboardID into outgoing message if missing
                            if isinstance(data.get("Data"), dict):
                                data["Data"]["MainboardID"] = mainboard_id
                                message_data = json.dumps(data)
                                self.logger.debug(
                                    "Injected MainboardID %s into outgoing message",
                                    mainboard_id,
                                )
                            await printer_connections[mainboard_id].send_str(
                                message_data
                            )
                        else:
                            # Broadcast to all connected printers
                            for remote_ws in printer_connections.values():
                                await remote_ws.send_str(message_data)
                    except (json.JSONDecodeError, KeyError, TypeError):
                        # If we can't parse/modify the message, send it as-is
                        for remote_ws in printer_connections.values():
                            await remote_ws.send_str(message_data)
                elif message.type == WSMsgType.BINARY:
                    # Broadcast binary data to all printers
                    for remote_ws in printer_connections.values():
                        await remote_ws.send_bytes(message.data)
                elif message.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        except aiohttp.ClientError:
            self.logger.exception("Error in client-to-printer routing")

    async def _handle_specific_printer_connection(
        self,
        mainboard_id: str,
        request: web.Request,
        client_ws: web.WebSocketResponse,
    ) -> list[asyncio.Task[None]]:
        """Handle connection to a specific printer by MainboardID."""
        printer = self.printer_registry.get_printer_by_mainboard_id(mainboard_id)
        if not printer:
            self.logger.warning("Printer with MainboardID %s not found", mainboard_id)
            return []

        remote_ws = await self._connect_to_printer(request, printer)
        if not remote_ws:
            return []

        printer_connections = {mainboard_id: remote_ws}

        # Start bidirectional routing tasks
        tasks = []
        tasks.append(
            asyncio.create_task(
                self._route_printer_to_client(
                    mainboard_id, remote_ws, client_ws, printer_connections
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                self._route_client_to_printers(client_ws, printer_connections)
            )
        )
        return tasks

    async def _handle_multi_printer_connection(
        self,
        request: web.Request,
        client_ws: web.WebSocketResponse,
    ) -> list[asyncio.Task[None]]:
        """Handle connection to all available printers."""
        printers = self.printer_registry.get_all_printers()
        printer_connections: dict[str, aiohttp.ClientWebSocketResponse] = {}

        for printer in printers.values():
            if printer.id:
                remote_ws = await self._connect_to_printer(request, printer)
                if remote_ws:
                    printer_connections[printer.id] = remote_ws

        if not printer_connections:
            return []

        # Start routing tasks for all connected printers
        tasks = []
        for printer_id, remote_ws in printer_connections.items():
            tasks.append(
                asyncio.create_task(
                    self._route_printer_to_client(
                        printer_id, remote_ws, client_ws, printer_connections
                    )
                )
            )

        tasks.append(
            asyncio.create_task(
                self._route_client_to_printers(client_ws, printer_connections)
            )
        )
        return tasks

    async def _centralized_websocket_handler(
        self, request: web.Request
    ) -> web.WebSocketResponse:
        """Handle WebSocket connections by routing to appropriate printers."""
        client_ws = web.WebSocketResponse(heartbeat=30.0)
        await client_ws.prepare(request)

        # Get MainboardID from query parameter
        query_params = parse_qs(request.query_string)
        mainboard_id = (
            query_params.get("id", [None])[0]
            or query_params.get("mainboard_id", [None])[0]
        )

        tasks: list[asyncio.Task[None]] = []

        try:
            if mainboard_id:
                tasks = await self._handle_specific_printer_connection(
                    mainboard_id, request, client_ws
                )
            else:
                tasks = await self._handle_multi_printer_connection(request, client_ws)

            if tasks:
                # Wait for any task to complete
                _, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )

                # Cancel remaining tasks
                for task in pending:
                    task.cancel()

        except Exception:
            self.logger.exception("Error in centralized WebSocket handler")
        finally:
            # Cleanup
            await self._cleanup_websocket_connections(tasks, {}, client_ws)

        return client_ws

    async def _cleanup_websocket_connections(
        self,
        tasks: list[asyncio.Task[None]],
        printer_connections: dict[str, aiohttp.ClientWebSocketResponse],
        client_ws: web.WebSocketResponse,
    ) -> None:
        """Clean up WebSocket connections and tasks."""
        # Cancel any remaining tasks
        for task in tasks:
            if not task.done():
                task.cancel()

        # Close all printer connections (copy values to avoid iteration issues)
        for remote_ws in list(printer_connections.values()):
            if not remote_ws.closed:
                await remote_ws.close()

        # Close client connection
        if not client_ws.closed:
            await client_ws.close()

        # Wait for all tasks to complete
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _try_query_param_routing(self, request: web.Request) -> Printer | None:
        """Try to route based on query parameters."""
        query_params = parse_qs(request.query_string)
        mainboard_id = (
            query_params.get("id", [None])[0]
            or query_params.get("mainboard_id", [None])[0]
        )

        if mainboard_id and len(mainboard_id) >= MIN_MAINBOARD_ID_LENGTH:
            printer = self.printer_registry.get_printer_by_mainboard_id(mainboard_id)
            if printer:
                self.logger.debug(
                    "Routed request via query parameter to printer %s (%s)",
                    printer.name,
                    mainboard_id[:MAX_LOG_LENGTH],
                )
                return printer
        return None

    def _try_path_routing(self, path_parts: list[str]) -> Printer | None:
        """Try to route based on path patterns."""
        # API path routing (/api/{MainboardID}/...)
        if len(path_parts) >= MIN_API_PATH_PARTS and path_parts[0] == "api":
            potential_id = path_parts[1]
            if len(potential_id) >= MIN_MAINBOARD_ID_LENGTH:
                printer = self.printer_registry.get_printer_by_mainboard_id(
                    potential_id
                )
                if printer:
                    self.logger.debug(
                        "Routed request via API path to printer %s (%s)",
                        printer.name,
                        potential_id[:MAX_LOG_LENGTH],
                    )
                    return printer

        # Video path routing (/video/{MainboardID})
        if len(path_parts) >= MIN_VIDEO_PATH_PARTS and path_parts[0] == "video":
            potential_id = path_parts[1]
            if len(potential_id) >= MIN_MAINBOARD_ID_LENGTH:
                printer = self.printer_registry.get_printer_by_mainboard_id(
                    potential_id
                )
                if printer:
                    self.logger.debug(
                        "Routed request via video path to printer %s (%s)",
                        printer.name,
                        potential_id[:MAX_LOG_LENGTH],
                    )
                    return printer
        return None

    def _try_referer_routing(self, request: web.Request) -> Printer | None:
        """Try to route based on referer header."""
        referer = request.headers.get("Referer", "")
        if referer:
            referer_mainboard_id = extract_mainboard_id_from_header(referer)
            if referer_mainboard_id:
                printer = self.printer_registry.get_printer_by_mainboard_id(
                    referer_mainboard_id
                )
                if printer:
                    self.logger.debug(
                        "Routed request via referer header to printer %s (%s)",
                        printer.name,
                        referer_mainboard_id[:MAX_LOG_LENGTH],
                    )
                    return printer
        return None

    def _try_fallback_routing(self, path_parts: list[str]) -> Printer | None:
        """Try fallback routing by examining path segments."""
        if len(path_parts) >= MIN_PATH_PARTS_FOR_FALLBACK:
            for part in path_parts:
                if len(part) >= MIN_MAINBOARD_ID_LENGTH:
                    printer = self.printer_registry.get_printer_by_mainboard_id(part)
                    if printer:
                        self.logger.debug(
                            "Routed request via path fallback to printer %s (%s)",
                            printer.name,
                            part[:MAX_LOG_LENGTH],
                        )
                        return printer
        return None

    def _get_first_available_printer(self) -> Printer | None:
        """Get first available printer as last resort."""
        printers = self.printer_registry.get_all_printers()
        if printers:
            printer = next(iter(printers.values()))
            self.logger.debug(
                "No routing found, using first available printer %s", printer.name
            )
            return printer
        return None

    def _get_target_printer_from_request(self, request: web.Request) -> Printer | None:
        """Determine target printer from request using various routing methods."""
        # Method 1: Query parameter (?id=mainboardid)
        printer = self._try_query_param_routing(request)
        if printer:
            return printer

        # Method 2 & 3: Path-based routing
        path_parts = request.path.strip("/").split("/")
        printer = self._try_path_routing(path_parts)
        if printer:
            return printer

        # Method 4: Referer header fallback
        printer = self._try_referer_routing(request)
        if printer:
            return printer

        # Method 5: Fallback to path segment analysis
        printer = self._try_fallback_routing(path_parts)
        if printer:
            return printer

        # Last resort: first available printer
        return self._get_first_available_printer()

    def _get_cleaned_path_for_printer(self, request_path: str) -> str:
        """Clean request path by removing MainboardID segments for forwarding."""
        path_parts = request_path.strip("/").split("/")

        # Remove API prefix and MainboardID: /api/{MainboardID}/rest -> /rest
        if len(path_parts) >= MIN_API_PATH_PARTS and path_parts[0] == "api":
            potential_id = path_parts[1]
            if len(potential_id) >= MIN_MAINBOARD_ID_LENGTH:
                # Check if this is actually a MainboardID by trying to find the printer
                printer = self.printer_registry.get_printer_by_mainboard_id(
                    potential_id
                )
                if printer:
                    cleaned_parts = path_parts[2:]  # Remove 'api' and MainboardID
                    return "/" + "/".join(cleaned_parts) if cleaned_parts else "/"

        # Remove video prefix and MainboardID: /video/{MainboardID} -> /video
        if len(path_parts) >= MIN_VIDEO_PATH_PARTS and path_parts[0] == "video":
            potential_id = path_parts[1]
            if len(potential_id) >= MIN_MAINBOARD_ID_LENGTH:
                printer = self.printer_registry.get_printer_by_mainboard_id(
                    potential_id
                )
                if printer:
                    return "/video"  # Always route to /video on the actual printer

        # Return original path if no MainboardID patterns found
        return request_path

    async def _centralized_http_handler(
        self, request: web.Request
    ) -> web.StreamResponse:
        """Central HTTP handler that routes all requests."""
        # Handle WebSocket upgrade requests
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._centralized_websocket_handler(request)

        # Handle file uploads
        if request.method == "POST" and (
            request.path == "/uploadFile/upload"
            or request.path.endswith("/uploadFile/upload")
        ):
            return await self._centralized_file_handler(request)

        # Handle regular HTTP requests
        return await self._centralized_http_proxy_handler(request)

    async def _centralized_http_proxy_handler(
        self, request: web.Request
    ) -> web.StreamResponse:
        """Handle HTTP requests by forwarding to the specified printer."""
        if not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        # Find target printer
        printer = self._get_target_printer_from_request(request)
        if not printer:
            return web.Response(status=404, text="Printer not found")

        # Clean path by removing MainboardID before forwarding to printer
        cleaned_path = self._get_cleaned_path_for_printer(request.path)
        q = [
            (k, v)
            for k, v in parse_qsl(request.query_string, keep_blank_values=True)
            if k.lower() not in ("id", "mainboard_id")
        ]
        query_string = f"?{urlencode(q, doseq=True)}" if q else ""

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
            # Forward the request
            async with self.api_session.request(
                request.method,
                target_url,
                headers=get_request_headers(request.method, request.headers),
                data=(
                    request.content
                    if request.method in ("POST", "PUT", "PATCH")
                    else None
                ),
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=10, sock_read=None
                ),
            ) as upstream_response:
                # Determine response type and handle appropriately
                content_type = upstream_response.headers.get("content-type", "")
                is_transformable = any(
                    mime_type in content_type for mime_type in TRANSFORMABLE_MIME_TYPES
                )
                is_cacheable = any(
                    mime_type in content_type for mime_type in CACHEABLE_MIME_TYPES
                )

                # Prepare response headers
                resp_headers = get_response_headers(
                    request.method, upstream_response.headers
                )

                # Set caching headers for static content
                if is_cacheable:
                    resp_headers = set_caching_headers(resp_headers)

                # Create response
                client_response = web.StreamResponse(
                    status=upstream_response.status,
                    reason=upstream_response.reason,
                    headers=resp_headers,
                )

                # Handle transformable content (JavaScript files with
                # MainboardID injection)
                if is_transformable and request.method in ("GET", "HEAD"):
                    return await self._transformed_streamed_response(
                        request, client_response, upstream_response, printer
                    )
                # Stream non-transformable content directly
                return await self._streamed_response(
                    request, client_response, upstream_response
                )

        except aiohttp.ClientError as e:
            self.logger.debug("HTTP proxy error: %s", e)
            return web.Response(status=502, text="Bad Gateway")

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
        try:
            async for chunk in upstream_response.content.iter_any():
                if request.transport is None or request.transport.is_closing():
                    self.logger.debug("Client disconnected during streaming")
                    break
                await client_response.write(chunk)
            await client_response.write_eof()
        except (
            aiohttp.ClientConnectionResetError,
            ConnectionResetError,
            asyncio.CancelledError,
        ) as e:
            self.logger.debug("Stream interrupted by client disconnect: %s", e)
        except Exception:
            self.logger.exception("Unexpected error during streaming")
        return client_response

    def _process_replacements(self, content: str, printer: Printer) -> str:
        # Apply existing IP address and port replacements
        processed_content = (
            content.replace(printer.ip_address or DEFAULT_FALLBACK_IP, get_local_ip())
            .replace(
                f"{get_local_ip()}/",
                f"{get_local_ip()}:{WEBSOCKET_PORT}/",
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

    async def _centralized_file_handler(self, request: web.Request) -> web.Response:
        """Handle file upload requests by forwarding to the specified printer."""
        if not self.file_session or self.file_session.closed:
            return web.Response(
                status=502, text="Bad Gateway: File session not available"
            )

        # Find target printer
        printer = self._get_target_printer_from_request(request)
        if not printer:
            return web.Response(status=404, text="Printer not found")

        # Clean path by removing MainboardID before forwarding to printer
        cleaned_path = self._get_cleaned_path_for_printer(request.path)
        q = [
            (k, v)
            for k, v in parse_qsl(request.query_string, keep_blank_values=True)
            if k.lower() not in ("id", "mainboard_id")
        ]
        query_string = f"?{urlencode(q, doseq=True)}" if q else ""
        remote_url = (
            f"http://{printer.ip_address}:{WEBSOCKET_PORT}{cleaned_path}{query_string}"
        )

        try:
            async with self.file_session.post(
                remote_url,
                headers=get_request_headers("POST", request.headers),
                data=request.content,
            ) as upstream_response:
                # Read response content
                response_content = await upstream_response.read()

                # Return response to client
                return web.Response(
                    status=upstream_response.status,
                    body=response_content,
                    headers=get_response_headers("POST", upstream_response.headers),
                )

        except aiohttp.ClientError as e:
            self.logger.debug("File upload proxy error: %s", e)
            return web.Response(status=502, text="Bad Gateway")

    async def _printer_http_proxy_handler(
        self, request: web.Request, printer: Printer
    ) -> web.StreamResponse:
        """Handle HTTP requests for a specific printer (direct pass-through)."""
        if not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        # Forward directly to printer
        target_url = f"http://{printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"

        self.logger.debug(
            "Direct proxy forwarding: %s %s -> %s",
            request.method,
            request.path,
            target_url,
        )

        try:
            async with self.api_session.request(
                request.method,
                target_url,
                headers=get_request_headers(request.method, request.headers),
                data=(
                    request.content
                    if request.method in ("POST", "PUT", "PATCH")
                    else None
                ),
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=10, sock_read=None
                ),
            ) as upstream_response:
                # Prepare response headers
                resp_headers = get_response_headers(
                    request.method, upstream_response.headers
                )

                # Create and return response
                client_response = web.StreamResponse(
                    status=upstream_response.status,
                    reason=upstream_response.reason,
                    headers=resp_headers,
                )

                return await self._streamed_response(
                    request, client_response, upstream_response
                )

        except aiohttp.ClientError as e:
            self.logger.debug("Direct HTTP proxy error: %s", e)
            return web.Response(status=502, text="Bad Gateway")

    async def _printer_file_handler(
        self, request: web.Request, printer: Printer
    ) -> web.Response:
        """Handle file upload requests for a specific printer (direct pass-through)."""
        if not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        # Forward directly to printer's file upload endpoint
        parts = urlsplit(request.path_qs)
        q = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in ("id", "mainboard_id")
        ]
        remote_url = urlunsplit(
            (
                "http",
                f"{printer.ip_address}:{WEBSOCKET_PORT}",
                parts.path,
                urlencode(q, doseq=True) if q else "",
                parts.fragment,
            )
        )

        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower()
            not in (
                "host",
                "content-length",
            )
        }

        try:
            async with self.file_session.post(
                remote_url, headers=headers, data=request.content
            ) as upstream_response:
                response_content = await upstream_response.read()
                return web.Response(
                    status=upstream_response.status,
                    body=response_content,
                    headers=get_response_headers("POST", upstream_response.headers),
                )

        except aiohttp.ClientError as e:
            self.logger.debug("Direct file upload proxy error: %s", e)
            return web.Response(status=502, text="Bad Gateway")
