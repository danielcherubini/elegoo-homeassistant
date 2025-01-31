import json
import os
import socket
import time
from threading import Thread

import websocket

from ..const import LOGGER
from .models import Printer, PrinterStatus

discovery_timeout = 1
port = 54780
if os.environ.get("PORT") is not None:
    port = os.environ.get("PORT")


class ElegooPrinterClient:
    """
    ElegooPrinterClient is the main client to be used to connect to an elegoo printer
    Uses the SDCP Protocol https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0
    """

    def __init__(self, ip_address: str) -> None:
        self.ip_address: str = ip_address
        self.printer_websocket: websocket.WebSocketApp
        self.printer: Printer = Printer()
        self.printer_status: PrinterStatus = PrinterStatus()

    def get_printer_status(self) -> PrinterStatus:
        self._send_printer_cmd(0)
        return self.printer_status

    def get_printer_attributes(self):
        self._send_printer_cmd(1)

    def _send_printer_cmd(self, cmd: int, data: dict[str, str] = {}):
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
        LOGGER.debug(f"printer << \n{json.dumps(payload, indent=4)}")
        self.printer_websocket.send(json.dumps(payload))

    def discover_printer(self):
        LOGGER.debug(f"Starting printer discovery. {self.ip_address}")
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
        j = json.loads(data.decode("utf-8"))
        printer = Printer(j)
        LOGGER.debug(f"Discovered: {printer.name} ({printer.ip})")
        return printer

    def connect_printer(self):
        url = f"ws://{self.printer.ip}:3030/websocket"
        LOGGER.debug(f"Connecting to: {self.printer.name}")
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

    def _ws_connected_handler(self, name: str):
        LOGGER.debug(f"Connected to: {name}")

    def _ws_msg_handler(self, ws, msg):
        self._parse_response(msg)

    def _parse_response(self, response):
        data = json.loads(response)
        topic = data["Topic"]
        # Extract the second part of the topic (e.g., "response")
        match topic.split("/")[1]:
            case "response":
                # Printer Response Handler
                LOGGER.debug(f"response >> \n{json.dumps(data, indent=5)}")
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

    def _status_handler(self, msg):
        printer_status = PrinterStatus.from_json(msg)
        self.printer_status = printer_status
        status = printer_status.status
        print_info = status.print_info
        layers_remaining = print_info.total_layer - print_info.current_layer

        printer_data = {
            "uv_temperature": status.temp_of_uvled,
            "time_total": print_info.total_ticks,
            "time_printing": print_info.current_ticks,
            "time_remaining": printer_status.calculate_time_remaining(),
            "filename": print_info.filename,
            "current_layer": print_info.current_layer,
            "total_layers": print_info.total_layer,
            "remaining_layers": layers_remaining,
        }
        LOGGER.debug(f"printer_data >>> \n{json.dumps(printer_data, indent=2)}")
