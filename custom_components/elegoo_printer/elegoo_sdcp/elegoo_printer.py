"""Elegoo Printer."""

import asyncio
import json
import os
import socket
import time
from threading import Thread
from typing import Any

import websocket

from custom_components.elegoo_printer.elegoo_sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)

from .const import DEBUG
from .models.attributes import PrinterAttributes
from .models.print_history_detail import PrintHistoryDetail
from .models.printer import Printer, PrinterData
from .models.status import PrinterStatus

DISCOVERY_TIMEOUT = 1
DEFAULT_PORT = 54780


class ElegooPrinterClientWebsocketError(Exception):
    """Exception to indicate a general API error."""


class ElegooPrinterClientWebsocketConnectionError(Exception):
    """Exception to indicate a Websocket Connection error."""


class ElegooPrinterClient:
    """
    Client for interacting with an Elegoo printer.

    Uses the SDCP Protocol (https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0).
    """

    def __init__(self, ip_address: str, logger: Any) -> None:
        """Initialize the ElegooPrinterClient."""
        self.ip_address: str = ip_address
        self.printer_websocket: websocket.WebSocketApp | None = None
        self.printer: Printer = Printer()
        self.printer_data = PrinterData()
        self.logger = logger

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
        """Retreves current print Thumbnail."""
        print_history = await self.get_printer_current_task()
        if print_history:
            return print_history[0].Thumbnail

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
                printer = Printer(printer_info)
            except (ValueError, TypeError):
                self.logger.exception("Error creating Printer object")
            else:
                self.logger.info(f"Discovered: {printer.name} ({printer.ip_address})")
                return printer

        return None

    async def connect_printer(self) -> bool:
        """
        Connect to the Elegoo printer.

        Establishes a WebSocket connection to the printer using the
        discovered IP address and port.

        Returns:
            True if the connection was successful, False otherwise.

        """
        url = f"ws://{self.printer.ip_address}:3030/websocket"
        self.logger.info(f"Connecting to: {self.printer.name}")

        websocket.setdefaulttimeout(1)

        def ws_msg_handler(ws, msg: str) -> None:  # noqa: ANN001, ARG001
            self._parse_response(msg)

        def ws_connected_handler(name: str) -> None:
            self.logger.info(f"Connected to: {name}")

        def on_close(
            ws,  # noqa: ANN001, ARG001
            close_status_code: str,
            close_msg: str,
        ) -> None:
            self.logger.debug(
                f"Connection to {self.printer.name} closed: {close_msg} ({close_status_code})"  # noqa: E501
            )
            self.printer_websocket = None

        def on_error(ws, error) -> None:  # noqa: ANN001, ARG001
            self.logger.error(f"Connection to {self.printer.name} error: {error}")
            self.printer_websocket = None

        ws = websocket.WebSocketApp(
            url,
            on_message=ws_msg_handler,
            on_open=ws_connected_handler(self.printer.name),
            on_close=on_close,
            on_error=on_error,
        )
        self.printer_websocket = ws

        thread = Thread(target=ws.run_forever, kwargs={"reconnect": 1}, daemon=True)
        thread.start()

        start_time = time.monotonic()
        timeout = 5
        while time.monotonic() - start_time < timeout:
            if ws.sock and ws.sock.connected:
                await asyncio.sleep(2)
                self.logger.info(f"Connected to {self.printer.name}")
                return True

        self.logger.warning(f"Failed to connect to {self.printer.name} within timeout")
        self.printer_websocket = None
        return False

    def _parse_response(self, response: str) -> None:
        try:  # Add try-except block for json.loads
            data = json.loads(response)
            topic = data.get("Topic")  # Use .get to handle missing "Topic"
            if topic:  # Check if topic exists
                match topic.split("/")[1]:
                    case "response":
                        self._response_handler(data)  # Pass the parsed JSON data
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
                self.logger.warning(
                    "Received message without 'Topic'"
                )  # Log if Topic is missing
                self.logger.debug(
                    f"Message content: {response}"
                )  # Log the whole message for debugging
        except json.JSONDecodeError:
            self.logger.exception("Invalid JSON received")

    def _response_handler(self, data: dict[str, Any]) -> None:  # Pass parsed data
        if DEBUG:
            self.logger.debug(f"response >> \n{json.dumps(data, indent=5)}")
        try:
            data_data = data.get("Data", {}).get("Data", {})
            self._print_history_handler(data_data)
        except json.JSONDecodeError:
            self.logger.exception("Invalid JSON")

    def _status_handler(self, data: dict[str, Any]) -> None:  # Pass parsed data
        if DEBUG:
            self.logger.debug(f"status >> \n{json.dumps(data, indent=5)}")
        printer_status = PrinterStatus.from_json(
            json.dumps(data)
        )  # Pass json string to from_json
        self.printer_data.status = printer_status

    def _attributes_handler(self, data: dict[str, Any]) -> None:  # Pass parsed data
        if DEBUG:
            self.logger.debug(f"attributes >> \n{json.dumps(data, indent=5)}")
        printer_attributes = PrinterAttributes.from_json(
            json.dumps(data)
        )  # Pass json string to from_json
        self.printer_data.attributes = printer_attributes

    def _print_history_handler(self, data_data: dict[str, Any]) -> None:
        history_data_list = data_data.get("HistoryDetailList")
        if history_data_list:
            print_history_detail_list: list[PrintHistoryDetail] = [
                PrintHistoryDetail(history_data) for history_data in history_data_list
            ]
            self.printer_data.print_history = print_history_detail_list
