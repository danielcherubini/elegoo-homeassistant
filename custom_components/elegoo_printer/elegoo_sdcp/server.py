"""Elegoo Printer Server and Proxy."""

import asyncio
import json
import os
import socket
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
        """
        Initialize the ElegooPrinterServer, starting proxy servers for the specified printer in a background thread.
        
        Raises:
            ValueError: If the provided printer does not have an IP address.
            Exception: If the proxy server fails to start within 10 seconds.
        """
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
        """
        Shuts down the proxy server, closes network connections, and releases associated resources.
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
        Return a copy of the printer object with its IP address set to the local proxy's IP.
        
        Returns:
            Printer: A new Printer instance representing the proxied printer, using the local proxy IP address.
        """
        proxied_printer = Printer()
        proxied_printer.__dict__.update(self.printer.__dict__)
        proxied_printer.ip_address = self.get_local_ip()
        return proxied_printer

    def get_local_ip(self):
        """
        Determine the local IP address used to reach the printer.
        
        Attempts to open a UDP socket to the printer's IP address to discover the local network interface IP. Returns localhost if the operation fails.
        
        Returns:
            str: The detected local IP address, or "127.0.0.1" if detection fails.
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
        Initializes and starts the HTTP/WebSocket proxy server and UDP discovery server in a dedicated asyncio event loop running on a separate thread.
        
        Sets up an aiohttp web server to handle HTTP and WebSocket requests, and a UDP server for printer discovery. Signals readiness via the startup event and runs the event loop indefinitely.
        """
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def startup():
            # Create the persistent session
            """
            Initializes and starts the aiohttp web server for HTTP and WebSocket proxying.
            
            Creates a persistent HTTP client session, sets up the aiohttp application with a catch-all route handled by the internal HTTP handler, and starts the server listening on all interfaces at the designated WebSocket port.
            """
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
            """
            Creates and returns a new DiscoveryProtocol instance configured with the current logger, printer, and local proxy IP address.
            """
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
        """
        Routes incoming HTTP requests to the appropriate handler for WebSocket upgrades or file upload proxying.
        
        Returns:
            web.Response or web.WebSocketResponse: The response from the proxied handler or a 404 response if the request is unrecognized.
        """
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
        """
        Proxies a WebSocket connection between a client and the remote Elegoo printer.
        
        Establishes a WebSocket connection with the client and a separate connection to the printer, forwarding messages bidirectionally until either side disconnects. Cleans up connections and logs connection events and errors.
        Returns:
            ws (web.WebSocketResponse): The WebSocket response object for the client connection.
        """
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
                    """
                    Forwards incoming WebSocket messages from the client to the remote printer WebSocket.
                    
                    This coroutine iterates over messages received from the client WebSocket and sends text or binary messages to the remote printer WebSocket.
                    """
                    async for msg in ws:
                        if (
                            msg.type == web.WSMsgType.TEXT
                            or msg.type == web.WSMsgType.BINARY
                        ):
                            await remote_ws.send(msg.data)

                async def forward_to_client():
                    """
                    Forwards messages received from the remote WebSocket to the client WebSocket.
                    
                    Messages are sent as bytes if the received message is of type `bytes`; otherwise, they are sent as strings.
                    """
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
        Proxies multipart file upload POST requests to the printer, buffering the request to add a Content-Length header and forwarding required headers.
        
        Reads the entire request body to ensure compatibility with the printer's API, extracts and forwards necessary headers (such as Content-Type, S-File-MD5, Uuid, etc.), and relays the printer's response back to the client. Returns a 400 response if mandatory headers are missing, or a 502 response if an error occurs during proxying.
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
        """
        Initialize the DiscoveryProtocol for handling UDP discovery requests.
        
        Parameters:
            logger: Logger instance for recording events and errors.
            printer (Printer): The printer object containing metadata to be advertised.
            proxy_ip (str): The local proxy IP address to include in discovery responses.
        """
        self.logger = logger
        self.printer = printer
        self.proxy_ip = proxy_ip
        self.transport = None

    def connection_made(self, transport):
        """
        Store the transport object for sending UDP responses.
        """
        self.transport = transport

    def datagram_received(self, data, addr):
        """
        Handles incoming UDP datagrams for printer discovery requests.
        
        If the received data matches the discovery request string ("M99999"), sends a JSON response containing printer metadata and the proxy IP address to the sender.
        """
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
        """
        Logs errors encountered by the UDP discovery server.
        """
        self.logger.error(f"UDP Discovery Server Error: {exc}")
