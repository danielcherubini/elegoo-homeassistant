"""Elegoo Printer."""

import asyncio
import json
import os
import socket
import time
from threading import Thread
from types import MappingProxyType
from typing import Any

import websocket

from .const import DEBUG, LOGGER
from .models.attributes import PrinterAttributes
from .models.print_history_detail import PrintHistoryDetail
from .models.printer import Printer, PrinterData
from .models.status import PrinterStatus

DISCOVERY_TIMEOUT = 2
DISCOVERY_PORT = 3000
DEFAULT_PORT = 54780
WEBSOCKET_PORT = 3030


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
        self,
        ip_address: str,
        logger: Any = LOGGER,
        config: MappingProxyType[str, Any] = MappingProxyType({}),
    ) -> None:
        """
        Initialize the ElegooPrinterClient for communication with an Elegoo 3D printer.
        
        Creates internal printer data models, sets up configuration, and prepares logging and websocket references for subsequent printer operations.
        
        Parameters:
            ip_address (str): The IP address of the target printer.
            config (MappingProxyType[str, Any], optional): Read-only configuration for the printer.
        """
        self.ip_address: str = ip_address
        self.printer_websocket: websocket.WebSocketApp | None = None
        self.config = config
        self.printer: Printer = Printer(config=config)
        self.printer_data = PrinterData()
        self.logger = logger

    def get_printer_status(self) -> PrinterData:
        """
        Retrieve the latest status information from the printer.
        
        Sends a status request command to the printer and returns the most recent printer status data.
        
        Returns:
            PrinterData: The current status of the printer.
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
        """
        Enable or disable the printer's video stream.
        
        Parameters:
            toggle (bool): If True, enables the video stream; if False, disables it.
        """
        self._send_printer_cmd(386, {"Enable": int(toggle)})

    def get_printer_historical_tasks(self) -> None:
        """
        Requests the list of historical print tasks from the printer.
        
        Sends a command to retrieve the printer's print history records.
        """
        self._send_printer_cmd(320)

    def get_printer_task_detail(self, id_list: list[str]) -> None:
        """
        Request detailed information for a list of historical print tasks from the printer.
        
        Parameters:
            id_list (list[str]): List of task IDs for which to retrieve details.
        """
        self._send_printer_cmd(321, data={"Id": id_list})

    async def get_printer_current_task(self) -> list[PrintHistoryDetail]:
        """
        Retrieve details of the current print task from the printer.
        
        Returns:
            A list of PrintHistoryDetail objects representing the current print task, or an empty list if no current task is active.
        """
        if self.printer_data.status.print_info.task_id:
            self.get_printer_task_detail([self.printer_data.status.print_info.task_id])

            await asyncio.sleep(2)  # Give the printer time to respond
            return self.printer_data.print_history

        return []

    async def get_current_print_thumbnail(self) -> str | None:
        """
        Asynchronously obtains the thumbnail URL for the current print task.
        
        Returns:
            The thumbnail URL as a string if a current print task exists, otherwise None.
        """
        print_history = await self.get_printer_current_task()
        if print_history:
            return print_history[0].thumbnail

        return None

    def _send_printer_cmd(self, cmd: int, data: dict[str, Any] | None = None) -> None:
        """
        Send a JSON command with the specified command ID and data to the printer via the websocket connection.
        
        Raises:
            ElegooPrinterClientWebsocketError: If a websocket error occurs during sending.
            ElegooPrinterClientWebsocketConnectionError: If the websocket is not connected.
            OSError: If an operating system error occurs while sending the command.
        """
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

    def discover_printer(
        self, broadcast_address: str = "<broadcast>"
    ) -> Printer | None:
        """
        Broadcasts a UDP discovery message to find an Elegoo printer or proxy on the local network.
        
        Parameters:
            broadcast_address (str): The network address to which the discovery message is broadcast. Defaults to "<broadcast>".
        
        Returns:
            Printer | None: The discovered Printer object if a valid response is received and parsed; otherwise, None.
        """
        self.logger.info("Broadcasting for printer/proxy discovery...")
        msg = b"M99999"
        with socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP
        ) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(DISCOVERY_TIMEOUT)
            try:
                sock.sendto(msg, (broadcast_address, DISCOVERY_PORT))
                data, addr = sock.recvfrom(8192)
                self.logger.info(f"Discovery response received from {addr}")
            except TimeoutError:
                self.logger.warning("Printer/proxy discovery timed out.")
                return None
            except OSError as e:
                self.logger.exception(f"Socket error during discovery: {e}")
                return None

            # The response from the proxy will be JSON.
            printer = self._save_discovered_printer(data)
            if printer:
                self.logger.debug("Discovery successful.")
                self.printer = printer
                return printer

        return None

    def _save_discovered_printer(self, data: bytes) -> Printer | None:
        """
        Parse discovery response bytes and create a Printer object if valid.
        
        Attempts to decode the provided bytes as UTF-8 and instantiate a Printer using the decoded information and current configuration. Returns the Printer object if successful, or None if decoding or instantiation fails.
        """
        try:
            printer_info = data.decode("utf-8")
        except UnicodeDecodeError:
            self.logger.exception(
                "Error decoding printer discovery data. Data may be malformed."
            )
        else:
            try:
                printer = Printer(printer_info, config=self.config)
            except (ValueError, TypeError):
                self.logger.exception("Error creating Printer object")
            else:
                self.logger.info(f"Discovered: {printer.name} ({printer.ip_address})")
                return printer

        return None

    async def connect_printer(self, printer: Printer) -> bool:
        """
        Asynchronously connects to the specified Elegoo printer via a local WebSocket proxy.
        
        Attempts to establish a WebSocket connection to the printer or its proxy, enabling communication and shared access for multiple clients. Waits for the connection to be established within a timeout period.
        
        Parameters:
            printer (Printer): The printer instance to connect to.
        
        Returns:
            bool: True if the connection is successfully established, False otherwise.
        """
        self.printer = printer

        # Connect this client to the discovered printer/proxy's WebSocket.
        url = f"ws://{self.printer.ip_address}:{WEBSOCKET_PORT}/websocket"
        self.logger.info(f"Client connecting to WebSocket at: {url}")

        websocket.setdefaulttimeout(1)

        def ws_msg_handler(ws, msg: str) -> None:  # noqa: ANN001, ARG001
            """
            Handle an incoming websocket message by parsing and dispatching it to the appropriate response handler.
            """
            self._parse_response(msg)

        def ws_connected_handler(name: str) -> None:
            """
            Log a message indicating a successful connection to the specified proxy target.
            
            Parameters:
                name (str): Identifier of the proxy target to which the client has connected.
            """
            self.logger.info(f"Client successfully connected via proxy to: {name}")

        def on_close(
            ws,  # noqa: ANN001, ARG001
            close_status_code: str,
            close_msg: str,
        ) -> None:
            """
            Handles the closure of the websocket connection to the printer.
            
            Resets the internal websocket reference and logs the closure event with the provided status code and message.
            """
            self.logger.debug(
                f"Connection to {self.printer.name} (via proxy) closed: {close_msg} ({close_status_code})"
            )
            self.printer_websocket = None

        def on_error(ws, error) -> None:  # noqa: ANN001, ARG001
            """
            Handle websocket errors by logging the error and resetting the printer websocket reference.
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
                    f"Verified WebSocket connection to {self.printer.name}."
                )
                return True

        self.logger.warning(
            f"Failed to connect WebSocket to {self.printer.name} within timeout."
        )
        self.printer_websocket = None
        return False

    def _parse_response(self, response: str) -> None:
        """
        Parses an incoming JSON response message from the printer and dispatches it to the appropriate handler based on the message topic.
        
        If the topic is recognized, routes the message to the corresponding internal handler for response, status, or attributes. Logs notices, errors, unknown topics, missing topics, and JSON decoding errors.
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
        Process a response message by extracting nested print history data and forwarding it to the print history handler.
        
        Parameters:
            data (dict): Parsed JSON response containing print history details.
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
        Parse printer status data and update the internal printer status.
        
        Parameters:
            data (dict): JSON-compatible dictionary containing the printer's status information.
        """
        if DEBUG:
            self.logger.debug(f"status >> \n{json.dumps(data, indent=5)}")
        printer_status = PrinterStatus.from_json(json.dumps(data))
        self.printer_data.status = printer_status

    def _attributes_handler(self, data: dict[str, Any]) -> None:
        """
        Parse printer attribute data from a dictionary and update the internal printer attributes state.
        
        Parameters:
            data (dict): JSON-compatible dictionary containing printer attribute information.
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
