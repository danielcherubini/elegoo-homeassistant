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
from collections import deque
from copy import deepcopy
from typing import TYPE_CHECKING, Any

import aiomqtt

from custom_components.elegoo_printer.const import CC2_VIDEO_PATH, CC2_VIDEO_PORT
from custom_components.elegoo_printer.sdcp.exceptions import (
    PRINT_TRANSPORT_ERRORS,
    ElegooPrinterConnectionError,
    ElegooPrinterNotConnectedError,
    ElegooPrinterTimeoutError,
)
from custom_components.elegoo_printer.sdcp.models.ams import AMSStatus
from custom_components.elegoo_printer.sdcp.models.file_info import PrinterFile
from custom_components.elegoo_printer.sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.sdcp.models.printer import (
    Printer,
    PrinterData,
)
from custom_components.elegoo_printer.sdcp.models.video import ElegooVideo

from . import upload as _upload
from .const import (
    CC2_CMD_DELETE_FILE,
    CC2_CMD_GET_ATTRIBUTES,
    CC2_CMD_GET_CANVAS_STATUS,
    CC2_CMD_GET_FILE_DETAIL,
    CC2_CMD_GET_FILE_LIST,
    CC2_CMD_GET_FILE_THUMBNAIL,
    CC2_CMD_GET_STATUS,
    CC2_CMD_PAUSE_PRINT,
    CC2_CMD_RESUME_PRINT,
    CC2_CMD_SET_FAN_SPEED,
    CC2_CMD_SET_LIGHT,
    CC2_CMD_SET_PRINT_SPEED,
    CC2_CMD_SET_TEMPERATURE,
    CC2_CMD_SET_VIDEO_STREAM,
    CC2_CMD_START_PRINT,
    CC2_CMD_STOP_PRINT,
    CC2_COMMAND_TIMEOUT,
    CC2_DISCONNECT_DELAY,
    CC2_EVENT_ATTRIBUTES,
    CC2_EVENT_STATUS,
    CC2_HEARTBEAT_INTERVAL,
    CC2_HEARTBEAT_TIMEOUT,
    CC2_MAX_NON_CONTINUOUS_EVENTS,
    CC2_MQTT_DEFAULT_PASSWORD,
    CC2_MQTT_KEEPALIVE,
    CC2_MQTT_PORT,
    CC2_MQTT_USERNAME,
    CC2_PRINT_STATUS_TRANSITION_QUEUE_MAX,
    CC2_REG_OK,
    CC2_REG_TOO_MANY_CLIENTS,
    CC2_REGISTRATION_TIMEOUT,
    CC2_SERIAL_DISCOVERY_TIMEOUT,
    CC2_SERIAL_DISCOVERY_TOPIC,
    CC2_STATUS_TOPIC,
    CC2_STATUS_TOPIC_PARTS,
    CC2_TOPIC_PREFIX,
    LOGGER,
)
from .models import CC2StatusMapper

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    import aiohttp

    from custom_components.elegoo_printer.sdcp.models.enums import ElegooFan
    from custom_components.elegoo_printer.sdcp.models.status import (
        LightStatus,
        PrinterStatus,
    )

    from .gcode_proxy import GCodeProxyClient
    from .types import (
        CC2Attributes,
        CC2CanvasStatus,
        CC2Envelope,
        CC2FileList,
        CC2FileThumbnailResponse,
        CC2StatusFrame,
        CC2VideoResponse,
    )
    from .upload import UploadFile


# Returned by ``delete_file`` when the printer answered a 1047 with an
# ``error_code`` that is not a number. It is NOT a printer code: it is this
# client's own marker, deliberately non-zero so a caller that only asks "did the
# printer refuse?" treats an unreadable acknowledgement as a refusal, and
# deliberately negative so it can never collide with a real CC2 error code
# (1000-9004 in ``cc2/const.py``).
CC2_ERROR_REPLY_UNPARSEABLE = -1


class CC2UnusableFileListError(Exception):
    """
    A 1044 reply that cannot be read as a trustworthy file listing.

    Raised by ``get_verified_file_list`` — the listing method the destructive
    ``cleanup_files`` service uses — in two distinct cases:

    * the reply's ``result`` is not a dict carrying ``file_list``: an error
      envelope, a rejected-parameter reply, or any other shape, since 1044's
      sibling 1047 documents no response body at all and nobody has captured
      what a CC2 answers when it dislikes a 1044;
    * the reply carries a non-zero (or non-numeric) ``error_code``, i.e. the
      printer answered the request by refusing it — even when it also sent a
      ``file_list`` alongside the refusal, because then the listing is the
      printer's own words for "here is what I did NOT do" and cannot be
      evidence that anything is gone.

    Why a distinct exception rather than ``ElegooPrinterConnectionError``: the
    printer DID answer, so "not reachable" would be the wrong diagnosis, and
    ``PRINT_TRANSPORT_ERRORS`` is what a caller catches to mean "the link is
    down". Why a distinct exception rather than an empty dict: an empty dict is
    also what an EMPTY PRINTER answers, and a caller deciding "is this file
    still there?" cannot tell the two apart. Treating an unreadable reply as an
    empty inventory is what turns a failed re-list into a reported success, so
    the ambiguity is refused here rather than resolved in favour of deleting.
    """


def _error_code(value: object) -> int | None:
    """
    Read an ``error_code`` out of a printer reply, or None if it says nothing.

    None means "unreadable", which the callers turn into
    ``CC2_ERROR_REPLY_UNPARSEABLE``. A float is accepted only when it is
    integral: ``int(0.5)`` truncates to ``0``, and on this method ``0`` means
    "the printer did not refuse" — so rounding a fractional code down would
    report a delete the printer never confirmed. Same direction of error as the
    truncated ``older_than_days`` the cleanup service refuses: never resolve
    ambiguity toward "it is safe to destroy this".
    """
    if isinstance(value, bool):
        # True is 1 — non-zero, so harmless here — but it is not a code, and
        # reporting a boolean as error_code 1 would send a reader looking for a
        # firmware code that was never sent.
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class ElegooCC2Client:
    """
    MQTT client for CC2 printers.

    Connects TO the printer's MQTT broker (inverted architecture).
    """

    def __init__(  # noqa: PLR0913
        self,
        printer_ip: str,
        serial_number: str,
        access_code: str | None = None,
        logger: Any = LOGGER,
        printer: Printer | None = None,
        gcode_proxy: GCodeProxyClient | None = None,
        client_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        """
        Initialize an ElegooCC2Client.

        Arguments:
            printer_ip: The IP address of the printer.
            serial_number: The printer's serial number (used in MQTT topics).
            access_code: Optional access code for authentication.
            logger: The logger to use.
            printer: Optional Printer object with existing configuration.
            gcode_proxy: Optional proxy client for per-extruder filament data.
            client_factory: Optional factory override (test seam). Defaults to
                the real aiomqtt client.

        """
        self.printer_ip = printer_ip
        self.serial_number = serial_number
        self.access_code = access_code  # Can be None - will try fallbacks
        self.logger = logger
        self.printer: Printer = printer or Printer()
        self._gcode_proxy = gcode_proxy
        self._client_factory = client_factory
        self.printer_data = PrinterData(printer=self.printer)

        # MQTT client state
        self.mqtt_client: aiomqtt.Client | None = None
        self._is_connected: bool = False
        self._is_registered: bool = False

        # Connection generation for stale callback guard
        self._connection_generation: int = 0
        self._listener_generation: int = 0

        # Auth failure tracking
        self._last_auth_failure: bool = False

        # Delayed disconnect
        self._disconnect_delay_task: asyncio.Task | None = None

        # Task management
        self._listener_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()

        # Request/response tracking
        self._response_events: dict[int, asyncio.Event] = {}
        self._response_data: dict[int, dict[str, Any]] = {}
        self._response_lock = asyncio.Lock()
        self._upload_lock = asyncio.Lock()
        self._request_counter = 0

        # Client identification - match web interface format
        # Format: "0cli" + timestamp_hex + random_hex, truncated to exactly 10 chars
        # Web interface: w7e() function from elegoo-fdm-web/src/14-app-helpers.js
        # JavaScript equivalent: concatenate prefix/timestamp/random then slice to 10
        timestamp_hex = format(int(time.time() * 1000), "x")[-5:]  # Last 5 hex chars
        random_hex = format(secrets.randbelow(4096), "x")  # Random hex (0-fff)
        self._client_id = f"0cli{timestamp_hex}{random_hex}"[:10]  # Truncate to 10

        # Registration request ID - match web interface format
        # Format: UUID-like string + timestamp in hex
        # Web interface: JX() function from elegoo-fdm-web/src/14-app-helpers.js
        uuid_part = "".join(
            format(
                secrets.randbelow(16) if c == "x" else (secrets.randbelow(4) + 8), "x"
            )
            for c in "xxxxxxxxxxxxxxxx"
        )
        timestamp_hex_long = format(int(time.time() * 1000), "x")
        self._request_id = f"{uuid_part}{timestamp_hex_long}"

        self.logger.debug(
            "Generated CC2 client identifiers (web interface format): "
            "client_id=%s, request_id=%s",
            self._client_id,
            self._request_id,
        )

        # Status caching for delta updates (printer-reported keys only)
        self._cached_status: dict[str, Any] = {}
        # Enrichment not present in MQTT status (file details, thumbnails, etc.)
        self._integration_data: dict[str, Any] = {}
        self._status_sequence = 0
        self._non_continuous_count = 0
        # Queued snapshots: each distinct print_info.status between HA polls
        self._print_status_transition_queue: deque[PrinterStatus] = deque(
            maxlen=CC2_PRINT_STATUS_TRANSITION_QUEUE_MAX
        )

        # Registration tracking
        self._registration_event: asyncio.Event | None = None
        self._registration_result: dict[str, Any] | None = None

        # Heartbeat tracking
        self._last_pong_time: float = 0

    @property
    def is_connected(self) -> bool:
        """Return true if the client is connected and registered."""
        return self._is_connected and self._transport_open()

    def _transport_open(self) -> bool:
        """Report whether the cc2 transport is usable (registered + client object)."""
        return self._is_registered and self.mqtt_client is not None

    @staticmethod
    def _is_auth_failure(exc: Exception) -> bool:
        """
        Check if an MQTT exception indicates an authentication failure.

        aiomqtt raises MqttCodeError with rc=4 (bad username/password)
        or rc=5 (not authorised) for MQTT 3.1.1, or ReasonCode objects
        with value 134/135 for MQTT 5.
        """
        # Local import to avoid circular dependency at module level
        from paho.mqtt.reasoncodes import ReasonCode  # noqa: PLC0415

        if isinstance(exc, aiomqtt.MqttCodeError):
            rc = exc.rc
            # MQTT 3.1.1 integer codes: 4 = bad username/password, 5 = not authorised
            if isinstance(rc, int) and rc in (4, 5):
                return True
            # MQTT 5 ReasonCode: 134 = bad username/password, 135 = not authorized
            if isinstance(rc, ReasonCode) and rc.value in (134, 135):
                return True
        return False

    async def connect_printer(self, printer: Printer) -> bool:
        """
        Connect to the CC2 printer's MQTT broker.

        Tries multiple password strategies if no access code provided:
        1. User-provided access code (if any)
        2. Empty password
        3. Default "123456" password

        Arguments:
            printer: The Printer object to connect to.

        Returns:
            True if connection was successful, False otherwise.

        """
        # Reset auth failure flag for fresh connection attempt
        self._last_auth_failure = False

        if self.is_connected:
            self.logger.debug("Already connected to CC2 printer")
            return True

        await self.disconnect()

        self.printer = printer
        # connection_host is the proxy when one is configured, else the printer
        # IP; every CC2 connection (MQTT broker, g-code upload, camera) targets it.
        self.printer_ip = printer.connection_host or self.printer_ip
        self.serial_number = printer.id or self.serial_number

        self.logger.info(
            "Connecting to CC2 printer %s at %s:%s",
            self.printer.name,
            self.printer_ip,
            CC2_MQTT_PORT,
        )

        # Build list of passwords to try
        passwords_to_try: list[str] = []

        if self.access_code is not None:
            # User provided an access code - only try that
            passwords_to_try = [self.access_code]
            self.logger.debug("Using user-provided access code")
        else:
            # No access code provided - try common passwords
            passwords_to_try = ["", CC2_MQTT_DEFAULT_PASSWORD]
            self.logger.debug(
                "No access code provided, will try fallback passwords: "
                "[empty string, %s]",
                CC2_MQTT_DEFAULT_PASSWORD,
            )

        # Try each password in sequence
        for attempt_num, password in enumerate(passwords_to_try, 1):
            # Create safe description for logging (never log actual credentials)
            if self.access_code is not None:
                auth_desc = "user-provided access code"
            elif password == "":
                auth_desc = "empty access code"
            else:
                auth_desc = "default access code"

            self.logger.info(
                "Connection attempt %d/%d using %s",
                attempt_num,
                len(passwords_to_try),
                auth_desc,
            )

            success = await self._try_connect_with_password(password)
            if success:
                # Store the working password
                self.access_code = password
                self.logger.info(
                    "Successfully connected to CC2 printer %s using %s",
                    self.printer.name,
                    auth_desc,
                )
                return True

            # Failed - disconnect and try next password
            self.logger.debug(
                "Connection attempt %d/%d failed using %s",
                attempt_num,
                len(passwords_to_try),
                auth_desc,
            )
            # Stop trying only if a user-provided code failed auth — no
            # point falling back to defaults when the explicit code is wrong.
            # When cycling fallback passwords, keep going: the first may fail
            # while a later one succeeds (e.g. empty→rejected, "123456"→ok).
            if self._last_auth_failure and self.access_code is not None:
                break
            self._last_auth_failure = False
            await self.disconnect()

        # All attempts failed
        self.logger.error(
            "Failed to connect to CC2 printer %s after trying %d password(s). "
            "Please verify the access code in printer settings: "
            "Settings → Network → Access Code",
            self.printer.name,
            attempt_num,
        )
        return False

    @staticmethod
    def _serial_from_status_topic(topic: str) -> str | None:
        """
        Extract the printer serial from an ``elegoo/{sn}/api_status`` topic.

        Arguments:
            topic: The MQTT topic a message arrived on.

        Returns:
            The serial, or None when the topic is not a status topic.

        """
        parts = topic.split("/")
        if (
            len(parts) == CC2_STATUS_TOPIC_PARTS
            and parts[0] == CC2_TOPIC_PREFIX
            and parts[2] == CC2_STATUS_TOPIC
        ):
            return parts[1]
        return None

    @staticmethod
    async def _await_serial_topic(client: Any, wait_timeout: float) -> str | None:
        """
        Read messages until a status topic reveals the serial, or time runs out.

        Arguments:
            client: The connected MQTT client to read from.
            wait_timeout: Seconds to wait for a status push.

        Returns:
            The serial, or None on timeout or clean disconnect.

        """
        try:
            async with asyncio.timeout(wait_timeout):
                async for message in client.messages:
                    serial = ElegooCC2Client._serial_from_status_topic(
                        str(message.topic)
                    )
                    if serial:
                        return serial
        except TimeoutError:
            return None
        return None

    async def async_discover_serial(
        self, wait_timeout: float = CC2_SERIAL_DISCOVERY_TIMEOUT
    ) -> str | None:
        """
        Best-effort learn the printer serial from an MQTT status push.

        Used when discovery was skipped (a forward proxy does not answer UDP
        discovery), so the serial — which is baked into every CC2 topic — is
        unknown. Connects to the broker, subscribes to ``elegoo/+/api_status``
        and returns the serial from the first matching status topic.

        This is a *transient* probe: it deliberately does NOT register (an
        empty serial would publish ``elegoo//api_register``), does not start the
        message listener or heartbeat, and always disconnects on exit. It never
        raises — a proxy that is down or a firmware that pushes nothing returns
        ``None`` so the caller can fall back to prompting the user.

        The bound is named ``wait_timeout`` rather than ``timeout`` because it
        applies per password attempt: the outer caller cannot express that with a
        single structured-concurrency scope around the whole loop.

        Arguments:
            wait_timeout: Seconds to wait for a status push, per password attempt.

        Returns:
            The learned serial, or None if none could be learned.

        """
        # Mirror connect_printer's password strategy exactly: an explicit access
        # code is the only candidate, otherwise the usual fallbacks.
        if self.access_code is not None:
            passwords_to_try: list[str] = [self.access_code]
        else:
            passwords_to_try = ["", CC2_MQTT_DEFAULT_PASSWORD]

        for password in passwords_to_try:
            serial: str | None = None
            try:
                client_cls = self._client_factory or aiomqtt.Client
                client = client_cls(
                    hostname=self.printer_ip,
                    port=CC2_MQTT_PORT,
                    keepalive=CC2_MQTT_KEEPALIVE,
                    username=CC2_MQTT_USERNAME,
                    password=password,
                    identifier=self._client_id,
                )
                # Explicit enter/exit (not `async with`) so disconnect() stays
                # the only closer, exactly like _try_connect_with_password.
                self.mqtt_client = client
                await client.__aenter__()
                await client.subscribe(CC2_SERIAL_DISCOVERY_TOPIC)
                serial = await self._await_serial_topic(client, wait_timeout)
            except (TimeoutError, OSError, aiomqtt.MqttError):
                serial = None
            finally:
                await self.disconnect()

            if serial:
                self.logger.debug(
                    "Learned CC2 serial %s from %s", serial, self.printer_ip
                )
                return serial

        self.logger.info(
            "Could not learn a CC2 serial from %s within %ss",
            self.printer_ip,
            wait_timeout,
        )
        return None

    async def _try_connect_with_password(self, password: str) -> bool:
        """
        Try to connect with a specific password.

        Arguments:
            password: The password to try.

        Returns:
            True if connection and registration succeeded, False otherwise.

        """
        # Increment generation for this connection attempt
        self._connection_generation += 1

        try:
            # Build MQTT client configuration
            client_kwargs = {
                "hostname": self.printer_ip,
                "port": CC2_MQTT_PORT,
                "keepalive": CC2_MQTT_KEEPALIVE,
                "username": CC2_MQTT_USERNAME,
                "password": password,
                "identifier": self._client_id,
            }

            self.logger.debug(
                "Creating MQTT client: hostname=%s, port=%d, username=%s, "
                "client_id=%s, password_length=%d",
                self.printer_ip,
                CC2_MQTT_PORT,
                CC2_MQTT_USERNAME,
                self._client_id,
                len(password) if password else 0,
            )

            client_cls = self._client_factory or aiomqtt.Client
            self.mqtt_client = client_cls(**client_kwargs)
            await self.mqtt_client.__aenter__()
            self.logger.debug("MQTT connection established successfully")

            # Subscribe to topics before registration
            await self._subscribe_to_topics()

            self._is_connected = True

            # Start the message listener
            self._listener_task = asyncio.create_task(self._mqtt_listener())

            # Register with the printer
            registered = await self._register()
            if not registered:
                self.logger.debug(
                    "Registration failed. This usually indicates incorrect access code."
                )
                return False

            self._is_registered = True

            # Cancel any pending disconnect delay (reconnect succeeded)
            if self._disconnect_delay_task is not None:
                self._disconnect_delay_task.cancel()
                self._disconnect_delay_task = None
                self.logger.debug("Cancelled pending disconnect delay")

            # Start heartbeat task
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # Request initial status and attributes
            await self._request_initial_data()

        except (asyncio.TimeoutError, OSError, aiomqtt.MqttError) as e:
            self.logger.debug(
                "Connection attempt failed: %s",
                e,
            )
            # Check for auth failure to skip remaining passwords
            if ElegooCC2Client._is_auth_failure(e):
                self._last_auth_failure = True
        else:
            return True

        return False

    async def disconnect(self) -> None:
        """Disconnect from the CC2 printer."""
        # Increment generation to invalidate any in-flight callbacks
        self._connection_generation += 1

        self.logger.info("Closing CC2 connection to printer")

        # Cancel any pending disconnect delay
        if self._disconnect_delay_task is not None:
            self._disconnect_delay_task.cancel()
            self._disconnect_delay_task = None

        # Cancel heartbeat task
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None

        # Cancel listener task
        if self._listener_task:
            self._listener_task.cancel()
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await self._listener_task
            except Exception:
                # A terminal listener exception must never escape — failing here
                # would skip the remaining cleanup and leak the exception.
                self.logger.exception("CC2 MQTT listener ended with an exception")
            self._listener_task = None
            # The listener's finally may have created a new delay task — cancel it
            if self._disconnect_delay_task is not None:
                self._disconnect_delay_task.cancel()
                self._disconnect_delay_task = None

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
        self._print_status_transition_queue.clear()

    def consume_print_status_transition_queue(self) -> list[PrinterStatus]:
        """
        Drain and return all queued PrinterStatus snapshots for distinct print_info.status changes.

        Each snapshot was captured when the mapped print sub-status changed. The data
        coordinator replays them so Home Assistant records transient values that would
        otherwise be overwritten before the next poll (for example COMPLETE before IDLE).

        Returns:
            list[PrinterStatus]: Snapshots in the order the transitions occurred.

        """  # noqa: E501
        snapshots = list(self._print_status_transition_queue)
        self._print_status_transition_queue.clear()
        return snapshots

    async def _subscribe_to_topics(self) -> None:
        """Subscribe to all required MQTT topics."""
        if not self.mqtt_client:
            self.logger.warning(
                "Cannot subscribe to topics: MQTT client not initialized"
            )
            return

        sn = self.serial_number
        client_id = self._client_id
        request_id = self._request_id

        topics = [
            f"elegoo/{sn}/{client_id}/api_response",  # Command responses
            f"elegoo/{sn}/api_status",  # Status updates
            f"elegoo/{sn}/{request_id}/register_response",  # Registration response
        ]

        self.logger.debug(
            "Subscribing to %d CC2 MQTT topics for client_id=%s",
            len(topics),
            client_id,
        )

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
            self.logger.error("Cannot register: MQTT client not initialized")
            return False

        sn = self.serial_number
        topic = f"elegoo/{sn}/api_register"
        payload = {
            "client_id": self._client_id,
            "request_id": self._request_id,
        }

        self.logger.info(
            "Starting CC2 registration: client_id=%s, request_id=%s, topic=%s",
            self._client_id,
            self._request_id,
            topic,
        )
        self.logger.debug("Registration payload: %s", json.dumps(payload))

        # Create event for registration response
        reg_event = asyncio.Event()
        self._registration_event = reg_event
        self._registration_result: dict[str, Any] | None = None

        try:
            await self.mqtt_client.publish(topic, json.dumps(payload))
            self.logger.debug(
                "Published registration request to topic %s, waiting for response...",
                topic,
            )

            # Wait for registration response
            try:
                await asyncio.wait_for(
                    reg_event.wait(), timeout=CC2_REGISTRATION_TIMEOUT
                )
                self.logger.debug("Registration response received within timeout")
            except asyncio.TimeoutError:
                self.logger.warning(
                    (
                        "Registration timeout after %ds - no response from printer. "
                        "Expected response on topic: elegoo/%s/%s/register_response. "
                        "This may indicate: (1) Incorrect access code, "
                        "(2) Printer firmware issue, (3) Client ID format rejected, "
                        "(4) Network blocking MQTT messages. "
                        "Check printer settings: Settings → Network → Access Code"
                    ),
                    CC2_REGISTRATION_TIMEOUT,
                    sn,
                    self._request_id,
                )
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
                    self._is_registered = False
                    # Schedule proper cleanup in background
                    task = asyncio.create_task(self.disconnect())
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
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
                self._is_registered = False
                task = asyncio.create_task(self.disconnect())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
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

    async def _delayed_disconnect(self) -> None:
        """
        Wait before finalizing disconnect to allow quick reconnects.

        If reconnect succeeds during the delay, the cleanup is skipped.
        This prevents losing transient state (e.g., COMPLETE->IDLE transitions)
        during brief network interruptions.
        """
        try:
            await asyncio.sleep(CC2_DISCONNECT_DELAY)
        except asyncio.CancelledError:
            # Reconnect succeeded during delay — task was cancelled
            return
        # Re-check after sleep (handles cancel arriving after sleep resumes)
        if self._is_connected and self._is_registered:
            self.logger.debug(
                "CC2 reconnect succeeded during disconnect delay, skipping cleanup"
            )
            return
        # Finalize disconnect cleanup
        self._print_status_transition_queue.clear()
        self.logger.debug("CC2 disconnect delay expired, cleanup complete")

    def _on_disconnect_delay_done(self, task: asyncio.Task) -> None:
        """Handle disconnect delay task completion."""
        self._background_tasks.discard(task)
        # Only clear if this is still the current task (avoid dropping newer tasks)
        if self._disconnect_delay_task is task:
            self._disconnect_delay_task = None

    async def _mqtt_listener(self) -> None:
        """Listen for messages on MQTT and handle them."""
        if not self.mqtt_client:
            return

        # Capture generation for stale callback detection (local var, not shared)
        listener_generation = self._connection_generation

        try:
            async for message in self.mqtt_client.messages:
                # Stale callback guard: if disconnect() incremented the generation,
                # stop processing messages from the old connection.
                if self._connection_generation != listener_generation:
                    self.logger.debug("CC2 MQTT listener: generation changed, stopping")
                    break

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
            # Only tear down state for the same generation (stale listener check)
            if self._connection_generation == listener_generation:
                self._is_connected = False
                if self._disconnect_delay_task is None:
                    self._disconnect_delay_task = asyncio.create_task(
                        self._delayed_disconnect()
                    )
                    self._background_tasks.add(self._disconnect_delay_task)
                    self._disconnect_delay_task.add_done_callback(
                        self._on_disconnect_delay_done
                    )
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
            self.logger.info(
                "Received registration response on topic %s: %s", topic, data
            )
            self._registration_result = data
            if self._registration_event:
                self._registration_event.set()
                self.logger.debug("Registration event signaled")
            else:
                self.logger.warning(
                    "Received registration response but no event listener waiting"
                )
            return

        # Handle command responses
        if "api_response" in topic:
            await self._handle_response(data)
            return

        # Handle status updates
        if "api_status" in topic:
            await self._handle_status_event(data)
            return

    async def _handle_response(self, data: CC2Envelope) -> None:
        """Handle a command response message."""
        request_id = data.get("id")
        method = data.get("method")

        self.logger.debug("Received response: method=%s, id=%s", method, request_id)

        # Store response data only if a waiter exists
        if request_id is not None:
            async with self._response_lock:
                if event := self._response_events.get(request_id):
                    self._response_data[request_id] = data
                    event.set()

        # Process specific response types
        result = data.get("result", {})

        if method == CC2_CMD_GET_STATUS:
            self._handle_full_status(result)
        elif method == CC2_CMD_GET_ATTRIBUTES:
            self._handle_attributes(result)
        elif method == CC2_CMD_SET_VIDEO_STREAM:
            self._handle_video_response(result)
        elif method == CC2_CMD_GET_CANVAS_STATUS:
            self._handle_canvas_status(result)
        elif method == CC2_CMD_GET_FILE_LIST:
            self._handle_file_list(result)

    async def _handle_status_event(self, data: CC2Envelope) -> None:
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

    def _handle_full_status(self, status_data: CC2StatusFrame | dict[str, Any]) -> None:
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

    def _cc2_status_view(self) -> dict[str, Any]:
        """Printer cache plus integration-only keys for status mappers."""
        if not self._integration_data:
            return self._cached_status
        return self._cached_status | self._integration_data

    def _update_printer_status(self) -> None:
        """
        Update printer_data.status from cached status.

        Concurrency note: all callers run on the same asyncio event loop and
        never ``await`` between mutating ``_cached_status`` /
        ``_integration_data`` and calling this method, so no lock is needed.
        Keep this method synchronous and do not insert awaits in callers
        before this call to preserve that guarantee.
        """
        try:
            cc2_view = self._cc2_status_view()
            previous_print_status = self.printer_data.status.print_info.status
            # Map CC2 status format to PrinterStatus
            mapped_status = CC2StatusMapper.map_status(
                cc2_view, self.printer.printer_type
            )
            new_print_status = mapped_status.print_info.status
            if new_print_status != previous_print_status:
                self._print_status_transition_queue.append(deepcopy(mapped_status))
            self.printer_data.status = mapped_status
            self.logger.debug(
                "Updated printer status: %s",
                self.printer_data.status.current_status,
            )

            # Map filament data from cached file details
            current_filename = self._cached_status.get("print_status", {}).get(
                "filename"
            )
            self.printer_data.gcode_filament_data = CC2StatusMapper.map_filament_data(
                cc2_view, current_filename
            )

            # Update current job for begin_time/end_time sensors
            self._update_current_job()
        except Exception:
            self.logger.exception("Failed to map CC2 status to PrinterStatus")

    def _clear_stale_caches(self, filename: str, file_info: dict[str, Any]) -> None:
        """Drop proxy, MQTT file-detail, and thumbnail caches for a re-used filename."""
        for key in (
            "proxy_filament",
            "proxy_filament_status",
            "total_filament_used",
            "color_map",
            "print_time",
            "TotalLayers",
        ):
            file_info.pop(key, None)
        file_thumbnails = self._integration_data.get("_file_thumbnails", {})
        file_thumbnails.pop(filename, None)

    def _update_current_job(self) -> None:
        """Update current job from print status data."""
        print_status = self._cached_status.get("print_status", {})
        task_id = print_status.get("uuid")
        filename = print_status.get("filename")

        if not task_id or not filename:
            # No active print task
            return

        # Get total_layer from print_status or cached file details
        total_layer = print_status.get("total_layer")
        file_details = self._integration_data.get("_file_details", {})
        file_info = file_details.get(filename, {})

        # Same filename can be re-uploaded with new content; drop cached details
        # for this file so proxy + MQTT file-detail enrichment refetch for the
        # new print (task_id is unique per job).
        is_new_task = self.printer_data.print_history.get(task_id) is None
        if is_new_task:
            self._clear_stale_caches(filename, file_info)

        # Check enrichment data (TotalLayers, total_filament_used,
        # color_map, print_time)
        if total_layer is None:
            # Check if file_info has any enrichment markers (not just any cached data)
            has_enrichment = (
                file_info.get("TotalLayers")
                or file_info.get("total_filament_used")
                or file_info.get("color_map")
                or file_info.get("print_time")
            )
            if not has_enrichment and not hasattr(self, "_pending_file_detail_request"):
                self._pending_file_detail_request = filename
                self._request_file_detail_background(filename)

        # Fetch proxy filament data independently of total_layer / file details
        if self._gcode_proxy:
            proxy_filament_status = file_info.get("proxy_filament_status", "none")
            needs_proxy_update = (
                (
                    proxy_filament_status == "none"
                    and not hasattr(self, "_pending_proxy_request")
                )
                or proxy_filament_status == "retry"
                or not file_info.get("proxy_filament")
            )
            if needs_proxy_update:
                self._pending_proxy_request = filename
                self._request_proxy_filament_background(filename)
            if (
                file_info.get("proxy_filament")
                and hasattr(self, "_pending_proxy_request")
                and self._pending_proxy_request == filename
            ):
                del self._pending_proxy_request

        # Get cached thumbnail for this file
        file_thumbnails = self._integration_data.get("_file_thumbnails", {})
        thumbnail = file_thumbnails.get(filename)
        if not thumbnail and not hasattr(self, "_pending_thumbnail_request"):
            # Request thumbnail in background (only once per filename)
            self._pending_thumbnail_request = filename
            self._request_file_thumbnail_background(filename)

        # Get or create PrintHistoryDetail for current task
        current_job = self.printer_data.print_history.get(task_id)
        if current_job is None:
            # Create new PrintHistoryDetail for this task
            # Calculate begin_time from print_duration
            print_duration = print_status.get("print_duration")
            begin_time_ts = (
                int(time.time() - print_duration)
                if print_duration is not None
                else None
            )
            total_duration = print_status.get("total_duration")

            task_data = {
                "TaskId": task_id,
                "TaskName": filename,
                "BeginTime": begin_time_ts,
                "Thumbnail": thumbnail,
                "SliceInformation": {
                    "total_layer_numbers": total_layer,
                    "print_time": total_duration,
                },
            }
            current_job = PrintHistoryDetail(task_data)
            self.printer_data.print_history[task_id] = current_job
            self.logger.debug(
                "Created current job: task_id=%s, begin_time=%s, total_layers=%s",
                task_id,
                current_job.begin_time,
                total_layer,
            )
        else:
            slice_info = current_job.slice_information
            if total_layer and slice_info.total_layer_numbers is None:
                # Update existing job with total_layer if we got it
                slice_info.total_layer_numbers = total_layer
                self.logger.debug("Updated current job total_layers=%s", total_layer)
            if thumbnail and current_job.thumbnail != thumbnail:
                # Update existing job with thumbnail if we got it
                current_job.thumbnail = thumbnail
                self.logger.debug("Updated current job thumbnail")

    def _request_file_detail_background(self, filename: str) -> None:
        """Request file details in the background."""
        task = asyncio.create_task(self._request_file_detail(filename))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _request_proxy_filament_background(self, filename: str) -> None:
        """
        Fetch per-extruder filament data from the gcode proxy in the background.

        Args:
            filename: The G-code filename to fetch proxy filament data for.

        """
        task = asyncio.create_task(self._request_proxy_filament(filename))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _request_proxy_filament(self, filename: str) -> None:
        """
        Query the gcode capture proxy for filament metadata.

        Args:
            filename: The G-code filename to fetch proxy filament data for.

        """
        if not self._gcode_proxy:
            return
        try:
            data = await self._gcode_proxy.fetch_filament_data(filename)
            if data:
                if "_file_details" not in self._integration_data:
                    self._integration_data["_file_details"] = {}
                file_info = self._integration_data["_file_details"].setdefault(
                    filename, {}
                )
                file_info["proxy_filament"] = data
                file_info["proxy_filament_status"] = "success"
                self.logger.debug("Proxy filament data cached for %s", filename)
                self._update_printer_status()
        except (TimeoutError, OSError) as exc:
            self.logger.debug(
                "Failed to fetch proxy filament data for %s: %s",
                filename,
                exc,
            )
            # Only set retry status if we have _file_details initialized
            if "_file_details" in self._integration_data:
                file_info = self._integration_data["_file_details"].setdefault(
                    filename, {}
                )
                file_info.setdefault("proxy_filament_status", "retry")
        finally:
            # Clear pending request flag only if we're not retrying
            if (
                hasattr(self, "_pending_proxy_request")
                and self._pending_proxy_request == filename
            ):
                del self._pending_proxy_request

    async def _request_file_detail(self, filename: str) -> None:
        """Request file details from printer."""
        try:
            # Determine storage_media (usually "local" for internal storage)
            result = await self._send_command(
                CC2_CMD_GET_FILE_DETAIL,
                {"storage_media": "local", "filename": filename},
            )
            if result:
                # _send_command returns the full message; extract inner result
                inner = result.get("result", result)
                self._handle_file_detail_response(filename, inner)
        except PRINT_TRANSPORT_ERRORS:
            self.logger.debug("Failed to get file details for %s", filename)
        finally:
            # Clear pending request flag after handling response
            if (
                hasattr(self, "_pending_file_detail_request")
                and self._pending_file_detail_request == filename
            ):
                del self._pending_file_detail_request

    def _handle_file_detail_response(
        self, filename: str, result: dict[str, Any]
    ) -> None:
        """Handle file detail response and cache TotalLayers + filament data."""
        if "_file_details" not in self._integration_data:
            self._integration_data["_file_details"] = {}

        total_layers = (
            result.get("TotalLayers")
            or result.get("layer")
            or result.get("total_layer")
        )

        total_filament_used = result.get("total_filament_used")
        color_map = result.get("color_map")
        print_time = result.get("print_time")

        # Check for enrichment markers (the actual 1046-enrichment fields)
        has_enrichment_markers = (
            total_layers is not None
            or total_filament_used is not None
            or color_map
            or print_time is not None
        )

        if has_enrichment_markers:
            detail = self._integration_data["_file_details"].get(filename, {})
            if total_layers is not None:
                detail["TotalLayers"] = total_layers
            if total_filament_used is not None:
                detail["total_filament_used"] = total_filament_used
            if color_map:
                detail["color_map"] = color_map
            if print_time is not None:
                detail["print_time"] = print_time

            self._integration_data["_file_details"][filename] = detail
            self.logger.debug(
                "Cached file details for %s: TotalLayers=%s, "
                "total_filament_used=%s, color_map_entries=%s",
                filename,
                total_layers,
                total_filament_used,
                len(color_map) if color_map else 0,
            )
            self._update_printer_status()
        else:
            self.logger.debug(
                "File detail response for %s had no enrichment markers. Keys: %s",
                filename,
                list(result.keys()),
            )

    def _request_file_thumbnail_background(self, filename: str) -> None:
        """Request file thumbnail in the background."""
        task = asyncio.create_task(self._request_file_thumbnail(filename))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _request_file_thumbnail(self, filename: str) -> None:
        """Request file thumbnail from printer."""
        try:
            result = await self._send_command(
                CC2_CMD_GET_FILE_THUMBNAIL,
                {"storage_media": "local", "file_name": filename},
            )
            if result:
                inner = result.get("result", result)
                self._handle_file_thumbnail_response(filename, inner)
        except PRINT_TRANSPORT_ERRORS:
            self.logger.debug("Failed to get file thumbnail for %s", filename)
        finally:
            if hasattr(self, "_pending_thumbnail_request"):
                del self._pending_thumbnail_request

    def _handle_file_thumbnail_response(
        self,
        filename: str,
        result: CC2FileThumbnailResponse | dict[str, Any],
    ) -> None:
        """Handle file thumbnail response and cache thumbnail data."""
        if "_file_thumbnails" not in self._integration_data:
            self._integration_data["_file_thumbnails"] = {}

        thumbnail = result.get("thumbnail")
        if thumbnail:
            # Store as data URI for use by the image entity
            if not thumbnail.startswith("data:"):
                thumbnail = f"data:image/png;base64,{thumbnail}"
            self._integration_data["_file_thumbnails"][filename] = thumbnail
            self.logger.debug("Cached file thumbnail for %s", filename)
            # Update printer status so the thumbnail propagates to the job
            self._update_printer_status()
        else:
            self.logger.debug(
                "File thumbnail response for %s had no thumbnail. Keys: %s",
                filename,
                list(result.keys()),
            )

    def _request_full_status_background(self) -> None:
        """Request full status in the background."""
        task = asyncio.create_task(self._request_full_status())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _request_full_status(self) -> None:
        """Request full status from printer."""
        try:
            await self._send_command(CC2_CMD_GET_STATUS)
        except PRINT_TRANSPORT_ERRORS:
            self.logger.warning("Failed to request full status")

    def _handle_attributes(self, attrs_data: CC2Attributes | dict[str, Any]) -> None:
        """Handle attributes response."""
        self.logger.debug("Received attributes update")
        try:
            # Map CC2 attributes to PrinterAttributes
            mapped_attrs = CC2StatusMapper.map_attributes(attrs_data)
            self.printer_data.attributes = mapped_attrs
            if self.printer:
                self.printer.sync_from_attributes(mapped_attrs)
        except Exception:
            self.logger.exception("Failed to map CC2 attributes")

    def _handle_video_response(
        self, video_data: CC2VideoResponse | dict[str, Any]
    ) -> None:
        """Handle video stream response."""
        error_code = video_data.get("error_code", 0)

        # CC2 may return video_url directly or just success
        # Construct URL for the MJPEG stream if successful
        video_url = video_data.get("video_url", "")
        if error_code == 0 and (self.printer.proxy_host or not video_url):
            # Proxy mode: always build from the proxy host and ignore the
            # printer-supplied URL (it embeds the printer's real IP, which a
            # transparent tunnel does not expose to Home Assistant).
            # No proxy: keep the existing fallback for a success without a URL.
            # Shares its port and path with the camera entity's initial URL, so
            # the two cannot drift apart.
            video_url = f"http://{self.printer_ip}:{CC2_VIDEO_PORT}{CC2_VIDEO_PATH}"

        # Convert to format ElegooVideo expects
        converted_data = {
            "Ack": 0 if error_code == 0 else error_code,
            "VideoUrl": video_url,
        }
        self.printer_data.video = ElegooVideo(converted_data)

    def _handle_canvas_status(self, result: CC2CanvasStatus | dict[str, Any]) -> None:
        """
        Process Canvas status response and update printer_data.

        Parses the AMS data structure and creates AMSStatus object.
        """
        # Log raw response for debugging
        self.logger.debug("Raw Canvas response: %s", result)

        try:
            # Extract canvas_info from the result
            canvas_info = result.get("canvas_info", {})
            ams_status = AMSStatus(canvas_info)
            self.printer_data.ams_status = ams_status
            self.logger.debug("Canvas status updated: %s", ams_status)
        except (KeyError, ValueError, TypeError):
            self.logger.exception("Failed to parse Canvas status")

    def _handle_file_list(self, result: CC2FileList | dict[str, Any]) -> None:
        """
        Replace ``printer_data.file_list`` with the entries of a 1044 result.

        Lenient and silent by design: a reply that is not a dict carrying
        ``file_list`` — an error envelope, a rejected-parameter reply, firmware
        that answers 1044 with a list or a bare string — leaves the cache
        UNTOUCHED and this returns normally. Two reasons:

        * ``result.get(...)`` on a list/str/int raises ``AttributeError``, which
          is not a transport error and would escape the MQTT listener and stop
          it (see ``_mqtt_listener``'s narrow excepts);
        * a reply nobody has captured is not evidence that the printer has no
          files. Overwriting a listing we could read with ``{}`` would empty the
          Print File select and, worse, hand an "empty inventory" to anything
          deciding whether a file still exists.

        It reports nothing back on purpose. All three call sites run on paths
        that cannot act on the difference — the MQTT listener (an unsolicited
        1044 from another client on the broker) and the lenient
        ``get_file_list``, whose callers only display or enumerate and must not
        start raising at the coordinator, which catches only transport errors —
        and a boolean they all ignored read as a contract someone was failing
        to honour. The caller that CAN act on it does not ask here: it judges
        the reply itself, before calling this, in ``get_verified_file_list``.
        """
        if not isinstance(result, dict) or "file_list" not in result:
            self.logger.warning(
                "Ignoring a 1044 reply that is not a file listing (%s); "
                "keeping the previous listing",
                type(result).__name__,
            )
            return
        files = {}
        for entry in result.get("file_list") or []:
            if not isinstance(entry, dict) or entry.get("type", "file") != "file":
                continue
            try:
                file = PrinterFile(entry)
            except (ValueError, TypeError, OverflowError, OSError):
                # one malformed entry must not cost the rest of the listing
                self.logger.debug("Skipping unparseable file entry: %s", entry)
                continue
            if file.name:
                files[file.name] = file
        self.printer_data.file_list = files
        self.logger.debug("File list updated: %d files", len(files))

    async def get_file_list(self) -> dict[str, PrinterFile]:
        """
        Fetch the files in the printer's local storage (method 1044).

        Sends the parameters ElegooSlicer sends; no paging, the printer
        answered with all 54 files at once (fw 02.01.00.00). The result is
        kept in ``printer_data.file_list`` and returned.

        Lenient by design, because its callers only display or enumerate: a
        reply that cannot be read as a listing keeps the previous listing (see
        ``_handle_file_list``), so the return value can be a stale listing
        rather than a fresh one and the caller cannot tell. It also does not
        look at ``error_code``, so a refusal that arrives with a ``file_list``
        attached replaces the cache here. Anything deciding whether a file
        STILL EXISTS — the ``cleanup_files`` delete confirmation — must use
        ``get_verified_file_list`` instead, which checks both.

        Raises:
            ElegooPrinterConnectionError: if the printer did not answer.

        """
        response = await self._send_command(
            CC2_CMD_GET_FILE_LIST, {"storage_media": "local", "path": "/"}
        )
        if not response:
            msg = "Printer did not answer the file list request"
            raise ElegooPrinterConnectionError(msg)
        # Deliberately unchecked: _handle_file_list keeps the old listing for a
        # reply it cannot read and this path has no way to report the
        # difference, which is exactly why it is not the delete confirmation.
        self._handle_file_list(response.get("result", response))
        return self.printer_data.file_list

    async def get_verified_file_list(self) -> dict[str, PrinterFile]:
        """
        Fetch the file list (1044) only if the printer both answered and did not refuse.

        Same request as ``get_file_list``, but the answer is judged instead of
        assumed. A reply is usable only when BOTH hold:

        1. it is not a refusal — ``result`` carries no ``error_code``, or one
           that reads as the number 0; and
        2. it is a listing — ``result`` is a dict containing ``file_list``.

        The refusal is checked FIRST, and that order is the point. The shared
        response envelope documents ``result`` as "Response data including
        ``error_code``" (docs/CC2_PROTOCOL.md), so a refusal is reported INSIDE
        ``result``, and nothing documents that a refusal omits ``file_list``: a
        reply of the shape ``{"error_code": 1002, "file_list": []}`` passes the
        shape test alone and hands back an empty inventory as authoritative.
        A caller re-listing to confirm a delete then reports "deleted N of N"
        for files the printer just refused to touch, and nothing retries.
        Checking the shape first also cannot be rescued by "an error envelope
        has no file_list anyway" — that assumption is exactly what is not
        documented.

        Anything else raises, because a caller re-listing to confirm a delete
        has to be able to tell "the printer has no files" from "the printer
        said something I cannot read" — an error envelope, a rejected-parameter
        reply or an undocumented shape all parse to an empty listing through
        the lenient path, and an empty listing read as authoritative reports
        every accepted delete as a success while the files sit exactly where
        they were.

        A missing ``error_code`` is accepted, deliberately, and the assumption
        is stated because the protocol does not settle it: every reply this
        repo has captured carries it — 1001, 1044, 1045, 1046, 1048 and 2005
        each in their own sample, and the full status frame that
        docs/CC2_PROTOCOL.md describes as "returned by method 1002 or event
        6000" likewise, though it is only ever shown inside a 6000 envelope. So
        no CAPTURED reply omits it, but the envelope table only says ``result``
        "includ[es]" it and no method documents it as REQUIRED (1047 documents
        no response body at all). Hence absence reads as "said nothing", not as
        a refusal: a firmware that answers 1044 with a bare
        ``{"file_list": [...]}`` is a listing this client can read, and
        refusing one would make the service unable to clean up a printer it can
        otherwise read, forever. The guess is bounded by the shape rule below it
        — absence is only ever accepted when a real ``file_list`` is present, so
        this can never turn a shapeless reply into an empty inventory.

        An empty ``file_list`` with no refusal stays usable, so an empty
        printer remains cleanable rather than being told forever that its
        inventory cannot be read.

        The cache is only replaced by a listing that passed, so a failed
        verification cannot empty the Print File select either.

        Raises:
            ElegooPrinterConnectionError: if the printer did not answer.
            CC2UnusableFileListError: if it refused, or answered something that
                is not a file listing.

        """
        response = await self._send_command(
            CC2_CMD_GET_FILE_LIST, {"storage_media": "local", "path": "/"}
        )
        if not response:
            msg = "Printer did not answer the file list request"
            raise ElegooPrinterConnectionError(msg)
        # No ``or response`` fallback: an envelope with no ``result`` key is not
        # a listing either, and the envelope's own keys (id/method) prove it.
        result = response.get("result")
        if not isinstance(result, dict):
            msg = (
                "The printer's file-list reply is not a file listing "
                f"(result: {type(result).__name__})"
            )
            raise CC2UnusableFileListError(msg)
        # Refusal first, then shape — see the docstring: a refusal can arrive
        # with a ``file_list`` attached, and that listing proves nothing.
        # Absent key -> None -> "the printer said nothing", which is not a
        # refusal. An unreadable code (``_error_code`` -> None: "abc", True,
        # 0.5, a list) is a refusal, never a 0 invented by a default.
        if "error_code" in result:
            code = _error_code(result["error_code"])
            if code is None or code != 0:
                msg = (
                    "The printer refused or garbled the file-list request "
                    f"(error_code: {result['error_code']!r}"
                    f"{'' if code is None else f', parsed {code}'}); "
                    "refusing to treat its reply as a file inventory"
                )
                raise CC2UnusableFileListError(msg)
        if "file_list" not in result:
            msg = (
                "The printer's file-list reply is not a file listing "
                f"(result keys: {sorted(result)})"
            )
            raise CC2UnusableFileListError(msg)
        # Past the gate, this reply is a dict carrying ``file_list`` — the one
        # shape _handle_file_list accepts — so its leniency cannot undo the
        # verification and there is nothing for the caller to check here.
        self._handle_file_list(result)
        return self.printer_data.file_list

    async def refresh_status(self) -> None:
        """
        Ask the printer for its full status now (method 1002) and await it.

        ``printer_data.status`` otherwise reflects the last push, which can
        predate the current call by up to the coordinator interval — enough for
        a job to have started in between. A caller that must know what the
        printer is doing BEFORE it destroys something uses this to make the
        answer current; ``_request_full_status`` (fire-and-forget, for the
        reconnect path) is not enough, and neither is the delta stream, which
        only ever reaches this client through the listener.

        The status is readable when this returns, and that is not obvious: the
        response waiter is signalled in ``_handle_response`` BEFORE
        ``_handle_full_status`` runs, so this coroutine is woken a step ahead of
        the status being rebuilt. What saves it is that nothing between the two
        awaits — the handler cannot be interrupted there, so by the time the
        woken caller is actually scheduled the mapping has happened. Keep it
        that way: insert an ``await`` between signalling the waiter and mapping
        the status and the woken caller runs during that await, reading the
        PREVIOUS status and believing it is the new one. That is a silent
        failure, not an error — and on the destructive path it means the file
        being printed is compared against a status from before the job started.

        Pinned by ``test_a_woken_waiter_sees_the_status_mapped_by_the_same_reply``
        in ``cc2/tests/test_delete_file.py``, which parks a waiter on the real
        event and fails it against the previous status if the seam ever gains an
        await.

        Raises:
            ElegooPrinterConnectionError: if the printer did not answer.

        """
        await self._send_command(CC2_CMD_GET_STATUS)

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
                        await asyncio.wait_for(
                            event.wait(), timeout=CC2_COMMAND_TIMEOUT
                        )
                        async with self._response_lock:
                            return self._response_data.get(request_id)
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

    # Public API methods (matching ElegooMQTTClient interface)

    async def get_printer_status(self) -> PrinterData:
        """Return the current printer status."""
        return self.printer_data

    async def get_printer_attributes(self) -> PrinterData:
        """Return the printer attributes."""
        return self.printer_data

    async def set_printer_video_stream(self, *, enable: bool) -> None:
        """Enable or disable the printer's video stream."""
        # CC2 expects integer 1/0 not boolean true/false
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
        # Send both brightness and power for firmware compatibility.
        # Some firmware expect "power" (0/1), others "brightness" (0-255).
        # OctoEverywhere sends both — follow that pattern.
        # Assumes binary on/off only; dimming needs a separate field.
        power = 1 if light_status.second_light else 0
        await self._send_command(
            CC2_CMD_SET_LIGHT,
            {
                "brightness": 255 if power else 0,
                "power": power,
            },
        )

    async def print_pause(self) -> None:
        """Pause the current print."""
        await self._send_command(CC2_CMD_PAUSE_PRINT)

    async def print_stop(self) -> None:
        """Stop the current print."""
        await self._send_command(CC2_CMD_STOP_PRINT)

    async def print_resume(self) -> None:
        """
        Resume/continue the current print.

        Fire-and-forget: the printer does not acknowledge 1023 until the
        resume has actually completed — it reheats, homes and returns to
        position first. Measured on a Centauri Carbon 2 (firmware
        02.01.00.00): 122 s between the request and the ``error_code: 0``
        response. Waiting for that reply would always exceed
        CC2_COMMAND_TIMEOUT (10 s) and raise ElegooPrinterTimeoutError,
        even though the command was accepted and the print is resuming.
        """
        await self._send_command(CC2_CMD_RESUME_PRINT, wait_for_response=False)

    @property
    def upload_lock(self) -> asyncio.Lock:
        """
        Serialise uploads to this printer.

        An upload is assembled from several ranged PUTs and the protocol has no
        session id, so two concurrent uploads would interleave their ranges into
        one file. Callers hold this across the whole upload, including an
        optional start, so a print cannot begin from a half-written file.
        """
        return self._upload_lock

    async def upload_gcode(
        self,
        session: aiohttp.ClientSession,
        file: UploadFile,
        chunks: AsyncIterator[bytes],
    ) -> int:
        """
        Upload a G-code file to the printer's local storage over HTTP.

        The access code that connected the MQTT session doubles as the HTTP
        token (the SDK sends the access code, or "123456" when none is set).
        Returns the number of bytes sent; raises ElegooPrinterConnectionError
        on failure. See cc2/upload.py for the measured constraints.
        """
        target = _upload.UploadTarget(
            host=self.printer_ip, token=self.access_code or CC2_MQTT_DEFAULT_PASSWORD
        )
        return await _upload.upload_gcode(session, target, file, chunks)

    async def print_start(
        self,
        filename: str,
        *,
        tray_id: int | None = None,
        bed_leveling: bool = True,
    ) -> int:
        """
        Start printing a file that is already in the printer's local storage.

        Sends method 1020 with a ``config`` that carries only what is needed:
        ``slot_map`` maps every G-code tool the file uses to ``tray_id`` when
        one is given (an empty list lets the printer pick the tray), and
        ``printer_check`` carries ``bed_leveling``. The other ``config`` fields
        the slicer sends are not required.

        The tool list comes from the file's ``color_map`` in the 1044 listing
        (``PrinterFile.tools``), which the file-list poll already fills; when
        no file detail is available the map falls back to a single ``t`` 0
        entry, which is byte-identical to what a single-tool print sent
        before. ``canvas_id`` stays 0: UNVERIFIED with more than one Canvas
        unit, and a single-Canvas printer has only unit 0.

        The key is sent in both directions, never omitted, because the
        firmware REMEMBERS THE LAST VALUE IT WAS GIVEN. Measured on a Centauri
        Carbon 2 (02.01.00.00), same file and a 60 C bed throughout: with
        ``true`` it levels (sub-status 2901 for ~200 s, first layer at 398 s
        against 180 s), and a following job that omits the key levels again -
        it inherits that ``true``. An omitted key therefore does not mean
        "off", it means "carry on as before", and "before" may be someone
        else's job, such as one started from the slicer with its bed-leveling
        box ticked. Only an explicit ``false`` turns it off.

        Measured on a Centauri Carbon 2 (firmware 02.01.00.00): a ``slot_map``
        entry must carry ``t``, ``canvas_id`` and ``tray_id`` together. A
        partial entry, or an out-of-range ``tray_id``, is acknowledged with
        ``error_code`` 0 and the printer silently prints from tray 0 - so the
        response cannot be used to validate the mapping. ``color_map[].t`` is
        the G-code tool index, not a tray. An empty ``slot_map`` leaves the
        choice to the printer.

        NOT measured, and this method must not be read as having proven it:
        that a COMPLETE map - an entry for every tool the file uses - makes the
        printer honour the tray. That is the protocol doc's own wording ("maps
        each tool index") plus its two-entry example, but the example comes
        from the elegoo-link SDK's serialisation, not a packet capture, and the
        slicer's own captured Start Print sends an empty ``slot_map``. What the
        printer does with a tool index absent from ``slot_map`` is undocumented;
        "it falls back to tray 0" is the natural reading of a user report, not a
        recorded behaviour. Whether a complete map is required for any entry to
        be honoured, or merely sufficient, is likewise unknown. Issue #435 stays
        open pending a hardware run.

        Returns:
            The ``error_code`` from the printer's response
            (0 = started, 1009 = printer busy).

        Raises:
            ElegooPrinterConnectionError: if the printer did not acknowledge
                the command - ``_send_command`` returns ``None`` when the
                client disconnects while waiting, and a response without a
                result or error code is treated the same way, so a print is
                never reported as started without the printer saying so.

        """
        slot_map: list[dict[str, int]] = []
        if tray_id is not None:
            file = self.printer_data.file_list.get(filename)
            tools = file.tools if file is not None else []
            slot_map = [
                {"t": tool, "canvas_id": 0, "tray_id": tray_id}
                for tool in (tools or [0])
            ]
        config: dict[str, Any] = {
            "slot_map": slot_map,
            "printer_check": bed_leveling,
        }
        response = await self._send_command(
            CC2_CMD_START_PRINT,
            {"storage_media": "local", "filename": filename, "config": config},
        )
        result = (response or {}).get("result")
        if not isinstance(result, dict) or "error_code" not in result:
            msg = "No acknowledgement from the printer for start print"
            raise ElegooPrinterConnectionError(msg)
        return int(result["error_code"])

    async def delete_file(
        self,
        filename: str,
        *,
        storage_media: str = "local",
    ) -> int:
        """
        Delete one file from the printer's storage (method 1047).

        UNTESTED ON HARDWARE. The protocol doc marks 1047 "Untested" and
        documents the request only: unlike 1044, 1046 and 1048 it shows no
        response body at all, and nothing in this repo has ever received a 1047
        reply. Reading the answer as ``result.error_code`` is therefore an
        inference from the envelope every other file method uses — the listed
        ``1007 FILE_DELETE_FAILED`` is the only hint — not a measurement.

        So a ``0`` here means "the printer did not refuse", nothing more. It is
        NOT proof of deletion, and the caller decides whether the file is gone
        by re-listing (method 1044); that is what the ``cleanup_files`` service
        does rather than trusting this return value.

        A non-zero code is returned rather than raised: one locked or busy file
        must not abort a cleanup of forty, and the reason is reported per file.
        An ``error_code`` that is not a number is reported as
        ``CC2_ERROR_REPLY_UNPARSEABLE`` for the same reason — it is an answer
        that cannot be read as consent, so it must not be a code the caller has
        to parse, and it must not raise: ``int("abc")`` escaping here would
        unwind a cleanup AFTER earlier deletes had already landed, throwing away
        the report of what happened to them.

        Args:
            filename: the name exactly as the 1044 listing reports it.
            storage_media: defaults to the printer's internal storage; no
                caller in this integration offers another value, because 1044
                is only ever documented with ``path: "/"`` on ``local``.

        Returns:
            The ``error_code`` from the printer's reply, if it sent one, or
            ``CC2_ERROR_REPLY_UNPARSEABLE`` if it sent one that is not a number.

        Raises:
            ElegooPrinterConnectionError: if the printer sent no reply, or a
                reply with no error code — including firmware that does not
                implement 1047 and stays silent, which surfaces from
                ``_send_command`` as a timeout after 10 s.

        """
        response = await self._send_command(
            CC2_CMD_DELETE_FILE,
            {"storage_media": storage_media, "filename": filename},
        )
        result = (response or {}).get("result")
        if not isinstance(result, dict) or "error_code" not in result:
            msg = f"No acknowledgement from the printer for delete file {filename}"
            raise ElegooPrinterConnectionError(msg)
        code = _error_code(result["error_code"])
        if code is None:
            # "abc"/None/[]/0.5: an answer that cannot be read as consent. It is
            # reported as a refusal rather than raised, because raising here
            # unwinds a cleanup loop that has already deleted files and throws
            # away the report of what happened to them.
            self.logger.warning(
                "Delete reply for %s carried an unreadable error_code %r; "
                "reporting it as refused",
                filename,
                result["error_code"],
            )
            return CC2_ERROR_REPLY_UNPARSEABLE
        return code

    async def set_fan_speed(self, percentage: int, fan: ElegooFan) -> None:
        """Set the speed of a fan."""
        # CC2 expects 0-255, convert from percentage
        pct = max(0, min(100, int(percentage)))
        speed_value = round(pct / 100 * 255)
        # Map fan names to CC2 format
        fan_map = {
            "ModelFan": "fan",
            "AuxiliaryFan": "aux_fan",
            "BoxFan": "box_fan",
        }
        fan_key = fan_map.get(fan.value, "fan")
        await self._send_command(CC2_CMD_SET_FAN_SPEED, {fan_key: speed_value})

    async def set_print_speed(self, percentage: int) -> None:
        """Set the print speed (CC2 uses modes: 0=50%, 1=100%, 2=150%, 3=200%)."""
        # Map percentage to closest speed mode
        # Mode thresholds: 0-75% -> Silent, 76-125% -> Balanced,
        # 126-175% -> Sport, 176%+ -> Ludicrous
        speed_mode_silent = 75
        speed_mode_balanced = 125
        speed_mode_sport = 175
        if percentage <= speed_mode_silent:
            mode = 0  # Silent (50%)
        elif percentage <= speed_mode_balanced:
            mode = 1  # Balanced (100%)
        elif percentage <= speed_mode_sport:
            mode = 2  # Sport (150%)
        else:
            mode = 3  # Ludicrous (200%)
        await self._send_command(CC2_CMD_SET_PRINT_SPEED, {"mode": mode})

    async def set_target_nozzle_temp(self, temperature: int) -> None:
        """Set the target nozzle temperature."""
        clamped_temp = max(0, min(320, int(temperature)))
        await self._send_command(CC2_CMD_SET_TEMPERATURE, {"extruder": clamped_temp})

    async def set_target_bed_temp(self, temperature: int) -> None:
        """Set the target bed temperature."""
        clamped_temp = max(0, min(110, int(temperature)))
        await self._send_command(CC2_CMD_SET_TEMPERATURE, {"heater_bed": clamped_temp})

    async def get_canvas_status(self) -> dict[str, Any] | None:
        """
        Get Canvas/AMS status including filament colors and active tray.

        Returns response with AMS connection status, box info, tray colors.
        """
        return await self._send_command(CC2_CMD_GET_CANVAS_STATUS)

    # Discovery method (for compatibility with existing code)
    def discover_printer(
        self, broadcast_address: str = "255.255.255.255"
    ) -> list[Printer]:
        """Discover CC2 printers (delegates to CC2Discovery)."""
        from .discovery import CC2Discovery  # noqa: PLC0415

        return CC2Discovery.discover_as_printers(broadcast_address)
