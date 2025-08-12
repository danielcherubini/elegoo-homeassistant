"""Elegoo Printer."""

import asyncio
import json
import logging
import os
import socket
import time
from types import MappingProxyType
from typing import Any

import aiohttp
from aiohttp import ClientWebSocketResponse
from aiohttp.client import ClientWSTimeout

from custom_components.elegoo_printer.const import (
    DEFAULT_BROADCAST_ADDRESS,
    DEFAULT_FALLBACK_IP,
    DISCOVERY_MESSAGE,
    DISCOVERY_PORT,
    WEBSOCKET_PORT,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConfigurationError,
    ElegooPrinterConnectionError,
    ElegooPrinterNotConnectedError,
)
from custom_components.elegoo_printer.sdcp.models.video import ElegooVideo

from custom_components.elegoo_printer.sdcp.const import DEBUG, LOGGER
from custom_components.elegoo_printer.sdcp.models.attributes import PrinterAttributes
from custom_components.elegoo_printer.sdcp.models.enums import ElegooFan
from custom_components.elegoo_printer.sdcp.models.print_history_detail import PrintHistoryDetail
from custom_components.elegoo_printer.sdcp.models.printer import Printer, PrinterData
from custom_components.elegoo_printer.sdcp.models.status import LightStatus, PrinterStatus

logging.getLogger("websocket").setLevel(logging.CRITICAL)

DISCOVERY_TIMEOUT = 5
DEFAULT_PORT = 54780


class ElegooPrinterClient:
    """Client for interacting with an Elegoo printer.

    Uses the SDCP Protocol (https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0).
    Includes a local websocket proxy to allow multiple local clients to communicate with one printer.
    """

    def __init__(
        self,
        ip_address: str | None,
        session: aiohttp.ClientSession,
        logger: Any = LOGGER,
        config: MappingProxyType[str, Any] = MappingProxyType({}),
    ) -> None:
        """Initialize an ElegooPrinterClient for communicating with an Elegoo 3D printer.

        Args:
            ip_address: The IP address of the target printer.
            session: The aiohttp client session.
            logger: The logger to use.
            config: A dictionary containing the config for the printer.
        """
        if ip_address is None:
            raise ElegooPrinterConfigurationError(
                "IP address is required but not provided"
            )
        self.ip_address: str = ip_address
        self.printer_websocket: ClientWebSocketResponse | None = None
        self.config = config
        self.printer: Printer = Printer.from_dict(dict(config))
        self.printer_data = PrinterData()
        self.logger = logger
        self._is_connected: bool = False
        self._listener_task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession = session
        self._background_tasks: set[asyncio.Task] = set()

    @property
    def is_connected(self) -> bool:
        """Return true if the client is connected to the printer."""
        return (
            self._is_connected
            and self.printer_websocket is not None
            and not self.printer_websocket.closed
        )

    async def disconnect(self) -> None:
        """Disconnect from the printer."""
        self.logger.info("Closing connection to printer")
        if self._listener_task:
            self._listener_task.cancel()
            self._listener_task = None
        if self.printer_websocket and not self.printer_websocket.closed:
            await self.printer_websocket.close()
        self._is_connected = False

    async def get_printer_status(self) -> PrinterData:
        """Retrieve the current status of the printer.

        Returns:
            The latest printer status information.
        """
        await self._send_printer_cmd(0)
        return self.printer_data

    async def get_printer_attributes(self) -> PrinterData:
        """Retreves the printer attributes."""
        await self._send_printer_cmd(1)
        return self.printer_data

    async def set_printer_video_stream(self, *, toggle: bool) -> None:
        """Enable or disable the printer's video stream.

        Args:
            toggle: If True, enables the video stream; if False, disables it.
        """
        await self._send_printer_cmd(386, {"Enable": int(toggle)})

    async def get_printer_video(self, toggle: bool = False) -> ElegooVideo:
        """Toggle the printer's video stream and retrieve the current video stream information.

        Args:
            toggle: If True, enables the video stream; if False, disables it.

        Returns:
            The current video stream information from the printer.
        """
        await self.set_printer_video_stream(toggle=toggle)
        await asyncio.sleep(2)
        self.logger.debug(f"Sending printer video: {self.printer_data.video.to_dict()}")
        return self.printer_data.video

    async def async_get_printer_historical_tasks(
        self,
    ) -> dict[str, PrintHistoryDetail | None] | None:
        """Asynchronously requests the list of historical print tasks from the printer."""
        await self._send_printer_cmd(320)
        await asyncio.sleep(2)  # Give the printer time to respond
        return self.printer_data.print_history

    async def get_printer_task_detail(
        self, id_list: list[str]
    ) -> PrintHistoryDetail | None:
        """Retrieves historical tasks from the printer."""
        for task_id in id_list:
            if task := self.printer_data.print_history.get(task_id):
                return task
            else:
                await self._send_printer_cmd(321, data={"Id": [task_id]})
                await asyncio.sleep(2)
                return self.printer_data.print_history.get(task_id)

        return None

    def get_printer_current_task(self) -> PrintHistoryDetail | None:
        """Retreves current task."""
        if self.printer_data.status.print_info.task_id:
            task_id = self.printer_data.status.print_info.task_id
            current_task = self.printer_data.print_history.get(task_id)
            self.logger.debug(f"current_task: {current_task}")
            if current_task is not None:
                return current_task
            else:
                self.logger.debug("Getting printer task from api")
                task = asyncio.create_task(self.get_printer_task_detail([task_id]))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
                return self.printer_data.print_history.get(task_id)
        return None

    def get_printer_last_task(self) -> PrintHistoryDetail | None:
        """Retreves last task."""
        if self.printer_data.print_history:

            def sort_key(tid: str) -> int:
                task = self.printer_data.print_history.get(tid)
                return task.end_time or 0 if task else 0

            # Get task with the latest begin_time or end_time
            last_task_id = max(
                self.printer_data.print_history.keys(),
                key=sort_key,
            )
            task_data = self.printer_data.print_history.get(last_task_id)
            if task_data is None:
                task = asyncio.create_task(self.get_printer_task_detail([last_task_id]))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            return task_data
        return None

    def get_current_print_thumbnail(self) -> str | None:
        """Return the thumbnail URL of the current print task, or None if no thumbnail is available.

        Returns:
            The URL of the current print task's thumbnail image, or None if there is no active task or thumbnail.
        """

        task = self.get_printer_current_task()
        if task:
            return task.thumbnail
        return None

    async def async_get_printer_current_task(self) -> PrintHistoryDetail | None:
        """Asynchronously retrieves the current print task details from the printer.

        Returns:
            The details of the current print task if available, otherwise None.
        """
        if task_id := self.printer_data.status.print_info.task_id:
            LOGGER.debug(f"get_printer_current_task task_id: {task_id}")
            current_task = self.printer_data.print_history.get(task_id)
            if current_task is not None:
                LOGGER.debug("get_printer_current_task: got cached task")
                return current_task
            else:
                LOGGER.debug("get_printer_current_task: getting task from api")
                task = await self.get_printer_task_detail([task_id])
                if task:
                    LOGGER.debug(
                        f"get_printer_current_task: task from the api: {task.task_id}"
                    )

                else:
                    LOGGER.debug("get_printer_current_task: NO TASK FROM THE API")
                return task

        return None

    async def async_get_printer_last_task(self) -> PrintHistoryDetail | None:
        """Retreves last task."""
        if self.printer_data.print_history:

            def sort_key(tid: str) -> int:
                task = self.printer_data.print_history.get(tid)
                return task.end_time or 0 if task else 0

            # Get task with the latest begin_time or end_time
            last_task_id = max(
                self.printer_data.print_history.keys(),
                key=sort_key,
            )
            task = self.printer_data.print_history.get(last_task_id)
            if task is None:
                await self.get_printer_task_detail([last_task_id])
                await asyncio.sleep(2)  # Give the printer time to respond
                return self.printer_data.print_history.get(last_task_id)
            return task
        return None

    async def async_get_current_print_thumbnail(self) -> str | None:
        """Asynchronously retrieves the thumbnail URL of the current print task.

        Returns:
            The thumbnail URL if the current print task has one; otherwise, None.
        """
        if task := await self.async_get_printer_current_task():
            return task.thumbnail
        elif last_task := await self.async_get_printer_last_task():
            return last_task.thumbnail

        return None

    async def set_light_status(self, light_status: LightStatus) -> None:
        """Set the printer's light status to the specified configuration.

        Args:
            light_status: The light status configuration to apply.
        """
        await self._send_printer_cmd(403, light_status.to_dict())

    async def print_pause(self) -> None:
        """Pause the current print."""
        await self._send_printer_cmd(129, {})

    async def print_stop(self) -> None:
        """Stop the current print."""
        await self._send_printer_cmd(130, {})

    async def print_resume(self) -> None:
        """Resume/continue the current print."""
        await self._send_printer_cmd(131, {})

    async def set_fan_speed(self, percentage: int, fan: ElegooFan) -> None:
        """Set the speed of a fan.

        percentage: 0–100
        """
        pct = max(0, min(100, int(percentage)))
        data = {"TargetFanSpeed": {fan.value: pct}}
        await self._send_printer_cmd(403, data)

    async def set_print_speed(self, percentage: int) -> None:
        """Set the print speed.

        percentage: 0–160
        """
        pct = max(0, min(160, int(percentage)))
        data = {"PrintSpeedPct": pct}
        await self._send_printer_cmd(403, data)

    async def set_target_nozzle_temp(self, temperature: int) -> None:
        """Set the target nozzle temperature."""
        clamped_temperature = max(0, min(320, int(temperature)))
        data = {"TempTargetNozzle": clamped_temperature}
        await self._send_printer_cmd(403, data)

    async def set_target_bed_temp(self, temperature: int) -> None:
        """Set the target bed temperature."""
        clamped_temperature = max(0, min(110, int(temperature)))
        data = {"TempTargetHotbed": clamped_temperature}
        await self._send_printer_cmd(403, data)

    async def _send_printer_cmd(
        self, cmd: int, data: dict[str, Any] | None = None
    ) -> None:
        """Send a JSON command to the printer via the WebSocket connection.

        Args:
            cmd: The command to send.
            data: The data to send with the command.

        Raises:
            ElegooPrinterNotConnectedError: If the printer is not connected.
            ElegooPrinterConnectionError: If a WebSocket error or timeout occurs during sending.
            OSError: If an operating system error occurs while sending the command.
        """
        if not self.is_connected:
            raise ElegooPrinterNotConnectedError(
                "Printer not connected, cannot send command."
            )
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
                await self.printer_websocket.send_str(json.dumps(payload))
            except (
                OSError,
                asyncio.TimeoutError,
                aiohttp.ClientError,
            ) as e:
                self._is_connected = False
                self.logger.info("WebSocket connection closed error")
                raise ElegooPrinterConnectionError from e
        else:
            raise ElegooPrinterNotConnectedError("Not connected")

    def discover_printer(
        self, broadcast_address: str = DEFAULT_BROADCAST_ADDRESS
    ) -> list[Printer]:
        """Broadcasts a UDP discovery message to locate Elegoo printers or proxies on the local network.

        Sends a discovery request and collects responses within a timeout period,
        returning a list of discovered printers. If no printers are found or a
        socket error occurs, returns an empty list.

        Args:
            broadcast_address: The network address to send the discovery message to.

        Returns:
            A list of discovered printers, or an empty list if none are found.
        """
        discovered_printers: list[Printer] = []
        self.logger.info("Broadcasting for printer/proxy discovery...")
        msg = DISCOVERY_MESSAGE.encode()
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
        """Determine the local IP address used for outbound communication to the printer.

        Returns:
            The local IP address, or "127.0.0.1" if detection fails.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                # Doesn't have to be reachable
                s.connect((self.ip_address or DEFAULT_FALLBACK_IP, 1))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    def _save_discovered_printer(self, data: bytes) -> Printer | None:
        """Parse discovery response bytes and create a Printer object if valid.

        Attempts to decode the provided bytes as a UTF-8 string and instantiate a
        Printer object using the decoded information. Returns the Printer object if
        successful, or None if decoding or instantiation fails.

        Args:
            data: The discovery response data.

        Returns:
            A Printer object if the data is valid, otherwise None.
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
        """Establish an asynchronous connection to the Elegoo printer."""
        if self.is_connected:
            self.logger.debug("Already connected")
            return True

        await self.disconnect()

        self.printer = printer
        self.printer.proxy_enabled = proxy_enabled
        self.logger.debug(
            f"Connecting to printer: {self.printer.name} at {self.printer.ip_address} proxy_enabled: {proxy_enabled}"
        )
        url = f"ws://{self.printer.ip_address}:{WEBSOCKET_PORT}/websocket"
        self.logger.info(f"Client connecting to WebSocket at: {url}")

        try:
            timeout = ClientWSTimeout()
            self.printer_websocket = await self._session.ws_connect(
                url, timeout=timeout, heartbeat=20
            )
            self._is_connected = True
            self._listener_task = asyncio.create_task(self._ws_listener())
            self.logger.info(
                f"Client successfully connected to: {self.printer.name}, via proxy: {proxy_enabled}"
            )
            return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self.logger.debug(
                f"Failed to connect WebSocket to {self.printer.name}: {e}"
            )
            self.logger.info(
                "Will retry connecting to printer '%s' …",
                self.printer.name,
                exc_info=DEBUG,
            )
            await self.disconnect()
            return False

    async def _ws_listener(self) -> None:
        """Listen for messages on the WebSocket and handle them."""
        if not self.printer_websocket:
            return

        try:
            async for msg in self.printer_websocket:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._parse_response(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    error_str = f"WebSocket connection error: {self.printer_websocket.exception()}"
                    self.logger.info(error_str)
                    raise ElegooPrinterConnectionError(error_str)
        except asyncio.CancelledError:
            self.logger.debug("WebSocket listener cancelled.")
        except Exception as e:
            self.logger.error(f"WebSocket listener exception: {e}")
            raise ElegooPrinterConnectionError from e
        finally:
            self._is_connected = False
            self.logger.info("WebSocket listener stopped.")

    def _parse_response(self, response: str) -> None:
        """Parse and route an incoming JSON response message from the printer.

        Attempts to decode the response as JSON and dispatches it to the appropriate
        handler based on the message topic. Logs unknown topics, missing topics, and
        JSON decoding errors.

        Args:
            response: The JSON response message to parse.
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
        """Handles response messages by dispatching to the appropriate handler based on the command type.

        Routes print history and video stream response data to their respective
        handlers according to the command ID in the response.

        Args:
            data: The response data.
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
        """Parses and updates the printer's status information from the provided data.

        Args:
            data: Dictionary containing the printer status information in JSON-compatible format.
        """
        if DEBUG:
            self.logger.info(f"status >> \n{json.dumps(data, indent=5)}")
        printer_status = PrinterStatus.from_json(
            json.dumps(data), self.printer.printer_type
        )
        self.printer_data.status = printer_status

    def _attributes_handler(self, data: dict[str, Any]) -> None:
        """Parses and updates the printer's attribute data from a JSON dictionary.

        Args:
            data: Dictionary containing printer attribute information.
        """
        if DEBUG:
            self.logger.info(f"attributes >> \n{json.dumps(data, indent=5)}")
        printer_attributes = PrinterAttributes.from_json(json.dumps(data))
        self.printer_data.attributes = printer_attributes

    def _print_history_handler(self, data_data: dict[str, Any]) -> None:
        """Parses and updates the printer's print history details from the provided data."""
        history_data_list = data_data.get("HistoryData")
        if history_data_list:
            for task_id in history_data_list:
                if task_id not in self.printer_data.print_history:
                    self.printer_data.print_history[task_id] = None

    def _print_history_detail_handler(self, data_data: dict[str, Any]) -> None:
        """Parses and updates the printer's print history details from the provided data.

        If a list of print history details is present in the input, updates the
        printer data with a list of `PrintHistoryDetail` objects.

        Args:
            data_data: The data containing the print history details.
        """
        history_data_list = data_data.get("HistoryDetailList")
        if history_data_list:
            for history_data in history_data_list:
                detail = PrintHistoryDetail(history_data)
                if detail.task_id is not None:
                    self.printer_data.print_history[detail.task_id] = detail

    def _print_video_handler(self, data_data: dict[str, Any]) -> None:
        """Parse video stream data and update the printer's video attribute.

        Args:
            data_data: Dictionary containing video stream information.
        """
        self.printer_data.video = ElegooVideo(data_data)
