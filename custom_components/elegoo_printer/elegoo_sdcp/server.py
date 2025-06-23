"""Elegoo Printer."""

import asyncio
import json
import os
import socket
from threading import Event
from typing import Any, Union

from websockets.exceptions import ConnectionClosed
from websockets.legacy.client import WebSocketClientProtocol, connect
from websockets.legacy.server import WebSocketServerProtocol, serve

from .models.printer import Printer

DISCOVERY_PORT = 3000
WEBSOCKET_PORT = 3030


# --- Discovery and Websocket Proxy/Multiplexer ---

# Define a type alias for protocols that can be forwarded
Forwardable = Union[WebSocketServerProtocol, WebSocketClientProtocol]


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol to handle UDP discovery broadcasts by replying with JSON."""

    def __init__(self, logger: Any, printer: Printer, proxy_ip: str):
        self.logger = logger
        self.printer = printer
        self.proxy_ip = proxy_ip
        self.transport = None
        super().__init__()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        message = data.decode()
        if message == "M99999":
            self.logger.info(
                f"Discovery request received from {addr}, responding with JSON."
            )
            # Construct the JSON response based on the user-provided structure,
            # using details from the real printer object.
            response_payload = {
                "Id": getattr(self.printer, "connection", os.urandom(8).hex()),
                "Data": {
                    "Name": getattr(self.printer, "name", "Elegoo Proxy"),
                    "MachineName": getattr(self.printer, "name", "Elegoo Proxy"),
                    "BrandName": "Elegoo",
                    "MainboardIP": self.get_local_ip(),  # The crucial substitution
                    "MainboardID": getattr(self.printer, "id", "unknown"),
                    "ProtocolVersion": "V3.0.0",
                    "FirmwareVersion": getattr(self.printer, "version", "V1.0.0"),
                },
            }
            # The original implementation expects a string, so we send a string.
            # The client will parse this string.
            json_string = json.dumps(response_payload)
            if self.transport:
                self.transport.sendto(json_string.encode(), addr)

    def error_received(self, exc):
        self.logger.error(f"UDP Discovery Server Error: {exc}")

    def connection_lost(self, exc):
        self.logger.warning("UDP Discovery Server Closed.")
        super().connection_lost(exc)

    def get_local_ip(self):
        # Try to get the IP that would be used to reach the printer
        if self.printer.ip_address:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0)
            try:
                # Connect to the printer's IP to get the correct local interface
                s.connect((self.printer.ip_address, 1))
                IP = s.getsockname()[0]
            except Exception:
                IP = "127.0.0.1"
            finally:
                s.close()
            return IP
        return "127.0.0.1"


async def _forward_messages(source: Forwardable, dest: Forwardable, logger: Any):
    """Asynchronously forward messages from a source to a destination websocket."""
    try:
        while True:
            message = await source.recv()
            await dest.send(message)
    except ConnectionClosed:
        logger.info("Proxy connection closed, message forwarding stopped.")
    except Exception as e:
        logger.error(f"Error while forwarding messages in proxy: {e}")
    finally:
        # Ensure the other connection is also closed to terminate the pair.
        if not source.closed:
            await source.close()
        if not dest.closed:
            await dest.close()


async def _proxy_handler(
    local_client_ws: WebSocketServerProtocol, remote_ip: str, logger: Any
):
    """
    Handles a new client connection to the proxy.
    It establishes a connection to the remote printer and forwards messages between the client and the printer.
    """
    logger.info(f"Proxy client connected from {local_client_ws.remote_address}")
    remote_uri = f"ws://{remote_ip}:{WEBSOCKET_PORT}/websocket"
    try:
        # The context manager returns a WebSocketClientProtocol
        async with connect(remote_uri) as remote_printer_ws:
            logger.info(
                f"Proxy successfully connected to remote printer at {remote_uri}"
            )

            # Create two concurrent tasks to forward messages in both directions.
            forward_to_printer = asyncio.create_task(
                _forward_messages(local_client_ws, remote_printer_ws, logger)
            )
            forward_to_client = asyncio.create_task(
                _forward_messages(remote_printer_ws, local_client_ws, logger)
            )

            # Wait for either task to complete (which means a connection was closed).
            done, pending = await asyncio.wait(
                [forward_to_printer, forward_to_client],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel any pending tasks to ensure a clean shutdown of the proxy session.
            for task in pending:
                task.cancel()

    except ConnectionRefusedError:
        logger.error(
            f"Connection refused by the printer at {remote_uri}. Is it on and accessible?"
        )
    except Exception as e:
        logger.error(
            f"Proxy failed to connect or communicate with remote printer at {remote_uri}: {e}"
        )
    finally:
        if local_client_ws.remote_address:
            logger.info(
                f"Proxy client disconnected from {local_client_ws.remote_address}"
            )


def start_proxy_server(printer: Printer, logger: Any, startup_event: Event):
    """Starts the websocket and discovery proxy servers in its own asyncio event loop."""
    if not printer.ip_address:
        raise ValueError("Printer IP address is not set. Cannot start proxy server.")

    printer_ip_address = printer.ip_address
    logger.info(
        f"Attempting to start proxy server for remote printer {printer_ip_address}"
    )

    # Each thread needs its own asyncio event loop.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # --- Start WebSocket (TCP) Proxy Server ---
    def ws_handler(ws):
        return _proxy_handler(ws, printer_ip_address, logger)

    start_ws_server = serve(ws_handler, "0.0.0.0", WEBSOCKET_PORT)
    ws_server = loop.run_until_complete(start_ws_server)

    # --- Start Discovery (UDP) Proxy Server ---
    proxy_ip = socket.gethostbyname(socket.gethostname())

    def discovery_protocol_factory():
        return DiscoveryProtocol(logger, printer, proxy_ip)

    start_discovery_server = loop.create_datagram_endpoint(
        discovery_protocol_factory, local_addr=("0.0.0.0", DISCOVERY_PORT)
    )
    loop.run_until_complete(start_discovery_server)

    if ws_server.server.is_serving():
        logger.info(f"WebSocket Proxy running on ws://{proxy_ip}:{WEBSOCKET_PORT}")
        logger.info(f"Discovery Proxy listening on UDP port {DISCOVERY_PORT}")

    # Signal that the server is up and running.
    startup_event.set()

    loop.run_forever()


def is_port_in_use(host: str, port: int) -> bool:
    """Check if a TCP port is already in use on the given host."""
    check_host = "127.0.0.1" if host == "0.0.0.0" else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((check_host, port)) == 0
