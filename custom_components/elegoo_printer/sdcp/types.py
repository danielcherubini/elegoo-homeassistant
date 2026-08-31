"""
Wire-level TypedDicts for SDCP responses (received payloads only).

Not exhaustive: only the fields actually read from the wire today are
declared.  `total=False` models the firmware's inconsistent payload
shapes (missing keys and key variants are normal).  These are TYPES
ONLY — decoding stays in `sdcp.models`, and wire keys stay
wire-faithful, including the firmware's typos (`CurrenCoord`,
`PlatFormType`).

To extend: add keys here as they are read, never ahead of the model.
"""

from __future__ import annotations

from typing import Any, TypedDict


class SDCPFrame(TypedDict, total=False):
    """A response frame: `{Id, Data, Topic}`."""

    Id: int
    Topic: str
    Data: dict[str, Any]


class SDCPStatusWrapper(TypedDict, total=False):
    """The wrapper SDCP wraps the status payload in (`{Data: {...}}`)."""

    Data: dict[str, Any]
    # `_status_payload_extract` also treats a top-level
    # `{'Status': {...}}` frame as a status container.
    Status: dict[str, Any]


class SDCPStatusPayload(TypedDict, total=False):
    """The status fields actually read by `PrinterStatus` (sparse)."""

    MachineStatus: Any
    MachineStatusReason: Any
    PrintStatus: Any
    CurrenCoord: list[Any]
    EstimatedTime: float
    CurrentProgress: float
    LastUserPlacementNozzlePreheat: Any
    LastUserPlacementBedPreheat: Any
    CurrentNozzleTemperature: float
    CurrentBedTemperature: float
    AxisZHeight: float
    AvgFilamentSpeed: float
    PrintUnit: Any
    PlatFormType: Any  # firmware key variant (kept as-is on purpose)
    TotalTicks: Any
    TotalSeconds: Any
    Anchors: Any
    ReportedErrorTotal: Any
    ReportedError: Any
    TaskId: Any
    RemainingTime: Any
    # Flat/nested container read by `PrinterStatus.__init__`
    # (`status = data.get("Status", data)`).
    Status: Any
    CurrentStatus: Any
    PreviousStatus: int
    PrintScreen: int
    ReleaseFilm: int
    TimeLapseStatus: int
    TempOfUVLED: float
    TempOfBox: float
    TempTargetBox: float
    TempOfHotbed: float
    TempOfNozzle: float
    TempTargetHotbed: float
    TempTargetNozzle: float
    TempOfTank: float
    TempTargetTank: float
    HeatStatus: int
    ZOffset: float
    CurrentFanSpeed: dict[str, Any]
    # The nested `PrintInfo` object read by `PrinterStatus.__init__`
    # (its own sparse shape is owned by the SDCP print-info middleware,
    # not this tranche).
    PrintInfo: dict[str, Any]


class SDCPElegooVideoFrame(TypedDict, total=False):
    """An `ElegooVideo` frame (video stream status)."""

    FileName: str
    FileURL: str
    # Keys `ElegooVideo.__init__` actually reads.
    Ack: int
    VideoUrl: str


class SDCPElegooVideoUrlFrame(TypedDict, total=False):
    """A video-file URL response frame."""

    FileURL: str


class SDCPAMSStatusFrame(TypedDict, total=False):
    """An `AMSStatus` frame (sparse; nested box/tray shapes stay `Any`)."""

    BoxCount: int
    TrayCount: int
    UpdateTime: Any
    # Keys the ws `_canvas_handler` / `AMSStatus.__init__` actually read.
    Ack: Any
    canvas_list: list[Any]
    active_canvas_id: Any
    active_tray_id: Any
    auto_refill: Any


type SDCPStatusMessage = SDCPStatusWrapper | SDCPStatusPayload
"""The ws status handler decodes the full frame; the mqtt status handler
decodes the inner payload."""


class SDPPrintHistoryMessage(TypedDict, total=False):
    """A `print_history` frame (key -> sparse task-summary dicts)."""

    PrintHistory: dict[str, Any]
    # The shared handler body (`sdcp.transport.base`) reads the
    # `HistoryData` key (a list of task ids).
    HistoryData: list[Any]


class SDPPrintHistoryDetailFrame(TypedDict, total=False):
    """A `PrintHistoryDetail` frame (sparse; nested shape stays `Any`)."""

    PrintInfo: dict[str, Any]
    SliceInformation: dict[str, Any]
    # The shared handler body reads `HistoryDetailList` (a list of
    # per-task dicts).
    HistoryDetailList: list[dict[str, Any]]
