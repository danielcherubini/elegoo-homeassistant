"""
Wire-level TypedDicts for CC2 (Centauri Carbon 2) responses.

Not exhaustive: only the fields actually read from the wire today are
declared.  `total=False` models the firmware's inconsistent payload
shapes (missing keys are normal).  These are TYPES ONLY — decoding
stays in the client / mapper code.  No cross-file imports: the CC2
payload types stand alone.

To extend: add keys here as they are read, never ahead of the reader.
"""

from __future__ import annotations

from typing import Any, TypedDict


class CC2Envelope(TypedDict, total=False):
    """An incoming CC2 MQTT envelope (request / event / register)."""

    # The client sends `method` and `id` as ints in outbound payloads;
    # int | str stays wire-faithful to both directions.
    id: int | str
    method: int | str
    params: dict[str, Any] | None
    result: Any
    # Heartbeat envelopes use a `type` key (PING / PONG).
    type: str
    # Register responses use an `error` code string.
    error: str


class CC2FileThumbnailResponse(TypedDict, total=False):
    """A file-thumbnail response result (sparse)."""

    thumbnail: str


class CC2VideoResponse(TypedDict, total=False):
    """A video-stream response result (sparse)."""

    error_code: int
    video_url: str


class CC2StatusFrame(TypedDict, total=False):
    """
    A CC2 status payload (full or delta) (sparse).

    Keys match the actual reads across `cc2.client` and
    `cc2.models.CC2StatusMapper`.
    """

    sequence: int
    machine_status: dict[str, Any]
    print_status: dict[str, Any]
    extruder: dict[str, Any]
    heater_bed: dict[str, Any]
    ztemperature_sensor: dict[str, Any]
    fans: dict[str, Any]
    led: dict[str, Any]
    gcode_move_inf: dict[str, Any]
    # Fallback position key when `gcode_move_inf` is absent.
    gcode_move: dict[str, Any]
    z_offset: float
    error_code: int


class CC2StatusResult(TypedDict, total=False):
    """`CC2StatusFrame` as wrapped in a result block."""

    status: CC2StatusFrame


class CC2Response(TypedDict, total=False):
    """A CC2 command response envelope (sparse)."""

    Id: int
    Error: int
    Msg: str
    Data: Any


class CC2CanvasStatus(TypedDict, total=False):
    """A Canvas/AMS status response result (sparse)."""

    canvas_info: dict[str, Any]


class CC2Attributes(TypedDict, total=False):
    """
    A CC2 attributes response result (sparse).

    The keys `CC2StatusMapper.map_attributes` reads.
    """

    software_version: dict[str, Any]
    hostname: str
    machine_model: str
    resolution: str
    xyz_size: str
    ip: str
    sn: str
    video_connections: int
    max_video_connections: int
    network_type: str
    mac: str
    # Truthiness-checked, not type-checked, by `map_attributes`.
    usb_connected: Any
    camera_connected: Any
    remaining_memory: int


class CC2OledLCD(TypedDict, total=False):
    """
    The `ElegooOledClientSet` 2-element list stays `Any`-ish.

    The items are `{ElegooOledClient, Text}` dicts.
    """

    ElegooOledClientSet: list[Any]


# Sparse device-strings dict (device-name keyed).
CC2ControlStrategy = dict[str, str]


class CC2MaterialEntry(TypedDict, total=False):
    """One entry of `ElegooMaterialList`."""

    ElegooMaterial: Any
    platen: Any


# Sparse `ElegooMaterialList`: a list of dicts with
# `ElegooMaterial` / `platen` keys.
CC2FilamentList = list[CC2MaterialEntry]

# Sparse tray dict.
CC2FilamentStatus = dict[str, Any]


class CC2StatusInner(TypedDict, total=False):
    """
    A sparse `ElegooStatus` dict.

    The `XYZ` / `HomeStatus` / `NozzlePosition` / `ThermalStatus` shapes stay
    `Any`-ish.
    """

    HomeStatus: Any
    NozzlePosition: Any
    ThermalStatus: Any


class CC2FilamentData(TypedDict, total=False):
    """The `ElegooAMS` dict (sparse; inner shape stays `Any`)."""

    ElegooAMS: dict[str, Any]
