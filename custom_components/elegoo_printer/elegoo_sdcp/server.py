"""Elegoo Printer Server and Proxy."""

import asyncio
import json
import os
import socket
from threading import Event, Thread
from typing import Any, Union

from aiohttp import ClientSession, web
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
        self.printer = printer
        self.logger = logger
        self.startup_event = Event()
        self.proxy_thread: Thread | None = None
        self.runner: web.AppRunner | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

        if not self.printer.ip_address:
            raise ValueError(
                "Printer IP address is not set. Cannot start proxy server."
            )

        self.logger.info(
            f"Initializing proxy server for remote printer {self.printer.ip_address}"
        )
        self.proxy_thread = Thread(target=self._start_servers_in_thread, daemon=True)
        self.proxy_thread.start()

        ready = self.startup_event.wait(timeout=10.0)
        if not ready:
            self.logger.error("Proxy server failed to start within the timeout period.")
            self.stop()
            raise Exception("Proxy server failed to start.")
        self.logger.info("Proxy server has started successfully.")

    def stop(self):
        """Stops the running server and cleans up resources."""
        if self.loop and self.runner:
            asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result()
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.logger.info("Proxy server stopped.")

    def get_printer(self) -> Printer:
        proxied_printer = Printer()
        proxied_printer.__dict__.update(self.printer.__dict__)
        proxied_printer.ip_address = self.get_local_ip()
        return proxied_printer

    def get_local_ip(self):
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
        """Starts the aiohttp server in a dedicated asyncio event loop."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        app = web.Application(client_max_size=1024 * 1024 * 2)
        app.router.add_route("*", "/{path:.*}", self._http_handler)

        self.runner = web.AppRunner(app)
        self.loop.run_until_complete(self.runner.setup())

        site = web.TCPSite(self.runner, INADDR_ANY, WEBSOCKET_PORT)
        self.loop.run_until_complete(site.start())
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
        # Check if it's a WebSocket upgrade request
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._websocket_handler(request)
        # Check if it's a file upload request
        elif request.method == "POST" and request.path == "/uploadFile/upload":
            return await self._http_file_proxy_handler(request)
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

    async def _http_file_proxy_handler(self, request: web.Request):
        """Proxies HTTP file upload requests to the real printer."""
        remote_url = f"http://{self.printer.ip_address}:{WEBSOCKET_PORT}{request.path}"
        self.logger.info(f"Proxying file upload to {remote_url}")

        headers = {
            h: v
            for h, v in request.headers.items()
            if h.upper() not in ("HOST", "CONTENT-LENGTH", "CONTENT-TYPE")
        }

        try:
            async with ClientSession() as session:
                async with session.post(
                    remote_url, headers=headers, data=request.content
                ) as response:
                    self.logger.info(
                        f"Printer responded to file upload with status: {response.status}"
                    )

                    # Read the response from the printer
                    content = await response.read()

                    # Forward the printer's response back to the original client
                    return web.Response(
                        body=content,
                        status=response.status,
                        content_type=response.content_type,
                    )
        except Exception as e:
            self.logger.error(f"HTTP file proxy error: {e}")
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
            self.logger.info(
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
