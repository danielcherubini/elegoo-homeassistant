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

from custom_components.elegoo_printer.api import ElegooPrinterConnectionError
from custom_components.elegoo_printer.elegoo_sdcp.models.video import ElegooVideo

from .const import DEBUG, LOGGER
from .models.attributes import PrinterAttributes
from .models.print_history_detail import PrintHistoryDetail
from .models.printer import Printer, PrinterData
from .models.status import LightStatus, PrinterStatus

DISCOVERY_TIMEOUT = 5
DISCOVERY_PORT = 3000
DEFAULT_PORT = 54780
WEBSOCKET_PORT = 3030


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
        Initialize an ElegooPrinterClient for communicating with an Elegoo 3D printer.

        Parameters:
            ip_address (str): The IP address of the target printer.
            config (Dict, optional): A Dictionary containing the config for the printer

        Initializes internal state, including printer data models, websocket references, and logging.
        """
        self.ip_address: str = ip_address
        self.printer_websocket: websocket.WebSocketApp | None = None
        self.config = config
        self.printer: Printer = Printer(config=config)
        self.printer_data = PrinterData()
        self.logger = logger

    def get_printer_status(self) -> PrinterData:
        """
        Retrieve the current status of the printer.

        Returns:
            PrinterData: The latest printer status information.
        """
        self._send_printer_cmd(0)
        return self.printer_data

    def get_printer_attributes(self) -> PrinterData:
        """Retreves the printer attributes."""
        self._send_printer_cmd(1)
        return self.printer_data

    def set_printer_video_stream(self, *, toggle: bool) -> None:
        """
        Enable or disable the printer's video stream.

        Parameters:
                toggle (bool): If True, enables the video stream; if False, disables it.
        """
        self._send_printer_cmd(386, {"Enable": int(toggle)})

    async def get_printer_video(self, toggle: bool = False) -> ElegooVideo:
        """
        Toggle the printer's video stream and retrieve the current video stream information.

        Parameters:
            toggle (bool): If True, enables the video stream; if False, disables it.

        Returns:
            ElegooVideo: The current video stream information from the printer.
        """
        self.set_printer_video_stream(toggle=toggle)
        await asyncio.sleep(2)
        return self.printer_data.video

    def get_printer_historical_tasks(self) -> None:
        """
        Requests the list of historical print tasks from the printer.
        """
        self._send_printer_cmd(320)

    def get_printer_task_detail(self, id_list: list[str]) -> None:
        """
        Retreves historical tasks from printer.
        """
        self._send_printer_cmd(321, data={"Id": id_list})

    async def get_printer_current_task(self) -> list[PrintHistoryDetail]:
        """
        Retreves current task.
        """
        if self.printer_data.status.print_info.task_id:
            self.get_printer_task_detail([self.printer_data.status.print_info.task_id])

            await asyncio.sleep(2)  # Give the printer time to respond
            return self.printer_data.print_history

        return []

    async def get_current_print_thumbnail(self) -> str | None:
        """
        Asynchronously returns the thumbnail URL of the current print task, or None if unavailable.

        Returns:
            str | None: The thumbnail URL of the current print task, or None if no task is active or no thumbnail exists.
        """
        print_history = await self.get_printer_current_task()
        if print_history:
            return print_history[0].thumbnail

        return None

    def set_light_status(self, light_status: LightStatus) -> None:
        """
        Set the printer's light status using the provided LightStatus configuration.

        Parameters:
            light_status (LightStatus): The desired light status to apply to the printer.
        """
        self._send_printer_cmd(403, light_status.to_dict())

    def _send_printer_cmd(self, cmd: int, data: dict[str, Any] | None = None) -> None:
        """
        Sends a JSON command to the printer over the WebSocket connection.

        Raises:
            PlatformNotReady: If the websocket is not connected or a websocket error occurs.
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
                raise ElegooPrinterConnectionError from e
            except (
                OSError
            ):  # Catch potential OS errors like Broken Pipe, Connection Refused
                self.logger.exception("Operating System error during send")
                raise  # Re-raise OS errors
        else:
            self.logger.warning(
                "Attempted to send command but websocket is not connected."
            )
            raise ElegooPrinterConnectionError("Not connected")

    def discover_printer(
        self, broadcast_address: str = "<broadcast>"
    ) -> Printer | None:
        """
        Broadcasts a UDP message to discover an Elegoo printer or proxy on the local network.

        Parameters:
            broadcast_address (str): The network address to send the discovery message to. Defaults to "<broadcast>".

        Returns:
            Printer | None: The discovered Printer object if a valid response is received; otherwise, None.
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
        Establish an asynchronous connection to the Elegoo printer via a local WebSocket proxy.

        If a local proxy server is not running, starts one that connects to the remote printer, enabling multiple local clients to share a single printer connection. Discovers the printer or proxy, then connects to its WebSocket interface. Waits for the connection to be established or times out.

        Returns:
            bool: True if the connection to the printer via the proxy was successful, False otherwise.
        """
        self.printer = printer

        # Connect this client to the discovered printer/proxy's WebSocket.
        url = f"ws://{self.printer.ip_address}:{WEBSOCKET_PORT}/websocket"
        self.logger.info(f"Client connecting to WebSocket at: {url}")

        websocket.setdefaulttimeout(1)

        def ws_msg_handler(ws, msg: str) -> None:  # noqa: ANN001, ARG001
            """
            Handles incoming websocket messages by parsing the response and routing it to the appropriate handler.
            """
            self._parse_response(msg)

        def ws_connected_handler(name: str) -> None:
            """
            Logs a message indicating a successful client connection to the specified proxy target.

            Parameters:
                name (str): The name or identifier of the proxy target to which the client connected.
            """
            self.logger.info(f"Client successfully connected via proxy to: {name}")

        def on_close(
            ws,  # noqa: ANN001, ARG001
            close_status_code: str,
            close_msg: str,
        ) -> None:
            """
            Handles the event when the websocket connection to the printer is closed.

            Resets the internal websocket reference and logs the closure event with the provided status code and message.
            """
            self.logger.debug(
                f"Connection to {self.printer.name} (via proxy) closed: {close_msg} ({close_status_code})"
            )
            self.printer_websocket = None

        def on_error(ws, error) -> None:  # noqa: ANN001, ARG001
            """
            Handles websocket errors by logging the error and clearing the printer websocket reference.
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
        Parse and route an incoming JSON response message from the printer.

        Attempts to decode the response as JSON and dispatches it to the appropriate handler based on the message topic. Logs unknown topics, missing topics, and JSON decoding errors.
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
        Handles response messages by dispatching to the appropriate handler based on the command type.

        Routes print history and video stream response data to their respective handlers according to the command ID in the response.
        """
        if DEBUG:
            self.logger.debug(f"response >> \n{json.dumps(data, indent=5)}")
        try:
            inner_data = data.get("Data")
            if inner_data:
                data_data = inner_data.get("Data", {})
                cmd: int = inner_data.get("Cmd", 0)
                if cmd == 320:
                    self._print_history_handler(data_data)
                elif cmd == 386:
                    self._print_video_handler(data_data)
        except json.JSONDecodeError:
            self.logger.exception("Invalid JSON")

    def _status_handler(self, data: dict[str, Any]) -> None:
        """
        Parses and updates the printer's status information from the provided data.

        Parameters:
            data (dict): Dictionary containing the printer status information in JSON-compatible format.
        """
        if DEBUG:
            self.logger.debug(f"status >> \n{json.dumps(data, indent=5)}")
        printer_status = PrinterStatus.from_json(json.dumps(data))
        self.printer_data.status = printer_status

    def _attributes_handler(self, data: dict[str, Any]) -> None:
        """
        Parses and updates the printer's attribute data from a JSON dictionary.

        Parameters:
            data (dict): Dictionary containing printer attribute information.
        """
        if DEBUG:
            self.logger.debug(f"attributes >> \n{json.dumps(data, indent=5)}")
        printer_attributes = PrinterAttributes.from_json(json.dumps(data))
        self.printer_data.attributes = printer_attributes

    def _print_history_handler(self, data_data: dict[str, Any]) -> None:
        """
        Parses and updates the printer's print history details from the provided data.

        If a list of print history details is present in the input, updates the printer data with a list of `PrintHistoryDetail` objects.
        """
        history_data_list = data_data.get("HistoryDetailList")
        if history_data_list:
            print_history_detail_list: list[PrintHistoryDetail] = [
                PrintHistoryDetail(history_data) for history_data in history_data_list
            ]
            self.printer_data.print_history = print_history_detail_list

    def _print_video_handler(self, data_data: dict[str, Any]) -> None:
        """
        Parse video stream data and update the printer's video attribute.

        Parameters:
            data_data (dict[str, Any]): Dictionary containing video stream information.
        """
        self.printer_data.video = ElegooVideo(data_data)
