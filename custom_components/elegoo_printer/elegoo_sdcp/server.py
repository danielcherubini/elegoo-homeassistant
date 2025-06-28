"""Elegoo Printer Server and Proxy."""

import asyncio
import json
import os
import socket
from threading import Event, Thread
from typing import Any

import aiohttp
from aiohttp import ClientSession, WSMsgType, web
from homeassistant.exceptions import ConfigEntryNotReady

from .models.printer import Printer

LOCALHOST = "127.0.0.1"
INADDR_ANY = "0.0.0.0"
DISCOVERY_PORT = 3000
WEBSOCKET_PORT = 3030


class ElegooPrinterServer:
    """
    Manages local proxy servers for an Elegoo printer, including WebSocket, UDP discovery, and a full HTTP reverse proxy.
    """

    def __init__(self, printer: Printer, logger: Any):
        """
        Initialize the Elegoo printer proxy server and start HTTP/WebSocket and UDP discovery proxy services in a background thread.

        Validates the printer configuration and ensures required ports are available. Raises an exception if the printer IP is missing or if the proxy server fails to start within 10 seconds.

        Raises:
            ConfigEntryNotReady: If the printer IP address is not set or the proxy server does not start successfully within the timeout.
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
        Check if the required TCP and UDP ports for the proxy server are available.

        Returns:
            bool: True if both the WebSocket (TCP) and discovery (UDP) ports are free; False if either port is in use.
        """
        for port, proto, name in [
            (WEBSOCKET_PORT, socket.SOCK_STREAM, "TCP"),
            (DISCOVERY_PORT, socket.SOCK_DGRAM, "UDP"),
        ]:
            try:
                with socket.socket(socket.AF_INET, proto) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind((INADDR_ANY, port))
            except OSError:
                self.logger.error(
                    f"{name} port {port} is already in use. Proxy server cannot start."
                )
                return False
        return True

    def stop(self):
        """
        Stops the proxy server and performs asynchronous cleanup of resources.

        This method closes the HTTP client session, cleans up the web runner, and safely stops the event loop if it is running.
        """
        self.logger.info("Stopping proxy server...")

        async def cleanup():
            """
            Asynchronously closes the HTTP client session and cleans up the web application runner if they exist.
            """
            if self.session and not self.session.closed:
                await self.session.close()
            if self.runner:
                await self.runner.cleanup()

        if self.loop and self.loop.is_running():
            try:
                # Schedule cleanup and wait for it to complete
                future = asyncio.run_coroutine_threadsafe(cleanup(), self.loop)
                future.result(timeout=5)
            except Exception as e:
                self.logger.error(f"Error during async cleanup: {e}")
            finally:
                # Stop the event loop
                self.loop.call_soon_threadsafe(self.loop.stop)

        self.logger.info("Proxy server stopped.")

    def get_printer(self) -> Printer:
        """
        Return a copy of the printer object with its IP address set to the local proxy server.

        Returns:
            Printer: A new Printer instance identical to the original, except its IP address points to the local proxy.
        """
        proxied_printer = Printer()
        proxied_printer.__dict__.update(self.printer.__dict__)
        proxied_printer.ip_address = self.get_local_ip()
        return proxied_printer

    def get_local_ip(self) -> str:
        """
        Determine the local IP address used for outbound communication to the printer.

        Returns:
            str: The local IP address, or "127.0.0.1" if detection fails.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                # Doesn't have to be reachable
                s.connect((self.printer.ip_address or "8.8.8.8", 1))
                return s.getsockname()[0]
        except Exception:
            return LOCALHOST

    def _start_servers_in_thread(self):
        """
        Starts the HTTP/WebSocket and UDP discovery proxy servers in a dedicated asyncio event loop.

        Initializes an aiohttp server for HTTP and WebSocket proxying on TCP port 3030 and a UDP discovery server on port 3000. Handles server startup errors by logging and signaling the main thread. Runs the event loop until stopped, then closes it upon shutdown.
        """
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def startup():
            """
            Asynchronously initializes and starts the HTTP/WebSocket proxy server and UDP discovery server components.

            Sets up an aiohttp web application with increased client size, configures a catch-all route for proxying, and starts the TCP server for HTTP and WebSocket traffic. Also starts the UDP discovery server for printer discovery requests. Logs errors and signals startup completion or failure via an event.
            """
            self.session = aiohttp.ClientSession()

            # Configure max upload size (default 2MB for large firmware/model files)
            max_size = self.printer.__dict__.get("client_max_size", 2 * 1024 * 1024)
            app = web.Application(client_max_size=max_size)

            app.router.add_route("*", "/{path:.*}", self._http_handler)

            self.runner = web.AppRunner(app)
            await self.runner.setup()

            site = web.TCPSite(self.runner, INADDR_ANY, WEBSOCKET_PORT)

            try:
                await site.start()
            except OSError as e:
                self.logger.error(
                    f"Failed to start TCP site on port {WEBSOCKET_PORT}, it may be in use. Error: {e}"
                )
                self.startup_event.set()  # Signal to unblock main thread for shutdown
                return

            # --- Start Discovery (UDP) Proxy Server ---
            try:

                def discovery_factory():
                    """
                    Creates and returns a new DiscoveryProtocol instance configured with the current logger, printer, and local proxy IP address.
                    """
                    return DiscoveryProtocol(
                        self.logger, self.printer, self.get_local_ip()
                    )

                if self.loop:
                    await self.loop.create_datagram_endpoint(
                        discovery_factory, local_addr=(INADDR_ANY, DISCOVERY_PORT)
                    )
                    self.logger.info(
                        f"Discovery Proxy listening on UDP port {DISCOVERY_PORT}"
                    )
            except OSError as e:
                self.logger.error(
                    f"Failed to start UDP Discovery on port {DISCOVERY_PORT}. Error: {e}"
                )
                self.startup_event.set()  # Signal to unblock main thread for shutdown
                return

            self.logger.info(
                f"Unified HTTP/WebSocket Proxy running on http://{self.get_local_ip()}:{WEBSOCKET_PORT}"
            )
            # Signal that startup is complete and successful
            self.startup_event.set()

        self.loop.run_until_complete(startup())
        self.loop.run_forever()
        # After loop stops, close it
        self.loop.close()

    async def _http_handler(self, request: web.Request) -> web.StreamResponse:
        """
        Routes incoming HTTP requests to the appropriate proxy handler based on request type.

        WebSocket upgrade requests are routed to the WebSocket proxy handler, file upload POST requests to the file upload passthrough handler, and all other requests to the generic HTTP proxy handler.

        Returns:
            web.StreamResponse: The response generated by the selected proxy handler.
        """
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._websocket_handler(request)

        if request.method == "POST" and request.path == "/uploadFile/upload":
            return await self._http_file_proxy_passthrough_handler(request)

        # All other HTTP requests are forwarded by the generic proxy
        return await self._http_proxy_handler(request)

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """
        Proxies a WebSocket connection between the client and the remote printer.

        Establishes a WebSocket connection with the client, connects to the printer's WebSocket endpoint, and forwards messages bidirectionally between the two. Closes the client connection if the upstream session is unavailable or on error.

        Returns:
            web.WebSocketResponse: The WebSocket response object for the client connection.
        """
        client_ws = web.WebSocketResponse()
        await client_ws.prepare(request)
        self.logger.info(f"WebSocket client connected from {request.remote}")

        if not self.session or self.session.closed:
            self.logger.error("Cannot proxy WebSocket, main client session is closed.")
            await client_ws.close(code=1011, message=b"Upstream connection failed")
            return client_ws

        remote_ws_url = (
            f"ws://{self.printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"
        )
        try:
            async with self.session.ws_connect(
                remote_ws_url, headers=request.headers
            ) as remote_ws:
                self.logger.info(
                    f"Proxy connected to remote printer WebSocket at {self.printer.ip_address}"
                )

                async def forward(source, dest, direction):
                    """
                    Forwards WebSocket messages from a source to a destination, handling both text and binary frames.

                    Parameters:
                        source: The source WebSocket connection to read messages from.
                        dest: The destination WebSocket connection to send messages to.
                        direction: A string label used for logging the direction of message forwarding.
                    """
                    async for msg in source:
                        if msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
                            (
                                await dest.send_bytes(msg.data)
                                if msg.type == WSMsgType.BINARY
                                else await dest.send_str(msg.data)
                            )
                        elif msg.type == WSMsgType.ERROR:
                            self.logger.error(
                                f"WebSocket error in {direction}: {source.exception()}"
                            )
                            break

                # Create tasks to run the forwarding coroutines concurrently
                to_printer = asyncio.create_task(
                    forward(client_ws, remote_ws, "client-to-printer")
                )
                to_client = asyncio.create_task(
                    forward(remote_ws, client_ws, "printer-to-client")
                )

                done, pending = await asyncio.wait(
                    [to_printer, to_client], return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()

        except Exception as e:
            self.logger.error(f"WebSocket proxy error: {e}")
        finally:
            self.logger.info(f"WebSocket client disconnected from {request.remote}")
            if not client_ws.closed:
                await client_ws.close()
        return client_ws

    async def _http_proxy_handler(self, request: web.Request) -> web.StreamResponse:
        """
        Streams HTTP requests to the printer and relays the printer's response back to the client.

        Forwards any incoming HTTP request to the printer's HTTP server, streaming both the request body and the response content. Returns a 502 Bad Gateway response if the proxy is not properly configured or if an upstream connection error occurs.
        """
        if not self.printer.ip_address or not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        target_url = (
            f"http://{self.printer.ip_address}:{WEBSOCKET_PORT}{request.path_qs}"
        )

        try:
            async with self.session.request(
                request.method,
                target_url,
                headers=request.headers,
                data=request.content,  # Stream the request body
                allow_redirects=False,
            ) as upstream_response:
                # Prepare a streaming response for the client
                client_response = web.StreamResponse(
                    status=upstream_response.status, headers=upstream_response.headers
                )
                await client_response.prepare(request)

                # Stream the response content from the printer back to the client
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
        """
        Proxies multipart file upload requests to the printer by buffering the entire request and forwarding it in a single POST.

        Reads the full upload body into memory to avoid chunked encoding, then sends it to the printer and returns the printer's response to the client.

        Returns:
            web.Response: The response from the printer, or a 502 Bad Gateway response on error.
        """
        remote_url = f"http://{self.printer.ip_address}:{WEBSOCKET_PORT}{request.path}"
        self.logger.debug(
            f"Proxying file upload to {remote_url} via store-and-forward."
        )

        if not self.session or self.session.closed:
            return web.Response(status=502, text="Bad Gateway: Proxy not configured")

        try:
            # Store: Read the entire request body into memory.
            raw_body = await request.read()

            # Forward: Send the complete body to the printer. aiohttp will add the Content-Length.
            async with self.session.post(
                remote_url, headers=request.headers, data=raw_body
            ) as response:
                self.logger.debug(
                    f"Printer responded to proxied upload with status: {response.status}"
                )
                # Forward the printer's exact response back to the client.
                content = await response.read()
                return web.Response(
                    body=content,
                    status=response.status,
                    headers=response.headers,
                )
        except Exception as e:
            self.logger.error(f"HTTP file passthrough proxy error: {e}")
            return web.Response(status=502, text=f"Bad Gateway: {e}")


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol to handle UDP discovery broadcasts by replying with printer info."""

    def __init__(self, logger: Any, printer: Printer, proxy_ip: str):
        """
        Initialize the DiscoveryProtocol for handling UDP discovery requests.

        Parameters:
                printer (Printer): The printer object containing identification and version information.
                proxy_ip (str): The IP address of the proxy server to advertise in discovery responses.
        """
        super().__init__()
        self.logger = logger
        self.printer = printer
        self.proxy_ip = proxy_ip
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        """
        Handles incoming UDP datagrams for printer discovery requests.

        If the received datagram matches the discovery command ("M99999"), sends a JSON response containing printer identification and version information to the sender.
        """
        if data.decode() == "M99999":
            self.logger.debug(f"Discovery request received from {addr}, responding.")
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
