"""
Elegoo MQTT Client for SDCP.

This client connects to an MQTT broker that bridges communication
with Elegoo printers, rather than connecting directly to the printer.

Inherits the shared SDCP state / request-response machinery from
``SdcpPrinterClient`` (``sdcp.transport.base``) and keeps only the
MQTT-specific wire behaviour (leading-slash topics, the
``CMD_DISCONNECT`` handshake, the ``aiomqtt`` transport).
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import TYPE_CHECKING, Any

import aiomqtt

from custom_components.elegoo_printer.const import (
    DEFAULT_BROADCAST_ADDRESS,
    DISCOVERY_PORT,
)
from custom_components.elegoo_printer.sdcp.const import (
    CMD_CONTINUE_PRINT,
    CMD_CONTROL_DEVICE,
    CMD_DISCONNECT,
    CMD_PAUSE_PRINT,
    CMD_REQUEST_ATTRIBUTES,
    CMD_REQUEST_STATUS_REFRESH,
    CMD_RETRIEVE_HISTORICAL_TASKS,
    CMD_RETRIEVE_TASK_DETAILS,
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
from custom_components.elegoo_printer.sdcp.models.printer import (
    Printer,
)
from custom_components.elegoo_printer.sdcp.transport.base import (
    SdcpPrinterClient,
)
from custom_components.elegoo_printer.sdcp.transport.discovery import (
    perform_printer_discovery,
)

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
    from collections.abc import Callable, Coroutine

    from custom_components.elegoo_printer.sdcp.models.enums import ElegooFan
    from custom_components.elegoo_printer.sdcp.models.print_history_detail import (
        PrintHistoryDetail,
    )
    from custom_components.elegoo_printer.sdcp.models.printer import PrinterData
    from custom_components.elegoo_printer.sdcp.models.status import LightStatus


class ElegooMqttClient(SdcpPrinterClient):
    """
    MQTT client for interacting with an Elegoo printer via MQTT bridge.

    Connects to an MQTT broker that bridges communication with the printer
    rather than connecting directly to the printer.
    """

    def __init__(  # noqa: PLR0913
        self,
        mqtt_host: str = "localhost",
        mqtt_port: int = MQTT_PORT,
        advertise_host: str | None = None,
        logger: Any = LOGGER,
        printer: Printer | None = None,
        client_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        """
        Initialize an ElegooMqttClient.

        For communicating with an Elegoo 3D printer via MQTT bridge.

        Arguments:
            mqtt_host: The MQTT broker hostname.
            mqtt_port: The MQTT broker port.
            advertise_host: The host to tell the printer to connect to via the
                M66666 command, if different from mqtt_host (e.g. when this
                client reaches the broker over a different path than the
                printer does).
            logger: The logger to use.
            printer: Optional Printer object with existing configuration.
            client_factory: Optional factory override (test seam). Defaults
                to the real aiomqtt client.

        """
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.advertise_host = advertise_host or mqtt_host
        self.mqtt_client: aiomqtt.Client | None = None
        super().__init__(logger, printer or Printer())
        self._client_factory = client_factory

    def _transport_open(self) -> bool:
        """Report whether the mqtt transport is usable (client object exists)."""
        return self.mqtt_client is not None

    # ------------------------------------------------------------------
    # Transport-specific hooks / overrides on the shared base
    # ------------------------------------------------------------------

    def _request_topic(self, prefix: str) -> str:
        """
        Request topic prefix for this transport (in-frame "Topic").

        The leading slash is required to match the printer's subscription
        pattern — do NOT 'fix' it.
        """
        return f"/{prefix}"

    async def _publish_frame(self, frame: str) -> None:
        """Publish a request frame on the mqtt request topic."""
        if self.mqtt_client is None:
            msg = "Not connected"
            raise ElegooPrinterNotConnectedError(msg)
        try:
            # Leading slash required to match printer's subscription pattern
            topic = f"/{TOPIC_PREFIX}/{TOPIC_REQUEST}/{self.printer.id}"
            await self.mqtt_client.publish(topic, frame)
        except (OSError, aiomqtt.MqttError) as e:
            self._is_connected = False
            self.logger.info("MQTT connection error")
            raise ElegooPrinterConnectionError from e

    async def _disconnect_pre(self) -> None:
        """Send the disconnect command to the printer if connected."""
        if self._is_connected and self.mqtt_client:
            try:
                self.logger.debug("Sending disconnect command to printer")
                await self._send_printer_cmd(CMD_DISCONNECT, {})
            except (
                ElegooPrinterConnectionError,
                ElegooPrinterNotConnectedError,
                ElegooPrinterTimeoutError,
                OSError,
            ):
                self.logger.debug("Failed to send disconnect command")

    async def _on_disconnect(self) -> None:
        """Close the MQTT connection properly."""
        if self.mqtt_client:
            try:
                await self.mqtt_client.__aexit__(None, None, None)
            except (asyncio.TimeoutError, OSError, aiomqtt.MqttError):
                self.logger.exception("Error during MQTT disconnect")
        self.mqtt_client = None

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
            message_parts = ["M66666", str(self.advertise_host), str(self.mqtt_port)]
            message = " ".join(message_parts).encode()

            # Send to printer's discovery port
            sock.sendto(message, (printer_ip, DISCOVERY_PORT))
            sock.close()

            self.logger.info(
                "Sent M66666 command to printer %s to connect to MQTT broker %s:%s",
                printer_ip,
                self.advertise_host,
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

            client_cls = self._client_factory or aiomqtt.Client
            self.mqtt_client = client_cls(**client_kwargs)

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
            self._listener_task = self._start_listener()

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
        self.logger.info("Broadcasting for MQTT printer discovery...")
        discovered_printers = perform_printer_discovery(
            broadcast_address, logger=self.logger
        )

        for printer in discovered_printers:
            self.logger.debug(
                "Discovered printer: %s (transport: %s)",
                printer.name,
                printer.transport_type.value,
            )

        if not discovered_printers:
            self.logger.debug("No MQTT printers found during discovery.")
        else:
            msg = f"Discovered {len(discovered_printers)} MQTT printer(s)."
            self.logger.debug(msg)

        return discovered_printers

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

    def _mqtt_listener(self) -> Coroutine[Any, Any, None]:
        """Pinned-name listener alias (tests pin the ``_mqtt_listener`` name)."""
        return self._listen()

    async def _listen(self) -> None:
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
        Handle response messages by dispatching to the shared push handlers.

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
                self._handle_push_frame("print_history", data_data)
            elif cmd == CMD_RETRIEVE_TASK_DETAILS:
                self._handle_push_frame("print_history_detail", data_data)
            elif cmd == CMD_SET_VIDEO_STREAM:
                self._handle_push_frame("elegoo_video", data_data)
            # Signal waiters after handlers have updated state to avoid races
            request_id = inner_data.get("RequestID")
            if request_id:
                self._set_response_event_sync(request_id)

    def _attributes_handler(self, data: dict[str, Any]) -> None:
        """
        Parse and update the printer's attribute data from a JSON dictionary.

        MQTT printers send attributes nested under
        ``data["Data"]["Attributes"]``; the actual parse / apply / sync
        step is shared on the base.

        Arguments:
            data: Dictionary containing printer attribute information.

        """
        self.logger.debug("_attributes_handler called with keys: %s", list(data.keys()))
        if DEBUG:
            msg = f"attributes >> \n{json.dumps(data, indent=5)}"
            self.logger.info(msg)

        # MQTT printers send attributes nested under data["Data"]["Attributes"]
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
                    "Data key present but no Attributes: %s",
                    list(data_content.keys()),
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

        self._apply_attributes(attributes_data)

    def _set_response_event_sync(self, request_id: str) -> None:
        """Set the event for a given request ID (synchronous wrapper)."""
        if event := self._response_events.get(request_id):
            event.set()
        elif DEBUG:
            self.logger.debug("No waiter found for RequestID=%s", request_id)
