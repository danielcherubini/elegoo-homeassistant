from threading import Thread
from loguru import logger
import socket
import json
import os
import websocket
import time
import sys
import models.status
from homeassistant.core import HomeAssistant
debug = False
log_level = "INFO"
if os.environ.get("DEBUG"):
    debug = True
    log_level = "DEBUG"

logger.remove()
logger.add(sys.stdout, colorize=debug, level=log_level)

port = 54780
if os.environ.get("PORT") is not None:
    port = os.environ.get("PORT")

discovery_timeout = 1


class ElegooPrinter:
    def __init__(self, hass: HomeAssistant, ip_address, entities):
        self.hass = hass
        self.ip_address = ip_address
        self.entitites = entities
        self.printer_websocket = {}
        self.printer = {}

    def get_printer_status(self):
        self._send_printer_cmd(0)

    def get_printer_attributes(self):
        self._send_printer_cmd(1)

    def _send_printer_cmd(self, cmd, data={}):
        ts = int(time.time())
        payload = {
            "Id": self.printer["connection"],
            "Data": {
                "Cmd": cmd,
                "Data": data,
                "RequestID": os.urandom(8).hex(),
                "MainboardID": self.printer["id"],
                "TimeStamp": ts,
                "From": 0,
            },
            "Topic": "sdcp/request/" + self.printer['id'],
        }
        logger.debug("printer << \n{p}", p=json.dumps(payload, indent=4))
        self.printer_websocket.send(json.dumps(payload))

    def discover_printer(self):
        logger.info("Starting printer discovery. " + self.ip_address)
        msg = b"M99999"
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                             socket.IPPROTO_UDP)  # UDP
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
        logger.info("Discovery done.")
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
        logger.info("Discovered: {n} ({i})".format(
            n=printer["name"], i=printer["ip"]))
        return printer

    def connect_printer(self):
        url = "ws://{ip}:3030/websocket".format(ip=self.printer["ip"])
        logger.info("Connecting to: {n}".format(n=self.printer["name"]))
        websocket.setdefaulttimeout(1)
        ws = websocket.WebSocketApp(
            url,
            on_message=self._ws_msg_handler,
            on_open=lambda _: self._ws_connected_handler(self.printer["name"]),
            on_close=lambda _, s, m: logger.info(
                "Connection to '{n}' closed: {m} ({s})".format(
                    n=self.printer["name"], m=m, s=s
                )
            ),
            on_error=lambda _, e: logger.info(
                "Connection to '{n}' error: {e}".format(
                    n=self.printer["name"], e=e)
            ),
        )
        self.printer_websocket = ws
        Thread(target=lambda: ws.run_forever(
            reconnect=1), daemon=True).start()

        return True

    def _ws_connected_handler(self, name):
        logger.info("Connected to: {n}".format(n=name))

    def _ws_msg_handler(self, ws, msg):
        data = json.loads(msg)
        topic = data["Topic"]

        # Extract the second part of the topic (e.g., "response")
        match topic.split('/')[1]:
            case "response":
                # Printer Response Handler
                logger.debug("response >> \n{m}", m=json.dumps(data, indent=5))
            case "status":
                # Status Handler
                self._status_handler(msg)
            case "attributes":
                # Attribute handler
                logger.debug("attributes >> \n{m}",
                             m=json.dumps(data, indent=5))
            case "notice":
                # Notice Handler
                logger.debug("notice >> \n{m}", m=json.dumps(data, indent=5))
            case "error":
                # Error Handler
                logger.error("error >> \n{m}", m=json.dumps(data, indent=5))
            case _:  # Default case
                logger.warning("--- UNKNOWN MESSAGE ---")
                logger.warning(data)
                logger.warning("--- UNKNOWN MESSAGE ---")

    def _status_handler(self, msg):
        printer_status = models.status.PrinterStatus.from_json(msg)
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

        logger.info("printer_data >>> \n{m}",
                    m=json.dumps(printer_data, indent=2))

    async def _update_entities(self, printer_data):
        if printer_data:
            for entity in self.entities:
                entity.update_data(printer_data)
                await entity.async_update_ha_state()


def main():
    elegoo_printer = ElegooPrinter(None, "10.0.0.212", {})
    printer = elegoo_printer.discover_printer()
    if printer:
        connected = elegoo_printer.connect_printer()
        if connected:
            time.sleep(2)
            while True:
                elegoo_printer.get_printer_status()
                time.sleep(2)
    else:
        logger.error("No printers discovered.")


if __name__ == "__main__":
    main()
