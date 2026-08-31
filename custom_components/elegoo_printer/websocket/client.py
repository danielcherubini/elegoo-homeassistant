"""Elegoo Websocket Client for SDCP."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import ClientWebSocketResponse
from aiohttp.client import ClientWSTimeout

from custom_components.elegoo_printer.const import (
    DEFAULT_BROADCAST_ADDRESS,
    DEFAULT_FALLBACK_IP,
    WEBSOCKET_PORT,
)
from custom_components.elegoo_printer.sdcp.const import (
    CMD_CONTINUE_PRINT,
    CMD_CONTROL_DEVICE,
    CMD_GET_CANVAS_STATUS,
    CMD_PAUSE_PRINT,
    CMD_REQUEST_ATTRIBUTES,
    CMD_REQUEST_STATUS_REFRESH,
    CMD_RETRIEVE_HISTORICAL_TASKS,
    CMD_RETRIEVE_TASK_DETAILS,
    CMD_SET_VIDEO_STREAM,
    CMD_STOP_PRINT,
    CMD_XYZ_HOME_CONTROL,
    DEBUG,
    LOGGER,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConfigurationError,
    ElegooPrinterConnectionError,
    ElegooPrinterNotConnectedError,
)
from custom_components.elegoo_printer.sdcp.models.ams import AMSStatus
from custom_components.elegoo_printer.sdcp.models.printer import (
    FileFilamentData,
    Printer,
)
from custom_components.elegoo_printer.sdcp.models.status import PrinterStatus
from custom_components.elegoo_printer.sdcp.transport.base import (
    SdcpPrinterClient,
)
from custom_components.elegoo_printer.sdcp.transport.discovery import (
    perform_printer_discovery,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from custom_components.elegoo_printer.cc2.gcode_proxy import GCodeProxyClient
    from custom_components.elegoo_printer.sdcp.models.enums import ElegooFan
    from custom_components.elegoo_printer.sdcp.models.print_history_detail import (
        PrintHistoryDetail,
    )
    from custom_components.elegoo_printer.sdcp.models.printer import PrinterData
    from custom_components.elegoo_printer.sdcp.models.status import LightStatus
    from custom_components.elegoo_printer.sdcp.types import (
        SDCPFrame,
        SDCPStatusMessage,
    )

logging.getLogger("websocket").setLevel(logging.CRITICAL)

# Seconds to wait before re-querying the gcode proxy for a filename that
# previously returned no data (e.g. the file was printed from local storage
# and never passed through the proxy).
GCODE_PROXY_RETRY_SECONDS = 60


class ElegooPrinterClient(SdcpPrinterClient):
    """
    Client for interacting with an Elegoo printer.

    Uses the SDCP Protocol (https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0).
    Includes a local websocket proxy to allow multiple local clients to communicate with one printer.
    """  # noqa: E501

    def __init__(
        self,
        ip_address: str | None,
        session: aiohttp.ClientSession,
        logger: Any = LOGGER,
        config: MappingProxyType[str, Any] = MappingProxyType({}),
        gcode_proxy: GCodeProxyClient | None = None,
    ) -> None:
        """
        Initialize an ElegooPrinterClient for communicating with an Elegoo 3D printer.

        Arguments:
            ip_address: The IP address of the target printer.
            session: The aiohttp client session.
            logger: The logger to use.
            config: A dictionary containing the config for the printer.
            gcode_proxy: Optional proxy client for per-slot filament data.

        """
        if ip_address is None:
            msg = "IP address is required but not provided"
            raise ElegooPrinterConfigurationError(msg)
        self.ip_address: str = ip_address
        self.printer_websocket: ClientWebSocketResponse | None = None
        self.config = config
        super().__init__(
            logger, Printer.from_dict(dict(config)), gcode_proxy=gcode_proxy
        )
        self._session: aiohttp.ClientSession = session
        self._gcode_filament_fetched: tuple[str, str] | None = None
        self._gcode_filament_attempt_for: tuple[str, str] | None = None
        self._gcode_filament_attempt_at: float = 0.0

    def _transport_open(self) -> bool:
        """Report whether the ws transport is open (part of is_connected)."""
        return self.printer_websocket is not None and not self.printer_websocket.closed

    async def _publish_frame(self, frame: str) -> None:
        """Publish a request frame on the websocket."""
        if self.printer_websocket is None:
            msg = "Not connected"
            raise ElegooPrinterNotConnectedError(msg)
        try:
            await self.printer_websocket.send_str(frame)
        except (OSError, aiohttp.ClientError) as e:
            self._is_connected = False
            self.logger.info("WebSocket connection closed error")
            raise ElegooPrinterConnectionError from e

    async def _on_disconnect(self) -> None:
        """Close the websocket connection."""
        if self.printer_websocket and not self.printer_websocket.closed:
            await self.printer_websocket.close()

    async def get_printer_status(self) -> PrinterData:
        """
        Retrieve the current status of the printer.

        Returns:
            The latest printer status information.

        """
        await self._send_printer_cmd(CMD_REQUEST_STATUS_REFRESH)
        return self.printer_data

    async def get_printer_attributes(self) -> PrinterData:
        """Retreves the printer attributes."""
        await self._send_printer_cmd(CMD_REQUEST_ATTRIBUTES)
        return self.printer_data

    async def async_get_printer_historical_tasks(
        self,
    ) -> dict[str, PrintHistoryDetail | None] | None:
        """Asynchronously gets the list of historical print tasks from the printer."""
        await self._send_printer_cmd(CMD_RETRIEVE_HISTORICAL_TASKS)
        return self.printer_data.print_history

    async def get_printer_task_detail(
        self, id_list: list[str]
    ) -> PrintHistoryDetail | None:
        """Retrieve historical tasks from the printer."""
        for task_id in id_list:
            if task := self.printer_data.print_history.get(task_id):
                return task
            await self._send_printer_cmd(
                CMD_RETRIEVE_TASK_DETAILS, data={"Id": [task_id]}
            )
            return self.printer_data.print_history.get(task_id)

        return None

    async def async_get_printer_current_task(self) -> PrintHistoryDetail | None:
        """
        Asynchronously retrieves the current print task details from the printer.

        Returns:
            The details of the current print task if available, otherwise None.

        """
        if task_id := self.printer_data.status.print_info.task_id:
            LOGGER.debug(f"get_printer_current_task task_id: {task_id}")
            task = await self.get_printer_task_detail([task_id])
            if task:
                LOGGER.debug(
                    f"get_printer_current_task: task from the api: {task.task_id}"
                )

            else:
                LOGGER.debug("get_printer_current_task: NO TASK FROM THE API")
            return task

        return None

    async def async_get_current_print_thumbnail(self) -> str | None:
        """
        Asynchronously retrieves the thumbnail URL of the current print task.

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
        await self._send_printer_cmd(CMD_PAUSE_PRINT, {})

    async def print_stop(self) -> None:
        """Stop the current print."""
        await self._send_printer_cmd(CMD_STOP_PRINT, {})

    async def print_resume(self) -> None:
        """Resume/continue the current print."""
        await self._send_printer_cmd(CMD_CONTINUE_PRINT, {})

    async def home_axis(self, axis: str) -> None:
        """
        Home one or more printer axes.

        Args:
            axis: Axis to home - "X", "Y", "Z", or "XYZ" for all axes

        """
        allowed_axes = {"X", "Y", "Z", "XYZ"}
        if axis not in allowed_axes:
            msg = (
                f"Invalid axis '{axis}'. "
                f"Must be one of: {', '.join(sorted(allowed_axes))}"
            )
            raise ValueError(msg)
        data = {"Axis": axis}
        await self._send_printer_cmd(CMD_XYZ_HOME_CONTROL, data)

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

    async def get_canvas_status(self) -> None:
        """
        Request Canvas/AMS status (filament colors, active tray).

        SDCP replies asynchronously: the response arrives as a push frame
        handled by _canvas_handler, which stores the parsed result in
        printer_data.ams_status. Nothing useful is available to return here.
        """
        await self._send_printer_cmd(CMD_GET_CANVAS_STATUS, {})

    def discover_printer(
        self, broadcast_address: str = DEFAULT_BROADCAST_ADDRESS
    ) -> list[Printer]:
        """
        Broadcasts a UDP discovery message to locate Elegoo printers or proxies.

        Sends a discovery request and collects responses within a timeout period,
        returning a list of discovered printers. If no printers are found or a
        socket error occurs, returns an empty list.

        Arguments:
            broadcast_address: The network address to send the discovery message to.

        Returns:
            A list of discovered printers, or an empty list if none are found.

        """
        self.logger.info("Broadcasting for printer/proxy discovery...")
        discovered_printers = perform_printer_discovery(
            broadcast_address, logger=self.logger
        )

        for printer in discovered_printers:
            msg = f"Discovered: {printer.name} ({printer.ip_address})"
            self.logger.info(msg)

        if not discovered_printers:
            self.logger.debug("No printers found during discovery.")
        else:
            msg = f"Discovered {len(discovered_printers)} printer(s)."
            self.logger.debug(msg)

        # Filter out printers on the same IP as the server with "None" or "Proxy"
        local_ip = self.get_local_ip()
        return [
            p
            for p in discovered_printers
            if not (
                p.ip_address == local_ip and ("None" in p.name or "Proxy" in p.name)
            )
        ]

    def get_local_ip(self) -> str:
        """
        Determine the local IP address used for outbound communication to the printer.

        Returns:
            The local IP address, or "127.0.0.1" if detection fails.

        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                # Doesn't have to be reachable
                s.connect((self.ip_address or DEFAULT_FALLBACK_IP, 1))
                return s.getsockname()[0]
        except (socket.gaierror, OSError):
            return "127.0.0.1"

    async def connect_printer(self, printer: Printer, *, proxy_enabled: bool) -> bool:
        """Establish an asynchronous connection to the Elegoo printer."""
        if self.is_connected:
            self.logger.debug("Already connected")
            return True

        await self.disconnect()

        self.printer = printer
        self.printer.proxy_enabled = proxy_enabled
        msg = f"Connecting to printer: {self.printer.name} at {self.printer.ip_address} proxy_enabled: {proxy_enabled}"  # noqa: E501
        self.logger.info(msg)

        url = f"ws://{self.printer.ip_address}:{WEBSOCKET_PORT}/websocket"
        try:
            timeout = ClientWSTimeout()
            self.printer_websocket = await self._session.ws_connect(
                url, timeout=timeout, heartbeat=30
            )
            self._is_connected = True
            self._listener_task = self._start_listener()
            msg = f"Client successfully connected to: {self.printer.name}, via proxy: {proxy_enabled}"  # noqa: E501
            self.logger.info(msg)
            return True  # noqa: TRY300
        except (TimeoutError, aiohttp.ClientError) as e:
            msg = f"Failed to connect WebSocket to {self.printer.name}: {e}"
            self.logger.debug(msg)
            self.logger.info(
                "Will retry connecting to printer '%s' …",
                self.printer.name,
                exc_info=DEBUG,
            )
            await self.disconnect()
            return False

    def _ws_listener(self) -> Coroutine[Any, Any, None]:
        """Pinned-name listener alias (tests pin the ``_ws_listener`` name)."""
        return self._listen()

    async def _listen(self) -> None:
        """Listen for messages on the WebSocket and handle them."""
        if not self.printer_websocket:
            return

        try:
            async for msg in self.printer_websocket:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        self._parse_response(msg.data)
                    except Exception:
                        # A malformed frame must never kill the listener.
                        self.logger.exception("Failed to parse WebSocket message")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    error_str = f"WebSocket connection error: {self.printer_websocket.exception()}"  # noqa: E501
                    self.logger.debug(error_str)
                    raise ElegooPrinterConnectionError(error_str)  # noqa: TRY301
        except asyncio.CancelledError:
            self.logger.debug("WebSocket listener cancelled.")
        except Exception as e:
            # Classify heartbeat/PONG timeouts explicitly
            error_msg = str(e)
            is_timeout = isinstance(e, asyncio.TimeoutError)
            is_heartbeat = "PONG" in error_msg or "heartbeat" in error_msg.lower()
            if is_timeout or is_heartbeat:
                self.logger.debug("WebSocket heartbeat timeout: %s", e)
            else:
                self.logger.exception("WebSocket listener exception: %s", e)  # noqa: TRY401
            raise ElegooPrinterConnectionError from e
        finally:
            self._is_connected = False
            self.logger.info("WebSocket listener stopped.")

    def _parse_response(self, response: str) -> None:
        """
        Parse and route an incoming JSON response message from the printer.

        Attempts to decode the response as JSON and dispatches it to the appropriate
        handler based on the message topic. Logs unknown topics, missing topics, and
        JSON decoding errors.

        Arguments:
            response: The JSON response message to parse.

        """
        try:
            data = json.loads(response)
            topic = data.get("Topic")
            if topic:
                parts = topic.split("/")
                if len(parts) < 2:  # noqa: PLR2004
                    self.logger.warning(
                        "Ignoring message with malformed Topic: %s", topic
                    )
                    return
                match parts[1]:
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
                self.logger.warning("Received message without 'Topic'")
                msg = f"Message content: {response}"
                self.logger.debug(msg)
        except json.JSONDecodeError:
            self.logger.exception("Invalid JSON received")

    def _response_handler(self, data: SDCPFrame) -> None:
        """
        Handle response messages by dispatching to the appropriate handler based on the command type.

        Routes print history and video stream response data to their respective
        handlers according to the command ID in the response.

        Arguments:
            data: The response data.

        """  # noqa: E501
        if DEBUG:
            msg = f"response >> \n{json.dumps(data, indent=5)}"
            self.logger.debug(msg)
        try:
            inner_data = data.get("Data")
            if inner_data:
                request_id = inner_data.get("RequestID")
                if request_id:
                    task = asyncio.create_task(self._set_response_event(request_id))
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
                data_data = inner_data.get("Data", {})
                cmd: int = inner_data.get("Cmd", 0)
                if cmd == CMD_RETRIEVE_HISTORICAL_TASKS:
                    self._handle_push_frame("print_history", data_data)
                elif cmd == CMD_RETRIEVE_TASK_DETAILS:
                    self._handle_push_frame("print_history_detail", data_data)
                elif cmd == CMD_SET_VIDEO_STREAM:
                    self._handle_push_frame("elegoo_video", data_data)
                elif cmd == CMD_GET_CANVAS_STATUS:
                    self._canvas_handler(data_data)
        except json.JSONDecodeError:
            self.logger.exception("Invalid JSON")

    def _status_handler(self, data: SDCPStatusMessage) -> None:
        """
        Parse and updates the printer's status information from the provided data.

        ws shape: the whole frame is fed to the model (the ws printer sends
        status fields within the frame) and the gcode-filament hook runs.

        Arguments:
            data: Dictionary containing the printer status information in JSON-compatible format.

        """  # noqa: E501
        if DEBUG:
            msg = f"status >> \n{json.dumps(data, indent=5)}"
            self.logger.info(msg)
        printer_status = PrinterStatus.from_json(
            json.dumps(data), self.printer.printer_type
        )
        self.printer_data.status = printer_status
        self._maybe_fetch_gcode_filament(
            printer_status.print_info.filename,
            printer_status.print_info.task_id,
        )

    def _maybe_fetch_gcode_filament(
        self, filename: str | None, task_id: str | None
    ) -> None:
        """
        Schedule a gcode proxy fetch when a new print job appears.

        Keyed on (task_id, filename): task_id is unique per job, so
        re-printing the same filename (e.g. another plate of the same
        project re-sliced and re-uploaded) refetches fresh data. A job the
        proxy has no data for is retried at most every
        GCODE_PROXY_RETRY_SECONDS.
        """
        if not self._gcode_proxy or not filename or not task_id:
            return
        job_key = (task_id, filename)
        if job_key == self._gcode_filament_fetched:
            return
        now = time.monotonic()
        if (
            job_key == self._gcode_filament_attempt_for
            and now - self._gcode_filament_attempt_at < GCODE_PROXY_RETRY_SECONDS
        ):
            return
        if self._gcode_filament_fetched is not None:
            # New print job — drop the previous job's filament data
            self.printer_data.gcode_filament_data = None
            self._gcode_filament_fetched = None
        self._gcode_filament_attempt_for = job_key
        self._gcode_filament_attempt_at = now
        task = asyncio.create_task(self._fetch_gcode_filament(filename, job_key))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _fetch_gcode_filament(
        self, filename: str, job_key: tuple[str, str]
    ) -> None:
        """Fetch per-slot filament data for a print job from the gcode proxy."""
        if not self._gcode_proxy:
            return
        # SDCP status may report a storage path (e.g. /local/file.gcode);
        # the proxy archives by the bare upload filename.
        query_name = filename.rsplit("/", 1)[-1]
        payload = await self._gcode_proxy.fetch_filament_data(query_name)
        if not payload:
            self.logger.debug("No gcode proxy data for %s (will retry)", filename)
            return
        filament_data = FileFilamentData.from_proxy_payload(payload)
        if filament_data is None:
            self.logger.debug("Gcode proxy payload empty for %s", filename)
            return
        self.printer_data.gcode_filament_data = filament_data
        self._gcode_filament_fetched = job_key
        self.logger.debug("Gcode proxy filament data cached for %s", filename)

    def _attributes_handler(self, data: dict[str, Any]) -> None:
        """
        Parse and updates the printer's attribute data (ws shape).

        ws feeds the whole frame; the actual parse / apply / sync step is
        shared on the base.

        Arguments:
            data: Dictionary containing printer attribute information.

        """
        if DEBUG:
            msg = f"attributes >> \n{json.dumps(data, indent=5)}"
            self.logger.info(msg)
        self._apply_attributes(data)

    def _canvas_handler(self, data: dict[str, Any]) -> None:
        """Parse Canvas/AMS status response and update printer_data."""
        try:
            ack = data.get("Ack", -1)
            if ack != 0:
                self.logger.debug("Canvas status request returned Ack=%s", ack)
                return

            ams_status = AMSStatus(data)
            self.printer_data.ams_status = ams_status
            self.logger.debug("Canvas status updated: %s", ams_status)
        except (KeyError, ValueError, TypeError):
            self.logger.exception("Failed to parse Canvas status")

    async def _set_response_event(self, request_id: str) -> asyncio.Event:
        """Set the event for a given request ID."""
        async with self._response_lock:
            if event := self._response_events.get(request_id):
                event.set()
            elif DEBUG:
                self.logger.debug("No waiter found for RequestID=%s", request_id)
