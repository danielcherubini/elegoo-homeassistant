"""
Adds config flow for Elegoo.

Copyright (c) Daniel Cherubini
MIT License
"""

from __future__ import annotations

import asyncio
import json
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NamedTuple

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cc2.const import CC2_CONFIG_FLOW_DISCOVERY_TIMEOUT, CC2_MQTT_PORT
from .cc2.discovery import CC2Discovery
from .cc2.gcode_proxy import GCodeProxyClient
from .const import (
    CONF_ALLOW_PRINTER_OFFLINE,
    CONF_CAMERA_ENABLED,
    CONF_CC2_ACCESS_CODE,
    CONF_EXTERNAL_IP,
    CONF_GCODE_PROXY_URL,
    CONF_HAS_CANVAS,
    CONF_MQTT_EXTERNAL_HOST,
    CONF_MQTT_EXTERNAL_PORT,
    CONF_PROXY_ENABLED,
    CONF_PROXY_HOST,
    CONF_SERIAL,
    CONFIG_VERSION_5,
    DOMAIN,
    LOGGER,
    WEBSOCKET_PORT,
)
from .sdcp.exceptions import (
    ElegooConfigFlowConnectionError,
    ElegooConfigFlowGeneralError,
)
from .sdcp.models.enums import PrinterType, ProtocolVersion, TransportType
from .sdcp.models.printer import Printer
from .websocket.client import ElegooPrinterClient
from .websocket.server import ElegooPrinterServer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.helpers.selector import SelectOptionDict


def _sanitize_ip_address(ip: str) -> str | None:
    """
    Strip URL prefixes, trailing slashes and whitespace from user-provided IP addresses.

    Arguments:
        ip: The raw user input string (may include http://, /, etc.)

    Returns:
        Clean IP address or None if empty/invalid after sanitization.

    """
    if not ip:
        return None

    # Remove common URL prefixes
    cleaned = ip.strip()
    for prefix in ("http://", "https://", "://", "//"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()

    # Remove trailing slash
    cleaned = cleaned.rstrip("/")

    return cleaned or None


def _normalize_gcode_proxy_base_url(raw: str) -> str | None:
    """
    Build a canonical http(s) base URL for the GCode capture proxy from user input.

    Strips repeated ``http://`` / ``https://`` / ``//`` prefixes so values like
    ``http://http://192.168.1.1`` are not stored. Defaults to ``http`` when no
    scheme is given. Returns ``None`` if the value is empty or malformed.

    Arguments:
        raw: User-entered host, host:port, or URL.

    Returns:
        Canonical ``http://...`` or ``https://...`` base URL, or ``None``.

    """
    cleaned = raw.strip()
    if not cleaned:
        return None

    scheme = "http"
    while cleaned:
        lower = cleaned.lower()
        if lower.startswith("https://"):
            scheme = "https"
            cleaned = cleaned[8:].strip()
            continue
        if lower.startswith("http://"):
            cleaned = cleaned[7:].strip()
            continue
        if lower.startswith("//"):
            cleaned = cleaned[2:].strip()
            continue
        break

    cleaned = cleaned.rstrip("/")
    if not cleaned:
        return None
    if "://" in cleaned:
        return None
    return f"{scheme}://{cleaned}"


# Total budget for CC1 Canvas detection (WS handshake + first status push).
_CANVAS_DETECT_TIMEOUT: float = 5

# Cap on how much of a device-controlled frame is ever written to the log.
# Length alone is not the reason for the cap; the reason is that the text comes
# from a device the user just pointed Home Assistant at, so it is bounded rather
# than trusted. See :func:`_frame_for_log`.
_MAX_LOGGED_FRAME_CHARS: int = 400

# Cap on a device-reported identifier written at INFO (name, model, mainboard id).
_MAX_LOGGED_ID_CHARS: int = 64

# Keys a printer may use to describe **itself**.
#
# ``Printer.from_dict`` reads routing configuration out of the same dictionary it
# reads identity from — ``transport_type``, ``protocol_version``, ``proxy_host``,
# ``mqtt_external_host``, ``mqtt_external_port``, ``cc2_access_code``,
# ``cc2_token_status``, ``proxy_enabled``, ``camera_enabled``, ``has_canvas``,
# ``external_ip`` and the two proxy ports are all read from the frame — and every
# other call site on this integration hands it a dictionary the integration
# itself wrote (``to_dict`` output, entry data, the manual-entry form). The live
# probe is the only place the dictionary comes from the wire, so there the frame
# is filtered through this set first.
#
# What survives is exactly what the UDP discovery constructor
# (``Printer(json_string)``) has always been willing to believe, minus ``Proxy``:
# the seven keys below are the printer's name, model, brand, address, mainboard
# id and two version strings. Everything else in the frame is dropped, so a
# hostile device can name itself but cannot say where Home Assistant connects
# next — the reason ``Proxy`` is left out even though discovery reads it is that
# the probe is only ever pointed at an address the user typed to reach a printer,
# and letting that device claim to be a proxy buys nothing while adding a key the
# identity concept does not need.
#
# ``MainboardIP`` is listed because ``from_dict`` wants it, not because it is
# believed: the probe overwrites ``ip_address`` with the typed address right
# after parsing. Transport is deliberately absent — with ``transport_type`` and
# ``protocol_version`` gone, ``from_dict`` falls back to deriving both from
# ``ProtocolVersion``, which is how discovery does it.
_PRINTER_IDENTITY_KEYS: frozenset[str] = frozenset(
    {
        "Name",
        "MachineName",
        "BrandName",
        "MainboardIP",
        "MainboardID",
        "ProtocolVersion",
        "FirmwareVersion",
    }
)


def _identity_only(attributes: dict[str, Any]) -> dict[str, Any]:
    """
    Reduce a device-sent attributes frame to the keys that describe identity.

    This is the trust boundary of the live printer probe in
    :meth:`ElegooFlowHandler._async_probe_websocket_printer`: the frame answers
    "who are you", never "where should Home Assistant connect". Filtering rather
    than sanitising per key is deliberate — the dangerous keys are the ones a
    future ``Printer.from_dict`` might grow as much as the ones it reads today,
    and an allowlist cannot be widened by accident.

    Arguments:
        attributes: The attributes dict taken out of a printer frame.

    Returns:
        A new dict holding only :data:`_PRINTER_IDENTITY_KEYS`, keys absent from
        the frame simply missing.

    """
    return {
        key: value for key, value in attributes.items() if key in _PRINTER_IDENTITY_KEYS
    }


def _as_str(value: Any) -> str:
    """
    Return ``value`` when it is a string, else ``""``.

    Lets a membership test on a device-controlled field stay total:
    ``"status" in frame.get("Topic", "")`` is a ``TypeError`` when the topic is
    ``7`` or ``None``, and the frame loops run on frames the printer chose.

    Arguments:
        value: Anything read out of a frame.

    Returns:
        The string itself, or an empty string for any other type.

    """
    return value if isinstance(value, str) else ""


def _dig(frame: Any, *keys: str) -> dict[str, Any]:
    """
    Walk nested dictionaries in a device frame without being able to raise.

    ``frame.get("Data", {}).get("Data", {}).get("Status", {})`` — the shape CC1
    status pushes use — is an ``AttributeError`` the moment any level is a list,
    a string or a number instead of an object, and these frames come from the
    network. A level that is not a dict ends the walk.

    Arguments:
        frame: The value to descend from (typically a parsed frame).
        keys: The chain of keys to follow.

    Returns:
        The innermost dict found, or an empty dict if any level is missing or
        not an object.

    """
    current: Any = frame
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _frame_for_log(
    value: Any,
    limit: int = _MAX_LOGGED_FRAME_CHARS,
    *,
    collapse: bool = True,
) -> str:
    """
    Render a device-controlled value so it is safe and cheap to log.

    Two problems are solved here, in this order:

    * **Log injection.** The value is a frame from a device on the user's LAN.
      Rendered through ``repr``, newlines and quotes come back escaped, so a
      frame cannot forge extra lines in Home Assistant's log.
    * **Cost and noise.** A printer can push a large frame, and rendering it is
      itself recursive work that can raise ``RecursionError`` on a deeply nested
      one, so rendering is guarded and the result truncated.

    Arguments:
        value: The device-controlled value to render.
        limit: Maximum characters kept.
        collapse: Whether to squash runs of whitespace. True keeps a frame dump
            compact; False is for values whose whitespace IS the diagnosis — an
            all-blank mainboard id has to read differently from an empty one.

    Returns:
        A single-line, truncated representation, or a fixed placeholder if the
        value cannot be rendered at all.

    """
    try:
        text = repr(value)
    except (RecursionError, ValueError, TypeError):
        # A frame deep enough to break repr is exactly the input this whole
        # section is written to survive; the log line is not worth losing it over.
        return "<unrenderable frame>"
    if collapse:
        text = " ".join(text.split())
    if len(text) > limit:
        return f"{text[:limit]}... ({len(text)} chars total)"
    return text


def _id_for_log(value: Any) -> str:
    """
    Return a device-reported name or id, bounded for the log.

    A name a printer reports is normally an ordinary short string, and the log
    line is easier to read — and unchanged from before this existed — when it is
    echoed as it came. So a value that is already a short single-line string with
    no control characters passes through untouched; anything else (non-string,
    over-long, or carrying a newline or other control character that could forge a
    log line) goes through the escaped, truncated rendering.

    Arguments:
        value: The identifier or name the printer reported about itself.

    Returns:
        The value as it came when it is benign, else a bounded single-line
        representation (``"None"`` when nothing was reported).

    """
    if value is None:
        return "None"
    # `isprintable()` is False for anything containing a newline, a tab or another
    # control character, which is exactly the log-injection surface.
    if (
        isinstance(value, str)
        and len(value) <= _MAX_LOGGED_ID_CHARS
        and value.isprintable()
    ):
        return value
    return _frame_for_log(value, _MAX_LOGGED_ID_CHARS)


class _FrameScan(NamedTuple):
    """
    What one scan of a printer's live WebSocket connection produced.

    Attributes:
        found: Whatever the caller's ``find`` pulled out of the frame it matched,
            or None when no frame matched (or the scan never got frames at all).
        connected: Whether the WebSocket connection itself worked. Callers need
            this because "nothing is listening" and "something answered but said
            nothing useful" are different answers for the user and for the log,
            and only the connection outcome tells them apart.

    """

    found: Any
    connected: bool


async def _async_scan_websocket_frames(
    hass: HomeAssistant,
    ip_address: str | None,
    find: Callable[[dict[str, Any]], Any],
    context: str,
) -> _FrameScan:
    """
    Read a printer's live WebSocket push stream until ``find`` matches a frame.

    This is the ONE frame loop for both config-flow probes that talk to a printer
    over TCP 3030 — the identity probe and CC1 Canvas detection. They used to be
    two loops, and only this one was total: Canvas detection still had the
    pre-probe ``parsed.get("Topic")`` / ``.get("Data")`` chain, which raises
    ``AttributeError`` on a list frame, ``TypeError`` on a non-string ``Topic``,
    and ``RecursionError`` on a deeply nested one. Canvas detection is reachable
    with a device-controlled first frame now that the identity probe only
    guarantees "something at this IP accepts TCP", so that crash became a
    user-visible one.

    Never raises for anything the printer controls, for the reason the identity
    probe's docstring gives in full: ``data_entry_flow._async_handle_step``
    catches only ``AbortFlow``, so anything escaping here surfaces as the generic
    "Config flow could not be loaded". Hence:

    * non-text messages end the scan, as they always have;
    * undecodable JSON is skipped, not fatal (a garbage frame says nothing, it
      does not prove the printer is unreachable);
    * ``RecursionError`` is caught in both the per-frame decode and the outer
      handler — ``json.loads`` is itself recursive and a frame nested ~30k deep
      raises it, which is neither ``JSONDecodeError`` nor ``ClientError``;
    * every value taken out of a frame goes through :func:`_as_str` or
      :func:`_dig`, so nothing here can be made to raise by what the device
      sends.

    ``asyncio.CancelledError`` is deliberately NOT caught: it derives from
    ``BaseException``, and Home Assistant must still be able to cancel a flow.

    Arguments:
        hass: The Home Assistant instance, for the shared aiohttp session.
        ip_address: The address to dial, or None when the caller has none.
        find: Given one parsed frame, returns the value the caller wants, or
            None to keep reading. It is this integration's own code, and the two
            call sites keep it total with :func:`_as_str` / :func:`_dig`.
        context: Short phrase naming the caller, used in the failure log so a
            Canvas-detection timeout is not reported as an identity probe one.

    Returns:
        A :class:`_FrameScan`: the matched value (None if nothing matched) and
        whether the connection worked.

    """
    import aiohttp  # noqa: PLC0415

    if not ip_address:
        return _FrameScan(found=None, connected=False)

    ws_url = f"ws://{ip_address}:{WEBSOCKET_PORT}/websocket"
    session = async_get_clientsession(hass)
    try:
        # The timeout must cover ws_connect too: a device that accepts TCP but
        # never completes the WS handshake would otherwise hang the config flow
        # on HA's shared session (no handshake timeout).
        async with (
            asyncio.timeout(_CANVAS_DETECT_TIMEOUT),
            session.ws_connect(ws_url) as ws,
        ):
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                try:
                    frame = json.loads(msg.data)
                except (json.JSONDecodeError, RecursionError):
                    # A frame that is not JSON, or is JSON too deeply nested to
                    # decode, carries nothing this integration can use. Skip it
                    # and keep reading: the printer is still connected.
                    continue
                # Everything from here down is device-controlled, so the frame is
                # type-checked before any key is read out of it.
                if not isinstance(frame, dict):
                    continue
                candidate = find(frame)
                if candidate is not None:
                    return _FrameScan(found=candidate, connected=True)
    except (TimeoutError, aiohttp.ClientError, RecursionError) as exception:
        # The narrow set this integration already uses for its own connectivity
        # probes, plus RecursionError for the deeply-nested frame json.loads
        # cannot decode inside `find`. Deliberately not widened further: a
        # printer cannot cause anything else here, and swallowing more would hide
        # real bugs.
        LOGGER.debug(
            "%s: reading the WebSocket push stream from %s:%s failed (%s)",
            context,
            ip_address,
            WEBSOCKET_PORT,
            type(exception).__name__,
        )
        return _FrameScan(found=None, connected=False)
    return _FrameScan(found=None, connected=True)


MANUAL_IP_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_IP_ADDRESS,
        ): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT,
            ),
        ),
        vol.Optional(
            CONF_PROXY_HOST,
        ): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT,
            ),
        ),
        vol.Optional(
            CONF_EXTERNAL_IP,
        ): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT,
            ),
        ),
    },
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_IP_ADDRESS,
        ): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT,
            ),
        ),
        vol.Required(
            CONF_PROXY_ENABLED,
        ): selector.BooleanSelector(
            selector.BooleanSelectorConfig(),
        ),
        vol.Optional(
            CONF_EXTERNAL_IP,
        ): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT,
            ),
        ),
    },
)


async def _async_test_connection(
    hass: HomeAssistant, printer_object: Printer, user_input: dict[str, Any]
) -> Printer:
    """
    Attempt to connect to an Elegoo printer.

    Arguments:
        hass: The Home Assistant instance.
        printer_object: The printer object to test.
        user_input: The user input data.

    Returns:
        The validated Printer object if the connection is successful.

    Raises:
        ElegooConfigFlowGeneralError: If the printer's IP address is missing.
        ElegooConfigFlowConnectionError: If the connection to the printer fails.

    """
    if printer_object.ip_address is None:
        msg = "IP address is required to connect to the printer"
        raise ElegooConfigFlowGeneralError(msg)

    # Create appropriate client based on transport type
    if printer_object.transport_type == TransportType.CC2_MQTT:
        LOGGER.info(
            "Using CC2 MQTT transport for printer %s during config flow",
            printer_object.name,
        )
        # Import CC2 client for connection testing
        from .cc2.client import ElegooCC2Client  # noqa: PLC0415

        # Get access code from user input
        access_code = user_input.get(CONF_CC2_ACCESS_CODE)

        # Create CC2 client and test connection
        cc2_client = ElegooCC2Client(
            # The proxy when one is configured, else the printer IP.
            printer_ip=printer_object.connection_host or "",
            serial_number=printer_object.id or "",
            access_code=access_code,
            logger=LOGGER,
            printer=printer_object,
        )

        try:
            # Attempt connection with provided credentials
            connected = await cc2_client.connect_printer(printer_object)
            if not connected:
                if printer_object.proxy_host:
                    # Blaming the access code here would be wrong: the usual
                    # cause is a proxy that is not running or not reachable.
                    msg = (
                        f"Cannot reach the proxy at "
                        f"{printer_object.connection_host}:{CC2_MQTT_PORT} - "
                        f"is it running?"
                    )
                else:
                    msg = (
                        f"Failed to authenticate with CC2 printer {printer_object.name}"
                    )
                raise ElegooConfigFlowConnectionError(msg)

            # Store the working password back to user_input for persistence
            # This captures the actual working password (even if it's an empty string)
            user_input[CONF_CC2_ACCESS_CODE] = cc2_client.access_code

            # Success - clean up test connection
            await cc2_client.disconnect()

            # Configure printer settings
            printer_object.mqtt_broker_enabled = False
            printer_object.proxy_enabled = False
            LOGGER.debug(
                "Successfully tested CC2 printer %s connection",
                printer_object.name,
            )
            return printer_object
        finally:
            # Ensure cleanup even on exception
            if cc2_client.is_connected:
                await cc2_client.disconnect()

    if printer_object.transport_type == TransportType.MQTT:
        LOGGER.info(
            "Using MQTT transport for printer %s during config flow",
            printer_object.name,
        )
        # Skip live connection test - embedded broker starts during setup
        # Persist embedded-broker defaults
        printer_object.mqtt_broker_enabled = True
        printer_object.proxy_enabled = False
        LOGGER.debug(
            "Prepared MQTT printer %s for embedded broker (no connect test in flow)",
            printer_object.name,
        )
        return printer_object
    LOGGER.info(
        "Using WebSocket/SDCP protocol for printer %s during config flow",
        printer_object.name,
    )
    elegoo_printer = ElegooPrinterClient(
        printer_object.ip_address,
        config=MappingProxyType(user_input),
        logger=LOGGER,
        session=async_get_clientsession(hass),
    )
    printer_object.proxy_enabled = user_input.get(CONF_PROXY_ENABLED, False)
    # WebSocket printers don't use MQTT broker
    printer_object.mqtt_broker_enabled = False
    LOGGER.debug(
        "Connecting to WebSocket printer: %s at %s with proxy enabled: %s",
        printer_object.name,
        printer_object.ip_address,
        printer_object.proxy_enabled,
    )
    if await elegoo_printer.connect_printer(
        printer_object, proxy_enabled=printer_object.proxy_enabled
    ):
        await elegoo_printer.disconnect()
        return printer_object

    msg = f"Failed to connect to printer {printer_object.name} at {printer_object.ip_address}"  # noqa: E501
    raise ElegooConfigFlowConnectionError(msg)


async def _async_validate_input(  # noqa: PLR0912
    hass: HomeAssistant,
    user_input: dict[str, Any],
    discovered_printers: list[Printer] | None = None,
) -> dict[str, Any]:
    """
    Asynchronously validates user input for Elegoo printer configuration.

    Matches a discovered printer or locates one by IP address, and verifies
    connectivity.

    Arguments:
        hass: The Home Assistant instance.
        user_input: Configuration data that may include a printer ID or IP address.
        discovered_printers: A list of discovered printers.

    Returns:
        A dictionary containing the validated printer object under the "printer" key
        (or None if validation fails), and error details under the "errors" key.

    """
    _errors = {}
    printer_object: Printer | None = None

    if "printer_id" in user_input and discovered_printers:
        # User selected a discovered printer
        selected_printer_id = user_input["printer_id"]
        for p in discovered_printers:
            if p.id == selected_printer_id:
                printer_object = p
                break
        if not printer_object:
            _errors["base"] = "invalid_printer_selection"
    elif CONF_IP_ADDRESS in user_input:
        # Manual IP entry - try WebSocket discovery first, then CC2
        raw_ip = user_input[CONF_IP_ADDRESS]
        ip_address = _sanitize_ip_address(raw_ip)
        if not ip_address:
            LOGGER.warning("Manual IP entry: no valid IP address provided: %s", raw_ip)
            return {"printer": None, "errors": {"base": "manual_ip_no_valid_ip"}}
        elegoo_printer = ElegooPrinterClient(
            ip_address,
            config=MappingProxyType(user_input),
            logger=LOGGER,
            session=async_get_clientsession(hass),
        )
        printers = await hass.async_add_executor_job(
            elegoo_printer.discover_printer, ip_address
        )
        if printers:
            printer_object = printers[0]
        else:
            # Try CC2 discovery as fallback
            cc2_printers = await hass.async_add_executor_job(
                CC2Discovery.discover_as_printers, ip_address
            )
            if cc2_printers:
                printer_object = cc2_printers[0]
            else:
                _errors["base"] = "no_printer_found"
                return {"printer": None, "errors": _errors}
    if printer_object:
        # Assign ports if proxy is enabled
        if user_input.get(CONF_PROXY_ENABLED, False):
            ws_port, video_port = ElegooPrinterServer.get_next_available_ports()
            printer_object.proxy_websocket_port = ws_port
            printer_object.proxy_video_port = video_port
            LOGGER.debug(
                "Assigned ports for proxy: WS:%d Video:%d", ws_port, video_port
            )

        try:
            # Pass the full user_input to _async_test_connection for centauri_carbon and proxy_enabled  # noqa: E501
            validated_printer = await _async_test_connection(
                hass, printer_object, user_input
            )
            return {"printer": validated_printer, "errors": None}  # noqa: TRY300
        except ElegooConfigFlowConnectionError as exception:
            LOGGER.error("Config Flow: Connection error: %s", exception)
            _errors["base"] = "connection"
        except ElegooConfigFlowGeneralError as exception:
            LOGGER.error("Config Flow: No printer found: %s", exception)
            _errors["base"] = "validation_no_printer_found"
        except PlatformNotReady as exception:
            LOGGER.error(exception)
            _errors["base"] = "connection"
        except OSError as exception:
            LOGGER.exception(exception)
            _errors["base"] = "unknown"
    else:
        _errors["base"] = "no_printer_selected_or_ip_provided"

    return {"printer": None, "errors": _errors}


# The model a proxy-only CC2 is registered as when discovery was skipped.
# `Printer.from_dict` recomputes `printer_type` from this string, and
# `PrinterType.from_model` matches it on the "centauri" keyword, so it has to
# be a real model name. The live model/name are synced from the printer's own
# attributes once connected.
CC2_PROXY_MODEL = "Centauri Carbon 2"


class ElegooFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Elegoo."""

    VERSION = CONFIG_VERSION_5
    MINOR_VERSION = 0
    _detected_canvas: bool | None = None
    # The CC2 access code is a parameter of _attempt_cc2_connection, but a flow
    # that pauses (serial prompt) must resume with it, so it is kept on the
    # handler as well. Without this an access-code-protected printer would be
    # retried with fallback passwords only, and fail.
    _cc2_access_code: str | None = None

    def _cleanup_user_input(self, raw_ip: str) -> str | None:
        """
        Sanitize user-provided IP address input.

        Strips URL prefixes (http://, https://, //), trailing slashes,
        and whitespace from the raw user input.

        Arguments:
            raw_ip: Raw string value from config form

        Returns:
            Cleaned IP address or None if empty/invalid.

        """
        return _sanitize_ip_address(raw_ip)

    async def _async_detect_canvas(self, printer: Printer) -> bool:
        """
        Detect Canvas on CC1 by reading AmsConnectStatus from a status push.

        The frame stream is read by :func:`_async_scan_websocket_frames`, which
        is total against a device-controlled frame; this method only decides what
        counts as an answer. The first frame here is NOT known to come from a
        printer: the identity probe guarantees only that something at this IP
        accepts TCP, so Canvas detection can be pointed at whatever is listening,
        and any exception escaping would reach the user as HA's generic "Config
        flow could not be loaded".

        A frame whose `AmsConnectStatus` is present but not a bool is read as
        whatever it is and `bool()`-ed, matching the previous behaviour; a status
        frame that does not carry the key at all means "no Canvas", as before.

        Arguments:
            printer: The selected printer (only its address is used).

        Returns:
            True when the printer reported Canvas connected, False for anything
            else — including a device that answers with frames that are not
            status frames at all.

        """

        def _ams_connect_status(frame: dict[str, Any]) -> bool | None:
            """Pull AmsConnectStatus from a status frame; None keeps reading."""
            if "status" not in _as_str(frame.get("Topic", "")):
                return None
            status = _dig(frame, "Data", "Data", "Status")
            # `False` is a real answer, so the "keep reading" signal is None and
            # not falsiness: a status frame that omits AmsConnectStatus means no
            # Canvas, exactly as the pre-probe loop read it.
            return bool(status.get("AmsConnectStatus", 0))

        scan = await _async_scan_websocket_frames(
            self.hass, printer.ip_address, _ams_connect_status, "CC1 Canvas detection"
        )
        if scan.found is None:
            LOGGER.debug(
                "CC1 Canvas detection: no AmsConnectStatus from %s"
                " — assuming no Canvas",
                printer.ip_address,
            )
            return False
        LOGGER.debug(
            "CC1 Canvas detection: AmsConnectStatus=%s",
            scan.found,
        )
        return bool(scan.found)

    async def _async_probe_websocket_printer(self, ip_address: str) -> Printer | None:
        """
        Describe a WebSocket printer over the connection the integration uses.

        Used only when both UDP discovery protocols came back empty for an IP
        the user typed by hand. Discovery and reachability are different
        questions: a CC1 that cannot ping its default gateway stops answering
        SDCP discovery while TCP 3030 stays open the whole time (#431), so the
        printer is asked who it is over the socket it is already known to
        accept, and described from what **it reports about itself**.

        Identity is not invented. ``printer.id`` doubles as the config entry's
        unique id, the device identifier and the entity unique-id prefix, and
        nothing repairs it later: with no id, ``_abort_if_unique_id_configured``
        early-returns on ``None`` and duplicate protection is silently off while
        ``coordinator.generate_unique_id`` raises ``TypeError`` for every entity;
        and an id that is present but not a non-empty string fails the same way
        (``123 + "_"`` is a ``TypeError``) after passing a bare truthiness
        check. So a printer that pushes no attributes frame, no
        ``MainboardID``, or a ``MainboardID`` that is not a usable string is
        REFUSED — ``None``, which the caller reports as ``no_printer_found`` —
        rather than entered with an id that cannot work.

        The frame supplies identity, never routing. That is the second half of
        the contract, and it is not optional: this is the only place in the
        integration where ``Printer.from_dict`` is handed a dictionary the device
        wrote, and ``from_dict`` reads transport selection out of the same dict it
        reads the name out of (``transport_type``, ``protocol_version``,
        ``proxy_host``, ``mqtt_external_host``, ``cc2_access_code``, the proxy
        ports). Measured before the filter was added, a frame carrying
        ``{"transport_type": "cc2_mqtt", "protocol_version": "CC2",
        "proxy_host": "attacker.example"}`` produced a printer whose
        ``connection_host`` was ``attacker.example``, whose ``to_dict`` carried
        ``proxy_host``, and which the caller routed to the CC2 auth flow — a
        hostile device on the LAN choosing the host Home Assistant dials next and
        sending the CC2 registration and fallback passwords to it, permanently, in
        ``entry.data``. UDP discovery never had this hole because the
        ``Printer(json_string)`` constructor reads only the identity keys. So the
        frame is reduced to :data:`_PRINTER_IDENTITY_KEYS` before it is parsed:
        the device can say who it is, and transport is derived from
        ``ProtocolVersion`` the way ``from_dict`` already derives it when the
        routing keys are absent.

        Never raises for anything the printer controls. HA's
        ``data_entry_flow._async_handle_step`` catches only ``AbortFlow``, so an
        exception escaping here reaches the UI as the generic "Config flow could
        not be loaded" — strictly worse than the error this replaces. The frame
        loop is total by construction, and now shared with Canvas detection via
        :func:`_async_scan_websocket_frames`: every value taken out of the frame
        is type-checked before use, so no device-controlled frame can make it
        raise. ``Printer.from_dict`` is not exception-safe for shapes it did not
        anticipate (measured: ``AttributeError`` for a non-string
        ``MachineName``/``ProtocolVersion``/``FirmwareVersion``, ``ValueError``
        for an unknown ``transport_type``), so the parse is guarded, and the
        guard ends in a declared broad ``except Exception`` because the input is
        a frame the printer chooses to send and the cost of missing a class is
        the crash this section exists to prevent. It is ``Exception``, not
        ``BaseException``: ``asyncio.CancelledError`` must still propagate so HA
        can cancel the flow.

        Arguments:
            ip_address: The address the user typed (already cleaned).

        Returns:
            The printer as it described itself, or None when nothing usable
            could be read from it.

        """

        def _attributes_of(frame: dict[str, Any]) -> dict[str, Any] | None:
            """Pull the attributes dict out of one frame, None to keep reading."""
            if "attributes" not in _as_str(frame.get("Topic", "")):
                return None
            # Two attributes shapes exist in this repo's own evidence and they
            # disagree: scripts/test_server.py pushes {Data: {Attributes: ...}}
            # while the conformance test and the MQTT client's "WebSocket
            # fallback format" comment use {Attributes: ...} at the frame root.
            # Which one a real CC1 sends is UNVERIFIED, so both are read.
            candidate = frame.get("Attributes")
            if not isinstance(candidate, dict) or not candidate:
                # An EMPTY attributes object is treated as no answer, not as a
                # description with no keys: matching on it would end the scan on
                # the first frame and let a device hide a real attributes frame
                # behind an empty one. The refusal is the same either way, so this
                # only buys the chance that a usable frame follows.
                candidate = _dig(frame, "Data", "Attributes")
            return candidate or None

        scan = await _async_scan_websocket_frames(
            self.hass,
            ip_address,
            _attributes_of,
            "live printer probe",
        )
        attributes: dict[str, Any] | None = scan.found

        if attributes is None:
            # The two cases read differently to an operator: nothing listening on
            # the port at all, versus something answering that never described
            # itself. Only the second one is a printer problem worth a warning.
            if not scan.connected:
                LOGGER.debug(
                    "Nothing at %s:%s could be read over the live connection"
                    " (no connection, no usable frame)",
                    ip_address,
                    WEBSOCKET_PORT,
                )
            else:
                LOGGER.warning(
                    "Nothing at %s:%s described itself: no attributes frame was"
                    " received, so no printer identity could be read",
                    ip_address,
                    WEBSOCKET_PORT,
                )
            return None

        # The trust boundary. Everything from here on is built from the seven
        # identity keys only, so no routing decision downstream can be steered by
        # what this device chose to send.
        identity = _identity_only(attributes)

        try:
            printer = Printer.from_dict(identity)
        except (AttributeError, TypeError, ValueError):
            # The measured classes a device-controlled attributes frame can
            # raise out of Printer.from_dict. The frame is logged at DEBUG and
            # scrubbed: it is device-controlled text, so it is neither a
            # default-visible log line nor an unbounded one.
            LOGGER.warning(
                "Printer at %s:%s sent an attributes frame this integration"
                " cannot parse",
                ip_address,
                WEBSOCKET_PORT,
            )
            LOGGER.debug(
                "Unparseable attributes frame from %s:%s: %s",
                ip_address,
                WEBSOCKET_PORT,
                _frame_for_log(identity),
            )
            return None
        except Exception:  # noqa: BLE001  # declared, see the docstring
            # Declared judgement, not a derived fact: the input is a frame the
            # printer controls and from_dict grows keys, so an unanticipated
            # class must end in a refusal, not in HA's "Config flow could not be
            # loaded". Logged as an exception so it stays a findable bug to
            # narrow rather than silence.
            LOGGER.exception(
                "Unexpected error parsing the attributes frame from %s:%s",
                ip_address,
                WEBSOCKET_PORT,
            )
            return None

        # "The printer reported an id" is not "the id is usable as a unique id":
        # require the second claim. A truthy non-string (123, True, 1.5, ["a"])
        # or an all-whitespace string passes a bare `if not id:` and then breaks
        # every entity's unique id and the entry's device identity for good.
        candidate_id = printer.id
        if not (isinstance(candidate_id, str) and candidate_id.strip()):
            LOGGER.warning(
                "Printer at %s:%s reported no usable mainboard id (got %s),"
                " refusing it rather than creating an entry that cannot work",
                ip_address,
                WEBSOCKET_PORT,
                # Not _id_for_log: an all-blank id and an empty one are different
                # bugs and must read differently, so the whitespace is kept. repr
                # escapes newlines, so this stays one line either way.
                _frame_for_log(candidate_id, _MAX_LOGGED_ID_CHARS, collapse=False),
            )
            return None

        # The address the user typed wins over MainboardIP: a printer behind NAT
        # or reporting a stale address must not overwrite what the integration
        # dials. `connection` mirrors what discovery itself does (Printer sets
        # it from the frame's Id) and what _apply_serial does for CC2.
        printer.ip_address = ip_address
        printer.connection = ip_address
        printer.brand = printer.brand or "ELEGOO"
        # WebSocket printers never run the embedded broker.
        printer.mqtt_broker_enabled = False
        # The routing and credential fields, asserted rather than only excluded by
        # the allowlist above. The filter is what makes them unreachable from the
        # frame; writing them here is what keeps that true if `Printer.from_dict`
        # ever grows a default, since every one of these decides where Home
        # Assistant dials next, what it authenticates with, or which proxy the
        # entry trusts. `transport_type`/`protocol_version` are deliberately NOT
        # reset: with the frame's copies filtered out, `from_dict` derives them
        # from `ProtocolVersion`, which is the derivation discovery itself uses and
        # the reason a real CC1 still lands on the WebSocket transport.
        printer.proxy_host = None
        printer.proxy_enabled = False
        printer.proxy_websocket_port = None
        printer.proxy_video_port = None
        printer.mqtt_external_host = None
        printer.mqtt_external_port = None
        printer.cc2_access_code = None
        printer.cc2_token_status = 0
        printer.camera_enabled = False
        printer.has_canvas = False
        # `external_ip` is what the MQTT transport advertises to the printer as
        # the broker to dial, so it belongs with the fields above even though
        # `async_step_manual_ip` overwrites it from the form straight after.
        printer.external_ip = None
        LOGGER.info(
            "Probed %s over TCP %s without discovery: found %s (%s, id=%s)",
            ip_address,
            WEBSOCKET_PORT,
            _id_for_log(printer.name or printer.model or "printer"),
            printer.printer_type,
            _id_for_log(candidate_id),
        )
        return printer

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> config_entries.ConfigFlowResult:
        """
        Initiate the configuration flow by attempting to discover available Elegoo printers.

        If printers are discovered, proceeds to the printer selection step; otherwise,
        prompts the user to manually enter a printer IP address.

        Arguments:
            user_input: The user input data.

        Returns:
            The result of the configuration flow step.

        """  # noqa: E501
        # Initiate discovery for WebSocket/MQTT printers
        elegoo_printer_client = ElegooPrinterClient(
            "0.0.0.0",  # noqa: S104
            logger=LOGGER,
            session=async_get_clientsession(self.hass),
        )  # IP doesn't matter for discovery
        discovered = await self.hass.async_add_executor_job(
            elegoo_printer_client.discover_printer
        )

        # Also discover CC2 printers. Bound this to a short, single-attempt
        # timeout: a printer that doesn't speak CC2 never answers, so the default
        # CC2_DISCOVERY_TIMEOUT x (CC2_DISCOVERY_RETRIES + 1) would block this
        # config-flow step long enough for the frontend to abort with the generic
        # "Config flow could not be loaded: Unknown error".
        cc2_discovered = await self.hass.async_add_executor_job(
            partial(
                CC2Discovery.discover_as_printers,
                timeout=CC2_CONFIG_FLOW_DISCOVERY_TIMEOUT,
                retries=0,
            )
        )
        LOGGER.debug("Discovered %d CC2 printer(s)", len(cc2_discovered))

        # Merge discovered printers, avoiding duplicates by serial number
        all_printers = list(discovered)
        existing_ids = {p.id for p in all_printers if p.id}
        for cc2_printer in cc2_discovered:
            if cc2_printer.id and cc2_printer.id not in existing_ids:
                all_printers.append(cc2_printer)
                existing_ids.add(cc2_printer.id)

        # Filter out proxy servers from discovered printers
        self.discovered_printers = [p for p in all_printers if not p.is_proxy]

        if self.discovered_printers:
            return await self.async_step_discover_printers()
        return await self.async_step_manual_ip()

    async def async_step_discover_printers(  # noqa: PLR0911
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Handle the step for selecting a discovered Elegoo printer.

        If user input is provided, processes the selection and either advances to
        manual IP entry or presents options for the selected printer. If no input is
        provided, displays a form listing discovered printers with IP addresses and an
        option to enter an IP manually.

        Arguments:
            user_input: The user input data.

        Returns:
            The result of the configuration flow step, advancing to the next step or
            displaying the selection form with any errors.

        """
        _errors = {}

        if user_input is not None:
            if user_input["selection"] == "manual_ip":
                return await self.async_step_manual_ip()

            selected_printer_id = user_input["selection"]
            self.selected_printer = next(
                (p for p in self.discovered_printers if p.id == selected_printer_id),
                None,
            )

            if self.selected_printer:
                # Check if printer uses CC2 MQTT transport
                if self.selected_printer.transport_type == TransportType.CC2_MQTT:
                    LOGGER.info(
                        "CC2 printer selected from discovery: %s (token_status=%s)",
                        self.selected_printer.name,
                        self.selected_printer.cc2_token_status,
                    )
                    # CC2 printers may need access code - check token_status
                    # Store in context for later use
                    return await self.async_step_cc2_options()

                # Check if printer uses MQTT transport
                if self.selected_printer.transport_type == TransportType.MQTT:
                    # Auto-configure MQTT printer with embedded broker
                    printer = Printer.from_dict(self.selected_printer.to_dict())
                    printer.mqtt_broker_enabled = True
                    printer.proxy_enabled = False

                    await self.async_set_unique_id(unique_id=printer.id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=printer.name or "Elegoo Printer",
                        data=printer.to_dict(),
                    )

                # For WebSocket/SDCP printers, show type-specific options
                if self.selected_printer.printer_type == PrinterType.RESIN:
                    return self.async_show_form(
                        step_id="resin_options",
                        data_schema=vol.Schema(
                            {
                                vol.Required(
                                    CONF_CAMERA_ENABLED,
                                    default=self.selected_printer.camera_enabled,
                                ): selector.BooleanSelector(
                                    selector.BooleanSelectorConfig()
                                ),
                            }
                        ),
                        errors=_errors,
                    )
                return await self.async_step_fdm_options()
            _errors["base"] = "invalid_printer_selection"

        # Filter out printers without an IP address
        valid_printers = [p for p in self.discovered_printers if p.ip_address]
        if not valid_printers:
            LOGGER.warning("No discovered printers with an IP address found.")
            return await self.async_step_manual_ip()

        printer_options: list[SelectOptionDict] = [
            {"value": p.id, "label": f"{p.name} ({p.ip_address})"}
            for p in valid_printers
            if p.id is not None
        ]
        printer_options.append({"value": "manual_ip", "label": "Enter IP manually"})

        return self.async_show_form(
            step_id="discover_printers",
            data_schema=vol.Schema(
                {
                    vol.Required("selection"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=printer_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=_errors,
        )

    def _build_cc2_printer_from_proxy_input(
        self,
        ip_address: str | None,
        proxy_host: str,
    ) -> Printer | None:
        """
        Construct a CC2 ``Printer`` for a proxy-only entry (no discovery).

        A transparent forward proxy does not answer UDP discovery, so a user who
        enters one cannot be discovered; the printer is described by hand
        instead, mirroring ``CC2DiscoveredPrinter.to_printer()``. The serial is
        deliberately left empty — it is acquired later in the CC2 flow.

        Arguments:
            ip_address: The printer's real IP (cleaned), or None if blank.
            proxy_host: The forward proxy host to connect through.

        Returns:
            The constructed Printer, or None when no usable IP was given.

        """
        if not ip_address:
            return None

        printer = Printer()
        # Name left empty on purpose: it is unknown without discovery, and the
        # entry title falls back to "Elegoo Printer" as it does elsewhere. Both
        # name and model are corrected from the printer's own attributes once
        # the connection test runs.
        printer.name = ""
        printer.model = CC2_PROXY_MODEL
        printer.ip_address = ip_address
        printer.connection = ip_address
        printer.protocol_version = ProtocolVersion.CC2
        printer.transport_type = TransportType.CC2_MQTT
        printer.printer_type = PrinterType.from_model(CC2_PROXY_MODEL)
        printer.brand = "ELEGOO"
        # The printer runs its own broker; nothing embedded is needed here.
        printer.mqtt_broker_enabled = False
        printer.proxy_host = proxy_host
        return printer

    async def _async_handle_proxy_entry(
        self,
        user_input: dict[str, Any],
        ip_address: str | None,
    ) -> config_entries.ConfigFlowResult | None:
        """
        Handle a manual-IP submission that names a forward proxy.

        A forward proxy does not answer UDP discovery, so discovery is skipped
        and the CC2 printer is described by hand. Returns ``None`` when no
        proxy host was entered, meaning the caller should discover as before.

        Arguments:
            user_input: The manual-IP form input.
            ip_address: The cleaned printer IP (None when blank).

        Returns:
            The next flow step, or None to continue with discovery.

        """
        proxy_host = (user_input.get(CONF_PROXY_HOST) or "").strip()
        if not proxy_host:
            return None

        LOGGER.info(
            "Manual IP entry: proxy_host set (%s) - skipping discovery",
            proxy_host,
        )
        printer = self._build_cc2_printer_from_proxy_input(ip_address, proxy_host)
        if printer is None:
            LOGGER.warning(
                "Manual IP entry: proxy_host %s set but no valid IP provided: %s",
                proxy_host,
                user_input.get(CONF_IP_ADDRESS),
            )
            return self.async_show_form(
                step_id="manual_ip",
                data_schema=self.add_suggested_values_to_schema(
                    MANUAL_IP_SCHEMA, user_input
                ),
                errors={"base": "manual_ip_no_valid_ip"},
            )

        self.selected_printer = printer
        self.selected_printer.external_ip = user_input.get(CONF_EXTERNAL_IP)
        await self.async_set_unique_id(unique_id=printer.id)
        self._abort_if_unique_id_configured()
        LOGGER.info("Routing to CC2 auth flow for proxy entry")
        return await self.async_step_cc2_auth_check()

    async def async_step_manual_ip(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Handle the configuration flow step for manually entering a printer's IP address.

        Discovers the printer at the provided IP and routes to the appropriate
        configuration step based on the printer type (CC2, MQTT, FDM, or Resin).

        When ``proxy_host`` is provided, discovery is skipped entirely and a CC2
        printer is built directly (see :meth:`_async_handle_proxy_entry`).

        Arguments:
            user_input: The user input data.

        Returns:
            The result of the configuration flow step.

        """
        _errors = {}
        if user_input is not None:
            raw_ip = user_input[CONF_IP_ADDRESS]
            ip_address = self._cleanup_user_input(raw_ip)

            proxy_result = await self._async_handle_proxy_entry(user_input, ip_address)
            if proxy_result is not None:
                return proxy_result

            LOGGER.info(
                "Manual IP entry: attempting to discover printer at %s (original: %s)",
                ip_address,
                raw_ip,
            )

            # Check if sanitized input is empty after stripping
            if not ip_address:
                LOGGER.warning(
                    "Manual IP entry: no valid IP address provided: %s", raw_ip
                )
                _errors["base"] = "manual_ip_no_valid_ip"

            # Try WebSocket/SDCP discovery first
            elegoo_printer = ElegooPrinterClient(
                ip_address,
                config=MappingProxyType(user_input),
                logger=LOGGER,
                session=async_get_clientsession(self.hass),
            )
            printers = await self.hass.async_add_executor_job(
                elegoo_printer.discover_printer, ip_address
            )

            printer_object: Printer | None = None
            if printers:
                printer_object = printers[0]
                LOGGER.info(
                    "Found %s printer via WebSocket/SDCP discovery: %s",
                    printer_object.printer_type,
                    printer_object.name,
                )
            else:
                # Try CC2 discovery as fallback
                LOGGER.debug("WebSocket/SDCP discovery failed, trying CC2 discovery")
                cc2_printers = await self.hass.async_add_executor_job(
                    CC2Discovery.discover_as_printers, ip_address
                )
                if cc2_printers:
                    printer_object = cc2_printers[0]
                    LOGGER.info(
                        (
                            "Found CC2 printer via directed discovery: %s"
                            " (token_status=%s)"
                        ),
                        printer_object.name,
                        printer_object.cc2_token_status,
                    )

            if not printer_object and ip_address:
                # Neither UDP protocol answered. Ask the printer over the
                # connection the integration actually uses before giving up: a
                # CC1 that stops answering discovery still holds TCP 3030 open
                # (#429/#431), and it describes itself over that socket.
                LOGGER.debug(
                    "Discovery silent for %s, probing the live WebSocket connection",
                    ip_address,
                )
                printer_object = await self._async_probe_websocket_printer(ip_address)

            if not printer_object:
                LOGGER.warning("No printer found at IP address: %s", ip_address)
                _errors["base"] = "no_printer_found"
            else:
                # Store discovered printer and external_ip for later steps
                self.selected_printer = printer_object
                self.selected_printer.external_ip = user_input.get(CONF_EXTERNAL_IP)

                # Check if already configured
                await self.async_set_unique_id(unique_id=printer_object.id)
                self._abort_if_unique_id_configured()

                # Route based on transport type
                if printer_object.transport_type == TransportType.CC2_MQTT:
                    LOGGER.info("Routing to CC2 auth flow for manual IP entry")
                    return await self.async_step_cc2_auth_check()

                if printer_object.transport_type == TransportType.MQTT:
                    # MQTT printers auto-configure with embedded broker
                    LOGGER.info("Auto-configuring MQTT printer from manual IP entry")
                    printer_object.mqtt_broker_enabled = True
                    printer_object.proxy_enabled = False
                    return self.async_create_entry(
                        title=printer_object.name or "Elegoo Printer",
                        data=printer_object.to_dict(),
                    )

                # WebSocket/SDCP printers - route based on printer type
                if printer_object.printer_type == PrinterType.RESIN:
                    LOGGER.info("Routing to resin options for manual IP entry")
                    return await self.async_step_resin_options()

                LOGGER.info("Routing to FDM options for manual IP entry")
                return await self.async_step_fdm_options()

        return self.async_show_form(
            step_id="manual_ip",
            data_schema=self.add_suggested_values_to_schema(
                MANUAL_IP_SCHEMA, user_input
            ),
            errors=_errors,
        )

    async def async_step_resin_options(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the configuration of additional options for a discovered Elegoo printer."""  # noqa: E501
        _errors = {}
        if user_input is not None and self.selected_printer:
            printer_to_validate = Printer.from_dict(self.selected_printer.to_dict())
            printer_to_validate.camera_enabled = user_input[CONF_CAMERA_ENABLED]
            # Preserve external_ip from manual IP entry if set
            if self.selected_printer.external_ip:
                printer_to_validate.external_ip = self.selected_printer.external_ip
            try:
                # Pass the full user_input to _async_test_connection for centauri_carbon and proxy_enabled  # noqa: E501
                validated_printer = await _async_test_connection(
                    self.hass, printer_to_validate, user_input
                )
                await self.async_set_unique_id(unique_id=validated_printer.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=validated_printer.name or "Elegoo Printer",
                    data=validated_printer.to_dict(),
                )
            except ElegooConfigFlowConnectionError as exception:
                LOGGER.error("Connection error: %s", exception)
                _errors["base"] = "connection"
            except ElegooConfigFlowGeneralError as exception:
                LOGGER.error("No printer found: %s", exception)
                _errors["base"] = "manual_options_no_printer_found"
            except PlatformNotReady as exception:
                LOGGER.error(exception)
                _errors["base"] = "connection"
            except OSError as exception:
                LOGGER.exception(exception)
                _errors["base"] = "unknown"

        return self.async_show_form(
            step_id="resin_options",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CAMERA_ENABLED,
                        default=(
                            self.selected_printer.camera_enabled
                            if self.selected_printer
                            else False
                        ),
                    ): selector.BooleanSelector(selector.BooleanSelectorConfig()),
                }
            ),
            errors=_errors,
        )

    async def async_step_fdm_options(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the configuration of additional options for a discovered Elegoo printer."""  # noqa: E501
        _errors = {}

        if (
            user_input is None
            and self.selected_printer
            and self._detected_canvas is None
        ):
            self._detected_canvas = await self._async_detect_canvas(
                self.selected_printer
            )

        if user_input is not None and self.selected_printer:
            printer_to_validate = Printer.from_dict(self.selected_printer.to_dict())
            printer_to_validate.proxy_enabled = user_input[CONF_PROXY_ENABLED]
            printer_to_validate.has_canvas = user_input.get(CONF_HAS_CANVAS, False)
            # Preserve external_ip from manual IP entry if set
            if self.selected_printer.external_ip:
                printer_to_validate.external_ip = self.selected_printer.external_ip

            # Assign ports if proxy is enabled
            if user_input[CONF_PROXY_ENABLED]:
                ws_port, video_port = ElegooPrinterServer.get_next_available_ports()
                printer_to_validate.proxy_websocket_port = ws_port
                printer_to_validate.proxy_video_port = video_port
                LOGGER.debug(
                    "Assigned ports for proxy: WS:%d Video:%d", ws_port, video_port
                )

            try:
                validated_printer = await _async_test_connection(
                    self.hass, printer_to_validate, user_input
                )
                await self.async_set_unique_id(unique_id=validated_printer.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=validated_printer.name or "Elegoo Printer",
                    data=validated_printer.to_dict(),
                )
            except ElegooConfigFlowConnectionError as exception:
                LOGGER.error("Connection error: %s", exception)
                _errors["base"] = "connection"
            except ElegooConfigFlowGeneralError as exception:
                LOGGER.error("No printer found: %s", exception)
                _errors["base"] = "manual_options_no_printer_found"
            except PlatformNotReady as exception:
                LOGGER.error(exception)
                _errors["base"] = "connection"
            except OSError as exception:
                LOGGER.exception(exception)
                _errors["base"] = "unknown"

        return self.async_show_form(
            step_id="fdm_options",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PROXY_ENABLED,
                        default=(
                            self.selected_printer.proxy_enabled
                            if self.selected_printer
                            else False
                        ),
                    ): selector.BooleanSelector(
                        selector.BooleanSelectorConfig(),
                    ),
                    vol.Required(
                        CONF_HAS_CANVAS,
                        default=self._detected_canvas or False,
                    ): selector.BooleanSelector(
                        selector.BooleanSelectorConfig(),
                    ),
                }
            ),
            errors=_errors,
        )

    async def async_step_cc2_options(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> config_entries.ConfigFlowResult:
        """Handle the configuration of CC2 printer options (redirect to new flow)."""
        # Redirect to new two-step authentication flow
        return await self.async_step_cc2_auth_check()

    async def async_step_cc2_auth_check(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Ask user if their CC2 printer requires an access code."""
        LOGGER.debug("CC2 auth check step - user_input: %s", user_input)

        if user_input is not None:
            # Store user's choice
            self._requires_access_code = user_input["requires_access_code"] == "yes"
            LOGGER.info(
                "CC2 auth check: user selected requires_access_code=%s",
                "yes" if self._requires_access_code else "no",
            )

            if self._requires_access_code:
                # Show password input step
                LOGGER.debug("Redirecting to access code input step")
                return await self.async_step_cc2_access_code_input()
            # No access code needed - attempt connection with fallback
            LOGGER.debug(
                "No access code required - attempting connection with fallback"
            )
            return await self._attempt_cc2_connection(access_code=None)

        # Show form asking if access code is required
        LOGGER.info(
            "Showing CC2 auth check form for printer: %s",
            self.selected_printer.name if self.selected_printer else "Unknown",
        )
        return self.async_show_form(
            step_id="cc2_auth_check",
            data_schema=vol.Schema(
                {
                    vol.Required("requires_access_code"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": "yes", "label": "Yes"},
                                {"value": "no", "label": "No"},
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_cc2_access_code_input(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle access code input for CC2 printers."""
        _errors = {}

        if user_input is not None:
            access_code = user_input.get(CONF_CC2_ACCESS_CODE)

            # Validate access code is provided
            if not access_code:
                _errors["base"] = "cc2_access_code_required"
            else:
                # Attempt connection with provided access code
                return await self._attempt_cc2_connection(access_code=access_code)

        # Show password input form
        return self.async_show_form(
            step_id="cc2_access_code_input",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CC2_ACCESS_CODE): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD,
                        ),
                    ),
                }
            ),
            errors=_errors,
        )

    async def _async_auto_learn_serial(self, printer: Printer) -> str | None:
        """
        Learn the CC2 serial over MQTT when discovery was skipped.

        Used only for proxy entries: a forward proxy does not answer UDP
        discovery, so the serial that every CC2 topic embeds is otherwise
        unknown. Best-effort — ``None`` means the caller should prompt.

        Arguments:
            printer: The printer being configured (updated in place on success).

        Returns:
            The learned serial, or None.

        """
        from .cc2.client import ElegooCC2Client  # noqa: PLC0415

        client = ElegooCC2Client(
            printer_ip=printer.connection_host or "",
            # Deliberately empty: learning the serial is the whole point.
            serial_number="",
            access_code=self._cc2_access_code,
            logger=LOGGER,
            printer=printer,
        )
        try:
            serial = await client.async_discover_serial()
            if serial:
                self._apply_serial(printer, serial)
                LOGGER.info("Auto-learned CC2 serial %s via proxy", serial)
            else:
                LOGGER.info(
                    "Could not auto-learn the CC2 serial via proxy %s - prompting",
                    printer.proxy_host,
                )
            return serial
        finally:
            await client.disconnect()

    def _apply_serial(self, printer: Printer, serial: str) -> None:
        """
        Record a learned/entered serial on both printer objects.

        ``selected_printer`` is what the next attempt rebuilds from, so it has
        to be updated too or the serial would be lost on a retry.

        Arguments:
            printer: The printer built for the current attempt.
            serial: The serial number to record.

        """
        for target in (printer, self.selected_printer):
            if target is not None:
                target.id = serial
                # CC2 uses the serial as its connection identifier.
                target.connection = serial

    async def async_step_cc2_serial_input(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Ask for the serial number when it could not be learned automatically.

        Only reached for proxy entries whose serial could not be auto-learned.
        Loop-safe: the resumed connection attempt only returns here while the
        serial is still empty, so any submitted value breaks the loop.

        Arguments:
            user_input: The user input data.

        Returns:
            The result of the configuration flow step.

        """
        _errors = {}
        if user_input is not None:
            serial = (user_input.get(CONF_SERIAL) or "").strip()
            if not serial:
                _errors["base"] = "cc2_serial_required"
            elif self.selected_printer is not None:
                self._apply_serial(self.selected_printer, serial)
                # Resume with the stored code, not None.
                return await self._attempt_cc2_connection(
                    access_code=self._cc2_access_code
                )

        return self.async_show_form(
            step_id="cc2_serial_input",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SERIAL): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT,
                        ),
                    ),
                }
            ),
            errors=_errors,
        )

    async def _attempt_cc2_connection(
        self,
        access_code: str | None,
    ) -> config_entries.ConfigFlowResult:
        """
        Attempt to connect to CC2 printer with given access code.

        Arguments:
            access_code: The access code to use (or None for fallback logic).

        Returns:
            ConfigFlowResult with success or error.

        """
        LOGGER.debug(
            "CC2 connection attempt - access_code provided: %s (type: %s)",
            "Yes" if access_code is not None else "No (will use fallback)",
            type(access_code).__name__ if access_code is not None else "NoneType",
        )

        if not self.selected_printer:
            return self.async_abort(reason="no_printer_selected_or_ip_provided")

        # Remember the code so a flow that pauses (serial prompt) can resume
        # with it instead of falling back to the default passwords.
        self._cc2_access_code = access_code

        printer = Printer.from_dict(self.selected_printer.to_dict())
        printer.mqtt_broker_enabled = False
        printer.proxy_enabled = False
        printer.has_canvas = True
        # Preserve external_ip from manual IP entry if set
        if self.selected_printer.external_ip:
            printer.external_ip = self.selected_printer.external_ip

        # A proxy entry skipped discovery, so the serial (which is part of every
        # CC2 topic and required to register) is still unknown. Learn it, or ask.
        needs_serial = bool(printer.proxy_host) and not printer.id
        if needs_serial and not await self._async_auto_learn_serial(printer):
            return await self.async_step_cc2_serial_input()

        # Prepare user_input with access code
        # Use explicit None check to allow empty strings
        user_input = {}
        if access_code is not None:
            user_input[CONF_CC2_ACCESS_CODE] = access_code
            LOGGER.debug(
                "Access code added to user_input (length: %d)", len(access_code)
            )
        else:
            LOGGER.debug(
                "No access code in user_input - client will try fallback passwords"
            )

        try:
            LOGGER.info("Starting CC2 connection test for printer: %s", printer.name)
            validated_printer = await _async_test_connection(
                self.hass, printer, user_input
            )
            LOGGER.info("CC2 connection test succeeded for printer: %s", printer.name)
            await self.async_set_unique_id(unique_id=validated_printer.id)
            self._abort_if_unique_id_configured()

            # Add access code to printer data
            # Get working password from user_input (updated by _async_test_connection)
            printer_data = validated_printer.to_dict()
            working_password = user_input.get(CONF_CC2_ACCESS_CODE)
            if working_password is not None:
                printer_data[CONF_CC2_ACCESS_CODE] = working_password

            return self.async_create_entry(
                title=validated_printer.name or "Elegoo Printer",
                data=printer_data,
            )
        except ElegooConfigFlowConnectionError as exception:
            LOGGER.error("Connection error: %s", exception)
            # Show helpful error message
            return self.async_show_form(
                step_id=(
                    "cc2_access_code_input"
                    if self._requires_access_code
                    else "cc2_auth_check"
                ),
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_CC2_ACCESS_CODE): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.PASSWORD,
                            ),
                        ),
                    }
                    if self._requires_access_code
                    else {
                        vol.Required("requires_access_code"): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[
                                    {"value": "yes", "label": "Yes"},
                                    {"value": "no", "label": "No"},
                                ],
                                mode=selector.SelectSelectorMode.LIST,
                            )
                        )
                    }
                ),
                # An unreachable proxy is not a wrong access code; blaming the
                # code would send the user hunting for a non-existent typo.
                errors={
                    "base": (
                        "cc2_proxy_unreachable"
                        if printer.proxy_host
                        else "cc2_authentication_failed"
                    )
                },
            )
        except ElegooConfigFlowGeneralError as exception:
            LOGGER.error("No printer found: %s", exception)
            return self.async_abort(reason="cc2_options_no_printer_found")
        except (PlatformNotReady, OSError) as exception:
            LOGGER.error(exception)
            return self.async_show_form(
                step_id=(
                    "cc2_access_code_input"
                    if self._requires_access_code
                    else "cc2_auth_check"
                ),
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_CC2_ACCESS_CODE): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.PASSWORD,
                            ),
                        ),
                    }
                    if self._requires_access_code
                    else {
                        vol.Required("requires_access_code"): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[
                                    {"value": "yes", "label": "Yes"},
                                    {"value": "no", "label": "No"},
                                ],
                                mode=selector.SelectSelectorMode.LIST,
                            )
                        )
                    }
                ),
                errors={"base": "connection"},
            )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ElegooOptionsFlowHandler:
        """
        Return an options flow handler for managing configuration options.

        Arguments:
            config_entry: The configuration entry for which to create the options flow.

        Returns:
            The handler managing the options flow for the given configuration entry.

        """
        return ElegooOptionsFlowHandler(config_entry)

    @classmethod
    @callback
    def async_supports_options_flow(
        cls,
        config_entry: config_entries.ConfigEntry,  # noqa: ARG003
    ) -> bool:
        """Return options flow support for this handler."""
        return True


# Maps a TransportType to its options-flow step id.
_OPTIONS_STEP_IDS: dict[TransportType, str] = {
    TransportType.CC2_MQTT: "cc2_options",
    TransportType.MQTT: "mqtt_options",
    TransportType.WEBSOCKET: "websocket_options",
}


class ElegooOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow handler for Elegoo Printer."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """
        Display the options form for an existing Elegoo printer configuration.

        Routes to appropriate options step based on printer transport type.

        Arguments:
            user_input: The user input data.

        Returns:
            The result of the configuration flow step.

        """
        current_settings = {
            **(self.config_entry.data or {}),
            **(self.config_entry.options or {}),
        }
        printer = Printer.from_dict(current_settings)

        # Route to appropriate options based on transport type
        if printer.transport_type == TransportType.CC2_MQTT:
            return await self.async_step_cc2_options(user_input)
        if printer.transport_type == TransportType.MQTT:
            return await self.async_step_mqtt_options(user_input)
        return await self.async_step_websocket_options(user_input)

    async def _async_step_transport_options(  # noqa: PLR0913
        self,
        user_input: dict[str, Any] | None,
        transport: TransportType,
        schema: dict | Callable[[Printer], dict],
        suggested_values: Callable[[dict[str, Any]], dict[str, Any]],
        process_input: Callable[
            [dict[str, Any], Printer, dict[str, str]],
            Awaitable[config_entries.ConfigFlowResult | None],
        ],
        description_placeholders: (
            dict[str, Any] | Callable[[Printer], dict[str, Any]] | None
        ) = None,
    ) -> config_entries.ConfigFlowResult:
        """
        Shared skeleton for the transport-specific options steps.

        Flow: ``current_settings`` merge → ``Printer.from_dict`` →
        transport-specific input processing (``process_input``) →
        ``async_create_entry`` or ``async_show_form``.

        Arguments:
            user_input: The user input data.
            transport: The transport-type discriminator (maps to the step id).
            schema: The raw form schema, or a callable that receives the
                constructed printer.
            suggested_values: Builds the ``suggested_values`` for the final
                form from ``current_settings``.
            process_input: The transport-specific input processing (including
                the network checks). Returns the early form show or
                ``async_create_entry`` result, or ``None`` to fall through
                to the final form show.
            description_placeholders: Optional description placeholders, or a
                callable that receives the constructed printer.

        Returns:
            The result of the configuration flow step.

        """
        _errors: dict[str, str] = {}
        current_settings = {
            **(self.config_entry.data or {}),
            **(self.config_entry.options or {}),
        }
        printer = Printer.from_dict(current_settings)

        if user_input is not None:
            result = await process_input(user_input, printer, _errors)
            if result is not None:
                return result

        # A failed websocket connection test (process_input returned None)
        # falls through and re-shows the form.
        if callable(schema):
            schema = schema(printer)
        if callable(description_placeholders):
            description_placeholders = description_placeholders(printer)

        show_form_kwargs: dict[str, Any] = {}
        if description_placeholders is not None:
            show_form_kwargs["description_placeholders"] = description_placeholders
        return self.async_show_form(
            step_id=_OPTIONS_STEP_IDS[transport],
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema),
                suggested_values=suggested_values(current_settings),
            ),
            errors=_errors,
            **show_form_kwargs,
        )

    async def _async_validate_gcode_proxy(
        self, proxy_raw: str | None
    ) -> tuple[str | None, str | None]:
        """
        Normalize and health-check a gcode proxy URL.

        Returns (normalized_url, error_key); both None when the field is blank.
        """
        proxy_raw = (proxy_raw or "").strip()
        if not proxy_raw:
            return None, None
        proxy_url = _normalize_gcode_proxy_base_url(proxy_raw)
        if proxy_url is None:
            return None, "gcode_proxy_invalid"
        session = async_get_clientsession(self.hass)
        proxy_client = GCodeProxyClient(proxy_url, session)
        if not await proxy_client.check_health():
            return None, "gcode_proxy_unreachable"
        return proxy_url, None

    async def async_step_cc2_options(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle options for CC2 printers."""

        async def _process_cc2_options_input(
            user_input: dict[str, Any],
            printer: Printer,
            _errors: dict[str, str],
        ) -> config_entries.ConfigFlowResult | None:
            typed_ip = user_input.get(CONF_IP_ADDRESS)
            if typed_ip is not None and not typed_ip.strip():
                # A cleared address is a typo, not a switched-off printer. This
                # step saves without any connection test, so without this refusal
                # the form persists ``ip_address: ""`` -- an entry that can never
                # connect (#171 review).
                _errors[CONF_IP_ADDRESS] = "manual_ip_no_valid_ip"
                return self.async_show_form(
                    step_id="cc2_options",
                    data_schema=self.add_suggested_values_to_schema(
                        vol.Schema(self._cc2_options_schema()),
                        suggested_values=user_input,
                    ),
                    errors=_errors,
                )

            printer.ip_address = user_input.get(CONF_IP_ADDRESS, printer.ip_address)
            printer_data = printer.to_dict()
            access_code = user_input.get(CONF_CC2_ACCESS_CODE)
            if access_code:
                printer_data[CONF_CC2_ACCESS_CODE] = access_code

            proxy_url, proxy_error = await self._async_validate_gcode_proxy(
                user_input.get(CONF_GCODE_PROXY_URL)
            )
            if proxy_error:
                _errors[CONF_GCODE_PROXY_URL] = proxy_error
                return self.async_show_form(
                    step_id="cc2_options",
                    data_schema=self.add_suggested_values_to_schema(
                        vol.Schema(self._cc2_options_schema()),
                        suggested_values=user_input,
                    ),
                    errors=_errors,
                )
            if proxy_url:
                printer_data[CONF_GCODE_PROXY_URL] = proxy_url
            else:
                printer_data.pop(CONF_GCODE_PROXY_URL, None)

            # Persist proxy_host unconditionally, *including* the empty string.
            # The options flow merges data-first ({**entry.data, **entry.options}),
            # so a key that only exists in entry.data cannot be removed here --
            # only an empty-string override actually disables the proxy.
            printer_data[CONF_PROXY_HOST] = (
                user_input.get(CONF_PROXY_HOST) or ""
            ).strip()

            printer_data[CONF_ALLOW_PRINTER_OFFLINE] = bool(
                user_input.get(CONF_ALLOW_PRINTER_OFFLINE, False)
            )

            return self.async_create_entry(
                title=printer.name,
                data=printer_data,
            )

        return await self._async_step_transport_options(
            user_input,
            TransportType.CC2_MQTT,
            schema=self._cc2_options_schema(),
            suggested_values=self._suggested_with_normalized_proxy,
            process_input=_process_cc2_options_input,
        )

    @staticmethod
    def _cc2_options_schema() -> dict:
        return {
            vol.Required(CONF_IP_ADDRESS): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
            ),
            vol.Optional(CONF_CC2_ACCESS_CODE): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD),
            ),
            vol.Optional(CONF_GCODE_PROXY_URL, default=""): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
            ),
            vol.Optional(CONF_PROXY_HOST, default=""): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
            ),
            vol.Required(
                CONF_ALLOW_PRINTER_OFFLINE, default=False
            ): selector.BooleanSelector(selector.BooleanSelectorConfig()),
        }

    @staticmethod
    def _suggested_with_normalized_proxy(settings: dict) -> dict:
        suggested = dict(settings)
        proxy_url = (suggested.get(CONF_GCODE_PROXY_URL) or "").strip()
        if proxy_url:
            normalized = _normalize_gcode_proxy_base_url(proxy_url)
            suggested[CONF_GCODE_PROXY_URL] = (
                normalized if normalized is not None else proxy_url
            )
        else:
            suggested[CONF_GCODE_PROXY_URL] = ""
        return suggested

    async def async_step_mqtt_options(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle options for MQTT printers (embedded broker)."""
        data_schema = {
            vol.Required(CONF_IP_ADDRESS): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
            ),
            vol.Optional(CONF_EXTERNAL_IP): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
            ),
            vol.Optional(CONF_MQTT_EXTERNAL_HOST): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
            ),
            vol.Optional(CONF_MQTT_EXTERNAL_PORT): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
            ),
            vol.Required(
                CONF_ALLOW_PRINTER_OFFLINE, default=False
            ): selector.BooleanSelector(selector.BooleanSelectorConfig()),
        }

        async def _process_mqtt_options_input(
            user_input: dict[str, Any],
            printer: Printer,
            _errors: dict[str, str],
        ) -> config_entries.ConfigFlowResult | None:
            typed_ip = user_input.get(CONF_IP_ADDRESS)
            if typed_ip is not None and not typed_ip.strip():
                # A cleared address is a typo, not a switched-off printer. This
                # step saves without any connection test, so without this refusal
                # the form persists ``ip_address: ""`` -- an entry that can never
                # connect (#171 review).
                _errors[CONF_IP_ADDRESS] = "manual_ip_no_valid_ip"
                return self.async_show_form(
                    step_id="mqtt_options",
                    data_schema=self.add_suggested_values_to_schema(
                        vol.Schema(data_schema),
                        suggested_values=user_input,
                    ),
                    errors=_errors,
                )

            printer.ip_address = user_input.get(CONF_IP_ADDRESS, printer.ip_address)
            printer.external_ip = user_input.get(CONF_EXTERNAL_IP)
            printer.mqtt_external_host = user_input.get(CONF_MQTT_EXTERNAL_HOST)

            port_raw = (user_input.get(CONF_MQTT_EXTERNAL_PORT) or "").strip()
            if port_raw:
                if not port_raw.isdigit() or not (
                    1 <= int(port_raw) <= 65535  # noqa: PLR2004
                ):
                    _errors[CONF_MQTT_EXTERNAL_PORT] = "mqtt_external_port_invalid"
                    return self.async_show_form(
                        step_id="mqtt_options",
                        data_schema=self.add_suggested_values_to_schema(
                            vol.Schema(data_schema),
                            suggested_values=user_input,
                        ),
                        errors=_errors,
                    )
                printer.mqtt_external_port = port_raw
            else:
                printer.mqtt_external_port = None

            printer_data = printer.to_dict()
            printer_data[CONF_ALLOW_PRINTER_OFFLINE] = bool(
                user_input.get(CONF_ALLOW_PRINTER_OFFLINE, False)
            )

            return self.async_create_entry(
                title=printer.name,
                data=printer_data,
            )

        return await self._async_step_transport_options(
            user_input,
            TransportType.MQTT,
            schema=data_schema,
            suggested_values=lambda settings: settings,
            process_input=_process_mqtt_options_input,
        )

    async def async_step_websocket_options(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle options for WebSocket/SDCP printers."""
        LOGGER.debug("data: %s", self.config_entry.data)
        LOGGER.debug("options: %s", self.config_entry.options)

        def _websocket_description_placeholders(
            printer: Printer,
        ) -> dict[str, Any]:
            return {"printer_model": printer.model or printer.name or "Printer"}

        def _websocket_options_schema_for(printer: Printer) -> dict:
            # Resin printers have no Canvas unit and nothing for the gcode proxy
            # to capture, so those fields are FDM-only.
            is_fdm = printer.printer_type != PrinterType.RESIN
            return self._websocket_options_schema(is_fdm=is_fdm)

        async def _process_websocket_options_input(
            user_input: dict[str, Any],
            printer: Printer,
            _errors: dict[str, str],
        ) -> config_entries.ConfigFlowResult | None:
            if not (user_input.get(CONF_IP_ADDRESS) or "").strip():
                # A blank address is a typo, not a switched-off printer, so this
                # refusal is independent of the offline option: the offline save
                # below would otherwise persist ``ip_address: ""``, an entry that
                # can never connect and that -- per the documented trade-off of
                # the option -- then fails silently.
                _errors[CONF_IP_ADDRESS] = "manual_ip_no_valid_ip"
                return self.async_show_form(
                    step_id="websocket_options",
                    data_schema=self.add_suggested_values_to_schema(
                        vol.Schema(_websocket_options_schema_for(printer)),
                        suggested_values=user_input,
                    ),
                    errors=_errors,
                    description_placeholders=_websocket_description_placeholders(
                        printer
                    ),
                )

            printer.ip_address = user_input[CONF_IP_ADDRESS]
            proxy_url, proxy_error = await self._async_validate_gcode_proxy(
                user_input.get(CONF_GCODE_PROXY_URL)
            )
            if proxy_error:
                _errors[CONF_GCODE_PROXY_URL] = proxy_error
                return self.async_show_form(
                    step_id="websocket_options",
                    data_schema=self.add_suggested_values_to_schema(
                        vol.Schema(_websocket_options_schema_for(printer)),
                        suggested_values=user_input,
                    ),
                    errors=_errors,
                    description_placeholders=_websocket_description_placeholders(
                        printer
                    ),
                )

            if not user_input[CONF_PROXY_ENABLED]:
                printer.proxy_websocket_port = None
                printer.proxy_video_port = None

            allow_offline = bool(user_input.get(CONF_ALLOW_PRINTER_OFFLINE, False))
            # ``allow_offline`` picks the log level, never the outcome. For an
            # entry that opted into offline tolerance a powered-off printer is an
            # *expected* condition, not an error (#171): the failed connection
            # test that precedes the offline save is recorded at DEBUG (and
            # without the OSError traceback) so the log does not spam exactly the
            # user the option exists for. Without the option the failure is a
            # real problem, so it keeps being logged as an error.
            log_failure = LOGGER.debug if allow_offline else LOGGER.error
            log_failure_with_traceback = (
                LOGGER.debug if allow_offline else LOGGER.exception
            )

            try:
                tested_printer = await _async_test_connection(
                    self.hass, printer, user_input
                )
                tested_printer.proxy_enabled = user_input[CONF_PROXY_ENABLED]
                tested_printer.has_canvas = user_input.get(CONF_HAS_CANVAS, False)
                tested_printer.external_ip = user_input.get(CONF_EXTERNAL_IP)
                LOGGER.debug("Tested printer: %s", tested_printer.to_dict_safe())
                printer_data = tested_printer.to_dict()
                if proxy_url:
                    printer_data[CONF_GCODE_PROXY_URL] = proxy_url
                else:
                    printer_data.pop(CONF_GCODE_PROXY_URL, None)
                printer_data[CONF_ALLOW_PRINTER_OFFLINE] = allow_offline
                return self.async_create_entry(
                    title=tested_printer.name,
                    data=printer_data,
                )
            except ElegooConfigFlowConnectionError as exception:
                log_failure("Connection error: %s", exception)
                _errors["base"] = "connection"
            except ElegooConfigFlowGeneralError as exception:
                log_failure("No printer found: %s", exception)
                _errors["base"] = "init_no_printer_found"
            except PlatformNotReady as exception:
                log_failure("%s", exception)
                _errors["base"] = "connection"
            except OSError as exception:
                log_failure_with_traceback("%s", exception)
                _errors["base"] = "unknown"

            # Reaching here means the connection test raised: every except
            # branch above recorded an error and fell through.
            if allow_offline:
                return self._offline_options_result(printer, user_input, proxy_url)
            # A failed connection test re-shows the form instead.
            return None

        return await self._async_step_transport_options(
            user_input,
            TransportType.WEBSOCKET,
            schema=_websocket_options_schema_for,
            suggested_values=self._suggested_with_normalized_proxy,
            process_input=_process_websocket_options_input,
            description_placeholders=_websocket_description_placeholders,
        )

    def _offline_options_result(
        self,
        printer: Printer,
        user_input: dict[str, Any],
        proxy_url: str | None,
    ) -> config_entries.ConfigFlowResult:
        """
        Save WebSocket options after a failed connection test (#171).

        The printer is switched off, so it cannot be re-queried: the identity
        the tested printer would have supplied (id, model, name, firmware,
        connection) is taken from the stored settings the ``printer`` argument
        was built from, and only the values the user typed are applied.

        Arguments:
            printer: The printer built from the stored (data + options) settings.
            user_input: The submitted form values.
            proxy_url: The already health-checked gcode proxy URL, if any.

        Returns:
            The ``async_create_entry`` result saving the options.

        """
        # ``ip_address`` was already applied by the caller; ``proxy_enabled`` is
        # re-applied here rather than trusted from ``_async_test_connection``,
        # which may have raised before it got as far as setting it.
        printer.proxy_enabled = user_input[CONF_PROXY_ENABLED]
        printer.has_canvas = user_input.get(CONF_HAS_CANVAS, False)
        printer.external_ip = user_input.get(CONF_EXTERNAL_IP)
        printer_data = printer.to_dict()
        if proxy_url:
            printer_data[CONF_GCODE_PROXY_URL] = proxy_url
        else:
            printer_data.pop(CONF_GCODE_PROXY_URL, None)
        printer_data[CONF_ALLOW_PRINTER_OFFLINE] = bool(
            user_input.get(CONF_ALLOW_PRINTER_OFFLINE, False)
        )
        return self.async_create_entry(
            title=printer.name,
            data=printer_data,
        )

    @staticmethod
    def _websocket_options_schema(*, is_fdm: bool) -> dict:
        """Build the WebSocket/SDCP options schema; Canvas fields are FDM-only."""
        schema: dict = {
            # Both keep ``required: True`` in the rendered form; the defaults only
            # mean a partial (non-UI) validation pass has something to fall back to.
            vol.Required(CONF_IP_ADDRESS, default=""): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
            ),
            vol.Required(CONF_PROXY_ENABLED, default=False): selector.BooleanSelector(
                selector.BooleanSelectorConfig(),
            ),
        }
        if is_fdm:
            schema[vol.Required(CONF_HAS_CANVAS, default=False)] = (
                selector.BooleanSelector(selector.BooleanSelectorConfig())
            )
            schema[vol.Optional(CONF_GCODE_PROXY_URL, default="")] = (
                selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
                )
            )
        schema[vol.Optional(CONF_EXTERNAL_IP)] = selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
        )
        schema[vol.Required(CONF_ALLOW_PRINTER_OFFLINE, default=False)] = (
            selector.BooleanSelector(selector.BooleanSelectorConfig())
        )
        return schema
