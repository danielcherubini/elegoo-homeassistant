"""Elegoo Printer."""

import asyncio
import json
import os
import socket
import time
from threading import Thread
from typing import Any

import websocket
from websockets.exceptions import ConnectionClosed
from websockets.legacy.client import connect
from websockets.legacy.protocol import WebSocketCommonProtocol
from websockets.legacy.server import WebSocketServerProtocol, serve

from .const import DEBUG, LOGGER
from .models.attributes import PrinterAttributes
from .models.print_history_detail import PrintHistoryDetail
from .models.printer import Printer, PrinterData
from .models.status import PrinterStatus

DISCOVERY_TIMEOUT = 1
DEFAULT_PORT = 54780
PROXY_PORT = 3030


# --- Websocket Proxy/Multiplexer ---
# This section contains the logic for the local proxy server.


async def _forward_messages(
    source: WebSocketCommonProtocol, dest: WebSocketCommonProtocol, logger: Any
):
    """
    Continuously forwards messages from the source websocket to the destination websocket until the connection is closed or an error occurs. Ensures both websockets are closed when forwarding stops.
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
    Handles a new client connection to the local proxy server by establishing a websocket connection to the remote printer and forwarding messages bidirectionally between the client and the printer.
    
    Parameters:
        local_client_ws (WebSocketServerProtocol): The websocket connection from the local client.
        remote_ip (str): The IP address of the remote printer.
        logger (Any): Logger for recording connection events and errors.
    """
    logger.info(f"Proxy client connected from {local_client_ws.remote_address}")
    remote_uri = f"ws://{remote_ip}:{PROXY_PORT}/websocket"
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

    except Exception as e:
        logger.error(
            f"Proxy failed to connect or communicate with remote printer at {remote_uri}: {e}"
        )
    finally:
        logger.info(f"Proxy client disconnected from {local_client_ws.remote_address}")


def start_proxy_server(remote_ip: str, logger: Any):
    """
    Start a websocket proxy server on localhost to forward connections to a remote printer.
    
    The proxy server runs in its own asyncio event loop and thread, listening on port 3030. It forwards websocket messages between local clients and the specified remote printer IP.
    """
    logger.info(
        f"Attempting to start websocket proxy server for remote printer {remote_ip}"
    )

    # Each thread needs its own asyncio event loop.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Create a partial function to pass arguments to the handler.
    def handler(ws, path):
        """
        Handles incoming websocket connections to the local proxy server and forwards them to the remote printer.
        
        Delegates the connection to the internal proxy handler, enabling bidirectional message forwarding between the local client and the remote printer.
        """
        return _proxy_handler(ws, remote_ip, logger)

    start_server = serve(handler, "localhost", PROXY_PORT)

    loop.run_until_complete(start_server)
    logger.info(f"Proxy server is running on ws://localhost:{PROXY_PORT}")
    loop.run_forever()


def is_port_in_use(host: str, port: int) -> bool:
    """
    Check whether a TCP port is currently open and accepting connections on the specified host.
    
    Parameters:
        host (str): The hostname or IP address to check.
        port (int): The TCP port number to check.
    
    Returns:
        bool: True if the port is in use, False otherwise.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


# --- Original Client Class (Modified) ---


class ElegooPrinterClientWebsocketError(Exception):
    """Exception to indicate a general API error."""


class ElegooPrinterClientWebsocketConnectionError(Exception):
    """Exception to indicate a Websocket Connection error."""


class ElegooPrinterClient:
    """
    Client for interacting with an Elegoo printer.

    Uses the SDCP Protocol (https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0).
    Includes a local websocket proxy to allow multiple local clients to communicate with one printer.
    """

    def __init__(
        self, ip_address: str, centauri_carbon: bool = False, logger: Any = LOGGER
    ) -> None:
        """
        Initialize an ElegooPrinterClient for communicating with an Elegoo 3D printer.
        
        Parameters:
            ip_address (str): The IP address of the target printer.
            centauri_carbon (bool, optional): Set to True if the printer is a Centauri Carbon model. Defaults to False.
        
        Initializes internal state, including printer data, websocket connection, logger, and proxy thread management.
        """
        self.ip_address: str = ip_address
        self.centauri_carbon: bool = centauri_carbon
        self.printer_websocket: websocket.WebSocketApp | None = None
        self.printer: Printer = Printer()
        self.printer_data = PrinterData()
        self.logger = logger
        self.proxy_thread: Thread | None = None

    def get_printer_status(self) -> PrinterData:
        """
        Retrieve the current status of the printer.
        
        Returns:
            PrinterData: The updated printer status data.
        
        Raises:
            ElegooPrinterClientWebsocketError: If a websocket communication error occurs.
            OSError: If an OS-level error occurs during communication.
        """
        try:
            self._send_printer_cmd(0)
        except (ElegooPrinterClientWebsocketError, OSError):
            self.logger.exception(
                "Error sending printer command in process_printer_job"
            )
            raise
        return self.printer_data

    def get_printer_attributes(self) -> PrinterData:
        """Retreves the printer attributes."""
        try:
            self._send_printer_cmd(1)
        except (ElegooPrinterClientWebsocketError, OSError):
            self.logger.exception(
                "Error sending printer command in process_printer_job"
            )
            raise
        return self.printer_data

    def set_printer_video_stream(self, *, toggle: bool) -> None:
        """Toggles the printer video stream."""
        self._send_printer_cmd(386, {"Enable": int(toggle)})

    def get_printer_historical_tasks(self) -> None:
        """Retreves historical tasks from printer."""
        self._send_printer_cmd(320)

    def get_printer_task_detail(self, id_list: list[str]) -> None:
        """Retreves historical tasks from printer."""
        self._send_printer_cmd(321, data={"Id": id_list})

    async def get_printer_current_task(self) -> list[PrintHistoryDetail]:
        """Retreves current task."""
        if self.printer_data.status.print_info.task_id:
            self.get_printer_task_detail([self.printer_data.status.print_info.task_id])

            await asyncio.sleep(2)
            return self.printer_data.print_history

        return []

    async def get_current_print_thumbnail(self) -> str | None:
        """
        Asynchronously retrieves the thumbnail URL of the current print task.

        Returns:
            str | None: The thumbnail URL if a current print task exists, otherwise None.
        """
        print_history = await self.get_printer_current_task()
        if print_history:
            return print_history[0].thumbnail

        return None

    def _send_printer_cmd(self, cmd: int, data: dict[str, Any] | None = None) -> None:
        """Send a command to the printer."""
        ts = int(time.time())
        data = data or {}
        payload = {
            "Id": self.printer.connection,
            "Data": {
                "Cmd": cmd,
                "Data": data,
                "RequestID": os.urandom(8).hex(),
                "MainboardID": self.printer.id,
                "TimeStamp": ts,
                "From": 0,
            },
            "Topic": f"sdcp/request/{self.printer.id}",
        }
        if DEBUG:
            self.logger.debug(f"printer << \n{json.dumps(payload, indent=4)}")
        if self.printer_websocket:
            try:
                self.printer_websocket.send(json.dumps(payload))
            except (
                websocket.WebSocketConnectionClosedException,
                websocket.WebSocketException,
            ) as e:
                self.logger.exception("WebSocket connection closed error")
                raise ElegooPrinterClientWebsocketError from e
            except (
                OSError
            ):  # Catch potential OS errors like Broken Pipe, Connection Refused
                self.logger.exception("Operating System error during send")
                raise  # Re-raise OS errors
        else:
            self.logger.warning(
                "Attempted to send command but websocket is not connected."
            )
            raise ElegooPrinterClientWebsocketConnectionError from Exception(
                "Not connected"
            )

    def discover_printer(self) -> Printer | None:
        """Discover the Elegoo printer on the network."""
        self.logger.info(f"Starting printer discovery at {self.ip_address}")
        msg = b"M99999"
        with socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP
        ) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(DISCOVERY_TIMEOUT)
            sock.bind(("", DEFAULT_PORT))
            try:
                _ = sock.sendto(msg, (self.ip_address, 3000))
                data = sock.recv(8192)
            except TimeoutError:
                self.logger.warning("Printer discovery timed out.")
            except OSError:
                self.logger.exception("Socket error during discovery")
            else:
                printer = self._save_discovered_printer(data)
                if printer:
                    self.logger.debug("Discovery done.")
                    self.printer = printer
                    return printer

        return None

    def _save_discovered_printer(self, data: bytes) -> Printer | None:
        try:
            printer_info = data.decode("utf-8")
        except UnicodeDecodeError:
            self.logger.exception(
                "Error decoding printer discovery data. Data may be malformed."
            )
        else:
            try:
                printer = Printer(printer_info, centauri_carbon=self.centauri_carbon)
            except (ValueError, TypeError):
                self.logger.exception("Error creating Printer object")
            else:
                self.logger.info(f"Discovered: {printer.name} ({printer.ip_address})")
                return printer

        return None

    async def connect_printer(self) -> bool:
        """
        Asynchronously connects to the Elegoo printer via a local websocket proxy server.
        
        If the proxy server is not running on localhost:3030, starts it in a daemon thread to forward messages between local clients and the remote printer. Then connects the client to the proxy, enabling multiple local applications to share the printer connection.
        
        Returns:
            bool: True if the connection to the printer via the proxy is successful, False otherwise.
        """
        # 1. Check if the proxy is already running on the default port.
        if not is_port_in_use("localhost", PROXY_PORT):
            self.logger.info(
                f"Local proxy not found on port {PROXY_PORT}. Starting new proxy server..."
            )
            proxy_thread = Thread(
                target=start_proxy_server,
                args=(self.printer.ip_address, self.logger),
                daemon=True,  # Daemon threads exit when the main program exits.
            )
            proxy_thread.start()
            self.proxy_thread = proxy_thread
            # Give the server a moment to initialize before trying to connect.
            await asyncio.sleep(2)
        else:
            self.logger.info(
                f"Local proxy found on port {PROXY_PORT}. Connecting to it."
            )

        # 2. Connect this client to the local proxy.
        url = f"ws://localhost:{PROXY_PORT}"
        self.logger.info(f"Client connecting to local proxy at: {url}")

        websocket.setdefaulttimeout(1)

        def ws_msg_handler(ws, msg: str) -> None:  # noqa: ANN001, ARG001
            """
            Handles incoming websocket messages by parsing the response.
            
            Parameters:
                msg (str): The message received from the websocket.
            """
            self._parse_response(msg)

        def ws_connected_handler(name: str) -> None:
            """
            Logs a message indicating that a client has successfully connected to the specified proxy.
            
            Parameters:
                name (str): The identifier or address of the proxy to which the client connected.
            """
            self.logger.info(f"Client successfully connected via proxy to: {name}")

        def on_close(
            ws,  # noqa: ANN001, ARG001
            close_status_code: str,
            close_msg: str,
        ) -> None:
            """
            Handles the event when the websocket connection to the printer (via proxy) is closed.
            
            Logs the closure event and resets the internal websocket reference.
            """
            self.logger.debug(
                f"Connection to {self.printer.name} (via proxy) closed: {close_msg} ({close_status_code})"  # noqa: E501
            )
            self.printer_websocket = None

        def on_error(ws, error) -> None:  # noqa: ANN001, ARG001
            """
            Handles websocket errors by logging the error and resetting the printer websocket connection state.
            """
            self.logger.error(
                f"Connection to {self.printer.name} (via proxy) error: {error}"
            )
            self.printer_websocket = None

        ws = websocket.WebSocketApp(
            url,
            on_message=ws_msg_handler,
            on_open=ws_connected_handler(self.printer.name),
            on_close=on_close,
            on_error=on_error,
        )
        self.printer_websocket = ws

        # Run the client's websocket connection in its own thread.
        thread = Thread(target=ws.run_forever, kwargs={"reconnect": 1}, daemon=True)
        thread.start()

        # Wait for the connection to be established.
        start_time = time.monotonic()
        timeout = 5
        while time.monotonic() - start_time < timeout:
            if ws.sock and ws.sock.connected:
                await asyncio.sleep(2)  # Allow time for initial messages if any.
                self.logger.info(
                    f"Verified connection to {self.printer.name} via proxy."
                )
                return True

        self.logger.warning(
            f"Failed to connect to {self.printer.name} via proxy within timeout."
        )
        self.printer_websocket = None
        return False

    def _parse_response(self, response: str) -> None:
        """
        Parses a JSON response message from the printer and dispatches it to the appropriate handler based on its topic.
        
        If the message contains a recognized topic, it is processed accordingly; otherwise, logs a warning or debug information for unknown or malformed messages.
        """
        try:
            data = json.loads(response)
            topic = data.get("Topic")
            if topic:
                match topic.split("/")[1]:
                    case "response":
                        self._response_handler(data)
                    case "status":
                        self._status_handler(data)
                    case "attributes":
                        self._attributes_handler(data)
                    case "notice":
                        self.logger.debug(f"notice >> \n{json.dumps(data, indent=5)}")
                    case "error":
                        self.logger.debug(f"error >> \n{json.dumps(data, indent=5)}")
                    case _:
                        self.logger.debug("--- UNKNOWN MESSAGE ---")
                        self.logger.debug(data)
                        self.logger.debug("--- UNKNOWN MESSAGE ---")
            else:
                self.logger.warning("Received message without 'Topic'")
                self.logger.debug(f"Message content: {response}")
        except json.JSONDecodeError:
            self.logger.exception("Invalid JSON received")

    def _response_handler(self, data: dict[str, Any]) -> None:
        """
        Handles a printer response message by extracting nested print history data and passing it to the print history handler.
        
        Parameters:
            data (dict): The response message containing nested "Data" fields.
        """
        if DEBUG:
            self.logger.debug(f"response >> \n{json.dumps(data, indent=5)}")
        try:
            data_data = data.get("Data", {}).get("Data", {})
            self._print_history_handler(data_data)
        except json.JSONDecodeError:
            self.logger.exception("Invalid JSON")

    def _status_handler(self, data: dict[str, Any]) -> None:
        """
        Parses printer status data from a dictionary and updates the internal printer status.
        """
        if DEBUG:
            self.logger.debug(f"status >> \n{json.dumps(data, indent=5)}")
        printer_status = PrinterStatus.from_json(json.dumps(data))
        self.printer_data.status = printer_status

    def _attributes_handler(self, data: dict[str, Any]) -> None:
        """
        Parses and updates the printer's attributes from the provided data dictionary.
        
        Parameters:
            data (dict): Dictionary containing printer attribute information in JSON-compatible format.
        """
        if DEBUG:
            self.logger.debug(f"attributes >> \n{json.dumps(data, indent=5)}")
        printer_attributes = PrinterAttributes.from_json(json.dumps(data))
        self.printer_data.attributes = printer_attributes

    def _print_history_handler(self, data_data: dict[str, Any]) -> None:
        history_data_list = data_data.get("HistoryDetailList")
        if history_data_list:
            print_history_detail_list: list[PrintHistoryDetail] = [
                PrintHistoryDetail(history_data) for history_data in history_data_list
            ]
            self.printer_data.print_history = print_history_detail_list
