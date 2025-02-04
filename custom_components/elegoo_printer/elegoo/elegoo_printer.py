"""Elegoo Printer."""

import json
import os
import socket
import time
from threading import Thread
from typing import Any

import websocket

from .const import LOGGER, logger
from .models.attributes import PrinterAttributes
from .models.printer import Printer, PrinterData
from .models.status import PrinterStatus

DISCOVERY_TIMEOUT = 1
DEFAULT_PORT = 54780
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"


class ElegooPrinterClient:
    """
    Client for interacting with an Elegoo printer.

    Uses the SDCP Protocol (https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0).
    """

    def __init__(self, ip_address: str) -> None:
        """Initialize the ElegooPrinterClient."""
        self.ip_address: str = ip_address
        self.printer_websocket: websocket.WebSocketApp | None = None
        self.printer: Printer = Printer()
        self.printer_data = PrinterData()

    def get_printer_status(self) -> PrinterData:
        """Retreves the printer status."""
        self._send_printer_cmd(0)
        return self.printer_data

    def get_printer_attributes(self) -> PrinterData:
        """Retreves the printer attributes."""
        self._send_printer_cmd(1)
        return self.printer_data

    def set_printer_video_stream(self, *, toggle: bool) -> None:
        """Toggles the printer video stream."""
        self._send_printer_cmd(386, {"Enable": int(toggle)})

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
            logger.debug(f"printer << \n{json.dumps(payload, indent=4)}")
        if self.printer_websocket:
            self.printer_websocket.send(json.dumps(payload))
        else:
            LOGGER.warning("Attempted to send command but websocket is not connected.")

    def discover_printer(self) -> Printer | None:
        """Discover the Elegoo printer on the network."""
        LOGGER.info(f"Starting printer discovery at {self.ip_address}")
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
                LOGGER.warning("Printer discovery timed out.")
            except OSError as e:
                LOGGER.error(f"Socket error during discovery: {e}")
            else:
                printer = self._save_discovered_printer(data)
                if printer:
                    LOGGER.debug("Discovery done.")
                    self.printer = printer
                    return printer

        return None

    def _save_discovered_printer(self, data: bytes) -> Printer | None:
        try:
            printer_info = data.decode("utf-8")
        except UnicodeDecodeError:
            LOGGER.error(
                "Error decoding printer discovery data. Data may be malformed."
            )
        else:
            try:
                printer = Printer(printer_info)
            except (ValueError, TypeError) as e:
                LOGGER.error(f"Error creating Printer object: {e}")
            else:
                LOGGER.info(f"Discovered: {printer.name} ({printer.ip_address})")
                return printer

        return None

    def connect_printer(self) -> bool:
        """
        Connect to the Elegoo printer.

        Establishes a WebSocket connection to the printer using the
        discovered IP address and port.

        Returns:
            True if the connection was successful, False otherwise.

        """
        url = f"ws://{self.printer.ip_address}:3030/websocket"
        LOGGER.info(f"Connecting to: {self.printer.name}")

        websocket.setdefaulttimeout(1)

        def ws_msg_handler(ws, msg: str) -> None:  # noqa: ANN001, ARG001
            self._parse_response(msg)

        def ws_connected_handler(name: str) -> None:
            LOGGER.info(f"Connected to: {name}")
            self.get_printer_attributes()
            self.get_printer_status()

        def on_close(
            ws, close_status_code: str, close_msg: str  # noqa: ANN001, ARG001
        ) -> None:
            LOGGER.debug(
                f"Connection to {self.printer.name} closed: {close_msg} ({close_status_code})"  # noqa: E501
            )
            self.printer_websocket = None

        def on_error(ws, error) -> None:  # noqa: ANN001, ARG001
            LOGGER.error(f"Connection to {self.printer.name} error: {error}")
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
                LOGGER.info(f"Connected to {self.printer.name}")
                return True
            time.sleep(0.1)

        LOGGER.warning(f"Failed to connect to {self.printer.name} within timeout")
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
                        LOGGER.debug(f"notice >> \n{json.dumps(data, indent=5)}")
                    case "error":
                        LOGGER.debug(f"error >> \n{json.dumps(data, indent=5)}")
                    case _:
                        LOGGER.debug("--- UNKNOWN MESSAGE ---")
                        LOGGER.debug(data)
                        LOGGER.debug("--- UNKNOWN MESSAGE ---")
            else:
                LOGGER.warning(
                    "Received message without 'Topic'"
                )  # Log if Topic is missing
                LOGGER.debug(
                    f"Message content: {response}"
                )  # Log the whole message for debugging
        except json.JSONDecodeError:
            LOGGER.error(
                f"Invalid JSON received: {response}"
            )  # Log the error and message

    def _response_handler(self, data: dict[str, Any]) -> None:  # Pass parsed data
        if DEBUG:
            logger.debug(f"response >> \n{json.dumps(data, indent=5)}")

    def _status_handler(self, data: dict[str, Any]) -> None:  # Pass parsed data
        if DEBUG:
            logger.debug(f"status >> \n{json.dumps(data, indent=5)}")
        printer_status = PrinterStatus.from_json(
            json.dumps(data)
        )  # Pass json string to from_json
        self.printer_data.status = printer_status

    def _attributes_handler(self, data: dict[str, Any]) -> None:  # Pass parsed data
        if DEBUG:
            logger.debug(f"attributes >> \n{json.dumps(data, indent=5)}")
        printer_attributes = PrinterAttributes.from_json(
            json.dumps(data)
        )  # Pass json string to from_json
        self.printer_data.attributes = printer_attributes
