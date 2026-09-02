"""
Shared SDCP transport base for the websocket and mqtt printer clients.

``ElegooPrinterClient`` (websocket) and ``ElegooMQTTClient`` (MQTT
bridge) both speak SDCP over different wires; this base owns the
machinery they share one-for-one:

- the shared ``asyncio.Event``-based request/response registry and the
  :meth:`SdcpPrinterClient._send_printer_cmd` SDCP request framing;
- the shared push-handler bodies (print history / history detail / video)
  routed by :meth:`SdcpPrinterClient._handle_push_frame`;
- the 5-case status payload extraction chain
  (:meth:`SdcpPrinterClient._status_payload_extract`) and the shared
  status / attributes apply helpers.

Per-transport divergence stays in the subclasses: the mqtt leading-slash
topic shape (the in-frame request topic via
:meth:`SdcpPrinterClient._request_topic` and the publish/subscribe
topics), the per-transport ``_parse_response`` routing, the ws
Ack-gated ``_canvas_handler``, the ws gcode-filament keyed-retry state,
and the per-class connect/disconnect hooks. CC2 does not inherit this
base (distinct wire: registration, heartbeat, delayed disconnect).

This module is stdlib + sdcp-models only: no aiohttp / aiomqtt / paho
(enforced by ``tests/test_import_sdcppack.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
from typing import TYPE_CHECKING, Any

from custom_components.elegoo_printer.sdcp.const import (
    CMD_SET_VIDEO_STREAM,
    DEBUG,
    SDCP_COMMAND_TIMEOUT,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterNotConnectedError,
    ElegooPrinterTimeoutError,
)
from custom_components.elegoo_printer.sdcp.models.attributes import (
    PrinterAttributes,
)
from custom_components.elegoo_printer.sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.sdcp.models.printer import (
    Printer,
    PrinterData,
)
from custom_components.elegoo_printer.sdcp.models.status import PrinterStatus
from custom_components.elegoo_printer.sdcp.models.video import ElegooVideo

if TYPE_CHECKING:
    from custom_components.elegoo_printer.sdcp.types import (
        SDCPElegooVideoFrame,
        SDCPStatusMessage,
        SDPPrintHistoryDetailFrame,
        SDPPrintHistoryMessage,
    )

__all__ = ["SdcpPrinterClient"]


class SdcpPrinterClient:
    """
    Shared SDCP transport state and request/response plumbing.

    Deliberately a plain class (not ``abc.ABC``): the concrete
    transports only override what differs.
    """

    def __init__(
        self,
        logger: Any,
        printer: Printer,
        gcode_proxy: Any | None = None,
    ) -> None:
        """
        Initialize the shared SDCP state block.

        Arguments:
            logger: The logger to use.
            printer: The Printer this client talks to.
            gcode_proxy: Optional proxy client for per-slot filament data
                (owned here as the per-transport ``_gcode_proxy``).

        """
        self.logger = logger
        self.printer = printer
        self.printer_data = PrinterData(printer=printer)
        self._gcode_proxy = gcode_proxy
        self._is_connected: bool = False
        self._listener_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._response_events: dict[str, asyncio.Event] = {}
        self._response_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        """Return true if the client is connected to the printer."""
        return self._is_connected and self._transport_open()

    def _transport_open(self) -> bool:
        """Report whether the transport is usable (overridden per wire)."""
        msg = "transport_open must be implemented by the transport"
        raise NotImplementedError(msg)

    # ------------------------------------------------------------------
    # Connect / disconnect plumbing
    # ------------------------------------------------------------------

    def _start_listener(self) -> asyncio.Task:
        """Start the transport listener task."""
        return asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        """Listen for messages (overridden per wire)."""
        msg = "listen must be implemented by the transport"
        raise NotImplementedError(msg)

    async def disconnect(self) -> None:
        """
        Disconnect from the printer (shared shape, one implementation).

        ``_disconnect_pre()`` -> cancel + await the listener with a
        terminal-exception guard -> unblock any waiters ->
        ``_on_disconnect()`` -> reset the connection flag.
        """
        self.logger.info("Closing connection to printer")
        await self._disconnect_pre()

        if self._listener_task:
            self._listener_task.cancel()
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await self._listener_task
            except Exception:
                # A terminal listener exception must never escape — failing
                # here would skip the remaining cleanup and leak the
                # exception.
                self.logger.exception("Listener ended with an exception")
            self._listener_task = None

        # Unblock any waiters
        async with self._response_lock:
            for ev in self._response_events.values():
                ev.set()
            self._response_events.clear()

        await self._on_disconnect()
        self._is_connected = False

    async def _disconnect_pre(self) -> None:
        """Pre-disconnect hook (mqtt: the CMD_DISCONNECT handshake)."""

    async def _on_disconnect(self) -> None:
        """Post-cleanup hook (close the transport)."""

    # ------------------------------------------------------------------
    # SDCP request framing
    # ------------------------------------------------------------------

    def _request_topic(self, prefix: str) -> str:
        """
        Request topic prefix for this transport (in-frame "Topic").

        The mqtt override prepends the leading slash, which is required
        to match the printer's subscription pattern — do NOT 'fix' it.
        """
        return prefix

    async def _publish_frame(self, frame: str) -> None:
        """Publish a request frame on the wire (overridden per transport)."""
        msg = "publish_frame must be implemented by the transport"
        raise NotImplementedError(msg)

    async def get_printer_task_detail(
        self, id_list: list[str]
    ) -> PrintHistoryDetail | None:
        """Fetch task detail by ID (overridden per transport)."""
        msg = "get_printer_task_detail must be implemented by the transport"
        raise NotImplementedError(msg)

    async def _send_printer_cmd(
        self, cmd: int, data: dict[str, Any] | None = None
    ) -> None:
        """
        Send a JSON command to the printer and wait for its response.

        Arguments:
            cmd: The command to send.
            data: The data to send with the command.

        Raises:
            ElegooPrinterNotConnectedError: If the printer is not
                connected.
            ElegooPrinterConnectionError: If the transport publish
                fails (mapped in ``_publish_frame``).
            ElegooPrinterTimeoutError: If no response arrives within
                ``SDCP_COMMAND_TIMEOUT`` seconds.

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
            "Topic": f"{self._request_topic('sdcp/request')}/{self.printer.id}",
        }

        if DEBUG:
            msg = f"printer << \n{json.dumps(payload, indent=4)}"
            self.logger.debug(msg)

        event = asyncio.Event()
        async with self._response_lock:
            self._response_events[request_id] = event

        try:
            await self._publish_frame(json.dumps(payload))
            await asyncio.wait_for(event.wait(), SDCP_COMMAND_TIMEOUT)
        except TimeoutError as e:
            self.logger.debug(
                "Timed out waiting for response to cmd %s (RequestID=%s)",
                cmd,
                request_id,
            )
            raise ElegooPrinterTimeoutError from e
        finally:
            async with self._response_lock:
                self._response_events.pop(request_id, None)

    # ------------------------------------------------------------------
    # Shared push handler bodies + routing
    # ------------------------------------------------------------------

    def _handle_push_frame(self, topic: str, data: dict[str, Any]) -> None:
        """
        Route a shared push channel to its handler body.

        Arguments:
            topic: The logical channel name ('print_history',
                'print_history_detail', 'elegoo_video'); the per-transport
                ``_response_handler`` maps its wire dispatch (mqtt topic
                match / ws ``_parse_response`` topics + ``Cmd``) onto
                these channel names.
            data: The inner Data block of the frame.

        """
        match topic:
            case "print_history":
                self._print_history_handler(data)
            case "print_history_detail":
                self._print_history_detail_handler(data)
            case "elegoo_video":
                self._print_video_handler(data)

    def _print_history_handler(self, data_data: SDPPrintHistoryMessage) -> None:
        """
        Parse and update the printer's print history cache from the data.

        Seeds ``printer_data.print_history`` with the TaskIds of tasks the
        printer knows about (value None until detail is fetched).
        """
        history_data_list = data_data.get("HistoryData")
        if history_data_list:
            for task_id in history_data_list:
                if task_id not in self.printer_data.print_history:
                    self.printer_data.print_history[task_id] = None

    def _print_history_detail_handler(
        self, data_data: SDPPrintHistoryDetailFrame
    ) -> None:
        """
        Parse and update the printer's print history details from the data.

        Arguments:
            data_data: The data containing the print history details.

        """
        history_data_list = data_data.get("HistoryDetailList")
        if history_data_list:
            for history_data in history_data_list:
                detail = PrintHistoryDetail(history_data)
                if detail.task_id is not None:
                    self.printer_data.print_history[detail.task_id] = detail

    def _print_video_handler(self, data_data: SDCPElegooVideoFrame) -> None:
        """
        Parse video stream data and update the printer's video attribute.

        Arguments:
            data_data: Dictionary containing video stream information.

        """
        self.printer_data.video = ElegooVideo(data_data)

    # ------------------------------------------------------------------
    # Shared status / attributes helpers
    # ------------------------------------------------------------------

    def _status_payload_extract(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """
        Extract the status payload out of a status-topic frame.

        A 5-case union chain that subsumes both pre-extraction shapes:

        - ``{'Data': {'Status': {...}}}`` -> the inner Status (mqtt shape)
        - ``{'Status': {...}}`` (no Data) -> the top-level Status
        - ``{'Data': <dict without Status>}`` + a top-level ``Status`` ->
          the top-level Status wins (old ws subsumption; the old mqtt
          would warn+skip this ambiguous frame)
        - ``{'Data': <dict without Status>}`` and no top-level Status ->
          None: the handler must SKIP (no update; the old mqtt
          ``_status_handler`` skipped this shape — preserve that)
        - a frame with neither ``Data`` nor ``Status`` -> itself (the old
          ws whole-frame shape; the old mqtt warn+skip of this shape is
          intentionally subsumed — at most a debug log now)

        """
        inner = data.get("Data")
        if isinstance(inner, dict) and "Status" in inner:
            return inner["Status"]
        if "Status" in data:
            return data["Status"]
        if isinstance(inner, dict):
            return None
        return data

    def _apply_status(self, status_data: dict[str, Any]) -> None:
        """
        Parse a status payload and store it on ``printer_data``.

        Exceptions are logged and contained (the mqtt status handler
        behaviour).
        """
        try:
            printer_status = PrinterStatus.from_json(
                json.dumps(status_data), self.printer.printer_type
            )
            self.printer_data.status = printer_status
        except Exception:
            self.logger.exception("Exception in _status_handler")

    def _status_handler(self, data: SDCPStatusMessage) -> None:
        """
        Shared status push handler (mqtt shape: the extraction chain).

        ws overrides this (whole-frame parse + the gcode-filament hook).
        """
        self.logger.debug("_status_handler called with keys: %s", list(data.keys()))
        if DEBUG:
            msg = f"status >> \n{json.dumps(data, indent=5)}"
            self.logger.info(msg)

        status_data = self._status_payload_extract(data)
        if status_data is None:
            self.logger.warning(
                "Data key present but no Status: %s",
                list(data["Data"].keys()),
            )
            return

        self._apply_status(status_data)

    def _apply_attributes(self, attributes_data: dict[str, Any]) -> None:
        """
        Parse an attributes payload and store it, then sync the Printer.

        Shared by both transports — the per-payload extraction stays with
        the transport (ws passes the whole frame; mqtt unwraps
        ``Data -> Attributes`` before calling this).
        """
        try:
            printer_attributes = PrinterAttributes.from_json(
                json.dumps(attributes_data)
            )
            self.printer_data.attributes = printer_attributes
            if self.printer:
                self.printer.sync_from_attributes(printer_attributes)
        except Exception:
            self.logger.exception("Exception in _attributes_handler")

    # ------------------------------------------------------------------
    # Shared task accessors (structurally identical ws / mqtt bodies)
    # ------------------------------------------------------------------

    async def set_printer_video_stream(self, *, enable: bool) -> None:
        """
        Enable or disable the printer's video stream.

        Arguments:
            enable: If True, enables the video stream; if False,
                disables it.

        """
        await self._send_printer_cmd(CMD_SET_VIDEO_STREAM, {"Enable": int(enable)})

    async def get_printer_video(self, *, enable: bool = False) -> ElegooVideo:
        """
        Set the video stream and retrieve the stream information.

        Arguments:
            enable: If True, enables the video stream; if False, leaves
                it as is.

        Returns:
            The current video stream information from the printer.

        """
        await self.set_printer_video_stream(enable=enable)
        msg = f"Sending printer video: {self.printer_data.video.to_dict()}"
        self.logger.debug(msg)
        return self.printer_data.video

    def get_printer_current_task(self) -> PrintHistoryDetail | None:
        """Retrieve the current print task."""
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

    async def async_get_printer_last_task(self) -> PrintHistoryDetail | None:
        """Retrieve the last print task (asynchronous)."""
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
