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
        Initializes the Elegoo printer proxy server.

        Validates the printer configuration and starts HTTP/WebSocket and UDP discovery proxy services in a background thread.

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
        Determine if the required proxy ports are available.

        Returns:
            True if all ports are free; False if any port is already in use.
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
        """Shuts down the proxy server and cleans up resources."""
        self.logger.info("Stopping proxy server...")

        async def cleanup():
            """Asynchronously closes the session and cleans up the runner."""
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
        Return a copy of the printer object pointing to the local proxy server.
        """
        proxied_printer = Printer()
        proxied_printer.__dict__.update(self.printer.__dict__)
        proxied_printer.ip_address = self.get_local_ip()
        return proxied_printer

    def get_local_ip(self) -> str:
        """
        Returns the local IP address used to communicate with the printer.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                # Doesn't have to be reachable
                s.connect((self.printer.ip_address or "8.8.8.8", 1))
                return s.getsockname()[0]
        except Exception:
            return LOCALHOST

    def _start_servers_in_thread(self):
        """Starts the proxy servers in a dedicated asyncio event loop."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def startup():
            """Initializes and starts all server components."""
            self.session = aiohttp.ClientSession()

            app = web.Application(
                client_max_size=512 * 1024 * 1024
            )  # Increased max size
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
        """Handles all incoming HTTP requests, routing to the appropriate proxy handler."""
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._websocket_handler(request)

        if request.method == "POST" and request.path == "/uploadFile/upload":
            return await self._http_file_proxy_passthrough_handler(request)

        # All other HTTP requests are forwarded by the generic proxy
        return await self._http_proxy_handler(request)

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Handles and proxies WebSocket connections using aiohttp for both client and server."""
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
        Generic streaming HTTP reverse proxy handler.
        Forwards any HTTP request to the printer and streams the response back.
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
        Handles multipart file uploads via store-and-forward, as some printers do not support chunked encoding.
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
        super().__init__()
        self.logger = logger
        self.printer = printer
        self.proxy_ip = proxy_ip
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
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
