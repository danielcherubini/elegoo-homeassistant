"""Elegoo Printer Proxy Server."""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
from math import floor
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import ClientResponse, ClientSession, WSMsgType, web
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.elegoo_printer.const import (
    DEFAULT_FALLBACK_IP,
    DISCOVERY_MESSAGE,
    DISCOVERY_PORT,
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


class ElegooPrinterServer:
    """
    Manages local proxy servers for an Elegoo printer.

    This includes WebSocket, UDP discovery, and a full HTTP reverse proxy.
    This server runs on the main Home Assistant event loop.
    """

    _instances: list[ElegooPrinterServer] = []  # noqa: RUF012

    def __init__(
        self,
        printer: Printer,
        logger: Any,
        hass: HomeAssistant,
    ) -> None:
        """Initialize the Elegoo printer proxy server."""
        self.printer = printer
        self.logger = logger
        self.hass = hass
        # Three dedicated sessions for different use cases
        self.api_session: ClientSession | None = None  # API calls & WebSocket
        self.video_session: ClientSession | None = None  # Video streaming
        self.file_session: ClientSession | None = None  # File transfers
        self.runners: list[web.AppRunner] = []
        self._is_connected = False
        self.datagram_transport: asyncio.DatagramTransport | None = None

        if not self.printer.ip_address:
            msg = "Printer IP address is not set. Cannot start proxy server."
            raise ConfigEntryNotReady(msg)

    @classmethod
    async def async_create(
        cls,
        printer: Printer,
        logger: Any,
        hass: HomeAssistant,
    ) -> ElegooPrinterServer:
        """Asynchronously creates and starts the server."""
        self = cls(printer, logger, hass)
        await self.start()
        return self

    @property
    def is_connected(self) -> bool:
        """Return true if the proxy is connected to the printer."""
        return self._is_connected

    async def start(self) -> None:
        """Start the proxy server on the Home Assistant event loop."""
        # First try to cleanup any orphaned servers on our ports
        # This assumes only one proxy instance is active at a time.
        await self.__class__.stop_all()

        if not self._check_ports_are_available():
            msg = "Proxy server ports are in use."
            self.logger.info(msg)
            raise ConfigEntryNotReady(msg)

        msg = f"Initializing proxy server for remote printer {self.printer.ip_address}"
        self.logger.info(msg)

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

            def discovery_factory() -> DiscoveryProtocol:
                return DiscoveryProtocol(self.logger, self.printer, self.get_local_ip())

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

        self.__class__._instances.append(self)  # noqa: SLF001
        self.logger.info("Proxy server has started successfully.")

    @classmethod
    async def stop_all(cls) -> None:
        """Stop all running proxy server instances."""
        LOGGER.debug("stop_all called, found %d instances", len(cls._instances))
        for instance in list(cls._instances):
            LOGGER.debug("Stopping server instance...")
            await instance.stop()
        cls._instances.clear()
        LOGGER.debug("All instances stopped, waiting for ports to be released...")
        # Give time for ports to actually be released by the OS
        await asyncio.sleep(0.5)

        # Force cleanup any lingering connections
        await cls._force_cleanup_ports(LOGGER)
        LOGGER.debug("stop_all completed")

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

    def _check_ports_are_available(self) -> bool:
        """Check if the required TCP and UDP ports for the proxy server are free."""
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
                self.logger.exception(
                    "%s port %s is already in use. Proxy server cannot start.",
                    name,
                    port,
                )
                return False
        return True

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

        # Clean up web runners
        for runner in self.runners:
            try:
                await runner.cleanup()
            except (RuntimeError, OSError) as e:
                self.logger.warning("Error cleaning up runner: %s", e)
        self.runners.clear()

        # Remove from instances list
        if self in self.__class__._instances:  # noqa: SLF001
            self.__class__._instances.remove(self)  # noqa: SLF001

        # Small delay to ensure ports are fully released
        await asyncio.sleep(0.1)

        self.logger.info("Proxy server stopped.")

    def get_printer(self) -> Printer:
        """Return a printer object with its IP address set to the local proxy."""
        printer_dict = self.printer.to_dict()
        printer_dict["ip_address"] = self.get_local_ip()
        return Printer.from_dict(printer_dict)

    def get_local_ip(self) -> str:
        """Determine the local IP address for outbound communication."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((self.printer.ip_address or DEFAULT_FALLBACK_IP, 1))
                return s.getsockname()[0]
        except Exception:  # noqa: BLE001
            return PROXY_HOST

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

    async def _http_handler(self, request: web.Request) -> web.StreamResponse:
        """Dispatches incoming HTTP requests."""
        if request.path == "/video":
            return await self._video_proxy_handler(request)
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._websocket_handler(request)
        if request.method == "POST" and request.path == "/uploadFile/upload":
            return await self._http_file_proxy_passthrough_handler(request)
        return await self._http_proxy_handler(request)

    async def _video_proxy_handler(self, request: web.Request) -> web.StreamResponse:
        """Proxies video stream requests."""
        remote_url = f"http://{self.printer.ip_address}:{VIDEO_PORT}{request.path_qs}"
        if not self.video_session or self.video_session.closed:
            return web.Response(status=503, text="Video session not available.")

        try:
            async with self.video_session.get(
                remote_url,
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
                    # For MJPEG streams, use iter_any() to avoid breaking boundaries
                    content_type = proxy_response.headers.get("content-type", "")
                    content_type_lower = content_type.lower()
                    if (
                        "multipart" in content_type_lower
                        or "mjpeg" in content_type_lower
                    ):
                        # Use iter_any() for MJPEG to preserve multipart boundaries
                        body_iter = proxy_response.content.iter_any()
                    else:
                        # Use chunked reading for other content types
                        body_iter = proxy_response.content.iter_chunked(8192)

                    async for chunk in body_iter:
                        transport = request.transport
                        if transport is None or transport.is_closing():
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
        except TimeoutError as e:
            self.logger.debug("Video stream timeout from %s: %s", remote_url, e)
            return web.Response(status=504, text="Video stream not available")
        except aiohttp.ClientError as e:
            self.logger.debug("Video stream not available from %s: %s", remote_url, e)
            return web.Response(status=502, text="Video stream not available")

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Proxy a WebSocket connection."""
        client_ws = web.WebSocketResponse()
        await client_ws.prepare(request)

        if not self.api_session or self.api_session.closed:
            await client_ws.close(code=1011, message=b"Upstream connection failed")
            return client_ws

        remote_ws_url = (
            f"ws://{self.printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"
        )

        tasks = set()
        try:
            async with self.api_session.ws_connect(
                remote_ws_url,
                headers=self._get_request_headers("WS", request.headers),
                heartbeat=10.0,
            ) as remote_ws:
                self._is_connected = True

                async def forward(
                    source: web.WebSocketResponse,
                    dest: web.WebSocketResponse,
                    direction: str,
                ) -> None:
                    try:
                        async for message in source:
                            if message.type in (WSMsgType.TEXT, WSMsgType.BINARY):
                                await dest.send_str(
                                    self._process_replacements(message.data)
                                ) if message.type == WSMsgType.TEXT else await (
                                    dest.send_bytes(message.data)
                                )
                            elif message.type == WSMsgType.CLOSE:
                                await dest.close()
                                break
                            elif message.type == WSMsgType.ERROR:
                                msg = f"WebSocket error in {direction}: {source.exception()}"  # noqa: E501
                                self.logger.error(msg)
                                break
                    except Exception:
                        msg = f"WebSocket connection reset in {direction}."
                        self.logger.debug(msg)
                        raise

                to_printer = asyncio.create_task(
                    forward(client_ws, remote_ws, "client-to-printer"),
                    name="elegoo_ws:client_to_printer",
                )
                tasks.add(to_printer)
                to_client = asyncio.create_task(
                    forward(remote_ws, client_ws, "printer-to-client"),
                    name="elegoo_ws:printer_to_client",
                )
                tasks.add(to_client)
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task.exception():
                        raise task.exception()  # noqa: TRY301

        except (TimeoutError, aiohttp.ClientError) as e:
            msg = f"WebSocket connection to printer failed: {e}"
            self.logger.warning(msg)
            self._is_connected = False
        except Exception:
            self.logger.exception("WebSocket proxy error")
            self._is_connected = False
        finally:
            # Ensure connected state is reset on normal closure as well
            self._is_connected = False
            for task in tasks:
                task.cancel()
            # Drain cancellations to avoid unhandled exceptions
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not client_ws.closed:
                await client_ws.close()
        return client_ws

    async def _http_proxy_handler(self, request: web.Request) -> web.StreamResponse:
        """Streams HTTP requests."""
        if (
            not self.printer.ip_address
            or not self.api_session
            or self.api_session.closed
        ):
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        target_url = (
            f"http://{self.printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"
        )

        try:
            async with self.api_session.request(
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
                        request, client_response, upstream_response
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
    ) -> web.StreamResponse:
        client_response.headers.pop("content-length")
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
                replaced = self._process_replacements(combined)
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

    def _process_replacements(self, content: str) -> str:
        return (
            content.replace(
                self.printer.ip_address or DEFAULT_FALLBACK_IP, self.get_local_ip()
            )
            .replace(
                f"{self.get_local_ip()}/",
                f"{self.get_local_ip()}:{WEBSOCKET_PORT}/",
            )
            .replace(
                f"{self.get_local_ip()}:{VIDEO_PORT}/",
                f"{self.get_local_ip()}:{WEBSOCKET_PORT}/",
            )
            .replace(
                "${this.webSocketService.hostName}:80",
                "${this.webSocketService.hostName}:" + f"{WEBSOCKET_PORT}",
            )
        )

    async def _http_file_proxy_passthrough_handler(
        self, request: web.Request
    ) -> web.Response:
        """Proxies multipart file upload requests."""
        remote_url = (
            f"http://{self.printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"
        )
        if not self.file_session or self.file_session.closed:
            return web.Response(
                status=502, text="Bad Gateway: File session not available"
            )

        try:
            async with self.file_session.post(
                remote_url,
                headers=self._get_request_headers("POST", request.headers),
                data=request.content,
            ) as response:
                content = await response.read()
                return web.Response(
                    body=content,
                    status=response.status,
                    headers=self._get_response_headers("POST", response.headers),
                )
        except Exception as e:
            self.logger.exception("HTTP file passthrough proxy error")
            return web.Response(status=502, text=f"Bad Gateway: {e}")


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol to handle UDP discovery broadcasts."""

    def __init__(self, logger: Any, printer: Printer, proxy_ip: str) -> None:
        """Initialize the discovery protocol."""
        super().__init__()
        self.logger = logger
        self.printer = printer
        self.proxy_ip = proxy_ip
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        """Call when a connection is made."""
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle incoming UDP datagrams for discovery."""
        try:
            message = data.decode("utf-8", errors="ignore").strip()
        except Exception as e:  # noqa: BLE001
            msg = f"Ignoring undecodable discovery datagram from {addr}: {e}"
            self.logger.debug(msg)
            return
        if message == DISCOVERY_MESSAGE:
            msg = f"Discovery request received from {addr}, responding."
            self.logger.debug(msg)
            response_payload = {
                "Id": getattr(self.printer, "connection", os.urandom(8).hex()),
                "Data": {
                    "Name": f"{getattr(self.printer, 'name', 'Elegoo')} Proxy",
                    "MachineName": getattr(self.printer, "name", "Elegoo Proxy"),
                    "BrandName": "Elegoo",
                    "MainboardIP": self.proxy_ip,
                    "MainboardID": getattr(self.printer, "id", "unknown"),
                    "ProtocolVersion": getattr(self.printer, "protocol", "V3.0.0"),
                    "FirmwareVersion": getattr(self.printer, "firmware", "V1.0.0"),
                },
            }
            json_string = json.dumps(response_payload)
            if self.transport:
                self.transport.sendto(json_string.encode(), addr)

    def error_received(self, exc: Exception) -> None:
        """Call when an error is received."""
        msg = f"UDP Discovery Server Error: {exc}"
        self.logger.error(msg)
