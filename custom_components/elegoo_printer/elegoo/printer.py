"""Elegoo Printer."""  # noqa: INP001

import json
import os
import socket
import time
from threading import Thread

import websocket

from .const import LOGGER
from .models import Printer, PrinterStatus

discovery_timeout = 1
port = 54780
debug = False
if os.environ.get("PORT") is not None:
    port = os.environ.get("PORT")

if os.environ.get("DEBUG") is not None:
    debug = True


class ElegooPrinterClient:
    """
    ElegooPrinterClient is the main client to be used to connect to an elegoo printer.

    Uses the SDCP Protocol https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0
    """

    def __init__(self, ip_address: str) -> None:  # noqa: D107
        self.ip_address: str = ip_address
        self.printer_websocket: websocket.WebSocketApp
        self.printer: Printer = Printer()
        self.printer_status: PrinterStatus = PrinterStatus()

    def get_printer_status(self) -> PrinterStatus:
        """Gets the printer status."""  # noqa: D401
        self._send_printer_cmd(0)
        return self.printer_status

    def get_printer_attributes(self) -> None:
        """Gets the printer attributes."""  # noqa: D401
        self._send_printer_cmd(1)

    def _send_printer_cmd(self, cmd: int, data: dict = {}) -> None:  # noqa: B006
        ts = int(time.time())
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
        if debug:
            LOGGER.debug(f"printer << \n{json.dumps(payload, indent=4)}")
        self.printer_websocket.send(json.dumps(payload))

    def discover_printer(self) -> Printer:
        """Discovers printer and returns it."""
        LOGGER.info(f"Starting printer discovery. {self.ip_address}")
        msg = b"M99999"
        sock = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP
        )  # UDP
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(discovery_timeout)
        sock.bind(("", 54781))
        _ = sock.sendto(msg, (self.ip_address, 3000))
        socket_open = True
        printer = Printer()
        while socket_open:
            try:
                data = sock.recv(8192)
                printer = self._save_discovered_printer(data)
            except TimeoutError:
                sock.close()
                break
        LOGGER.debug("Discovery done.")
        self.printer = printer
        return printer

    def _save_discovered_printer(self, data: bytes) -> Printer:
        printer = Printer(data.decode("utf-8"))
        LOGGER.info(f"Discovered: {printer.name} ({printer.ip})")
        return printer

    def connect_printer(self) -> bool:  # noqa: D102
        url = f"ws://{self.printer.ip}:3030/websocket"
        LOGGER.info(f"Connecting to: {self.printer.name}")
        websocket.setdefaulttimeout(1)
        ws = websocket.WebSocketApp(
            url,
            on_message=self._ws_msg_handler,
            on_open=lambda _: self._ws_connected_handler(self.printer.name),
            on_close=lambda _, s, m: LOGGER.debug(
                f"Connection to '{self.printer.name}' closed: {m} ({s})"
            ),
            on_error=lambda _, e: LOGGER.debug(
                f"Connection to '{self.printer.name}' error: {e}"
            ),
        )
        self.printer_websocket = ws
        Thread(target=lambda: ws.run_forever(reconnect=1), daemon=True).start()

        return True

    def _ws_connected_handler(self, name: str) -> None:
        LOGGER.info(f"Connected to: {name}")

    def _ws_msg_handler(self, ws, msg: str) -> None:  # noqa: ANN001, ARG002
        self._parse_response(msg)

    def _parse_response(self, response: str) -> None:
        data = json.loads(response)
        topic = data["Topic"]
        # Extract the second part of the topic (e.g., "response")
        match topic.split("/")[1]:
            case "response":
                # Printer Response Handler
                self._response_handler(response)
            case "status":
                # Status Handler
                self._status_handler(response)
            case "attributes":
                # Attribute handler
                LOGGER.debug(f"attributes >> \n{json.dumps(data, indent=5)}")
            case "notice":
                # Notice Handler
                LOGGER.debug(f"notice >> \n{json.dumps(data, indent=5)}")
            case "error":
                # Error Handler
                LOGGER.debug(f"error >> \n{json.dumps(data, indent=5)}")
            case _:  # Default case
                LOGGER.debug("--- UNKNOWN MESSAGE ---")
                LOGGER.debug(data)
                LOGGER.debug("--- UNKNOWN MESSAGE ---")

    def _response_handler(self, msg: str) -> None:  # noqa: ARG002
        return None

    def _status_handler(self, msg: str) -> None:
        printer_status = PrinterStatus.from_json(msg)
        self.printer_status = printer_status
        if debug:
            LOGGER.debug(f"status >> \n{json.dumps(json.loads(msg), indent=5)}")
