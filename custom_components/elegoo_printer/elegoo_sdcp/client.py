"""Elegoo Printer."""

import asyncio
import json
import os
import socket
import time
from threading import Event, Thread
from typing import Any

import websocket

from .const import DEBUG, LOGGER
from .models.attributes import PrinterAttributes
from .models.print_history_detail import PrintHistoryDetail
from .models.printer import Printer, PrinterData
from .models.status import PrinterStatus
from .server import is_port_in_use, start_proxy_server

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
        centauri_carbon: bool = False,
        logger: Any = LOGGER,
        ws_server: bool = True,
    ) -> None:
        """Initialize the ElegooPrinterClient."""
        self.ip_address: str = ip_address
        self.centauri_carbon: bool = centauri_carbon
        self.printer_websocket: websocket.WebSocketApp | None = None
        self.printer: Printer = Printer()
        self.printer_data = PrinterData()
        self.logger = logger
        self.proxy_thread: Thread | None = None
        self.ws_server = ws_server

    def get_printer_status(self) -> PrinterData:
        """Retreves the printer status."""
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

    def discover_printer(
        self, broadcast_address: str = "<broadcast>"
    ) -> Printer | None:
        """Discover the Elegoo printer (or proxy) on the network."""
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
                printer = Printer(printer_info, centauri_carbon=self.centauri_carbon)
            except (ValueError, TypeError):
                self.logger.exception("Error creating Printer object")
            else:
                self.logger.info(f"Discovered: {printer.name} ({printer.ip_address})")
                return printer

        return None

    async def connect_printer(self) -> bool:
        """
        Connect to the Elegoo printer via a local proxy.

        Checks for a local proxy on localhost:3030. If not found, it starts one
        which connects to the remote printer. The client then connects to the proxy.
        This allows multiple local applications to share one printer connection.

        Returns:
            True if the connection was successful, False otherwise.
        """
        # If this instance is designated to be the server host...
        if self.ws_server and not is_port_in_use("0.0.0.0", WEBSOCKET_PORT):
            self.logger.info("Proxy server not found. This instance will host it.")

            # This instance MUST discover the REAL printer first to impersonate it.
            self.logger.info("Performing initial discovery of the REAL printer...")
            real_printer = self._discover_real_printer()
            if not real_printer:
                self.logger.error(
                    "Could not find the real printer. Cannot start proxy server."
                )
                return False

            self.logger.info(
                f"Local proxy not found on port {WEBSOCKET_PORT}. Starting new proxy server..."
            )

            # Use an event to signal when the server is ready
            startup_event = Event()

            proxy_thread = Thread(
                target=start_proxy_server,
                args=(real_printer, self.logger, startup_event),
                daemon=True,
            )
            proxy_thread.start()
            self.proxy_thread = proxy_thread

            ready = startup_event.wait(timeout=5.0)
            if not ready:
                self.logger.error(
                    "Proxy server failed to start within the timeout period."
                )
                return False
            self.logger.info("Proxy server has started successfully.")

        # Now, discover the printer/proxy and connect to its WebSocket
        if not self.discover_printer():
            return False

        # Connect this client to the discovered printer/proxy's WebSocket.
        url = f"ws://{self.printer.ip_address}:{WEBSOCKET_PORT}/websocket"
        self.logger.info(f"Client connecting to WebSocket at: {url}")

        websocket.setdefaulttimeout(1)

        def ws_msg_handler(ws, msg: str) -> None:  # noqa: ANN001, ARG001
            self._parse_response(msg)

        def ws_connected_handler(name: str) -> None:
            self.logger.info(f"Client successfully connected via proxy to: {name}")

        def on_close(
            ws,  # noqa: ANN001, ARG001
            close_status_code: str,
            close_msg: str,
        ) -> None:
            self.logger.debug(
                f"Connection to {self.printer.name} (via proxy) closed: {close_msg} ({close_status_code})"
            )
            self.printer_websocket = None

        def on_error(ws, error) -> None:  # noqa: ANN001, ARG001
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

    def _discover_real_printer(self) -> Printer | None:
        """Discovers the real printer by sending a broadcast to its specific IP."""
        self.logger.info(f"Pinging real printer at {self.ip_address}")
        msg = b"M99999"
        with socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP
        ) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(DISCOVERY_TIMEOUT)
            sock.bind(("", DEFAULT_PORT))
            try:
                # Send directly to the known IP of the printer
                sock.sendto(msg, (self.ip_address, DISCOVERY_PORT))
                data, addr = sock.recvfrom(8192)
            except TimeoutError:
                self.logger.warning("Real printer discovery timed out.")
                return None
            except OSError:
                self.logger.exception("Socket error during real printer discovery")
                return None

            # The real printer sends a string, so we parse it here.
            return self._save_discovered_printer(data)

    def _parse_response(self, response: str) -> None:
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
        if DEBUG:
            self.logger.debug(f"response >> \n{json.dumps(data, indent=5)}")
        try:
            data_data = data.get("Data", {}).get("Data", {})
            self._print_history_handler(data_data)
        except json.JSONDecodeError:
            self.logger.exception("Invalid JSON")

    def _status_handler(self, data: dict[str, Any]) -> None:
        if DEBUG:
            self.logger.debug(f"status >> \n{json.dumps(data, indent=5)}")
        printer_status = PrinterStatus.from_json(json.dumps(data))
        self.printer_data.status = printer_status

    def _attributes_handler(self, data: dict[str, Any]) -> None:
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
