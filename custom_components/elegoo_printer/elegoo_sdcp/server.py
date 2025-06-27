"""Elegoo Printer Server and Proxy."""

import asyncio
import json
import os
import socket
from threading import Event, Thread
from typing import Any, Union

import aiohttp
from aiohttp import ClientSession, web
from homeassistant.exceptions import ConfigEntryNotReady
from websockets.legacy.client import WebSocketClientProtocol, connect
from websockets.legacy.server import WebSocketServerProtocol

from .models.printer import Printer

LOCALHOST = "127.0.0.1"
INADDR_ANY = "0.0.0.0"
DISCOVERY_PORT = 3000
WEBSOCKET_PORT = 3030

# Define a type alias for protocols that can be forwarded
Forwardable = Union[WebSocketServerProtocol, WebSocketClientProtocol]


class ElegooPrinterServer:
    """
    Manages local proxy servers for an Elegoo printer, including WebSocket, UDP discovery, and HTTP file uploads.
    """

    def __init__(self, printer: Printer, logger: Any):
        """
        Initializes the Elegoo printer proxy server, validating the printer configuration and starting HTTP/WebSocket and UDP discovery proxy services in a background thread.

        Raises:
            ConfigEntryNotReady: If the printer IP address is missing or if the proxy server fails to start within 10 seconds.
        """
        self.printer = printer
        self.logger = logger
        self.startup_event = Event()
        self.proxy_thread: Thread | None = None
        self.runner: web.AppRunner | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.session: ClientSession | None = None

        if not self.printer.ip_address:
            raise ConfigEntryNotReady(
                "Printer IP address is not set. Cannot start proxy server."
            )

        if self._check_ports_are_available():
            self.logger.info(
                f"Initializing proxy server for remote printer {self.printer.ip_address}"
            )
            self.proxy_thread = Thread(
                target=self._start_servers_in_thread, daemon=True
            )
            self.proxy_thread.start()

            ready = self.startup_event.wait(timeout=10.0)
            if not ready:
                self.logger.error(
                    "Proxy server failed to start within the timeout period."
                )
                self.stop()
                raise ConfigEntryNotReady("Proxy server failed to start.")
            self.logger.info("Proxy server has started successfully.")

    def _check_ports_are_available(self) -> bool:
        """
        Determine if both the WebSocket (TCP) and discovery (UDP) ports required by the proxy server are available.

        Returns:
            True if both ports are free; False if either port is already in use.
        """
        for port, proto, name in [
            (WEBSOCKET_PORT, socket.SOCK_STREAM, "TCP"),
            (DISCOVERY_PORT, socket.SOCK_DGRAM, "UDP"),
        ]:
            try:
                with socket.socket(socket.AF_INET, proto) as s:
                    # Set SO_REUSEADDR to allow immediate reuse of the port after it's been closed
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind((INADDR_ANY, port))
            except OSError:
                # The port is already in use.
                error_msg = (
                    f"{name} port {port} is already in use. Proxy server cannot start."
                )
                self.logger.debug(error_msg)
                return False
        return True

    def stop(self):
        """
        Shuts down the proxy server and cleans up resources.

        Closes the HTTP client session and aiohttp runner, stops the event loop if it is running, and logs the shutdown event.
        """

        async def cleanup():
            """
            Asynchronously closes the HTTP client session and cleans up the aiohttp web server runner if they exist.
            """
            if self.session:
                await self.session.close()  # Close the session
            if self.runner:
                await self.runner.cleanup()

        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(cleanup(), self.loop).result(timeout=5)
            self.loop.call_soon_threadsafe(self.loop.stop)

        self.logger.info("Proxy server stopped.")

    def get_printer(self) -> Printer:
        """
        Return a copy of the printer object with its IP address replaced by the local proxy server's IP.

        Returns:
            Printer: A copy of the printer object with the IP address set to the local IP used by the proxy.
        """
        proxied_printer = Printer()
        proxied_printer.__dict__.update(self.printer.__dict__)
        proxied_printer.ip_address = self.get_local_ip()
        return proxied_printer

    def get_local_ip(self):
        """
        Returns the local IP address used to communicate with the printer.

        Attempts to determine the outbound local IP by connecting a UDP socket to the printer's IP address. If detection fails, returns "127.0.0.1".

        Returns:
            str: The local IP address, or "127.0.0.1" if detection is unsuccessful.
        """
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((self.printer.ip_address or "8.8.8.8", 1))
            return s.getsockname()[0]
        except Exception:
            return LOCALHOST
        finally:
            if s:
                s.close()

    def _start_servers_in_thread(self):
        """
        Starts the HTTP/WebSocket and UDP discovery proxy servers in a dedicated asyncio event loop on a separate thread.

        Initializes an aiohttp server to proxy HTTP and WebSocket requests to the printer, handling startup exceptions to avoid crashes from port conflicts or multiple instances. Also launches a UDP discovery server to respond to printer discovery requests. The event loop runs indefinitely to keep proxy services active.
        """
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def startup():
            # Create the persistent session
            """
            Initializes and starts the aiohttp HTTP/WebSocket proxy server for the Elegoo printer.

            Creates a persistent HTTP client session, configures the aiohttp application with a catch-all route for proxying requests, and starts the server on all interfaces at the designated WebSocket port. Handles and logs exceptions during startup, including port conflicts, to ensure graceful failure without crashing the process.
            """
            self.session = aiohttp.ClientSession()

            app = web.Application(client_max_size=1024 * 1024 * 2)
            app.router.add_route("*", "/{path:.*}", self._http_handler)

            self.runner = web.AppRunner(app)
            if self.runner:
                await self.runner.setup()

            site = web.TCPSite(self.runner, INADDR_ANY, WEBSOCKET_PORT)

            # We only want one server here
            try:
                await site.start()
            except OSError:
                # So We ignore the OSError since that's when multiple happen
                self.logger.info("Extra server detected")
                return
            except Exception as e:
                # And we ignore exceptions since we dont care also
                self.logger.info(f"Exception on site start: {e}")
                return

        self.loop.run_until_complete(startup())

        self.logger.info(
            f"Unified HTTP/WebSocket Proxy running on http://{self.get_local_ip()}:{WEBSOCKET_PORT}"
        )

        # --- Start Discovery (UDP) Proxy Server ---
        def discovery_factory():
            return DiscoveryProtocol(self.logger, self.printer, self.get_local_ip())

        self.loop.run_until_complete(
            self.loop.create_datagram_endpoint(
                discovery_factory, local_addr=(INADDR_ANY, DISCOVERY_PORT)
            )
        )
        self.logger.info(f"Discovery Proxy listening on UDP port {DISCOVERY_PORT}")

        self.startup_event.set()

        self.loop.run_forever()

    async def _http_handler(self, request: web.Request):
        """Handles all incoming HTTP requests, routing to WebSocket or HTTP proxy."""
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._websocket_handler(request)
        elif request.method == "POST" and request.path == "/uploadFile/upload":
            return await self._http_file_proxy_passthrough_handler(request)
        else:
            self.logger.warning(
                f"Received unhandled HTTP request: {request.method} {request.path}"
            )
            return web.Response(status=404, text="Not Found")

    async def _websocket_handler(self, request: web.Request):
        """Handles WebSocket connections."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        remote_ip = self.printer.ip_address
        self.logger.info(f"WebSocket client connected from {request.remote}")

        try:
            async with connect(
                f"ws://{remote_ip}:{WEBSOCKET_PORT}/websocket"
            ) as remote_ws:
                self.logger.info(
                    f"Proxy connected to remote printer WebSocket at {remote_ip}"
                )

                async def forward_to_printer():
                    async for msg in ws:
                        if (
                            msg.type == web.WSMsgType.TEXT
                            or msg.type == web.WSMsgType.BINARY
                        ):
                            await remote_ws.send(msg.data)

                async def forward_to_client():
                    async for msg in remote_ws:
                        if isinstance(msg, bytes):
                            await ws.send_bytes(msg)
                        else:
                            await ws.send_str(str(msg))

                # Create tasks to run the forwarding coroutines concurrently
                client_task = asyncio.create_task(forward_to_client())
                printer_task = asyncio.create_task(forward_to_printer())

                # Wait for either task to complete
                done, pending = await asyncio.wait(
                    [client_task, printer_task], return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
        except Exception as e:
            self.logger.error(f"WebSocket proxy error: {e}")
        finally:
            self.logger.info(f"WebSocket client disconnected from {request.remote}")
            await ws.close()
        return ws

    async def _http_file_proxy_passthrough_handler(self, request: web.Request):
        """
        Correctly proxies a multipart file upload by buffering the request to add
        a Content-Length header, while preserving the original headers from the client
        (like Content-Type, S-File-MD5, Uuid, etc.).
        """
        remote_url = f"http://{self.printer.ip_address}:{WEBSOCKET_PORT}{request.path}"
        self.logger.debug(
            f"Proxying multipart request for {request.path} by re-assembling for printer"
        )

        try:
            # 1. Read the entire raw body from the client. This is the "store" part
            # of our store-and-forward proxy. It's necessary because the printer
            # can't handle chunked encoding.
            raw_body = await request.read()

            # 2. Extract the headers we need to forward from the original request.
            # The client has already created these, so we just pass them along.
            headers_to_forward = {}
            # The printer's API requires these, plus Content-Type for the boundary.
            required_headers = [
                "Content-Type",
                "S-File-MD5",
                "Check",
                "Offset",
                "Uuid",
                "TotalSize",
            ]

            self.logger.info(request.headers)

            for name in required_headers:
                if name in request.headers:
                    headers_to_forward[name] = request.headers[name]
                else:
                    # Content-Type is absolutely mandatory for a multipart request.
                    if name == "Content-Type":
                        msg = "Aborting proxy attempt: Client request is missing Content-Type header."
                        self.logger.error(msg)
                        return web.Response(status=400, text=msg)

            # 3. Forward the raw body and the extracted headers to the printer.
            # aiohttp will automatically calculate and add the Content-Length header
            # because 'raw_body' is a bytes object.
            if not self.session:
                raise Exception("Persistent session not initialized.")

            async with self.session.post(
                remote_url, headers=headers_to_forward, data=raw_body
            ) as response:
                self.logger.debug(
                    f"Printer responded to proxied upload with status: {response.status}"
                )

                # Forward the printer's exact response back to the client.
                content = await response.read()
                return web.Response(
                    body=content,
                    status=response.status,
                    headers=response.headers,  # Forward all of the printer's response headers
                )

        except Exception as e:
            self.logger.error(f"HTTP passthrough proxy error: {e}")
            return web.Response(status=502, text=f"Bad Gateway: {e}")


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol to handle UDP discovery broadcasts by replying with JSON."""

    def __init__(self, logger: Any, printer: Printer, proxy_ip: str):
        self.logger = logger
        self.printer = printer
        self.proxy_ip = proxy_ip
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data.decode() == "M99999":
            self.logger.debug(
                f"Discovery request received from {addr}, responding with JSON."
            )
            response_payload = {
                "Id": getattr(self.printer, "connection", os.urandom(8).hex()),
                "Data": {
                    "Name": f"{getattr(self.printer, 'name', 'Elegoo')} Proxy",
                    "MachineName": getattr(self.printer, "name", "Elegoo Proxy"),
                    "BrandName": "Elegoo",
                    "MainboardIP": self.proxy_ip,
                    "MainboardID": getattr(self.printer, "id", "unknown"),
                    "ProtocolVersion": "V3.0.0",
                    "FirmwareVersion": getattr(self.printer, "version", "V1.0.0"),
                },
            }
            json_string = json.dumps(response_payload)
            if self.transport:
                self.transport.sendto(json_string.encode(), addr)

    def error_received(self, exc):
        self.logger.error(f"UDP Discovery Server Error: {exc}")
