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


class ElegooPrinterConnectionError(Exception):
    """Exception to indicate a connection error with the Elegoo printer."""

    pass


class ElegooPrinterNotConnectedError(Exception):
    """Exception to indicate that the Elegoo printer is not connected."""

    pass


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
        self.printer: Printer = Printer.from_dict(dict(config))
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

    async def async_get_printer_historical_tasks(
        self,
    ) -> dict[str, PrintHistoryDetail | None] | None:
        """
        Asynchronously requests the list of historical print tasks from the printer.
        """
        self._send_printer_cmd(320)
        await asyncio.sleep(2)  # Give the printer time to respond
        return self.printer_data.print_history

    def get_printer_task_detail(self, id_list: list[str]) -> PrintHistoryDetail | None:
        """
        Retrieves historical tasks from the printer.
        """
        for task_id in id_list:
            if task_id in self.printer_data.print_history:
                if self.printer_data.print_history[task_id] is None:
                    self._send_printer_cmd(321, data={"Id": [task_id]})
                else:
                    return self.printer_data.print_history[task_id]
        return None

    def get_printer_current_task(self) -> PrintHistoryDetail | None:
        """
        Retreves current task.
        """
        if self.printer_data.status.print_info.task_id:
            task_id = self.printer_data.status.print_info.task_id
            if task_id in self.printer_data.print_history:
                return self.printer_data.print_history[task_id]
            else:
                self.get_printer_task_detail([task_id])
                return None
        return None

    def get_printer_last_task(self) -> PrintHistoryDetail | None:
        """
        Retreves last task.
        """
        if self.printer_data.print_history:
            # Get task with the latest begin_time or end_time
            last_task_id = max(
                self.printer_data.print_history.keys(),
                key=lambda tid: (
                    self.printer_data.print_history[tid].end_time or 0
                    if self.printer_data.print_history[tid]
                    else 0
                ),
            )
            task = self.printer_data.print_history.get(last_task_id)
            if task is None:
                self.get_printer_task_detail([last_task_id])
            return task
        return None

    def get_current_print_thumbnail(self) -> str | None:
        """
        Returns the thumbnail URL of the current print task, or None if unavailable.

        Returns:
            str | None: The thumbnail URL of the current print task, or None if no task is active or no thumbnail exists.
        """
        if (task := self.get_printer_current_task()) and task.thumbnail:
            return task.thumbnail
        elif (task := self.get_printer_last_task()) and task.thumbnail:
            return task.thumbnail
        return None

    async def async_get_printer_current_task(self) -> PrintHistoryDetail | None:
        """
        Retreves current task.
        """
        if self.printer_data.status.print_info.task_id:
            task_id = self.printer_data.status.print_info.task_id
            if (
                task_id in self.printer_data.print_history
                and self.printer_data.print_history[task_id] is not None
            ):
                return self.printer_data.print_history[task_id]
            else:
                self.get_printer_task_detail([task_id])
                await asyncio.sleep(2)  # Give the printer time to respond
                if task_id in self.printer_data.print_history:
                    return self.printer_data.print_history[task_id]
        return None

    async def async_get_printer_last_task(self) -> PrintHistoryDetail | None:
        """
        Retreves last task.
        """
        if self.printer_data.print_history:
            # Get task with the latest begin_time or end_time
            last_task_id = max(
                self.printer_data.print_history.keys(),
                key=lambda tid: (
                    self.printer_data.print_history[tid].end_time or 0
                    if self.printer_data.print_history[tid]
                    else 0
                ),
            )
            task = self.printer_data.print_history.get(last_task_id)
            if task is None:
                self.get_printer_task_detail([last_task_id])
                await asyncio.sleep(2)  # Give the printer time to respond
                return self.printer_data.print_history.get(last_task_id)
            return task
        return None

    async def async_get_current_print_thumbnail(self) -> str | None:
        """
        Asynchronously returns the thumbnail URL of the current print task, or None if unavailable.

        Returns:
            str | None: The thumbnail URL of the current print task, or None if no task is active or no thumbnail exists.
        """
        if (task := await self.async_get_printer_current_task()) and task.thumbnail:
            return task.thumbnail
        elif (task := await self.async_get_printer_last_task()) and task.thumbnail:
            return task.thumbnail
        return None

    def set_light_status(self, light_status: LightStatus) -> None:
        """
        Set the printer's light status using the provided LightStatus configuration.

        Parameters:
            light_status (LightStatus): The desired light status to apply to the printer.
        """
        self._send_printer_cmd(403, light_status.to_dict())

    def print_pause(self) -> None:
        """
        Pause the current print.
        """
        self._send_printer_cmd(129, {})

    def print_stop(self) -> None:
        """
        Stop the current print.
        """
        self._send_printer_cmd(130, {})

    def print_resume(self) -> None:
        """
        Resume/continue the current print.
        """
        self._send_printer_cmd(131, {})

    def _send_printer_cmd(self, cmd: int, data: dict[str, Any] | None = None) -> None:
        """
        Send a JSON command to the printer via the WebSocket connection.

        Raises:
            ElegooPrinterNotConnectedError: If the printer is not connected.
            ElegooPrinterConnectionError: If a WebSocket error or timeout occurs during sending.
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
            except websocket.WebSocketTimeoutException as e:
                self.logger.info("WebSocket timeout error during send")
                raise ElegooPrinterConnectionError("WebSocket timeout") from e
            except (
                websocket.WebSocketConnectionClosedException,
                websocket.WebSocketException,
            ) as e:
                self.logger.info("WebSocket connection closed error")
                raise ElegooPrinterConnectionError from e
            except (
                OSError
            ):  # Catch potential OS errors like Broken Pipe, Connection Refused
                self.logger.info("Operating System error during send")
                raise  # Re-raise OS errors
        else:
            raise ElegooPrinterNotConnectedError("Not connected")

    def discover_printer(self, broadcast_address: str = "<broadcast>") -> list[Printer]:
        """
        Broadcasts a UDP discovery message to locate Elegoo printers or proxies on the local network.

        Sends a discovery request and collects responses within a timeout period, returning a list of discovered printers. If no printers are found or a socket error occurs, returns an empty list.

        Parameters:
            broadcast_address (str): The network address to send the discovery message to. Defaults to "<broadcast>".

        Returns:
            list[Printer]: List of discovered printers, or an empty list if none are found.
        """
        discovered_printers: list[Printer] = []
        self.logger.info("Broadcasting for printer/proxy discovery...")
        msg = b"M99999"
        with socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP
        ) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(DISCOVERY_TIMEOUT)
            try:
                sock.sendto(msg, (broadcast_address, DISCOVERY_PORT))
                while True:
                    try:
                        data, addr = sock.recvfrom(8192)
                        self.logger.info(f"Discovery response received from {addr}")
                        printer = self._save_discovered_printer(data)
                        if printer:
                            discovered_printers.append(printer)
                    except socket.timeout:
                        break  # Timeout, no more responses
            except OSError as e:
                self.logger.exception(f"Socket error during discovery: {e}")
                return []

        if not discovered_printers:
            self.logger.warning("No printers found during discovery.")
        else:
            self.logger.debug(f"Discovered {len(discovered_printers)} printer(s).")

        # Filter out printers on the same IP as the server with "None" or "Proxy" in the name
        local_ip = self.get_local_ip()
        filtered_printers = [
            p
            for p in discovered_printers
            if not (
                p.ip_address == local_ip and ("None" in p.name or "Proxy" in p.name)
            )
        ]

        return filtered_printers

    def get_local_ip(self) -> str:
        """
        Determine the local IP address used for outbound communication to the printer.

        Returns:
            str: The local IP address, or "127.0.0.1" if detection fails.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                # Doesn't have to be reachable
                s.connect((self.ip_address or "8.8.8.8", 1))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    def _save_discovered_printer(self, data: bytes) -> Printer | None:
        """
        Parse discovery response bytes and create a Printer object if valid.

        Attempts to decode the provided bytes as a UTF-8 string and instantiate a Printer object using the decoded information. Returns the Printer object if successful, or None if decoding or instantiation fails.
        """
        try:
            printer_info = data.decode("utf-8")
        except UnicodeDecodeError:
            self.logger.exception(
                "Error decoding printer discovery data. Data may be malformed."
            )
        else:
            try:
                printer = Printer(printer_info)
            except (ValueError, TypeError):
                self.logger.exception("Error creating Printer object")
            else:
                self.logger.info(f"Discovered: {printer.name} ({printer.ip_address})")
                return printer

        return None

    async def connect_printer(self, printer: Printer, proxy_enabled: bool) -> bool:
        """
        Establish an asynchronous connection to the Elegoo printer via a local WebSocket proxy.

        If a local proxy server is not running, starts one that connects to the remote printer, enabling multiple local clients to share a single printer connection. Discovers the printer or proxy, then connects to its WebSocket interface. Waits for the connection to be established or times out.

        Returns:
            bool: True if the connection to the printer via the proxy was successful, False otherwise.
        """
        self.printer = printer
        self.printer.proxy_enabled = proxy_enabled
        self.logger.debug(
            f"Connecting to printer: {self.printer.name} at {self.printer.ip_address} proxy_enabled: {proxy_enabled}"
        )
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
                elif cmd == 321:
                    self._print_history_detail_handler(data_data)
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
        """
        history_data_list = data_data.get("HistoryData")
        if history_data_list:
            for task_id in history_data_list:
                if task_id not in self.printer_data.print_history:
                    self.printer_data.print_history[task_id] = None

    def _print_history_detail_handler(self, data_data: dict[str, Any]) -> None:
        """
        Parses and updates the printer's print history details from the provided data.

        If a list of print history details is present in the input, updates the printer data with a list of `PrintHistoryDetail` objects.
        """
        history_data_list = data_data.get("HistoryDetailList")
        if history_data_list:
            for history_data in history_data_list:
                detail = PrintHistoryDetail(history_data)
                if detail.task_id is not None:
                    self.printer_data.print_history[detail.task_id] = detail

    def _print_video_handler(self, data_data: dict[str, Any]) -> None:
        """
        Parse video stream data and update the printer's video attribute.

        Parameters:
            data_data (dict[str, Any]): Dictionary containing video stream information.
        """
        self.printer_data.video = ElegooVideo(data_data)
