from threading import Thread
from loguru import logger
import socket
import json
import os
import websocket
import time
import sys
import models.status
import asyncio
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
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


class PrinterSensor(Entity):
    """Representation of an Elegoo printer sensor."""

    def __init__(self, hass, entity_id, unit, data_key, icon):
        """Initialize the sensor."""
        self.hass = hass
        # The 'sensor.' prefix is important
        self._entity_id = f"sensor.{entity_id}"
        self._unit_of_measurement = unit
        self._data_key = data_key
        self._icon = icon
        self._state = None

    @property
    def entity_id(self):
        """Return the entity ID."""
        return self._entity_id

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def icon(self):
        """Return the icon."""
        return self._icon

    def update_data(self, printer_data):
        """Update the sensor data."""
        self._state = printer_data.get(self._data_key)


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
        asyncio.run(self._update_entities(self.entitites, printer_data))

    async def _update_entities(self, entities, printer_data):
        if printer_data:
            for entity in entities:
                entity.update_data(printer_data)
                await entity.async_update_ha_state()


def main():
    hass = None
    entities = [
        PrinterSensor(hass, "elegoo_printer_uvled_temperature",
                      "°C", "uv_temperature", "mdi:led-variant-on"),
        PrinterSensor(hass, "elegoo_printer_time_total",
                      "milliseconds", "time_total", "mdi:timer-clock-outline"),
        PrinterSensor(hass, "elegoo_printer_time_printing",
                      "milliseconds", "time_printing", "mdi:timer-sand"),
        PrinterSensor(hass, "elegoo_printer_time_remaining",
                      "milliseconds", "time_remaining", "mdi:timer-outline"),
        PrinterSensor(hass, "elegoo_printer_filename",
                      None, "filename", "mdi:file"),
        PrinterSensor(hass, "elegoo_printer_current_layer",
                      None, "current_layer", "mdi:layers"),
        PrinterSensor(hass, "elegoo_printer_total_layers", None,
                      "total_layers", "mdi:layers-triple"),
        PrinterSensor(hass, "elegoo_printer_remaining_layers",
                      None, "remaining_layers", "mdi:layers-minus"),
    ]
    elegoo_printer = ElegooPrinter(hass, "10.0.0.212", entities)
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
