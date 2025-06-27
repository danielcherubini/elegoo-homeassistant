"""Elegoo Printer Server and Proxy."""

import asyncio
import hashlib
import json
import os
import socket
import uuid
from threading import Event, Thread
from typing import Any, Union

import aiohttp
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
        self.session: ClientSession | None = None

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

        async def cleanup():
            if self.session:
                await self.session.close()  # Close the session
            if self.runner:
                await self.runner.cleanup()

        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(cleanup(), self.loop).result(timeout=5)
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

        async def startup():
            # Create the persistent session
            self.session = aiohttp.ClientSession()

            app = web.Application(client_max_size=1024 * 1024 * 2)
            app.router.add_route("*", "/{path:.*}", self._http_handler)

            self.runner = web.AppRunner(app)
            await self.runner.setup()

            site = web.TCPSite(self.runner, INADDR_ANY, WEBSOCKET_PORT)
            await site.start()

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
            # --- CHANGE THIS LINE to call the new multipart handler ---
            return await self._http_file_proxy_multipart_handler(request)
            # ---------------------------------------------------------
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

    async def _http_file_proxy_multipart_handler(self, request: web.Request):
        """
        Handles file uploads by creating a compliant multipart/form-data request
        for printers that do not support streaming.
        """
        remote_url = f"http://{self.printer.ip_address}:{WEBSOCKET_PORT}{request.path}"
        self.logger.info(
            f"Proxying file upload to {remote_url} using multipart/form-data"
        )

        try:
            # 1. Store the entire file in memory. This is required by the printer's API.
            file_data = await request.read()
            if not file_data:
                return web.Response(status=400, text="Bad Request: Empty file.")

            # 2. Calculate the required values for the headers.
            file_size = len(file_data)
            md5_hash = hashlib.md5(file_data).hexdigest()
            transfer_uuid = str(uuid.uuid4())

            self.logger.debug(
                f"File Size: {file_size}, MD5: {md5_hash}, UUID: {transfer_uuid}"
            )

            # 3. Construct the required headers for the printer.
            printer_headers = {
                "S-File-MD5": md5_hash,
                "Check": "1",
                "Offset": "0",
                "Uuid": transfer_uuid,
                "TotalSize": str(file_size),  # Must be a string
            }

            # 4. Create the multipart/form-data payload.
            # aiohttp.FormData will correctly set the Content-Type header.
            form_data = aiohttp.FormData()
            form_data.add_field(
                "file",  # 'file' is a standard field name, the API doesn't specify one.
                file_data,
                # The printer likely doesn't use the filename, but it's good practice.
                filename="proxied_file.gcode",
                content_type="application/octet-stream",
            )

            # 5. Send the compliant request to the printer.
            # Assumes you have the persistent self.session from the previous optimization.
            # If not, use 'async with aiohttp.ClientSession() as session:'.
            if not self.session:
                raise Exception("Persistent session not initialized.")

            async with self.session.post(
                remote_url, headers=printer_headers, data=form_data
            ) as response:
                self.logger.info(
                    f"Printer responded to multipart upload with status: {response.status}"
                )
                # 6. Forward the printer's response back to the original client.
                content = await response.read()
                return web.Response(
                    body=content,
                    status=response.status,
                    content_type=response.content_type,
                )

        except Exception as e:
            self.logger.error(f"HTTP multipart proxy error: {e}")
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
