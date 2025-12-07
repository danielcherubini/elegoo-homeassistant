"""
Elegoo MQTT Client for SDCP.

This client connects to an MQTT broker that bridges communication
with Elegoo printers, rather than connecting directly to the printer.
"""

from __future__ import annotations

import asyncio
import os
import contextlib
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
    CMD_START_PRINT,
    CMD_DISCONNECT,
    CMD_PAUSE_PRINT,
    CMD_REQUEST_ATTRIBUTES,
    CMD_REQUEST_STATUS_REFRESH,
    CMD_RETRIEVE_HISTORICAL_TASKS,
    CMD_RETRIEVE_TASK_DETAILS,
    CMD_UPLOAD_FILE,
    CMD_SET_STATUS_UPDATE_PERIOD,
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
from custom_components.elegoo_printer.mqtt.file_server import ElegooFileHost

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
        # File transfer tracking
        self._file_host: ElegooFileHost | None = None
        self._file_transfer_event: asyncio.Event | None = None
        self._file_transfer_status: dict[str, Any] | None = None

    @property
    def is_connected(self) -> bool:
        """Return true if the client is connected to the printer."""
        return self._is_connected and self.mqtt_client is not None

    async def disconnect(self) -> None:
        """Disconnect from the printer."""
        self.logger.info("Closing MQTT connection to printer")

        # Send disconnect command to printer if connected
        if self._is_connected and self.mqtt_client:
            try:
                self.logger.debug("Sending disconnect command to printer")
                await self._send_printer_cmd(CMD_DISCONNECT, {})
            except (ElegooPrinterConnectionError, ElegooPrinterTimeoutError, OSError):
                self.logger.debug("Failed to send disconnect command")

        if self._listener_task:
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
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
            except (asyncio.TimeoutError, OSError, aiomqtt.MqttError):
                self.logger.exception("Error during MQTT disconnect")

        self.mqtt_client = None
        self._is_connected = False

    def _send_mqtt_connect_command(self, printer_ip: str) -> bool:
        """
        Send UDP command to tell printer to connect to MQTT broker.

        Uses the M66666 command with the MQTT broker host and port to instruct
        the printer to connect to the specified MQTT broker.

        Arguments:
            printer_ip: The IP address of the printer.

        Returns:
            True if command was sent successfully, False otherwise.

        """
        try:
            # Create UDP socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            # Construct M66666 command with MQTT broker host and port
            # Format: M66666 <host> <port>
            # No authentication - embedded broker doesn't require credentials
            message_parts = ["M66666", str(self.mqtt_host), str(self.mqtt_port)]
            message = " ".join(message_parts).encode()

            # Send to printer's discovery port
            sock.sendto(message, (printer_ip, DISCOVERY_PORT))
            sock.close()

            self.logger.info(
                "Sent M66666 command to printer %s to connect to MQTT broker %s:%s",
                printer_ip,
                self.mqtt_host,
                self.mqtt_port,
            )
        except OSError:
            self.logger.exception("Failed to send M66666 command to printer")
            return False
        else:
            return True

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

        # First, tell the printer to connect to our MQTT broker
        if printer.ip_address:
            if not self._send_mqtt_connect_command(printer.ip_address):
                msg = (
                    "Failed to send MQTT connect command, "
                    "but will try to connect anyway"
                )
                self.logger.warning(msg)
            # Give printer time to connect to broker
            await asyncio.sleep(2)

        try:
            # Build client configuration
            # No authentication required - embedded broker is unauthenticated
            client_kwargs = {
                "hostname": self.mqtt_host,
                "port": self.mqtt_port,
                "keepalive": MQTT_KEEPALIVE,
            }

            self.mqtt_client = aiomqtt.Client(**client_kwargs)

            await self.mqtt_client.__aenter__()

            # Subscribe to all relevant topics for this printer
            # Note: Leading slash is required to match printer's subscription pattern
            topics = [
                f"/{TOPIC_PREFIX}/{TOPIC_RESPONSE}/{self.printer.id}",
                f"/{TOPIC_PREFIX}/{TOPIC_STATUS}/{self.printer.id}",
                f"/{TOPIC_PREFIX}/{TOPIC_ATTRIBUTES}/{self.printer.id}",
                f"/{TOPIC_PREFIX}/{TOPIC_NOTICE}/{self.printer.id}",
                f"/{TOPIC_PREFIX}/{TOPIC_ERROR}/{self.printer.id}",
            ]

            for topic in topics:
                await self.mqtt_client.subscribe(topic)

            self._is_connected = True
            self._listener_task = asyncio.create_task(self._mqtt_listener())

            # Send connection handshake commands (like Cassini does)
            # CMD_0 and CMD_1 are handshakes that trigger status/attributes
            await self._send_printer_cmd(CMD_REQUEST_STATUS_REFRESH)
            await self._send_printer_cmd(CMD_REQUEST_ATTRIBUTES)
            # Tell printer to auto-push status updates every 2 seconds
            await self._send_printer_cmd(
                CMD_SET_STATUS_UPDATE_PERIOD, {"TimePeriod": 2000}
            )

            msg = f"Client successfully connected via MQTT to: {self.printer.name}"
            self.logger.info(msg)
        except (asyncio.TimeoutError, OSError, aiomqtt.MqttError) as e:
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

    async def start_print(self, filename: str, *, start_layer: int = 0) -> bool:
        """Start printing a file already on the printer."""
        if not self.is_connected:
            raise ElegooPrinterNotConnectedError("Not connected to MQTT broker")

        data = {"Filename": filename, "StartLayer": int(start_layer)}
        await self._send_printer_cmd(CMD_START_PRINT, data)

        # Watch a few status updates to confirm transition into printing
        tries = 0
        while tries < 5:
            await asyncio.sleep(1)
            status = self.printer_data.status
            if status and status.current_status is not None:
                # Any active print status value indicates success
                if status.print_info and status.print_info.status is not None:
                    return True
            tries += 1
        return False

    async def upload_file_from_path(
        self,
        path: str,
        *,
        display_name: str | None = None,
        clean_cache: int = 1,
        check: int = 0,
        compress: int = 0,
        start_when_done: bool = False,
    ) -> bool:
        """Upload a local file to the printer using MQTT file transfer.

        Returns True on successful transfer (optionally also starting the print).
        """
        if not self.is_connected:
            raise ElegooPrinterNotConnectedError("Not connected to MQTT broker")

        # Ensure file host is running
        if self._file_host is None:
            self._file_host = ElegooFileHost()
            await self._file_host.start()

        # Register file and build command payload
        hosted = await self._file_host.register_file(path, name=display_name)

        # Compute local IP the printer will reach (same node as MQTT broker)
        local_ip = PrinterData.get_local_ip(self.printer.ip_address)
        url = f"http://${{ipaddr}}:{self._file_host.port}/{hosted.name}"

        cmd_data = {
            "Check": int(check),
            "CleanCache": int(clean_cache),
            "Compress": int(compress),
            "FileSize": hosted.size,
            "Filename": display_name or os.path.basename(path),
            "MD5": hosted.md5,
            "URL": url,
        }

        # Prepare transfer event
        self._file_transfer_event = asyncio.Event()
        self._file_transfer_status = None

        await self._send_printer_cmd(CMD_UPLOAD_FILE, cmd_data)

        # Wait for completion by watching status updates that carry FileTransferInfo
        try:
            # 10 minutes max (large files)
            await asyncio.wait_for(self._wait_for_file_transfer_end(), timeout=600)
        except asyncio.TimeoutError:
            LOGGER.warning("File upload timed out")
            await self._file_host.unregister_file(hosted.name)
            return False

        # Clean route after transfer completes
        await self._file_host.unregister_file(hosted.name)

        if not self._file_transfer_status:
            return False

        status_code = self._file_transfer_status.get("Status")
        if status_code == 2:  # DONE
            if start_when_done:
                filename = self._file_transfer_status.get("Filename") or (
                    display_name or os.path.basename(path)
                )
                return await self.start_print(filename)
            return True
        return False

    async def _wait_for_file_transfer_end(self) -> None:
        if self._file_transfer_event is None:
            return
        while True:
            await self._file_transfer_event.wait()
            # Consume and check
            self._file_transfer_event.clear()
            if not self._file_transfer_status:
                continue
            st = self._file_transfer_status.get("Status")
            if st in (2, 3):  # DONE or ERROR
                return

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
                                "Discovered printer: %s (transport: %s)",
                                printer.name,
                                printer.transport_type.value,
                            )
                        except (UnicodeDecodeError, ValueError, TypeError):
                            self.logger.exception("Failed to parse printer data")
                    except socket.timeout:
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
        except (asyncio.TimeoutError, OSError, aiomqtt.MqttError):
            self.logger.exception("MQTT listener exception")
            # Don't raise - let the finally block run to clean up
            # Raising here causes "Task exception was never retrieved"
            return
        finally:
            self._is_connected = False
            self.logger.info("MQTT listener stopped.")

    async def get_printer_status(self) -> PrinterData:
        """
        Retrieve the current status of the printer.

        For MQTT printers, we don't need to request status - the printer
        auto-pushes status updates every 5 seconds after we send
        CMD_SET_STATUS_UPDATE_PERIOD during connection. The background
        listener keeps printer_data fresh, so we just return it.

        Returns:
            The latest printer status information.

        """
        status = (
            self.printer_data.status.current_status
            if self.printer_data.status
            else None
        )
        self.logger.debug(
            "get_printer_status() returning printer_data (id: %s, status: %s)",
            id(self.printer_data),
            status,
        )
        return self.printer_data

    async def get_printer_attributes(self) -> PrinterData:
        """
        Retrieve the printer attributes.

        For MQTT printers, attributes are sent during the initial handshake
        and don't change frequently, so we just return the cached data.
        The background listener updates printer_data when attributes arrive.

        Returns:
            The latest printer attributes information.

        """
        has_attrs = self.printer_data.attributes is not None
        self.logger.debug(
            "get_printer_attributes() returning (id: %s, has attributes: %s)",
            id(self.printer_data),
            has_attrs,
        )
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
        """
        Asynchronously get the list of historical print tasks from the printer.

        MQTT printers do not provide task details, so we skip this request
        to avoid unnecessary traffic.
        """
        self.logger.debug(
            "Skipping historical tasks request (not available for MQTT printers)"
        )
        return self.printer_data.print_history

    async def get_printer_task_detail(
        self, id_list: list[str]
    ) -> PrintHistoryDetail | None:
        """Retrieve historical tasks from the printer."""
        self.logger.debug("get_printer_task_detail called with id_list: %s", id_list)

        # Check cache first for all IDs
        for task_id in id_list:
            if task := self.printer_data.print_history.get(task_id):
                self.logger.debug("Task %s found in cache", task_id)
                return task

        # If not found in cache, fetch the first ID
        if id_list:
            self.logger.debug(
                "Task %s not in cache, requesting from printer", id_list[0]
            )
            await self._send_printer_cmd(
                CMD_RETRIEVE_TASK_DETAILS, data={"Id": [id_list[0]]}
            )
            # Handler populates cache before setting event, no sleep needed
            task = self.printer_data.print_history.get(id_list[0])
            if task:
                self.logger.debug("Task %s retrieved successfully", id_list[0])
            else:
                self.logger.debug("Task %s NOT in cache after request", id_list[0])
            return task

        self.logger.debug("Empty id_list, returning None")
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

            def sort_key(tid: str) -> float:
                task = self.printer_data.print_history.get(tid)
                return task.end_time.timestamp() if task and task.end_time else 0.0

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

        MQTT printers do not send TaskId in their status messages, so task
        details (begin_time, end_time, thumbnail) are not available.

        Returns:
            The details of the current print task if available, otherwise None.

        """
        task_id = self.printer_data.status.print_info.task_id
        if not task_id:
            # MQTT printers don't send TaskId in PrintInfo status messages
            # Task details (begin_time, end_time, thumbnail) are not available
            self.logger.debug(
                "No task_id in status (normal for MQTT printers), skipping task fetch"
            )
            return None

        self.logger.debug("Requesting task details for task_id: %s", task_id)
        task = await self.get_printer_task_detail([task_id])
        if task:
            self.logger.debug(
                "Got task from printer: task_id=%s, begin_time=%s, end_time=%s",
                task.task_id,
                task.begin_time,
                task.end_time,
            )
        else:
            self.logger.debug("NO TASK RETURNED FROM PRINTER for task_id: %s", task_id)
        return task

    async def async_get_printer_last_task(self) -> PrintHistoryDetail | None:
        """Retrieve last task."""
        if self.printer_data.print_history:

            def sort_key(tid: str) -> float:
                task = self.printer_data.print_history.get(tid)
                return task.end_time.timestamp() if task and task.end_time else 0.0

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
            "Topic": f"/sdcp/request/{self.printer.id}",
        }

        if DEBUG:
            msg = f"printer << \n{json.dumps(payload, indent=4)}"
            self.logger.debug(msg)

        event = asyncio.Event()
        async with self._response_lock:
            self._response_events[request_id] = event

        if self.mqtt_client:
            try:
                # Leading slash required to match printer's subscription pattern
                topic = f"/{TOPIC_PREFIX}/{TOPIC_REQUEST}/{self.printer.id}"
                await self.mqtt_client.publish(topic, json.dumps(payload))
                await asyncio.wait_for(event.wait(), timeout=10)
            except asyncio.TimeoutError as e:
                self.logger.debug(
                    "Timed out waiting for response to cmd %s (RequestID=%s)",
                    cmd,
                    request_id,
                )
                raise ElegooPrinterTimeoutError from e
            except (OSError, aiomqtt.MqttError) as e:
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
            self.logger.debug("Received MQTT message on topic: %s", topic)
            self.logger.debug("Message structure keys: %s", list(data.keys()))
            # Extract topic type from MQTT topic
            # (e.g., "/sdcp/response/..." -> "response")
            # Note: Leading slash results in empty string at index 0
            topic_parts = topic.split("/")
            if len(topic_parts) >= MQTT_TOPIC_MIN_PARTS:
                # With leading slash: ['', 'sdcp', 'response', 'id']
                # Without leading slash: ['sdcp', 'response', 'id']
                # Use index 2 if there's a leading slash, otherwise index 1
                topic_type = topic_parts[2] if topic_parts[0] == "" else topic_parts[1]
                self.logger.debug("Routing to handler for topic_type: %s", topic_type)
                match topic_type:
                    case "response":
                        self._response_handler(data)
                    case "status":
                        self.logger.debug("Calling _status_handler")
                        self._status_handler(data)
                    case "attributes":
                        self.logger.debug("Calling _attributes_handler")
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
            data_data = inner_data.get("Data", {})
            cmd: int = inner_data.get("Cmd", 0)
            if cmd == CMD_RETRIEVE_HISTORICAL_TASKS:
                self._print_history_handler(data_data)
            elif cmd == CMD_RETRIEVE_TASK_DETAILS:
                self._print_history_detail_handler(data_data)
            elif cmd == CMD_SET_VIDEO_STREAM:
                self._print_video_handler(data_data)
            # Signal waiters after handlers have updated state to avoid races
            request_id = inner_data.get("RequestID")
            if request_id:
                self._set_response_event_sync(request_id)

    def _status_handler(self, data: dict[str, Any]) -> None:
        """
        Parse and update the printer's status information.

        Arguments:
            data: Dictionary containing the printer status information in
                JSON-compatible format.

        """
        self.logger.debug("_status_handler called with keys: %s", list(data.keys()))
        if DEBUG:
            msg = f"status >> \n{json.dumps(data, indent=5)}"
            self.logger.info(msg)

        # MQTT printers send status nested under data['Data']['Status']
        # Extract the actual status data
        if "Data" in data:
            self.logger.debug("Found 'Data' key, checking for 'Status'")
            data_content = data["Data"]
            self.logger.debug("Data content keys: %s", list(data_content.keys()))
            if "Status" in data_content:
                status_data = data_content["Status"]
                self.logger.debug("Extracted status data successfully")
            else:
                self.logger.warning(
                    "Data key present but no Status: %s", list(data_content.keys())
                )
                return
        elif "Status" in data:
            # Fallback for WebSocket format
            self.logger.debug("Using WebSocket fallback format")
            status_data = data["Status"]
        else:
            self.logger.warning("Unknown status message format: %s", list(data.keys()))
            return

        try:
            # Capture file transfer progress if present (MQTT printers provide this)
            if isinstance(status_data, dict) and "FileTransferInfo" in status_data:
                fti = status_data.get("FileTransferInfo") or {}
                self._file_transfer_status = fti
                if self._file_transfer_event:
                    # Notify waiter for progress/terminal
                    self._file_transfer_event.set()

            printer_status = PrinterStatus.from_json(
                json.dumps(status_data), self.printer.printer_type
            )
            self.logger.debug("PrinterStatus.from_json() succeeded")
            self.printer_data.status = printer_status
            print_info_status = (
                printer_status.print_info.status if printer_status.print_info else None
            )
            self.logger.debug(
                "Assigned printer_data.status (id: %s, status: %s, print_info: %s)",
                id(self.printer_data),
                printer_status.current_status,
                print_info_status,
            )
        except Exception:
            self.logger.exception("Exception in _status_handler")

    def _attributes_handler(self, data: dict[str, Any]) -> None:
        """
        Parse and update the printer's attribute data from a JSON dictionary.

        Arguments:
            data: Dictionary containing printer attribute information.

        """
        self.logger.debug("_attributes_handler called with keys: %s", list(data.keys()))
        if DEBUG:
            msg = f"attributes >> \n{json.dumps(data, indent=5)}"
            self.logger.info(msg)

        # MQTT printers send attributes nested under data['Data']['Attributes']
        # Extract the actual attributes data
        if "Data" in data:
            self.logger.debug("Found 'Data' key, checking for 'Attributes'")
            data_content = data["Data"]
            self.logger.debug("Data content keys: %s", list(data_content.keys()))
            if "Attributes" in data_content:
                attributes_data = data_content["Attributes"]
                self.logger.debug("Extracted attributes data successfully")
            else:
                self.logger.warning(
                    "Data key present but no Attributes: %s", list(data_content.keys())
                )
                return
        elif "Attributes" in data:
            # Fallback for WebSocket format
            self.logger.debug("Using WebSocket fallback format")
            attributes_data = data["Attributes"]
        else:
            keys = list(data.keys())
            self.logger.warning("Unknown attributes message format: %s", keys)
            return

        try:
            printer_attributes = PrinterAttributes.from_json(
                json.dumps(attributes_data)
            )
            self.logger.debug("PrinterAttributes.from_json() succeeded")
            self.printer_data.attributes = printer_attributes
            self.logger.debug("Assigned printer_data.attributes successfully")
        except Exception:
            self.logger.exception("Exception in _attributes_handler")

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
        self.logger.debug("_print_history_detail_handler received data")
        history_data_list = data_data.get("HistoryDetailList")
        if history_data_list:
            self.logger.debug("Processing %d history detail(s)", len(history_data_list))
            for history_data in history_data_list:
                detail = PrintHistoryDetail(history_data)
                if detail.task_id is not None:
                    self.printer_data.print_history[detail.task_id] = detail
                    self.logger.debug(
                        "Added task %s to history (begin: %s, end: %s, thumbnail: %s)",
                        detail.task_id,
                        detail.begin_time,
                        detail.end_time,
                        detail.thumbnail is not None,
                    )
                else:
                    self.logger.warning("Task detail has no task_id, skipping")
        else:
            self.logger.debug("No HistoryDetailList in data_data")

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
