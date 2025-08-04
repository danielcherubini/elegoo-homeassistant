import asyncio
import json
import os
import time
from types import MappingProxyType
from typing import Any, Callable, Optional

import anyio
import paho.mqtt.client as mqtt

from custom_components.elegoo_printer.elegoo_sdcp.const import DEBUG
from custom_components.elegoo_printer.elegoo_sdcp.exceptions import (
    ElegooPrinterNotConnectedError,
)
from custom_components.elegoo_printer.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.models.printer import Printer, PrinterData
from custom_components.elegoo_printer.models.status import LightStatus
from custom_components.elegoo_printer.models.video import ElegooVideo

from .const import DEFAULT_MQTT_PORT


class ElegooMqttClient:
    """MQTT client for interacting with an Elegoo printer."""

    def __init__(
        self,
        ip_address: str,
        logger: Any,
        config: MappingProxyType[str, Any] = MappingProxyType({}),
        port: int = DEFAULT_MQTT_PORT,
    ) -> None:
        """Initialize an ElegooMqttClient."""
        self.ip_address = ip_address
        self.port = port
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self._is_connected = False
        self._on_message_callback: Optional[Callable[[str, str], None]] = None
        self.config = config
        self.printer = Printer.from_dict(dict(config))
        self.printer_data = PrinterData()
        self.logger = logger
        self._background_tasks: set[asyncio.Task] = set()

    def on_connect(self, client, userdata, flags, rc):
        """Handle the MQTT on_connect event."""
        if rc == 0:
            self._is_connected = True
            self.logger.info("Connected to MQTT Broker!")
            # Subscribe to topics
            if self.printer.id:
                self.client.subscribe(f"sdcp/status/{self.printer.id}")
                self.client.subscribe(f"sdcp/attributes/{self.printer.id}")
                self.client.subscribe(f"sdcp/response/{self.printer.id}")
        else:
            self.logger.error(f"Failed to connect, return code {rc}\n")

    def on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages."""
        if DEBUG:
            self.logger.debug(
                f"Received `{msg.payload.decode()}` from `{msg.topic}` topic"
            )
        self.printer_data.update_from_websocket(msg.payload.decode())

    def set_on_message(self, callback: Callable[[str, str], None]) -> None:
        """Set the callback for incoming messages."""
        self._on_message_callback = callback

    async def connect(self) -> bool:
        """Connect to the MQTT broker."""
        try:
            await anyio.to_thread.run_sync(
                self.client.connect, self.ip_address, self.port
            )
            self.client.loop_start()
            await asyncio.sleep(1)
            return self.is_connected
        except Exception as e:
            self.logger.error(f"Failed to connect to MQTT broker: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        await anyio.to_thread.run_sync(self.client.loop_stop)
        await anyio.to_thread.run_sync(self.client.disconnect)
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        """Return true if the client is connected to the broker."""
        return self._is_connected

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
            current_task = self.printer_data.print_history.get(task_id)
            if current_task is not None:
                return current_task
            else:
                task = await self.get_printer_task_detail([task_id])
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

    async def _send_printer_cmd(
        self, cmd: int, data: dict[str, Any] | None = None
    ) -> None:
        """Send a JSON command to the printer.

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

        topic = f"sdcp/request/{self.printer.id}"
        try:
            mqtt_result = self.client.publish(topic, json.dumps(payload))
            if mqtt_result.rc != mqtt.MQTT_ERR_SUCCESS:
                self.logger.error(
                    f"Failed to publish message to {topic}, result code: {mqtt_result.rc}"
                )
        except Exception as e:
            self.logger.error(f"Exception while publishing message to {topic}: {e}")
