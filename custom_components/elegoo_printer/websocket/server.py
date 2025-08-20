from __future__ import annotations

import asyncio
import json
import os
import socket
from typing import Any, List

import aiohttp
from aiohttp import ClientSession, WSMsgType, web
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.elegoo_printer.const import (
    DEFAULT_FALLBACK_IP,
    DISCOVERY_MESSAGE,
    DISCOVERY_PORT,
    PROXY_HOST,
    VIDEO_PORT,
    WEBSOCKET_PORT,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer

INADDR_ANY = "0.0.0.0"


class ElegooPrinterServer:
    """
    Manages local proxy servers for an Elegoo printer.

    This includes WebSocket, UDP discovery, and a full HTTP reverse proxy.
    This server runs on the main Home Assistant event loop.
    """

    _instances: List["ElegooPrinterServer"] = []

    def __init__(
        self,
        printer: Printer,
        logger: Any,
        hass: HomeAssistant,
        session: ClientSession,
    ):
        """Initializes the Elegoo printer proxy server."""
        self.printer = printer
        self.logger = logger
        self.hass = hass
        self.session = session
        self.runners: List[web.AppRunner] = []
        self._is_connected = False
        self.datagram_transport: asyncio.DatagramTransport | None = None

        if not self.printer.ip_address:
            raise ConfigEntryNotReady(
                "Printer IP address is not set. Cannot start proxy server."
            )

    @classmethod
    async def async_create(
        cls,
        printer: Printer,
        logger: Any,
        hass: HomeAssistant,
        session: ClientSession,
    ) -> "ElegooPrinterServer":
        """Asynchronously creates and starts the server."""
        self = cls(printer, logger, hass, session)
        await self.start()
        return self

    @property
    def is_connected(self) -> bool:
        """Return true if the proxy is connected to the printer."""
        return self._is_connected

    async def start(self):
        """Starts the proxy server on the Home Assistant event loop."""
        if not self._check_ports_are_available():
            self.logger.info("Required proxy ports are in use; failing initialization.")
            raise ConfigEntryNotReady("Proxy server ports are in use.")

        self.logger.info(
            f"Initializing proxy server for remote printer {self.printer.ip_address}"
        )

        try:
            # Allow large uploads (streamed), keep headroom for typical print files.
            main_app = web.Application(client_max_size=1024 * 1024 * 1024)  # 1 GiB
            main_app.router.add_route("*", "/{path:.*}", self._http_handler)
            main_runner = web.AppRunner(main_app)
            await main_runner.setup()
            main_site = web.TCPSite(main_runner, INADDR_ANY, WEBSOCKET_PORT)
            await main_site.start()
            self.runners.append(main_runner)
            self.logger.info(
                f"Main HTTP/WebSocket Proxy running on http://{self.get_local_ip()}:{WEBSOCKET_PORT}"
            )

            video_app = web.Application()
            video_app.router.add_route("*", "/{path:.*}", self._video_proxy_handler)
            video_runner = web.AppRunner(video_app)
            await video_runner.setup()
            video_site = web.TCPSite(video_runner, INADDR_ANY, VIDEO_PORT)
            await video_site.start()
            self.runners.append(video_runner)
            self.logger.info(
                f"Video Proxy running on http://{self.get_local_ip()}:{VIDEO_PORT}"
            )

            def discovery_factory():
                return DiscoveryProtocol(self.logger, self.printer, self.get_local_ip())

            transport, _ = await self.hass.loop.create_datagram_endpoint(
                discovery_factory, local_addr=(INADDR_ANY, DISCOVERY_PORT)
            )
            self.datagram_transport = transport
            self.logger.info(f"Discovery Proxy listening on UDP port {DISCOVERY_PORT}")

        except OSError as e:
            self.logger.error(f"Failed to start proxy server component: {e}")
            await self.stop()
            raise ConfigEntryNotReady(f"Failed to start proxy server: {e}") from e

        self.__class__._instances.append(self)
        self.logger.info("Proxy server has started successfully.")

    @classmethod
    async def stop_all(cls):
        """Stops all running proxy server instances."""
        for instance in list(cls._instances):
            await instance.stop()
        cls._instances.clear()

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
                self.logger.warning(
                    f"{name} port {port} is already in use. Proxy server cannot start."
                )
                return False
        return True

    async def stop(self):
        """Stops the proxy server and cleans up all associated resources."""
        self.logger.info("Stopping proxy server...")
        self._is_connected = False

        if self.datagram_transport:
            self.datagram_transport.close()
            self.datagram_transport = None

        for runner in self.runners:
            await runner.cleanup()
        self.runners.clear()

        if self in self.__class__._instances:
            self.__class__._instances.remove(self)

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
        except Exception:
            return PROXY_HOST

    async def _http_handler(self, request: web.Request) -> web.StreamResponse:
        """Dispatches incoming HTTP requests."""
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._websocket_handler(request)
        if request.method == "POST" and request.path == "/uploadFile/upload":
            return await self._http_file_proxy_passthrough_handler(request)
        return await self._http_proxy_handler(request)

    async def _video_proxy_handler(self, request: web.Request) -> web.StreamResponse:
        """Proxies video stream requests."""
        remote_url = f"http://{self.printer.ip_address}:{VIDEO_PORT}{request.path_qs}"
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
                    self.logger.debug(f"Video stream stopped: {e}")
                except Exception as e:
                    self.logger.error(
                        f"An unexpected error occurred during video streaming: {e}"
                    )
                return response
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self.logger.error(f"Error proxying video stream: {e}")
            return web.Response(status=502, text="Bad Gateway")

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Proxy a WebSocket connection."""
        client_ws = web.WebSocketResponse()
        await client_ws.prepare(request)

        if not self.session or self.session.closed:
            await client_ws.close(code=1011, message=b"Upstream connection failed")
            return client_ws

        remote_ws_url = (
            f"ws://{self.printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"
        )
        allowed_headers = {
            "sec-websocket-version",
            "sec-websocket-key",
            "upgrade",
            "connection",
        }
        filtered_headers = {
            k: v for k, v in request.headers.items() if k.lower() in allowed_headers
        }

        tasks = set()
        try:
            async with self.session.ws_connect(
                remote_ws_url, headers=filtered_headers, heartbeat=10.0
            ) as remote_ws:
                self._is_connected = True

                async def forward(source, dest, direction):
                    try:
                        async for msg in source:
                            if msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
                                await dest.send_str(
                                    msg.data
                                ) if msg.type == WSMsgType.TEXT else await (
                                    dest.send_bytes(msg.data)
                                )
                            elif msg.type == WSMsgType.CLOSE:
                                break
                            elif msg.type == WSMsgType.ERROR:
                                self.logger.error(
                                    f"WebSocket error in {direction}: {source.exception()}"
                                )
                                break
                    except Exception:
                        self.logger.debug(f"WebSocket connection reset in {direction}.")
                        raise

                to_printer = self.hass.async_create_task(
                    forward(client_ws, remote_ws, "client-to-printer")
                )
                tasks.add(to_printer)
                to_client = self.hass.async_create_task(
                    forward(remote_ws, client_ws, "printer-to-client")
                )
                tasks.add(to_client)
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task.exception():
                        raise task.exception()

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self.logger.warning(f"WebSocket connection to printer failed: {e}")
            self._is_connected = False
        except Exception as e:
            self.logger.error(f"WebSocket proxy error: {e}")
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
        if not self.printer.ip_address or not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        target_url = (
            f"http://{self.printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"
        )
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
            self.logger.error(f"HTTP proxy error connecting to {target_url}: {e}")
            return web.Response(status=502, text=f"Bad Gateway: {e}")

    async def _http_file_proxy_passthrough_handler(
        self, request: web.Request
    ) -> web.Response:
        """Proxies multipart file upload requests."""
        remote_url = (
            f"http://{self.printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"
        )
        if not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

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
            self.logger.error(f"HTTP file passthrough proxy error: {e}")
            return web.Response(status=502, text=f"Bad Gateway: {e}")


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol to handle UDP discovery broadcasts."""

    def __init__(self, logger: Any, printer: Printer, proxy_ip: str):
        super().__init__()
        self.logger = logger
        self.printer = printer
        self.proxy_ip = proxy_ip
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        """Handles incoming UDP datagrams for discovery."""
        try:
            message = data.decode("utf-8", errors="ignore").strip()
        except Exception as e:
            self.logger.debug(
                f"Ignoring undecodable discovery datagram from {addr}: {e}"
            )
            return
        if message == DISCOVERY_MESSAGE:
            self.logger.debug(f"Discovery request received from {addr}, responding.")
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
            # self.logger.debug(response_payload)
            json_string = json.dumps(response_payload)
            if self.transport:
                self.transport.sendto(json_string.encode(), addr)

    def error_received(self, exc):
        self.logger.error(f"UDP Discovery Server Error: {exc}")
