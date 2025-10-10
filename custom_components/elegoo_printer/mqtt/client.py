"""
Elegoo MQTT Client for SDCP.

This client connects to an MQTT broker that bridges communication
with Elegoo printers, rather than connecting directly to the printer.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import socket
import time
from typing import TYPE_CHECKING, Any

import aiomqtt

from custom_components.elegoo_printer.const import (
    DEFAULT_BROADCAST_ADDRESS,
    DISCOVERY_MESSAGE,
    DISCOVERY_PORT,
    DISCOVERY_TIMEOUT,
)
from custom_components.elegoo_printer.sdcp.const import (
    CMD_CONTINUE_PRINT,
    CMD_CONTROL_DEVICE,
    CMD_PAUSE_PRINT,
    CMD_REQUEST_ATTRIBUTES,
    CMD_REQUEST_STATUS_REFRESH,
    CMD_RETRIEVE_HISTORICAL_TASKS,
    CMD_RETRIEVE_TASK_DETAILS,
    CMD_SET_VIDEO_STREAM,
    CMD_STOP_PRINT,
    DEBUG,
    LOGGER,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
    ElegooPrinterNotConnectedError,
    ElegooPrinterTimeoutError,
)
from custom_components.elegoo_printer.sdcp.models.attributes import PrinterAttributes
from custom_components.elegoo_printer.sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.sdcp.models.printer import (
    Printer,
    PrinterData,
)
from custom_components.elegoo_printer.sdcp.models.status import (
    LightStatus,
    PrinterStatus,
)
from custom_components.elegoo_printer.sdcp.models.video import ElegooVideo

from .const import (
    MQTT_KEEPALIVE,
    MQTT_PORT,
    MQTT_TOPIC_MIN_PARTS,
    TOPIC_ATTRIBUTES,
    TOPIC_ERROR,
    TOPIC_NOTICE,
    TOPIC_PREFIX,
    TOPIC_REQUEST,
    TOPIC_RESPONSE,
    TOPIC_STATUS,
)

if TYPE_CHECKING:
    from custom_components.elegoo_printer.sdcp.models.enums import ElegooFan


class ElegooMqttClient:
    """
    MQTT client for interacting with an Elegoo printer via MQTT bridge.

    Connects to an MQTT broker that bridges communication with the printer
    rather than connecting directly to the printer.
    """

    def __init__(
        self,
        mqtt_host: str = "localhost",
        mqtt_port: int = MQTT_PORT,
        logger: Any = LOGGER,
        printer: Printer | None = None,
    ) -> None:
        """
        Initialize an ElegooMqttClient.

        For communicating with an Elegoo 3D printer via MQTT bridge.

        Arguments:
            mqtt_host: The MQTT broker hostname.
            mqtt_port: The MQTT broker port.
            logger: The logger to use.
            printer: Optional Printer object with existing configuration.

        """
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_client: aiomqtt.Client | None = None
        self.printer: Printer = printer or Printer()
        self.printer_data = PrinterData(printer=self.printer)
        self.logger = logger
        self._is_connected: bool = False
        self._listener_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._response_events: dict[str, asyncio.Event] = {}
        self._response_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        """Return true if the client is connected to the printer."""
        return self._is_connected and self.mqtt_client is not None

    async def disconnect(self) -> None:
        """Disconnect from the printer."""
        self.logger.info("Closing MQTT connection to printer")
        if self._listener_task:
            self._listener_task.cancel()
            self._listener_task = None

        # Unblock any waiters
        async with self._response_lock:
            for ev in self._response_events.values():
                ev.set()
            self._response_events.clear()

        # Properly close MQTT connection
        if self.mqtt_client:
            try:
                await self.mqtt_client.__aexit__(None, None, None)
            except (OSError, TimeoutError, aiomqtt.MqttError):
                self.logger.exception("Error during MQTT disconnect")

        self.mqtt_client = None
        self._is_connected = False

    async def connect_printer(self, printer: Printer) -> bool:
        """Establish an asynchronous MQTT connection to the Elegoo printer."""
        if self.is_connected:
            self.logger.debug("Already connected")
            return True

        await self.disconnect()

        self.printer = printer
        msg = (
            f"Connecting to MQTT bridge for printer: {self.printer.name} "
            f"(broker: {self.mqtt_host}:{self.mqtt_port})"
        )
        self.logger.info(msg)

        try:
            self.mqtt_client = aiomqtt.Client(
                hostname=self.mqtt_host,
                port=self.mqtt_port,
                keepalive=MQTT_KEEPALIVE,
            )

            await self.mqtt_client.__aenter__()

            # Subscribe to all relevant topics for this printer
            topics = [
                f"{TOPIC_PREFIX}/{TOPIC_RESPONSE}/{self.printer.id}",
                f"{TOPIC_PREFIX}/{TOPIC_STATUS}/{self.printer.id}",
                f"{TOPIC_PREFIX}/{TOPIC_ATTRIBUTES}/{self.printer.id}",
                f"{TOPIC_PREFIX}/{TOPIC_NOTICE}/{self.printer.id}",
                f"{TOPIC_PREFIX}/{TOPIC_ERROR}/{self.printer.id}",
            ]

            for topic in topics:
                await self.mqtt_client.subscribe(topic)

            self._is_connected = True
            self._listener_task = asyncio.create_task(self._mqtt_listener())

            msg = f"Client successfully connected via MQTT to: {self.printer.name}"
            self.logger.info(msg)
        except (TimeoutError, OSError) as e:
            msg = f"Failed to connect via MQTT to {self.printer.name}: {e}"
            self.logger.debug(msg)
            self.logger.info(
                "Will retry connecting to printer '%s' via MQTT …",
                self.printer.name,
                exc_info=DEBUG,
            )
            await self.disconnect()
            return False
        else:
            return True

    def discover_printer(
        self, broadcast_address: str = DEFAULT_BROADCAST_ADDRESS
    ) -> list[Printer]:
        """
        Broadcast a UDP discovery message to locate MQTT-enabled Elegoo printers.

        Sends a discovery request and collects responses within a timeout period,
        returning a list of discovered printers. If no printers are found or a
        socket error occurs, returns an empty list.

        Arguments:
            broadcast_address: The network address to send the discovery message to.

        Returns:
            A list of discovered MQTT printers, or an empty list if none are found.

        """
        discovered_printers: list[Printer] = []
        self.logger.info("Broadcasting for MQTT printer discovery...")
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
                        msg_str = f"Discovery response received from {addr}"
                        self.logger.info(msg_str)
                        try:
                            printer_info = data.decode("utf-8")
                            printer = Printer(printer_info)
                            discovered_printers.append(printer)
                            self.logger.debug(
                                "Discovered printer: %s (protocol: %s)",
                                printer.name,
                                printer.protocol_type.value,
                            )
                        except (UnicodeDecodeError, ValueError, TypeError):
                            self.logger.exception("Failed to parse printer data")
                    except TimeoutError:
                        break  # Timeout, no more responses
            except OSError as e:
                msg_str = f"Socket error during discovery: {e}"
                self.logger.exception(msg_str)
                return []

        if not discovered_printers:
            self.logger.debug("No MQTT printers found during discovery.")
        else:
            msg_str = f"Discovered {len(discovered_printers)} MQTT printer(s)."
            self.logger.debug(msg_str)

        return discovered_printers

    async def _mqtt_listener(self) -> None:
        """Listen for messages on MQTT and handle them."""
        if not self.mqtt_client:
            return

        try:
            async for message in self.mqtt_client.messages:
                try:
                    payload = message.payload.decode("utf-8")
                    self._parse_response(payload, str(message.topic))
                except UnicodeDecodeError:
                    self.logger.exception("Failed to decode MQTT message")
                except (json.JSONDecodeError, KeyError, ValueError):
                    self.logger.exception("Error processing MQTT message")
        except asyncio.CancelledError:
            self.logger.debug("MQTT listener cancelled.")
        except (OSError, TimeoutError, aiomqtt.MqttError) as e:
            self.logger.debug("MQTT listener exception: %s", e)
            raise ElegooPrinterConnectionError from e
        finally:
            self._is_connected = False
            self.logger.info("MQTT listener stopped.")

    async def get_printer_status(self) -> PrinterData:
        """
        Retrieve the current status of the printer.

        Returns:
            The latest printer status information.

        """
        await self._send_printer_cmd(CMD_REQUEST_STATUS_REFRESH)
        return self.printer_data

    async def get_printer_attributes(self) -> PrinterData:
        """Retrieve the printer attributes."""
        await self._send_printer_cmd(CMD_REQUEST_ATTRIBUTES)
        return self.printer_data

    async def set_printer_video_stream(self, *, enable: bool) -> None:
        """
        Enable or disable the printer's video stream.

        Arguments:
            enable: If True, enables the video stream; if False, disables it.

        """
        await self._send_printer_cmd(CMD_SET_VIDEO_STREAM, {"Enable": int(enable)})

    async def get_printer_video(self, *, enable: bool = False) -> ElegooVideo:
        """
        Enable/disable video stream and retrieve stream information.

        Arguments:
            enable: If True, enables the video stream; if False, disables it.

        Returns:
            The current video stream information from the printer.

        """
        await self.set_printer_video_stream(enable=enable)
        msg = f"Sending printer video: {self.printer_data.video.to_dict()}"
        self.logger.debug(msg)
        return self.printer_data.video

    async def async_get_printer_historical_tasks(
        self,
    ) -> dict[str, PrintHistoryDetail | None] | None:
        """Asynchronously get the list of historical print tasks from the printer."""
        await self._send_printer_cmd(CMD_RETRIEVE_HISTORICAL_TASKS)
        return self.printer_data.print_history

    async def get_printer_task_detail(
        self, id_list: list[str]
    ) -> PrintHistoryDetail | None:
        """Retrieve historical tasks from the printer."""
        # Check cache first for all IDs
        for task_id in id_list:
            if task := self.printer_data.print_history.get(task_id):
                return task

        # If not found in cache, fetch the first ID
        if id_list:
            await self._send_printer_cmd(
                CMD_RETRIEVE_TASK_DETAILS, data={"Id": [id_list[0]]}
            )
            # Wait briefly for handler to populate cache
            await asyncio.sleep(0.1)
            return self.printer_data.print_history.get(id_list[0])

        return None

    def get_printer_current_task(self) -> PrintHistoryDetail | None:
        """Retrieve current task."""
        if self.printer_data.status.print_info.task_id:
            task_id = self.printer_data.status.print_info.task_id
            current_task = self.printer_data.print_history.get(task_id)
            msg = f"current_task: {current_task}"
            self.logger.debug(msg)
            if current_task is not None:
                return current_task
            self.logger.debug("Getting printer task from api")
            task = asyncio.create_task(self.get_printer_task_detail([task_id]))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return self.printer_data.print_history.get(task_id)
        return None

    def get_printer_last_task(self) -> PrintHistoryDetail | None:
        """Retrieve last task."""
        if self.printer_data.print_history:

            def sort_key(tid: str) -> int:
                task = self.printer_data.print_history.get(tid)
                return task.end_time or 0 if task else 0

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
        """
        Return the thumbnail URL of the current print task, or None if no thumbnail.

        Returns:
            The URL of the current print task's thumbnail image,
            or None if there is no active task or thumbnail.

        """
        task = self.get_printer_current_task()
        if task:
            return task.thumbnail
        return None

    async def async_get_printer_current_task(self) -> PrintHistoryDetail | None:
        """
        Asynchronously retrieve the current print task details from the printer.

        Returns:
            The details of the current print task if available, otherwise None.

        """
        if task_id := self.printer_data.status.print_info.task_id:
            self.logger.debug("get_printer_current_task task_id: %s", task_id)
            task = await self.get_printer_task_detail([task_id])
            if task:
                self.logger.debug(
                    "get_printer_current_task: task from the api: %s", task.task_id
                )
            else:
                self.logger.debug("get_printer_current_task: NO TASK FROM THE API")
            return task
        return None

    async def async_get_printer_last_task(self) -> PrintHistoryDetail | None:
        """Retrieve last task."""
        if self.printer_data.print_history:

            def sort_key(tid: str) -> int:
                task = self.printer_data.print_history.get(tid)
                return task.end_time or 0 if task else 0

            last_task_id = max(
                self.printer_data.print_history.keys(),
                key=sort_key,
            )
            task = self.printer_data.print_history.get(last_task_id)
            if task is None:
                await self.get_printer_task_detail([last_task_id])
                return self.printer_data.print_history.get(last_task_id)
            return task
        return None

    async def async_get_current_print_thumbnail(self) -> str | None:
        """
        Asynchronously retrieve the thumbnail URL of the current print task.

        Returns:
            The thumbnail URL if the current print task has one; otherwise, None.

        """
        if task := await self.async_get_printer_current_task():
            return task.thumbnail
        if last_task := await self.async_get_printer_last_task():
            return last_task.thumbnail
        return None

    async def set_light_status(self, light_status: LightStatus) -> None:
        """
        Set the printer's light status to the specified configuration.

        Arguments:
            light_status: The light status configuration to apply.

        """
        await self._send_printer_cmd(CMD_CONTROL_DEVICE, light_status.to_dict())

    async def print_pause(self) -> None:
        """Pause the current print."""
        await self._send_printer_cmd(CMD_PAUSE_PRINT)

    async def print_stop(self) -> None:
        """Stop the current print."""
        await self._send_printer_cmd(CMD_STOP_PRINT)

    async def print_resume(self) -> None:
        """Resume/continue the current print."""
        await self._send_printer_cmd(CMD_CONTINUE_PRINT)

    async def set_fan_speed(self, percentage: int, fan: ElegooFan) -> None:
        """
        Set the speed of a fan.

        percentage: 0 to 100
        """
        pct = max(0, min(100, int(percentage)))
        data = {"TargetFanSpeed": {fan.value: pct}}
        await self._send_printer_cmd(CMD_CONTROL_DEVICE, data)

    async def set_print_speed(self, percentage: int) -> None:
        """
        Set the print speed.

        percentage: 0 to 160
        """
        pct = max(0, min(160, int(percentage)))
        data = {"PrintSpeedPct": pct}
        await self._send_printer_cmd(CMD_CONTROL_DEVICE, data)

    async def set_target_nozzle_temp(self, temperature: int) -> None:
        """Set the target nozzle temperature."""
        clamped_temperature = max(0, min(320, int(temperature)))
        data = {"TempTargetNozzle": clamped_temperature}
        await self._send_printer_cmd(CMD_CONTROL_DEVICE, data)

    async def set_target_bed_temp(self, temperature: int) -> None:
        """Set the target bed temperature."""
        clamped_temperature = max(0, min(110, int(temperature)))
        data = {"TempTargetHotbed": clamped_temperature}
        await self._send_printer_cmd(CMD_CONTROL_DEVICE, data)

    async def _send_printer_cmd(
        self, cmd: int, data: dict[str, Any] | None = None
    ) -> None:
        """
        Send a JSON command to the printer via MQTT.

        Arguments:
            cmd: The command to send.
            data: The data to send with the command.

        Raises:
            ElegooPrinterNotConnectedError: If the printer is not connected.
            ElegooPrinterConnectionError: If an MQTT error or timeout occurs.
            OSError: If an operating system error occurs while sending the command.

        """
        if not self.is_connected:
            msg = "Printer not connected, cannot send command."
            raise ElegooPrinterNotConnectedError(msg)

        ts = int(time.time())
        data = data or {}
        request_id = secrets.token_hex(8)
        payload = {
            "Id": self.printer.connection,
            "Data": {
                "Cmd": cmd,
                "Data": data,
                "RequestID": request_id,
                "MainboardID": self.printer.id,
                "TimeStamp": ts,
                "From": 0,
            },
            "Topic": f"sdcp/request/{self.printer.id}",
        }

        if DEBUG:
            msg = f"printer << \n{json.dumps(payload, indent=4)}"
            self.logger.debug(msg)

        event = asyncio.Event()
        async with self._response_lock:
            self._response_events[request_id] = event

        if self.mqtt_client:
            try:
                topic = f"{TOPIC_PREFIX}/{TOPIC_REQUEST}/{self.printer.id}"
                await self.mqtt_client.publish(topic, json.dumps(payload))
                await asyncio.wait_for(event.wait(), timeout=10)
            except TimeoutError as e:
                self.logger.debug(
                    "Timed out waiting for response to cmd %s (RequestID=%s)",
                    cmd,
                    request_id,
                )
                raise ElegooPrinterTimeoutError from e
            except OSError as e:
                self._is_connected = False
                self.logger.info("MQTT connection error")
                raise ElegooPrinterConnectionError from e
            finally:
                async with self._response_lock:
                    self._response_events.pop(request_id, None)
        else:
            msg = "Not connected"
            raise ElegooPrinterNotConnectedError(msg)

    def _parse_response(self, response: str, topic: str) -> None:
        """
        Parse and route an incoming JSON response message from the printer.

        Attempts to decode the response as JSON and dispatches it to the appropriate
        handler based on the message topic.

        Arguments:
            response: The JSON response message to parse.
            topic: The MQTT topic the message was received on.

        """
        try:
            data = json.loads(response)
            # Extract topic type from MQTT topic
            # (e.g., "sdcp/response/..." -> "response")
            topic_parts = topic.split("/")
            if len(topic_parts) >= MQTT_TOPIC_MIN_PARTS:
                topic_type = topic_parts[1]
                match topic_type:
                    case "response":
                        self._response_handler(data)
                    case "status":
                        self._status_handler(data)
                    case "attributes":
                        self._attributes_handler(data)
                    case "notice":
                        msg = f"notice >> \n{json.dumps(data, indent=5)}"
                        self.logger.debug(msg)
                    case "error":
                        msg = f"error >> \n{json.dumps(data, indent=5)}"
                        self.logger.debug(msg)
                    case _:
                        self.logger.debug("--- UNKNOWN MESSAGE ---")
                        self.logger.debug(data)
                        self.logger.debug("--- UNKNOWN MESSAGE ---")
            else:
                self.logger.warning(
                    "Received message with invalid topic structure: %s", topic
                )
        except json.JSONDecodeError:
            self.logger.exception("Invalid JSON received")

    def _response_handler(self, data: dict[str, Any]) -> None:
        """
        Handle response messages by dispatching to appropriate handler.

        Based on the command type.

        Arguments:
            data: The response data.

        """
        if DEBUG:
            msg = f"response >> \n{json.dumps(data, indent=5)}"
            self.logger.debug(msg)
        inner_data = data.get("Data")
        if inner_data:
            request_id = inner_data.get("RequestID")
            if request_id:
                self._set_response_event_sync(request_id)
            data_data = inner_data.get("Data", {})
            cmd: int = inner_data.get("Cmd", 0)
            if cmd == CMD_RETRIEVE_HISTORICAL_TASKS:
                self._print_history_handler(data_data)
            elif cmd == CMD_RETRIEVE_TASK_DETAILS:
                self._print_history_detail_handler(data_data)
            elif cmd == CMD_SET_VIDEO_STREAM:
                self._print_video_handler(data_data)

    def _status_handler(self, data: dict[str, Any]) -> None:
        """
        Parse and update the printer's status information.

        Arguments:
            data: Dictionary containing the printer status information in
                JSON-compatible format.

        """
        if DEBUG:
            msg = f"status >> \n{json.dumps(data, indent=5)}"
            self.logger.info(msg)
        printer_status = PrinterStatus.from_json(
            json.dumps(data), self.printer.printer_type
        )
        self.printer_data.status = printer_status

    def _attributes_handler(self, data: dict[str, Any]) -> None:
        """
        Parse and update the printer's attribute data from a JSON dictionary.

        Arguments:
            data: Dictionary containing printer attribute information.

        """
        if DEBUG:
            msg = f"attributes >> \n{json.dumps(data, indent=5)}"
            self.logger.info(msg)
        printer_attributes = PrinterAttributes.from_json(json.dumps(data))
        self.printer_data.attributes = printer_attributes

    def _print_history_handler(self, data_data: dict[str, Any]) -> None:
        """Parse and update the printer's print history details from the data."""
        history_data_list = data_data.get("HistoryData")
        if history_data_list:
            for task_id in history_data_list:
                if task_id not in self.printer_data.print_history:
                    self.printer_data.print_history[task_id] = None

    def _print_history_detail_handler(self, data_data: dict[str, Any]) -> None:
        """
        Parse and update the printer's print history details from the provided data.

        Arguments:
            data_data: The data containing the print history details.

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

        Arguments:
            data_data: Dictionary containing video stream information.

        """
        self.printer_data.video = ElegooVideo(data_data)

    def _set_response_event_sync(self, request_id: str) -> None:
        """Set the event for a given request ID (synchronous wrapper)."""
        if event := self._response_events.get(request_id):
            event.set()
        elif DEBUG:
            self.logger.debug("No waiter found for RequestID=%s", request_id)
