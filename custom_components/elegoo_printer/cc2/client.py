"""
CC2 (Centauri Carbon 2) MQTT client.

This client implements the inverted MQTT architecture used by CC2 printers:
- The printer runs an MQTT broker on port 1883
- Home Assistant connects TO the printer as an MQTT client
- Client must register before sending commands
- Heartbeat mechanism keeps connection alive
- Status updates are delta-based (must merge with cached state)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
from copy import deepcopy
from typing import TYPE_CHECKING, Any

import aiomqtt

from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
    ElegooPrinterNotConnectedError,
    ElegooPrinterTimeoutError,
)
from custom_components.elegoo_printer.sdcp.models.printer import (
    Printer,
    PrinterData,
)
from custom_components.elegoo_printer.sdcp.models.video import ElegooVideo

from .const import (
    CC2_CMD_GET_ATTRIBUTES,
    CC2_CMD_GET_STATUS,
    CC2_CMD_PAUSE_PRINT,
    CC2_CMD_RESUME_PRINT,
    CC2_CMD_SET_FAN_SPEED,
    CC2_CMD_SET_LIGHT,
    CC2_CMD_SET_TEMPERATURE,
    CC2_CMD_SET_VIDEO_STREAM,
    CC2_CMD_STOP_PRINT,
    CC2_EVENT_ATTRIBUTES,
    CC2_EVENT_STATUS,
    CC2_HEARTBEAT_INTERVAL,
    CC2_HEARTBEAT_TIMEOUT,
    CC2_MAX_NON_CONTINUOUS_EVENTS,
    CC2_MQTT_DEFAULT_PASSWORD,
    CC2_MQTT_KEEPALIVE,
    CC2_MQTT_PORT,
    CC2_MQTT_USERNAME,
    CC2_REG_OK,
    CC2_REG_TOO_MANY_CLIENTS,
    CC2_REGISTRATION_TIMEOUT,
    LOGGER,
)
from .models import CC2StatusMapper

if TYPE_CHECKING:
    from custom_components.elegoo_printer.sdcp.models.enums import ElegooFan
    from custom_components.elegoo_printer.sdcp.models.print_history_detail import (
        PrintHistoryDetail,
    )
    from custom_components.elegoo_printer.sdcp.models.status import LightStatus


class ElegooCC2Client:
    """
    MQTT client for CC2 printers.

    Connects TO the printer's MQTT broker (inverted architecture).
    """

    def __init__(
        self,
        printer_ip: str,
        serial_number: str,
        access_code: str | None = None,
        logger: Any = LOGGER,
        printer: Printer | None = None,
    ) -> None:
        """
        Initialize an ElegooCC2Client.

        Arguments:
            printer_ip: The IP address of the printer.
            serial_number: The printer's serial number (used in MQTT topics).
            access_code: Optional access code for authentication.
            logger: The logger to use.
            printer: Optional Printer object with existing configuration.

        """
        self.printer_ip = printer_ip
        self.serial_number = serial_number
        self.access_code = access_code or CC2_MQTT_DEFAULT_PASSWORD
        self.logger = logger
        self.printer: Printer = printer or Printer()
        self.printer_data = PrinterData(printer=self.printer)

        # MQTT client state
        self.mqtt_client: aiomqtt.Client | None = None
        self._is_connected: bool = False
        self._is_registered: bool = False

        # Task management
        self._listener_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()

        # Request/response tracking
        self._response_events: dict[int, asyncio.Event] = {}
        self._response_data: dict[int, dict[str, Any]] = {}
        self._response_lock = asyncio.Lock()
        self._request_counter = 0

        # Client identification
        self._client_id = f"1_HA_{secrets.token_hex(4)}"
        self._request_id = f"{self._client_id}_req"

        # Status caching for delta updates
        self._cached_status: dict[str, Any] = {}
        self._status_sequence = 0
        self._non_continuous_count = 0

        # Heartbeat tracking
        self._last_pong_time: float = 0

    @property
    def is_connected(self) -> bool:
        """Return true if the client is connected and registered."""
        return (
            self._is_connected
            and self._is_registered
            and self.mqtt_client is not None
        )

    async def connect_printer(self, printer: Printer) -> bool:
        """
        Connect to the CC2 printer's MQTT broker.

        Arguments:
            printer: The Printer object to connect to.

        Returns:
            True if connection was successful, False otherwise.

        """
        if self.is_connected:
            self.logger.debug("Already connected to CC2 printer")
            return True

        await self.disconnect()

        self.printer = printer
        self.printer_ip = printer.ip_address or self.printer_ip
        self.serial_number = printer.id or self.serial_number

        self.logger.info(
            "Connecting to CC2 printer %s at %s:%s",
            self.printer.name,
            self.printer_ip,
            CC2_MQTT_PORT,
        )

        try:
            # Build MQTT client configuration
            client_kwargs = {
                "hostname": self.printer_ip,
                "port": CC2_MQTT_PORT,
                "keepalive": CC2_MQTT_KEEPALIVE,
                "username": CC2_MQTT_USERNAME,
                "password": self.access_code,
                "identifier": self._client_id,
            }

            self.mqtt_client = aiomqtt.Client(**client_kwargs)
            await self.mqtt_client.__aenter__()

            # Subscribe to topics before registration
            await self._subscribe_to_topics()

            self._is_connected = True

            # Start the message listener
            self._listener_task = asyncio.create_task(self._mqtt_listener())

            # Register with the printer
            registered = await self._register()
            if not registered:
                self.logger.error("Failed to register with CC2 printer")
                await self.disconnect()
                return False

            self._is_registered = True

            # Start heartbeat task
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # Request initial status and attributes
            await self._request_initial_data()

            self.logger.info(
                "Successfully connected to CC2 printer: %s",
                self.printer.name,
            )
        except (asyncio.TimeoutError, OSError, aiomqtt.MqttError) as e:
            self.logger.warning(
                "Failed to connect to CC2 printer %s: %s",
                self.printer.name,
                e,
            )
            await self.disconnect()
            return False
        else:
            return True

        return False

    async def disconnect(self) -> None:
        """Disconnect from the CC2 printer."""
        self.logger.info("Closing CC2 connection to printer")

        # Cancel heartbeat task
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None

        # Cancel listener task
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
            self._response_data.clear()

        # Close MQTT connection
        if self.mqtt_client:
            try:
                await self.mqtt_client.__aexit__(None, None, None)
            except (asyncio.TimeoutError, OSError, aiomqtt.MqttError):
                self.logger.debug("Error during MQTT disconnect")

        self.mqtt_client = None
        self._is_connected = False
        self._is_registered = False

    async def _subscribe_to_topics(self) -> None:
        """Subscribe to all required MQTT topics."""
        if not self.mqtt_client:
            return

        sn = self.serial_number
        client_id = self._client_id
        request_id = self._request_id

        topics = [
            f"elegoo/{sn}/{client_id}/api_response",  # Command responses
            f"elegoo/{sn}/api_status",  # Status updates
            f"elegoo/{sn}/{request_id}/register_response",  # Registration response
        ]

        for topic in topics:
            await self.mqtt_client.subscribe(topic)
            self.logger.debug("Subscribed to topic: %s", topic)

    async def _register(self) -> bool:
        """
        Register this client with the printer.

        Returns:
            True if registration was successful, False otherwise.

        """
        if not self.mqtt_client:
            return False

        sn = self.serial_number
        topic = f"elegoo/{sn}/api_register"
        payload = {
            "client_id": self._client_id,
            "request_id": self._request_id,
        }

        self.logger.debug("Registering with CC2 printer: %s", self._client_id)

        # Create event for registration response
        reg_event = asyncio.Event()
        self._registration_event = reg_event
        self._registration_result: dict[str, Any] | None = None

        try:
            await self.mqtt_client.publish(topic, json.dumps(payload))

            # Wait for registration response
            try:
                await asyncio.wait_for(
                    reg_event.wait(), timeout=CC2_REGISTRATION_TIMEOUT
                )
            except asyncio.TimeoutError:
                self.logger.warning("Registration timeout")
                return False

            # Check registration result
            if self._registration_result:
                error = self._registration_result.get("error", "")
                if error == CC2_REG_OK:
                    self.logger.info("Successfully registered with CC2 printer")
                    return True
                if error == CC2_REG_TOO_MANY_CLIENTS:
                    self.logger.error(
                        "Too many clients connected to CC2 printer. "
                        "Close other connections and try again."
                    )
                    return False
                self.logger.error("Registration failed: %s", error)
                return False

            return False

        finally:
            self._registration_event = None

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeat messages to keep connection alive."""
        self._last_pong_time = time.time()

        while self._is_connected:
            try:
                await asyncio.sleep(CC2_HEARTBEAT_INTERVAL)

                if not self._is_connected or not self.mqtt_client:
                    break

                # Check if we've received a PONG recently
                time_since_pong = time.time() - self._last_pong_time
                if time_since_pong > CC2_HEARTBEAT_TIMEOUT:
                    self.logger.warning(
                        "Heartbeat timeout (no PONG in %ds), connection may be lost",
                        int(time_since_pong),
                    )
                    self._is_connected = False
                    break

                # Send PING
                topic = f"elegoo/{self.serial_number}/{self._client_id}/api_request"
                ping_msg = {"type": "PING"}
                await self.mqtt_client.publish(topic, json.dumps(ping_msg))
                self.logger.debug("Sent heartbeat PING")

            except asyncio.CancelledError:
                break
            except (OSError, aiomqtt.MqttError) as e:
                self.logger.warning("Heartbeat error: %s", e)
                self._is_connected = False
                break

    async def _request_initial_data(self) -> None:
        """Request initial status and attributes from printer."""
        try:
            # Request attributes (version negotiation)
            await self._send_command(CC2_CMD_GET_ATTRIBUTES)
            # Request full status
            await self._send_command(CC2_CMD_GET_STATUS)
        except (ElegooPrinterTimeoutError, ElegooPrinterConnectionError):
            self.logger.warning("Failed to get initial data from CC2 printer")

    async def _mqtt_listener(self) -> None:
        """Listen for messages on MQTT and handle them."""
        if not self.mqtt_client:
            return

        try:
            async for message in self.mqtt_client.messages:
                try:
                    payload = message.payload.decode("utf-8")
                    topic = str(message.topic)
                    await self._handle_message(topic, payload)
                except UnicodeDecodeError:
                    self.logger.debug("Failed to decode MQTT message")
                except (json.JSONDecodeError, KeyError, ValueError):
                    self.logger.debug("Error processing MQTT message")
        except asyncio.CancelledError:
            self.logger.debug("CC2 MQTT listener cancelled")
        except (asyncio.TimeoutError, OSError, aiomqtt.MqttError):
            self.logger.debug("CC2 MQTT listener exception")
        finally:
            self._is_connected = False
            self.logger.info("CC2 MQTT listener stopped")

    async def _handle_message(self, topic: str, payload: str) -> None:
        """Handle an incoming MQTT message."""
        self.logger.debug("Received message on topic: %s", topic)

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            self.logger.debug("Invalid JSON in message")
            return

        # Handle PONG response
        if data.get("type") == "PONG":
            self._last_pong_time = time.time()
            self.logger.debug("Received heartbeat PONG")
            return

        # Handle registration response
        if "register_response" in topic:
            self._registration_result = data
            if hasattr(self, "_registration_event") and self._registration_event:
                self._registration_event.set()
            return

        # Handle command responses
        if "api_response" in topic:
            await self._handle_response(data)
            return

        # Handle status updates
        if "api_status" in topic:
            await self._handle_status_event(data)
            return

    async def _handle_response(self, data: dict[str, Any]) -> None:
        """Handle a command response message."""
        request_id = data.get("id")
        method = data.get("method")

        self.logger.debug("Received response: method=%s, id=%s", method, request_id)

        # Store response data
        if request_id is not None:
            async with self._response_lock:
                self._response_data[request_id] = data
                if event := self._response_events.get(request_id):
                    event.set()

        # Process specific response types
        result = data.get("result", {})

        if method == CC2_CMD_GET_STATUS:
            self._handle_full_status(result)
        elif method == CC2_CMD_GET_ATTRIBUTES:
            self._handle_attributes(result)
        elif method == CC2_CMD_SET_VIDEO_STREAM:
            self._handle_video_response(result)

    async def _handle_status_event(self, data: dict[str, Any]) -> None:
        """Handle a status event (push notification)."""
        method = data.get("method")

        if method == CC2_EVENT_STATUS:
            # Delta status update
            result = data.get("result", {})
            self._handle_delta_status(result)
        elif method == CC2_EVENT_ATTRIBUTES:
            # Attributes update
            result = data.get("result", {})
            self._handle_attributes(result)

    def _handle_full_status(self, status_data: dict[str, Any]) -> None:
        """Handle a full status response (from method 1002)."""
        self.logger.debug("Received full status update")
        self._cached_status = deepcopy(status_data)
        self._status_sequence = status_data.get("sequence", 0)
        self._non_continuous_count = 0

        # Convert to PrinterStatus
        self._update_printer_status()

    def _handle_delta_status(self, delta_data: dict[str, Any]) -> None:
        """Handle a delta status update (from event 6000)."""
        self.logger.debug("Received delta status update")

        # Check sequence continuity
        new_sequence = delta_data.get("sequence", 0)
        if new_sequence != self._status_sequence + 1:
            self._non_continuous_count += 1
            self.logger.debug(
                "Non-continuous sequence: expected %d, got %d (count: %d)",
                self._status_sequence + 1,
                new_sequence,
                self._non_continuous_count,
            )
            # After multiple non-continuous events, re-request full status
            if self._non_continuous_count >= CC2_MAX_NON_CONTINUOUS_EVENTS:
                self._request_full_status_background()
                self._non_continuous_count = 0
        else:
            self._non_continuous_count = 0

        self._status_sequence = new_sequence

        # Deep merge delta into cached status
        self._deep_merge(self._cached_status, delta_data)

        # Convert to PrinterStatus
        self._update_printer_status()

    def _deep_merge(self, base: dict, update: dict) -> None:
        """Deep merge update into base dictionary."""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def _update_printer_status(self) -> None:
        """Update printer_data.status from cached status."""
        try:
            # Map CC2 status format to PrinterStatus
            mapped_status = CC2StatusMapper.map_status(
                self._cached_status, self.printer.printer_type
            )
            self.printer_data.status = mapped_status
            self.logger.debug(
                "Updated printer status: %s",
                self.printer_data.status.current_status,
            )
        except Exception:
            self.logger.exception("Failed to map CC2 status to PrinterStatus")

    def _request_full_status_background(self) -> None:
        """Request full status in the background."""
        task = asyncio.create_task(self._request_full_status())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _request_full_status(self) -> None:
        """Request full status from printer."""
        try:
            await self._send_command(CC2_CMD_GET_STATUS)
        except (ElegooPrinterTimeoutError, ElegooPrinterConnectionError):
            self.logger.warning("Failed to request full status")

    def _handle_attributes(self, attrs_data: dict[str, Any]) -> None:
        """Handle attributes response."""
        self.logger.debug("Received attributes update")
        try:
            # Map CC2 attributes to PrinterAttributes
            mapped_attrs = CC2StatusMapper.map_attributes(attrs_data)
            self.printer_data.attributes = mapped_attrs
        except Exception:
            self.logger.exception("Failed to map CC2 attributes")

    def _handle_video_response(self, video_data: dict[str, Any]) -> None:
        """Handle video stream response."""
        self.printer_data.video = ElegooVideo(video_data)

    async def _send_command(
        self,
        method: int,
        params: dict[str, Any] | None = None,
        *,
        wait_for_response: bool = True,
    ) -> dict[str, Any] | None:
        """
        Send a command to the CC2 printer.

        Arguments:
            method: The command method ID.
            params: Optional parameters for the command.
            wait_for_response: Whether to wait for a response.

        Returns:
            The response data if wait_for_response is True, otherwise None.

        """
        if not self.is_connected:
            raise ElegooPrinterNotConnectedError

        self._request_counter += 1
        request_id = self._request_counter

        payload = {
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        topic = f"elegoo/{self.serial_number}/{self._client_id}/api_request"
        self.logger.debug("Sending command: method=%d, id=%d", method, request_id)

        if wait_for_response:
            event = asyncio.Event()
            async with self._response_lock:
                self._response_events[request_id] = event

        try:
            if self.mqtt_client:
                await self.mqtt_client.publish(topic, json.dumps(payload))

                if wait_for_response:
                    try:
                        await asyncio.wait_for(event.wait(), timeout=10)
                        async with self._response_lock:
                            return self._response_data.pop(request_id, None)
                    except asyncio.TimeoutError as e:
                        self.logger.debug("Timeout for method %d", method)
                        raise ElegooPrinterTimeoutError from e
            else:
                raise ElegooPrinterNotConnectedError
        except (OSError, aiomqtt.MqttError) as e:
            self._is_connected = False
            raise ElegooPrinterConnectionError from e
        finally:
            if wait_for_response:
                async with self._response_lock:
                    self._response_events.pop(request_id, None)
                    self._response_data.pop(request_id, None)

        return None

    # Public API methods (matching ElegooMqttClient interface)

    async def get_printer_status(self) -> PrinterData:
        """Return the current printer status."""
        return self.printer_data

    async def get_printer_attributes(self) -> PrinterData:
        """Return the printer attributes."""
        return self.printer_data

    async def set_printer_video_stream(self, *, enable: bool) -> None:
        """Enable or disable the printer's video stream."""
        await self._send_command(CC2_CMD_SET_VIDEO_STREAM, {"enable": int(enable)})

    async def get_printer_video(self, *, enable: bool = False) -> ElegooVideo:
        """Enable/disable video stream and retrieve stream information."""
        await self.set_printer_video_stream(enable=enable)
        return self.printer_data.video

    async def async_get_printer_historical_tasks(
        self,
    ) -> dict[str, PrintHistoryDetail | None] | None:
        """Get the list of historical print tasks."""
        # CC2 task history not fully implemented
        return self.printer_data.print_history

    async def get_printer_task_detail(
        self, id_list: list[str]
    ) -> PrintHistoryDetail | None:
        """Retrieve task details."""
        for task_id in id_list:
            if task := self.printer_data.print_history.get(task_id):
                return task
        return None

    def get_printer_current_task(self) -> PrintHistoryDetail | None:
        """Retrieve current task."""
        if self.printer_data.status.print_info.task_id:
            task_id = self.printer_data.status.print_info.task_id
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
            return self.printer_data.print_history.get(last_task_id)
        return None

    def get_current_print_thumbnail(self) -> str | None:
        """Return the thumbnail URL of the current print task."""
        task = self.get_printer_current_task()
        return task.thumbnail if task else None

    async def async_get_printer_current_task(self) -> PrintHistoryDetail | None:
        """Asynchronously retrieve the current print task details."""
        return self.get_printer_current_task()

    async def async_get_printer_last_task(self) -> PrintHistoryDetail | None:
        """Retrieve last task."""
        return self.get_printer_last_task()

    async def async_get_current_print_thumbnail(self) -> str | None:
        """Asynchronously retrieve the thumbnail URL of the current print task."""
        if task := await self.async_get_printer_current_task():
            return task.thumbnail
        if last_task := await self.async_get_printer_last_task():
            return last_task.thumbnail
        return None

    async def set_light_status(self, light_status: LightStatus) -> None:
        """Set the printer's light status."""
        await self._send_command(CC2_CMD_SET_LIGHT, light_status.to_dict())

    async def print_pause(self) -> None:
        """Pause the current print."""
        await self._send_command(CC2_CMD_PAUSE_PRINT)

    async def print_stop(self) -> None:
        """Stop the current print."""
        await self._send_command(CC2_CMD_STOP_PRINT)

    async def print_resume(self) -> None:
        """Resume/continue the current print."""
        await self._send_command(CC2_CMD_RESUME_PRINT)

    async def set_fan_speed(self, percentage: int, fan: ElegooFan) -> None:
        """Set the speed of a fan."""
        pct = max(0, min(100, int(percentage)))
        # Map fan names to CC2 format
        fan_map = {
            "ModelFan": "fan",
            "AuxiliaryFan": "aux_fan",
            "BoxFan": "box_fan",
        }
        fan_key = fan_map.get(fan.value, "fan")
        await self._send_command(CC2_CMD_SET_FAN_SPEED, {fan_key: pct})

    async def set_print_speed(self, percentage: int) -> None:  # noqa: ARG002
        """Set the print speed."""
        # CC2 may not support this directly, log for now
        self.logger.debug("set_print_speed not implemented for CC2")

    async def set_target_nozzle_temp(self, temperature: int) -> None:
        """Set the target nozzle temperature."""
        clamped_temp = max(0, min(320, int(temperature)))
        await self._send_command(CC2_CMD_SET_TEMPERATURE, {"extruder": clamped_temp})

    async def set_target_bed_temp(self, temperature: int) -> None:
        """Set the target bed temperature."""
        clamped_temp = max(0, min(110, int(temperature)))
        await self._send_command(CC2_CMD_SET_TEMPERATURE, {"heater_bed": clamped_temp})

    # Discovery method (for compatibility with existing code)
    def discover_printer(
        self, broadcast_address: str = "255.255.255.255"
    ) -> list[Printer]:
        """Discover CC2 printers (delegates to CC2Discovery)."""
        from .discovery import CC2Discovery  # noqa: PLC0415

        return CC2Discovery.discover_as_printers(broadcast_address)
