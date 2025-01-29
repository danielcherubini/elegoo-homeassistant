import json
import os
import socket
import time
from threading import Thread

import websocket

from .models import PrinterStatus

discovery_timeout = 1
port = 54780
if os.environ.get("PORT") is not None:
    port = os.environ.get("PORT")


class ElegooPrinterClient:
    def __init__(self, ip_address: str) -> None:
        self.ip_address = ip_address
        self.printer_websocket = {}
        self.printer = {}
        self.printer_status: PrinterStatus

    async def poll_printer_status(self):
        time.sleep(2)
        while True:
            self.get_printer_status()
            time.sleep(2)

    def get_printer_status(self) -> PrinterStatus:
        self._send_printer_cmd(0)
        return self.printer_status

    def get_printer_attributes(self):
        self._send_printer_cmd(1)

    def _send_printer_cmd(self, cmd, data={}):
        ts = int(time.time())
        payload = {
            "Id": self.printer["connection"],  # type: ignore
            "Data": {
                "Cmd": cmd,
                "Data": data,
                "RequestID": os.urandom(8).hex(),
                "MainboardID": self.printer["id"],  # type: ignore
                "TimeStamp": ts,
                "From": 0,
            },
            "Topic": "sdcp/request/" + self.printer["id"],  # type: ignore
        }
        print(f"printer << \n{json.dumps(payload, indent=4)}")
        self.printer_websocket.send(json.dumps(payload))  # type: ignore

    def discover_printer(self):
        print("Starting printer discovery. " + self.ip_address)
        msg = b"M99999"
        sock = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP
        )  # UDP
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(discovery_timeout)
        sock.bind(("", 54781))
        sock.sendto(msg, (self.ip_address, 3000))
        socketOpen = True
        printer = None
        while socketOpen:
            try:
                data = sock.recv(8192)
                printer = self._save_discovered_printer(data)
            except TimeoutError:
                sock.close()
                break
        print("Discovery done.")
        self.printer = printer
        return printer

    def _save_discovered_printer(self, data):
        j = json.loads(data.decode("utf-8"))
        printer = {}
        printer["connection"] = j["Id"]
        printer["name"] = j["Data"]["Name"]
        printer["model"] = j["Data"]["MachineName"]
        printer["brand"] = j["Data"]["BrandName"]
        printer["ip"] = j["Data"]["MainboardIP"]
        printer["protocol"] = j["Data"]["ProtocolVersion"]
        printer["firmware"] = j["Data"]["FirmwareVersion"]
        printer["id"] = j["Data"]["MainboardID"]
        print("Discovered: {n} ({i})".format(n=printer["name"], i=printer["ip"]))
        return printer

    def connect_printer(self):
        url = "ws://{ip}:3030/websocket".format(ip=self.printer["ip"])  # type: ignore
        print("Connecting to: {n}".format(n=self.printer["name"]))  # type: ignore
        websocket.setdefaulttimeout(1)
        ws = websocket.WebSocketApp(
            url,
            on_message=self._ws_msg_handler,
            on_open=lambda _: self._ws_connected_handler(self.printer["name"]),  # type: ignore
            on_close=lambda _, s, m: print(
                "Connection to '{n}' closed: {m} ({s})".format(
                    n=self.printer["name"],  # type: ignore
                    m=m,
                    s=s,  # type: ignore
                )
            ),
            on_error=lambda _, e: print(
                "Connection to '{n}' error: {e}".format(n=self.printer["name"], e=e)  # type: ignore
            ),
        )
        self.printer_websocket = ws
        Thread(target=lambda: ws.run_forever(reconnect=1), daemon=True).start()

        return True

    def _ws_connected_handler(self, name):
        print(f"Connected to: {name}")

    def _ws_msg_handler(self, ws, msg):
        self._parse_response(msg)

    def _parse_response(self, response):
        data = json.loads(response)
        topic = data["Topic"]
        m = json.dumps(data, indent=5)
        # Extract the second part of the topic (e.g., "response")
        match topic.split("/")[1]:
            case "response":
                # Printer Response Handler
                print("response >> \n" + m)
            case "status":
                # Status Handler
                self._status_handler(response)
            case "attributes":
                # Attribute handler
                print("attributes >> \n" + m)
            case "notice":
                # Notice Handler
                print("notice >> \n" + m)
            case "error":
                # Error Handler
                print("error >> \n" + m)
            case _:  # Default case
                print("--- UNKNOWN MESSAGE ---")
                print(data)
                print("--- UNKNOWN MESSAGE ---")

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
        print(f"printer_data >>> \n{json.dumps(printer_data, indent=2)}")


