"""Elegoo Printer."""

import asyncio
import json
import os
import socket
from threading import Event, Thread
from typing import Any, Union

from websockets.exceptions import ConnectionClosed
from websockets.legacy.client import WebSocketClientProtocol, connect
from websockets.legacy.server import WebSocketServerProtocol, serve

from .models.printer import Printer

DISCOVERY_PORT = 3000
WEBSOCKET_PORT = 3030


# Define a type alias for protocols that can be forwarded
Forwardable = Union[WebSocketServerProtocol, WebSocketClientProtocol]


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol to handle UDP discovery broadcasts by replying with JSON."""

    def __init__(self, logger: Any, printer: Printer, proxy_ip: str):
        """
        Initialize the DiscoveryProtocol with logging, printer information, and the proxy server's IP address.
        """
        self.logger = logger
        self.printer = printer
        self.proxy_ip = proxy_ip
        self.transport = None
        super().__init__()

    def connection_made(self, transport):
        """
        Store the transport object when a UDP connection is established.
        """
        self.transport = transport

    def datagram_received(self, data, addr):
        """
        Handles incoming UDP datagrams and responds to discovery requests with printer metadata in JSON format.

        When a discovery message ("M99999") is received, sends a JSON response containing printer identification and network details to the sender.
        """
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
        """
        Handles errors received by the UDP discovery server by logging the exception.
        """
        self.logger.error(f"UDP Discovery Server Error: {exc}")

    def connection_lost(self, exc):
        """
        Handles cleanup when the UDP discovery server connection is lost.
        """
        self.logger.warning("UDP Discovery Server Closed.")
        super().connection_lost(exc)

    def get_local_ip(self):
        # Try to get the IP that would be used to reach the printer
        """
        Determine the local IP address used to reach the printer.

        Returns:
            str: The local IP address that would be used to connect to the printer, or "127.0.0.1" if it cannot be determined.
        """
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
    """
    Continuously forwards messages from a source WebSocket to a destination WebSocket until the connection is closed or an error occurs.

    Parameters:
        source (Forwardable): The WebSocket connection to receive messages from.
        dest (Forwardable): The WebSocket connection to send messages to.
    """
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
    Handles a new WebSocket client connection to the proxy, establishing a connection to the remote printer and forwarding messages bidirectionally.

    Parameters:
        local_client_ws (WebSocketServerProtocol): The WebSocket connection from the local client.
        remote_ip (str): The IP address of the remote printer.

    The function manages the lifecycle of both connections, ensuring proper cleanup and logging connection events and errors.
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


class ElegooPrinterServer:
    """
    Manages the local WebSocket and UDP discovery proxy servers for an Elegoo printer.
    """

    def __init__(self, printer: Printer, logger: Any):
        """
        Initialize the ElegooPrinterServer.

        Args:
            printer (Printer): The Printer object representing the real printer.
            logger (Any): The logger instance to use for logging.
        """
        self.printer = printer
        self.logger = logger
        self.startup_event = Event()
        self.proxy_thread: Thread | None = None

        if not self.printer.ip_address:
            raise ValueError(
                "Printer IP address is not set. Cannot start proxy server."
            )

        self.logger.info(
            f"Attempting to start proxy server for remote printer {self.printer.ip_address}"
        )

        # Check if ports are available
        if is_port_in_use("127.0.0.1", WEBSOCKET_PORT):
            raise Exception(f"WebSocket port {WEBSOCKET_PORT} is already in use")

        self.proxy_thread = Thread(target=self._start_servers_in_thread, daemon=True)
        self.proxy_thread.start()

        ready = self.startup_event.wait(timeout=5.0)
        if not ready:
            self.logger.error("Proxy server failed to start within the timeout period.")
            raise Exception("Proxy server failed to start.")
        self.logger.info("Proxy server has started successfully.")

    def get_printer(self) -> Printer:
        proxied_printer = Printer()
        proxied_printer.__dict__.update(self.printer.__dict__)
        proxied_printer.ip_address = "127.0.0.1"
        return proxied_printer

    def _start_servers_in_thread(self):
        """
        Starts the WebSocket and UDP discovery proxy servers in a dedicated asyncio event loop.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # --- Start WebSocket (TCP) Proxy Server ---
        def ws_handler(ws):
            """
            Handles incoming WebSocket connections by forwarding them to the remote printer via the proxy.

            Returns:
                Coroutine that manages bidirectional message forwarding between the client WebSocket and the remote printer.
            """
            if not self.printer.ip_address:
                self.logger.error("No Printer IP Address")
                raise Exception("No Printer IP when starting proxy handler")

            return _proxy_handler(ws, self.printer.ip_address, self.logger)

        start_ws_server = serve(ws_handler, "0.0.0.0", WEBSOCKET_PORT)
        ws_server = loop.run_until_complete(start_ws_server)

        # --- Start Discovery (UDP) Proxy Server ---
        # Use the same logic as DiscoveryProtocol.get_local_ip()
        if self.printer.ip_address:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0)
            try:
                s.connect((self.printer.ip_address, 1))
                proxy_ip = s.getsockname()[0]
            except Exception:
                proxy_ip = "127.0.0.1"
            finally:
                s.close()
        else:
            proxy_ip = "127.0.0.1"

        def discovery_protocol_factory():
            """
            Creates and returns a new instance of the DiscoveryProtocol with the configured logger, printer, and proxy IP address.
            """
            return DiscoveryProtocol(self.logger, self.printer, proxy_ip)

        start_discovery_server = loop.create_datagram_endpoint(
            discovery_protocol_factory, local_addr=("0.0.0.0", DISCOVERY_PORT)
        )
        loop.run_until_complete(start_discovery_server)

        if ws_server.server.is_serving():
            self.logger.info(
                f"WebSocket Proxy running on ws://{proxy_ip}:{WEBSOCKET_PORT}"
            )
            self.logger.info(f"Discovery Proxy listening on UDP port {DISCOVERY_PORT}")

        # Signal that the server is up and running.
        self.startup_event.set()

        loop.run_forever()


def is_port_in_use(host: str, port: int) -> bool:
    """
    Determine whether a TCP port is currently open and accepting connections on the specified host.

    Parameters:
        host (str): The hostname or IP address to check. If "0.0.0.0", checks "127.0.0.1" instead.
        port (int): The TCP port number to check.

    Returns:
        bool: True if the port is in use (connection succeeds), False otherwise.
    """
    check_host = "127.0.0.1" if host == "0.0.0.0" else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((check_host, port)) == 0
