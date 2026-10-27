"""
CC2 Test Printer Simulator.

This simulates a Centauri Carbon 2 printer that runs its own MQTT broker.
The CC2 uses an inverted architecture where:
- The printer runs the MQTT broker (on port 1883)
- Home Assistant connects TO the printer
- Clients must register before sending commands
- Uses heartbeat/ping-pong mechanism
- Sends delta status updates (method 6000)

This simulator accurately replicates the real CC2 protocol based on
analysis of the elegoo-link library.

Run it with:

    VIRTUAL_ENV=.venv uv run python scripts/test_cc2_printer.py [--idle] [--upload-port N]

Switches that exist for the 1047 delete probe (``scripts/cc2_probe_delete.py``,
the script the next task adds to this directory)
-------------------------------------------------------------------------------

``--idle``
    Boot the printer IDLE with no file loaded. Without it the simulator boots
    mid-print (``STATUS_PRINTING`` / ``test_benchy.gcode``), which is what other
    tooling expects, but it also makes the probe's idle gate refuse to run — the
    probe refuses to delete anything from a printer that is printing. Pass
    ``--idle`` whenever you are validating the probe.

``DELETE_PARAM_KEY`` (edit the module constant below, then restart)
    Which parameter key the 1047 branch accepts. It is ``"filename"`` by
    default, because that is the shape the client's ``delete_file`` sent before
    it was removed for #432. Flip it to
    ``"file_name"`` — the spelling 1045 works with — and restart, and the
    simulator's answers SWAP: what was ``error_code: 0`` becomes
    ``error_code: 1003`` and vice versa. That flip is how you prove the probe
    keys off the parameter shape rather than guessing, so run the probe once
    with each value and compare the two tables.

``--upload-port`` (default 8080)
    Port for the HTTP ``PUT /upload`` endpoint, which the probe uses to create
    its own disposable target files. The real printer serves this on port 80,
    which needs root, so the simulator binds an unprivileged one; point the
    probe at it with ``--upload-port``. Uploaded files land in ``printer_files``
    and therefore show up in the next 1044 listing.
"""

import argparse
import asyncio
import hashlib
import json
import random
import signal
import socket
import time
import uuid
from contextlib import suppress
from copy import deepcopy

import aiomqtt
from aiohttp import web
from amqtt.broker import Broker

# =============================================================================
# PRINTER CONFIGURATION
# =============================================================================

SERIAL_NUMBER = "CC2TEST1234567890"
PRINTER_IP = "127.0.0.1"
PRINTER_NAME = "Centauri Carbon 2 Test"
PRINTER_MODEL = "Centauri Carbon 2"
FIRMWARE_VERSION = "1.0.5.2"

# Network settings
UDP_DISCOVERY_PORT = 52700
DISCOVERY_HOST = "0.0.0.0"
BROKER_HOST = "0.0.0.0"
BIND_HOST = "0.0.0.0"
MQTT_PORT = 1883
MQTT_USERNAME = "elegoo"
MQTT_PASSWORD = "123456"  # noqa: S105

# =============================================================================
# CC2 PROTOCOL CONSTANTS (from elegoo-link COMMAND_MAPPING_TABLE)
# =============================================================================

# Command IDs (requests from client)
CC2_CMD_GET_ATTRIBUTES = 1001
CC2_CMD_GET_STATUS = 1002
CC2_CMD_START_PRINT = 1020
CC2_CMD_PAUSE_PRINT = 1021
CC2_CMD_STOP_PRINT = 1022
CC2_CMD_RESUME_PRINT = 1023
CC2_CMD_HOME_AXES = 1026
CC2_CMD_MOVE_AXES = 1027
CC2_CMD_SET_TEMPERATURE = 1028
CC2_CMD_SET_LIGHT = 1029
CC2_CMD_SET_FAN_SPEED = 1030
CC2_CMD_SET_PRINT_SPEED = 1031
CC2_CMD_PRINT_TASK_LIST = 1036
CC2_CMD_PRINT_TASK_DETAIL = 1037
CC2_CMD_DELETE_PRINT_TASK = 1038
CC2_CMD_VIDEO_STREAM = 1042
CC2_CMD_UPDATE_PRINTER_NAME = 1043
CC2_CMD_GET_FILE_LIST = 1044
CC2_CMD_GET_FILE_DETAIL = 1046
CC2_CMD_DELETE_FILE = 1047
CC2_CMD_GET_DISK_INFO = 1048
CC2_CMD_SET_PRINTER_DOWNLOAD_FILE = 1057  # tells printer to download FROM a URL
CC2_CMD_CANCEL_PRINTER_DOWNLOAD = 1058

# Canvas/AMS commands
CC2_CMD_SET_AUTO_REFILL = 2004
CC2_CMD_GET_CANVAS_STATUS = 2005

# Error codes the file branch answers with (cc2/const.py)
CC2_ERROR_SUCCESS = 0
CC2_ERROR_INVALID_PARAMETER = 1003
CC2_ERROR_FILE_DELETE_FAILED = 1007
CC2_ERROR_MD5_FAILED = 9004
CC2_ERROR_TOKEN_FAILED = 1000
CC2_ERROR_FILE_WRITE_FAILED = 1004

# Event IDs (push notifications from printer)
CC2_EVENT_STATUS = 6000
CC2_EVENT_ATTRIBUTES = 6008

# Machine status codes
STATUS_INITIALIZING = 0
STATUS_IDLE = 1
STATUS_PRINTING = 2
STATUS_FILAMENT_OPERATING = 3
STATUS_FILAMENT_OPERATING_2 = 4
STATUS_AUTO_LEVELING = 5
STATUS_PID_CALIBRATING = 6
STATUS_RESONANCE_TESTING = 7
STATUS_SELF_CHECKING = 8
STATUS_UPDATING = 9
STATUS_HOMING = 10
STATUS_FILE_TRANSFERRING = 11
STATUS_VIDEO_COMPOSING = 12
STATUS_EXTRUDER_OPERATING = 13
STATUS_EMERGENCY_STOP = 14
STATUS_POWER_LOSS_RECOVERY = 15

# Print sub-status codes (from elegoo-link elegoo_fdm_cc2_message_adapter.cpp)
SUB_STATUS_NONE = 0
SUB_STATUS_EXTRUDER_PREHEATING = 1045
SUB_STATUS_EXTRUDER_PREHEATING_2 = 1096
SUB_STATUS_BED_PREHEATING = 1405
SUB_STATUS_BED_PREHEATING_2 = 1906
SUB_STATUS_PRINTING = 2075
SUB_STATUS_PRINTING_COMPLETED = 2077
SUB_STATUS_PAUSING = 2501
SUB_STATUS_PAUSED = 2502
SUB_STATUS_PAUSED_2 = 2505
SUB_STATUS_RESUMING = 2401
SUB_STATUS_RESUMING_COMPLETED = 2402
SUB_STATUS_STOPPING = 2503
SUB_STATUS_STOPPED = 2504
SUB_STATUS_HOMING = 2801
SUB_STATUS_HOMING_COMPLETED = 2802
SUB_STATUS_HOMING_FAILED = 2803
SUB_STATUS_AUTO_LEVELING = 2901
SUB_STATUS_AUTO_LEVELING_COMPLETED = 2902

# Filament operating sub-status codes (status=3,4)
SUB_STATUS_FILAMENT_LOADING = 1133
SUB_STATUS_FILAMENT_LOADING_COMPLETED = 1136
SUB_STATUS_FILAMENT_UNLOADING = 1144
SUB_STATUS_FILAMENT_UNLOADING_COMPLETED = 1145

# Extruder operating sub-status codes (status=13)
SUB_STATUS_EXTRUDER_LOADING = 1061
SUB_STATUS_EXTRUDER_UNLOADING = 1062
SUB_STATUS_EXTRUDER_LOADING_COMPLETED = 1063
SUB_STATUS_EXTRUDER_UNLOADING_COMPLETED = 1064

# File transfer sub-status codes (status=11)
SUB_STATUS_UPLOADING_FILE = 3000
SUB_STATUS_UPLOADING_FILE_COMPLETED = 3001

# Speed modes
SPEED_MODE_SILENT = 0
SPEED_MODE_BALANCED = 1
SPEED_MODE_SPORT = 2
SPEED_MODE_LUDICROUS = 3

# =============================================================================
# PRINTER STATE
# =============================================================================

# Registered clients
registered_clients: dict[str, str] = {}

# Delta status sequence counter
status_sequence_id = 0

# Full printer status (matches real CC2 structure from debug output)
# Start in printing state like the MQTT test printer
printer_status = {
    "error_code": 0,
    "external_device": {
        "camera": True,
        "type": "0303",
        "u_disk": False,
    },
    "extruder": {
        "filament_detect_enable": 1,
        "filament_detected": 1,
        "target": 220,
        "temperature": 215.0,
    },
    "fans": {
        "aux_fan": {"speed": 178.0},  # ~70%
        "box_fan": {"speed": 25.5},   # ~10%
        "controller_fan": {"speed": 255.0},  # 100%
        "fan": {"speed": 255.0},      # 100%
        "heater_fan": {"speed": 255.0},  # 100%
    },
    "gcode_move_inf": {
        "e": 138.87,
        "speed": 9019,
        "speed_mode": SPEED_MODE_BALANCED,
        "x": 88.148,
        "y": 139.946,
        "z": 1.6,
    },
    "heater_bed": {
        "target": 60,
        "temperature": 58.0,
    },
    "led": {
        "status": 1,  # 1 = on, 0 = off
    },
    "machine_status": {
        "exception_status": [],
        "progress": 20,
        "status": STATUS_PRINTING,
        "sub_status": SUB_STATUS_PRINTING,
        "sub_status_reason_code": 0,
    },
    "print_status": {
        "bed_mesh_detect": True,
        "current_layer": 100,
        "enable": True,
        "filament_detect": False,
        "filename": "test_benchy.gcode",
        "print_duration": 1440,  # seconds elapsed
        "remaining_time_sec": 5760,  # seconds remaining
        "state": "printing",
        "total_duration": 7200,  # total seconds
        "total_layer": 500,
        "uuid": "b52af24c-764e-4092-8a50-00e5f8f02b46",
    },
    "tool_head": {
        "homed_axes": "xyz",
    },
    "ztemperature_sensor": {
        "measured_max_temperature": 0,
        "measured_min_temperature": 0,
        "temperature": 33.0,
    },
}

# Printer attributes (matches real CC2 structure from debug output)
printer_attributes = {
    "error_code": 0,
    "hardware_version": "",
    "hostname": PRINTER_NAME,
    "ip": PRINTER_IP,
    "machine_model": PRINTER_MODEL,
    "protocol_version": "1.0.0",
    "sn": SERIAL_NUMBER,
    "software_version": {
        "mcu_version": "00.00.00.00",
        "ota_version": FIRMWARE_VERSION,
        "soc_version": "",
    },
}

# =============================================================================
# FILE LIST STATE (methods 1044 / 1047) and the HTTP upload endpoint
# =============================================================================

# Which parameter key method 1047 accepts. See the module docstring: flip this
# to "file_name" and restart to prove a probe keys off the parameter shape.
DELETE_PARAM_KEY = "filename"

# Port for the HTTP PUT /upload endpoint. The real printer serves it on 80,
# which this simulator cannot bind without root; override with --upload-port.
UPLOAD_PORT = 8080

# Ceiling on how many bytes one uploaded file may total, so a bogus Content-Range
# total cannot make the simulator buffer without limit.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

# Ceilings on the IN-FLIGHT uploads together, not just on one file.
# MAX_UPLOAD_BYTES caps a single file; nothing capped the number of half-finished
# files, so a contributor (or a probe run interrupted eleven times) could upload
# `cc2_probe_a.gcode` chunk 0, abandon it, repeat under a fresh name, and leave
# every one of those buffers resident for the life of the simulator. A
# long-running simulator under a looping probe would grow without bound.
MAX_PENDING_UPLOADS = 16
MAX_PENDING_UPLOAD_BYTES = 32 * 1024 * 1024
# An upload that has not seen a chunk in this many seconds is abandoned: the
# real client sends its chunks back to back, so a gap this long means the sender
# is gone (Ctrl-C, a refused chunk, a crashed run) and its buffer is worth
# nothing but the RAM it is holding.
PENDING_UPLOAD_IDLE_TIMEOUT = 300.0


def seed_file_list():
    """
    Build the fresh file list the printer reports from method 1044.

    Seeded on every start so repeated probe runs see the same files. Entries
    carry the printer's own field names, measured on fw 02.01.00.00 (see
    ``cc2/types.py`` ``CC2FileEntry``). The fields must stay inside what the
    client's entry parser accepts: a ``filename`` that is not a non-empty ``str``
    is DROPPED AS UNREADABLE, and any dropped entry makes the strict
    ``get_verified_file_list`` raise, which would leave the probe with no way to
    confirm a delete. So every entry here is a plain ``str`` name.
    ``type`` is present as ``"file"`` on two entries and absent on the rest,
    which is what the measured firmware sends; a ``type`` of ``"dir"`` is the
    one value the client deliberately skips rather than drops, so one entry
    carries it to keep that path exercised.
    """
    return [
        {
            "filename": "test_benchy.gcode",
            "size": 1234567,
            "create_time": 1706900000,
            "layer": 251,
            "print_time": 5137,
            "total_filament_used": 34.92,
            "color_map": [{"t": 0, "color": "#7F7E83", "name": "PLA"}],
            "type": "file",
        },
        {
            "filename": "CC2_temperature_tower.gcode",
            "size": 892341,
            "create_time": 1707000000,
            "layer": 180,
            "print_time": 3020,
            "total_filament_used": 21.5,
            "color_map": [{"t": 0, "color": "#2850DF", "name": "PLA"}],
        },
        {
            # create_time 0 is the never-set-clock case: PrinterFile maps it to
            # created=None, so an entry is still perfectly readable with it.
            "filename": "calibration_cube.gcode",
            "size": 45678,
            "create_time": 0,
            "layer": 40,
            "print_time": 600,
            "total_filament_used": 3.75,
            "color_map": [
                {"t": 0, "color": "#F32FF8", "name": "PLA Silk"},
                {"t": 3, "color": "#000000", "name": "PLA"},
            ],
        },
        {
            "filename": "multi_tool_logo.gcode",
            "size": 2048000,
            "create_time": 1707100000,
            "layer": 320,
            "print_time": 8000,
            "total_filament_used": 62.0,
            "color_map": [
                {"t": 0, "color": "#FFFFFF", "name": "PLA Basic"},
                {"t": 1, "color": "#00FF00", "name": "PETG"},
            ],
            "type": "file",
        },
        {
            # A folder: the client skips recognised non-file types on purpose,
            # so this entry neither counts as deletable nor makes the listing
            # unreadable.
            "filename": "timelapse",
            "size": 0,
            "create_time": 1707100000,
            "layer": 0,
            "print_time": 0,
            "total_filament_used": 0.0,
            "color_map": [],
            "type": "dir",
        },
    ]


# Mutable: 1047 removes entries, PUT /upload appends them.
printer_files = seed_file_list()

# In-flight uploads by file name: {name: {"md5": ..., "data": bytearray,
# "total": int, "seen": monotonic-seconds}}. Bounded by the two MAX_PENDING_*
# ceilings below via `_evict_stale_uploads` — see their comment for why an
# unfinished upload must not be able to live here forever.
pending_uploads: dict[str, dict] = {}


def _evict_stale_uploads(incoming_bytes=0, keep=None):
    """
    Drop in-flight uploads that are abandoned or that crowd out the memory budget.

    Called on every chunk BEFORE a new transfer is registered, with the size of
    the chunk about to arrive and the name it belongs to, so the two ceilings
    hold for the state the request leaves behind — including the transfer this
    request is part of. Evicting after the insert would instead leave the new
    upload permanently one over the limit, and could evict the very entry the
    handler is mid-way through writing to.

    `keep` is never evicted, whatever the budgets say: it is the upload this
    client is still sending, and discarding a transfer in progress would turn a
    memory guard into a broken upload. The honest worst case is therefore
    `MAX_PENDING_UPLOAD_BYTES` of OTHER in-flight files plus one whole file of up
    to `MAX_UPLOAD_BYTES` being actively received — a bound, which is what this
    is for, rather than the unbounded accumulation it replaces.

    Order is oldest-seen-first, which discards the uploads nobody is still
    sending. Returns the names dropped so the caller can report them: a
    contributor whose earlier attempt got evicted is about to be told "expected
    offset 0", and that is only explainable if it was named when it happened.
    """
    now = time.monotonic()
    dropped = [
        name
        for name, state in pending_uploads.items()
        if name != keep and now - state["seen"] > PENDING_UPLOAD_IDLE_TIMEOUT
    ]
    for name in dropped:
        del pending_uploads[name]

    # Then the hard ceilings, oldest first, until both are satisfied with room
    # left for the chunk that prompted this call.
    for name in sorted(
        (n for n in pending_uploads if n != keep),
        key=lambda n: pending_uploads[n]["seen"],
    ):
        buffered = sum(len(s["data"]) for s in pending_uploads.values())
        if (
            len(pending_uploads) < MAX_PENDING_UPLOADS
            and buffered + incoming_bytes <= MAX_PENDING_UPLOAD_BYTES
        ):
            break
        del pending_uploads[name]
        dropped.append(name)
    return dropped


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_timestamp():
    """Get current timestamp in seconds."""
    return int(time.time())


def machine_status_label():
    """Human-readable form of the current machine status + loaded file."""
    machine = printer_status["machine_status"]
    filename = printer_status["print_status"]["filename"]
    return f"status={machine['status']}, filename={filename!r}"


def start_idle():
    """
    Put the simulator into the IDLE state with nothing loaded.

    The default boot state is mid-print, which is what the other tooling drives.
    ``--idle`` moves it out of that, and it is the same transition the
    1022 STOP_PRINT branch performs: idle machine status, no filename, no
    targets, no progress. The delete probe refuses to run against a printer that
    names a file it is printing, so without this every validation run aborts.
    """
    printer_status["machine_status"]["status"] = STATUS_IDLE
    printer_status["machine_status"]["sub_status"] = SUB_STATUS_STOPPED
    printer_status["machine_status"]["progress"] = 0
    printer_status["print_status"]["filename"] = ""
    printer_status["print_status"]["state"] = "idle"
    printer_status["print_status"]["enable"] = False
    printer_status["print_status"]["current_layer"] = 0
    printer_status["print_status"]["progress"] = 0.0
    printer_status["print_status"]["print_duration"] = 0
    printer_status["print_status"]["remaining_time_sec"] = 0
    printer_status["extruder"]["target"] = 0.0
    printer_status["heater_bed"]["target"] = 0.0


def create_discovery_response():
    """Create CC2 discovery response."""
    return {
        "id": 0,
        "result": {
            "host_name": PRINTER_NAME,
            "machine_model": PRINTER_MODEL,
            "sn": SERIAL_NUMBER,
            "token_status": 0,  # 0 = no auth required, 1 = access code required
            "lan_status": 1,  # 1 = LAN mode
        },
    }


def create_response(request_id: int, method: int, result: dict):
    """Create a CC2 response message."""
    return {
        "id": request_id,
        "method": method,
        "result": result,
    }


def create_status_event():
    """Create a delta status event (method 6000)."""
    global status_sequence_id
    status_sequence_id += 1
    return {
        "id": status_sequence_id,
        "method": CC2_EVENT_STATUS,
        "result": deepcopy(printer_status),
    }


# =============================================================================
# MQTT MESSAGE HANDLERS
# =============================================================================


async def handle_registration(mqtt_client, client_id: str, request_id: str):
    """Handle client registration."""
    print(f"📋 Registration request from client: {client_id}")

    # Check if we have too many clients
    if len(registered_clients) >= 4:
        response = {"client_id": client_id, "error": "too many clients"}
        print(f"❌ Registration rejected: too many clients")
    else:
        registered_clients[client_id] = request_id
        response = {"client_id": client_id, "error": "ok"}
        print(f"✅ Registration accepted: {client_id}")

    topic = f"elegoo/{SERIAL_NUMBER}/{request_id}/register_response"
    await mqtt_client.publish(topic, json.dumps(response))


async def handle_command(mqtt_client, client_id: str, payload: dict):
    """Handle a command from a registered client."""
    request_id = payload.get("id", 0)
    method = payload.get("method", 0)
    params = payload.get("params", {})

    response_topic = f"elegoo/{SERIAL_NUMBER}/{client_id}/api_response"

    # Reject commands from unregistered clients
    if client_id not in registered_clients:
        print(f"⚠️  Rejecting command from unregistered client: {client_id}")
        response = create_response(
            request_id, method, {"error_code": 1000, "error": "not registered"}
        )
        await mqtt_client.publish(response_topic, json.dumps(response))
        return

    print(f"📨 Command from {client_id}: method={method}, id={request_id}")

    result = {"error_code": 0}

    if method == CC2_CMD_GET_ATTRIBUTES:
        result = deepcopy(printer_attributes)
        print(f"📤 Sending attributes: {PRINTER_MODEL}")

    elif method == CC2_CMD_GET_STATUS:
        result = deepcopy(printer_status)
        print(f"📤 Sending full status (status={printer_status['machine_status']['status']})")

    elif method == CC2_CMD_START_PRINT:
        filename = params.get("filename", "test_print.gcode")
        storage = params.get("storage_media", "local")
        print(f"🚀 Starting print: {filename} from {storage}")

        # Update printer state
        printer_status["machine_status"]["status"] = STATUS_PRINTING
        printer_status["machine_status"]["sub_status"] = SUB_STATUS_EXTRUDER_PREHEATING
        printer_status["print_status"]["filename"] = filename
        printer_status["print_status"]["total_layer"] = random.randint(100, 500)
        printer_status["print_status"]["current_layer"] = 0
        printer_status["print_status"]["total_duration"] = random.randint(3600, 14400)
        printer_status["print_status"]["print_duration"] = 0
        printer_status["print_status"]["remaining_time_sec"] = printer_status["print_status"]["total_duration"]
        printer_status["print_status"]["progress"] = 0.0

        # Set target temperatures
        printer_status["extruder"]["target"] = 220.0
        printer_status["heater_bed"]["target"] = 60.0

    elif method == CC2_CMD_PAUSE_PRINT:
        print("⏸️  Pausing print")
        printer_status["machine_status"]["sub_status"] = SUB_STATUS_PAUSED

    elif method == CC2_CMD_STOP_PRINT:
        print("⏹️  Stopping print")
        printer_status["machine_status"]["status"] = STATUS_IDLE
        printer_status["machine_status"]["sub_status"] = SUB_STATUS_STOPPED
        printer_status["print_status"]["filename"] = ""
        printer_status["print_status"]["progress"] = 0.0
        printer_status["extruder"]["target"] = 0.0
        printer_status["heater_bed"]["target"] = 0.0

    elif method == CC2_CMD_RESUME_PRINT:
        print("▶️  Resuming print")
        printer_status["machine_status"]["sub_status"] = SUB_STATUS_PRINTING

    elif method == CC2_CMD_SET_TEMPERATURE:
        if "extruder" in params:
            temp = params["extruder"]
            printer_status["extruder"]["target"] = float(temp)
            print(f"🌡️  Target extruder temp: {temp}°C")
        if "heater_bed" in params:
            temp = params["heater_bed"]
            printer_status["heater_bed"]["target"] = float(temp)
            print(f"🌡️  Target bed temp: {temp}°C")

    elif method == CC2_CMD_SET_FAN_SPEED:
        if "fan" in params:
            speed = params["fan"]
            printer_status["fans"]["fan"]["speed"] = speed
            printer_status["fans"]["fan"]["rpm"] = speed * 50
            print(f"🌀 Model fan: {speed}%")
        if "box_fan" in params:
            speed = params["box_fan"]
            printer_status["fans"]["box_fan"]["speed"] = speed
            printer_status["fans"]["box_fan"]["rpm"] = speed * 40
            print(f"🌀 Box fan: {speed}%")
        if "aux_fan" in params:
            speed = params["aux_fan"]
            printer_status["fans"]["aux_fan"]["speed"] = speed
            printer_status["fans"]["aux_fan"]["rpm"] = speed * 45
            print(f"🌀 Aux fan: {speed}%")

    elif method == CC2_CMD_SET_PRINT_SPEED:
        mode = params.get("mode", SPEED_MODE_BALANCED)
        printer_status["gcode_move_inf"]["speed_mode"] = mode
        mode_names = {0: "Silent", 1: "Balanced", 2: "Sport", 3: "Ludicrous"}
        print(f"⚡ Speed mode: {mode_names.get(mode, 'Unknown')}")

    elif method == CC2_CMD_SET_LIGHT:
        # Accept "power" (0/1), "brightness" (0-255), or "status" (0/1) formats
        power = params.get("power")
        brightness = params.get("brightness")
        status = params.get("status")
        if power is not None:
            printer_status["led"]["status"] = power
            print(f"💡 Light: {'on' if power else 'off'} (power={power})")
        elif brightness is not None:
            printer_status["led"]["status"] = 1 if brightness > 0 else 0
            print(f"💡 Light: {'on' if brightness > 0 else 'off'} (brightness={brightness})")
        elif status is not None:
            printer_status["led"]["status"] = status
            print(f"💡 Light: {'on' if status else 'off'} (status={status})")

    elif method == CC2_CMD_VIDEO_STREAM:
        enable = params.get("enable", 0)
        print(f"📹 Video stream: {'enabled' if enable else 'disabled'}")
        result = {
            "error_code": 0,
            "video_url": f"rtsp://{PRINTER_IP}:8554/live" if enable else "",
        }

    elif method == CC2_CMD_UPDATE_PRINTER_NAME:
        new_name = params.get("hostname", PRINTER_NAME)
        printer_attributes["hostname"] = new_name
        print(f"✏️  Printer name updated: {new_name}")

    elif method == CC2_CMD_HOME_AXES:
        axes = params.get("homed_axes", "xyz")
        printer_status["tool_head"]["homed_axes"] = axes
        print(f"🏠 Homing axes: {axes}")

    elif method == CC2_CMD_GET_FILE_LIST:
        storage = params.get("storage_media", "local")
        result = {
            "error_code": CC2_ERROR_SUCCESS,
            "file_list": deepcopy(printer_files),
        }
        print(
            f"📁 Sending file list ({len(printer_files)} entries on {storage}): "
            f"{[entry['filename'] for entry in printer_files]}"
        )

    elif method == CC2_CMD_DELETE_FILE:
        # The full params go to stdout so a run can be eyeballed for which
        # variant produced which code.
        print(f"🗑️  Delete request params: {json.dumps(params, sort_keys=True)}")
        if DELETE_PARAM_KEY not in params:
            # Request-shape rejection, evaluated BEFORE the file is looked up —
            # exactly the signature the real printer shows on fw 02.01.01.07,
            # where an existing file and a missing one both answer 1003.
            print(
                f"🚫 Delete refused: params carry no {DELETE_PARAM_KEY!r} "
                f"(error_code {CC2_ERROR_INVALID_PARAMETER})"
            )
            result = {"error_code": CC2_ERROR_INVALID_PARAMETER}
        else:
            target = params[DELETE_PARAM_KEY]
            # A non-string name can never match an entry; the client's own parser
            # would drop such an entry, so treat it as "no such file" (1007)
            # rather than as a shape rejection.
            removed = [
                entry
                for entry in printer_files
                if isinstance(target, str) and entry["filename"] == target
            ]
            if not removed:
                # Right key, no such file.
                print(
                    f"🚫 Delete refused: no file named {target!r} "
                    f"(error_code {CC2_ERROR_FILE_DELETE_FAILED})"
                )
                result = {"error_code": CC2_ERROR_FILE_DELETE_FAILED}
            else:
                printer_files[:] = [
                    entry
                    for entry in printer_files
                    if entry["filename"] != target
                ]
                print(
                    f"🗑️  Deleted {target!r} "
                    f"({len(printer_files)} entries remain, key={DELETE_PARAM_KEY!r})"
                )
                result = {"error_code": CC2_ERROR_SUCCESS}

    elif method == CC2_CMD_GET_CANVAS_STATUS:
        # Determine active tray based on print status
        # Canvas uses 0-based IDs (0, 1, 2, 3)
        active_canvas = 0  # First/only Canvas unit
        active_tray = 3 if printer_status["machine_status"]["status"] == STATUS_PRINTING else 0

        # Return Canvas with 4 loaded trays (matches real API format)
        result = {
            "error_code": 0,
            "canvas_info": {
                "active_canvas_id": active_canvas,
                "active_tray_id": active_tray,
                "auto_refill": False,
                "canvas_list": [
                    {
                        "canvas_id": 0,  # 0-based Canvas ID
                        "connected": 1,
                        "tray_list": [
                            {
                                "tray_id": 0,  # 0-based tray ID
                                "brand": "ELEGOO",
                                "filament_type": "PLA",
                                "filament_name": "PLA",
                                "filament_color": "#2850DF",  # Blue (with # prefix)
                                "min_nozzle_temp": 190,  # Correct field name
                                "max_nozzle_temp": 230,  # Correct field name
                                "status": 1,  # Filament present
                            },
                            {
                                "tray_id": 1,
                                "brand": "ELEGOO",
                                "filament_type": "PLA",
                                "filament_name": "PLA Basic",
                                "filament_color": "#FFFFFF",  # White
                                "min_nozzle_temp": 190,
                                "max_nozzle_temp": 230,
                                "status": 1,
                            },
                            {
                                "tray_id": 2,
                                "brand": "ELEGOO",
                                "filament_type": "PLA",
                                "filament_name": "PLA Silk",
                                "filament_color": "#F32FF8",  # Magenta
                                "min_nozzle_temp": 190,
                                "max_nozzle_temp": 230,
                                "status": 1,
                            },
                            {
                                "tray_id": 3,
                                "brand": "ELEGOO",
                                "filament_type": "PLA",
                                "filament_name": "PLA",
                                "filament_color": "#000000",  # Black
                                "min_nozzle_temp": 190,
                                "max_nozzle_temp": 230,
                                "status": 2,  # Status 2 = currently active
                            },
                        ],
                    }
                ],
            },
        }
        active_str = f"Canvas {active_canvas}, Tray {active_tray}"
        print(f"📦 Sending canvas status (connected, active: {active_str})")

    else:
        print(f"❓ Unknown command: {method}")
        result = {"error_code": 1001, "error": "unknown interface"}

    response = create_response(request_id, method, result)
    await mqtt_client.publish(response_topic, json.dumps(response))


async def handle_heartbeat(mqtt_client, client_id: str, payload: dict):
    """Handle heartbeat PING/PONG."""
    if payload.get("type") == "PING":
        response_topic = f"elegoo/{SERIAL_NUMBER}/{client_id}/api_response"
        await mqtt_client.publish(response_topic, json.dumps({"type": "PONG"}))
        print(f"💓 PONG -> {client_id}")


# =============================================================================
# MQTT MESSAGE ROUTER
# =============================================================================


async def mqtt_message_handler(mqtt_client, stop_event):
    """Handle incoming MQTT messages."""
    async for message in mqtt_client.messages:
        if stop_event.is_set():
            break

        topic = str(message.topic)
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        # Registration request
        if topic == f"elegoo/{SERIAL_NUMBER}/api_register":
            client_id = payload.get("client_id", "")
            request_id = payload.get("request_id", "")
            await handle_registration(mqtt_client, client_id, request_id)

        # Command request (extract client_id from topic)
        elif "/api_request" in topic:
            parts = topic.split("/")
            if len(parts) >= 3:
                client_id = parts[2]
                # Check for heartbeat
                if "type" in payload:
                    await handle_heartbeat(mqtt_client, client_id, payload)
                else:
                    await handle_command(mqtt_client, client_id, payload)


# =============================================================================
# SIMULATION TASKS
# =============================================================================


async def simulate_temperatures(stop_event):
    """Simulate temperature changes over time."""
    while not stop_event.is_set():
        await asyncio.sleep(2)

        # Simulate extruder temperature approaching target
        target = printer_status["extruder"]["target"]
        current = printer_status["extruder"]["temperature"]
        if target > 0:
            if current < target:
                printer_status["extruder"]["temperature"] = min(target, current + random.uniform(3, 8))
            elif current > target + 5:
                printer_status["extruder"]["temperature"] = max(target, current - random.uniform(1, 3))
        else:
            if current > 30:
                printer_status["extruder"]["temperature"] = max(25, current - random.uniform(2, 5))

        # Simulate bed temperature approaching target
        target = printer_status["heater_bed"]["target"]
        current = printer_status["heater_bed"]["temperature"]
        if target > 0:
            if current < target:
                printer_status["heater_bed"]["temperature"] = min(target, current + random.uniform(1, 4))
            elif current > target + 3:
                printer_status["heater_bed"]["temperature"] = max(target, current - random.uniform(0.5, 1.5))
        else:
            if current > 28:
                printer_status["heater_bed"]["temperature"] = max(25, current - random.uniform(1, 3))


async def simulate_printing(stop_event):
    """Simulate print progress."""
    while not stop_event.is_set():
        await asyncio.sleep(3)

        if printer_status["machine_status"]["status"] != STATUS_PRINTING:
            continue

        sub_status = printer_status["machine_status"]["sub_status"]

        # Handle preheating -> printing transition
        if sub_status in (SUB_STATUS_EXTRUDER_PREHEATING, SUB_STATUS_BED_PREHEATING):
            ext_temp = printer_status["extruder"]["temperature"]
            ext_target = printer_status["extruder"]["target"]
            bed_temp = printer_status["heater_bed"]["temperature"]
            bed_target = printer_status["heater_bed"]["target"]

            if ext_temp >= ext_target - 5 and bed_temp >= bed_target - 3:
                printer_status["machine_status"]["sub_status"] = SUB_STATUS_PRINTING
                print("🔥 Preheating complete, starting print")

        # Progress the print
        elif sub_status == SUB_STATUS_PRINTING:
            ps = printer_status["print_status"]
            if ps["current_layer"] < ps["total_layer"]:
                ps["current_layer"] += 1
                ps["progress"] = (ps["current_layer"] / ps["total_layer"]) * 100
                ps["print_duration"] += 3
                ps["remaining_time_sec"] = max(0, ps["total_duration"] - ps["print_duration"])

                printer_status["machine_status"]["progress"] = int(ps["progress"])

                if ps["current_layer"] % 10 == 0:
                    print(f"🖨️  Layer {ps['current_layer']}/{ps['total_layer']} ({ps['progress']:.1f}%)")
            else:
                # Print complete
                printer_status["machine_status"]["status"] = STATUS_IDLE
                printer_status["machine_status"]["sub_status"] = SUB_STATUS_PRINTING_COMPLETED
                printer_status["extruder"]["target"] = 0.0
                printer_status["heater_bed"]["target"] = 0.0
                print("✅ Print complete!")


async def status_publisher(mqtt_client, stop_event):
    """Publish delta status updates periodically."""
    status_topic = f"elegoo/{SERIAL_NUMBER}/api_status"

    while not stop_event.is_set():
        await asyncio.sleep(5)  # Publish status every 5 seconds

        if registered_clients:
            status_event = create_status_event()
            await mqtt_client.publish(status_topic, json.dumps(status_event))
            # Only log occasionally
            if status_sequence_id % 12 == 0:
                print(f"📊 Status update #{status_sequence_id}")


# =============================================================================
# UDP DISCOVERY SERVER
# =============================================================================


async def udp_discovery_server(stop_event):
    """Handle UDP discovery requests."""
    loop = asyncio.get_running_loop()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1)
        sock.bind((DISCOVERY_HOST, UDP_DISCOVERY_PORT))
        print(f"📡 CC2 Discovery server listening on {DISCOVERY_HOST}:{UDP_DISCOVERY_PORT}")

        while not stop_event.is_set():
            try:
                data, addr = await loop.run_in_executor(None, sock.recvfrom, 1024)
                message = data.decode("utf-8")

                try:
                    request = json.loads(message)
                    if request.get("method") == 7000:
                        print(f"🔍 Discovery request from {addr}")
                        response = create_discovery_response()
                        sock.sendto(json.dumps(response).encode("utf-8"), addr)
                        print(f"✅ Sent discovery response to {addr}")
                except json.JSONDecodeError:
                    pass

            except socket.timeout:
                continue
            except OSError as e:
                if not stop_event.is_set():
                    print(f"⚠️  UDP error: {e}")


# =============================================================================
# HTTP UPLOAD SERVER (PUT /upload)
# =============================================================================


def _parse_content_range(value: str | None):
    """
    Read a ``Content-Range: bytes {start}-{end}/{total}`` header.

    Returns ``(start, end, total)`` or ``None`` when the header is missing or
    unreadable. ``upload.py`` always sends this header, so an unreadable one is
    a request this simulator refuses rather than guesses at.
    """
    if not value or not value.startswith("bytes "):
        return None
    try:
        range_part, _, total_part = value[len("bytes ") :].partition("/")
        start_part, _, end_part = range_part.partition("-")
        return int(start_part), int(end_part), int(total_part)
    except ValueError:
        return None


async def handle_upload(request):
    """
    Handle one chunk of ``PUT /upload``, the way the printer's HTTP server does.

    ``upload_gcode`` (``cc2/upload.py``) sends the whole file in 1 MiB chunks to
    ``PUT http://{host}:{UPLOAD_PORT}/upload``, each carrying ``X-File-Name``,
    ``X-File-MD5`` (the MD5 of the WHOLE file), ``X-Token`` (the access code) and
    ``Content-Range``. It aborts on any non-200 or any ``error_code != 0``, so
    every rejection here has to be visible in one of those two places.

    Rejections this endpoint implements, because the probe's safety guarantee
    rests on them:
      * a wrong ``X-Token``  -> HTTP 401 + ``error_code`` 1000 TOKEN_FAILED
      * a corrupt body (MD5 of what arrived does not match ``X-File-MD5`` once
        the last chunk lands) -> HTTP 200 + ``error_code`` 9004 MD5_FAILED, which
        is the code the real printer answers with and the one ``upload.py``'s own
        docstring records
      * a range that does not continue the bytes already received, or a body that
        contradicts its own range -> HTTP 200 + ``error_code`` 9000 OFFSET_MISMATCH
      * a range that reaches past its own declared total (``end >= total``), a
        total that changes mid-transfer, or a completed transfer whose buffered
        byte count is not the declared total -> HTTP 200 + ``error_code`` 9000.
        All three are the same failure seen from three sides: a file listed under
        a size its bytes do not add up to. The probe uploads its own target files
        and judges the delete against the listing, so a simulator that accepted a
        malformed upload could hand a contributor a passing run over a corrupt
        file — and the whole point of this endpoint is that it refuses the shapes
        the real printer refuses.

    In-flight uploads are bounded (``MAX_PENDING_UPLOADS`` /
    ``MAX_PENDING_UPLOAD_BYTES`` / ``PENDING_UPLOAD_IDLE_TIMEOUT``) so that
    abandoned half-uploads cannot accumulate for the life of the simulator.

    On completion the file is APPENDED to ``printer_files``, so the next 1044
    lists it. An uploaded file that never appeared in the listing would make the
    probe's "still listed after delete" check meaningless.
    """
    filename = request.headers.get("X-File-Name", "")
    md5 = request.headers.get("X-File-MD5", "")
    token = request.headers.get("X-Token", "")
    range_header = request.headers.get("Content-Range")

    if not filename or not md5:
        missing = "X-File-Name" if not filename else "X-File-MD5"
        print(f"⚠️  Upload refused: no {missing} header")
        return web.json_response(
            {"error_code": CC2_ERROR_INVALID_PARAMETER}, status=200
        )

    if token != MQTT_PASSWORD:
        print(f"🔒 Upload of {filename} refused: bad X-Token")
        return web.json_response({"error_code": CC2_ERROR_TOKEN_FAILED}, status=401)

    parsed = _parse_content_range(range_header)
    if parsed is None:
        print(f"⚠️  Upload of {filename} refused: unreadable Content-Range {range_header!r}")
        return web.json_response({"error_code": CC2_ERROR_FILE_WRITE_FAILED}, status=200)
    start, end, total = parsed

    body = await request.read()
    if len(body) != end - start + 1 or start > end or total <= 0:
        print(
            f"⚠️  Upload of {filename} refused: body of {len(body)} bytes does not "
            f"match range {start}-{end}/{total}"
        )
        return web.json_response({"error_code": 9000}, status=200)
    if end >= total:
        # The range reaches past the end of the file it declared. Without this
        # check `bytes 0-1/2` carrying three bytes was accepted (the length check
        # above only compares the body against the RANGE, never against TOTAL),
        # and completion then fired on `received >= total` with three bytes
        # buffered — a "successful" upload of a file whose bytes contradict its
        # own declared size. The real printer refuses the range; so does this.
        print(
            f"⚠️  Upload of {filename} refused: range {start}-{end} reaches past "
            f"the declared total {total}"
        )
        return web.json_response({"error_code": 9000}, status=200)

    for abandoned in _evict_stale_uploads(len(body), keep=filename):
        print(f"🧹 Dropped abandoned in-flight upload {abandoned}")

    state = pending_uploads.get(filename)
    if state is None or state["md5"] != md5 or start == 0:
        # First chunk of this file (or a restart of it under a different MD5).
        if state is not None and state["data"] and start != 0:
            print(
                f"⚠️  Upload of {filename} refused: chunk at offset {start} arrived "
                f"under a new MD5 mid-transfer"
            )
            return web.json_response({"error_code": 9000}, status=200)
        state = {
            "md5": md5,
            "data": bytearray(),
            "total": total,
            "seen": time.monotonic(),
        }
        pending_uploads[filename] = state
    elif total != state["total"]:
        # The total may not move mid-transfer. `state["total"]` used to be stored
        # and then never read, so a later chunk could restate the size and pull
        # completion forward: chunks declaring 4 bytes total could be finished by
        # a final chunk claiming 2, and the file was listed with bytes it never
        # received. The declared size is fixed by its first chunk.
        print(
            f"⚠️  Upload of {filename} refused: chunk declares total {total}, but "
            f"its first chunk declared {state['total']}"
        )
        return web.json_response({"error_code": 9000}, status=200)

    if start != len(state["data"]):
        print(
            f"⚠️  Upload of {filename} refused: expected offset {len(state['data'])}, "
            f"got {start}"
        )
        return web.json_response({"error_code": 9000}, status=200)

    if len(state["data"]) + len(body) > MAX_UPLOAD_BYTES:
        print(f"⚠️  Upload of {filename} refused: larger than {MAX_UPLOAD_BYTES} bytes")
        return web.json_response(
            {"error_code": CC2_ERROR_FILE_WRITE_FAILED}, status=200
        )

    state["data"].extend(body)
    state["seen"] = time.monotonic()
    received = len(state["data"])
    print(f"📥 Uploading {filename}: {received}/{total} bytes")

    if received < total:
        return web.json_response({"error_code": CC2_ERROR_SUCCESS}, status=200)

    if received > total:
        # Unreachable while the two checks above hold (a range inside its total,
        # a fixed total, and chunks that continue exactly), and kept precisely
        # because it is the last gate before a file is announced as uploaded: no
        # path through this handler may list a file whose buffered byte count is
        # not the declared size.
        print(
            f"⚠️  Upload of {filename} refused: {received} bytes buffered for a "
            f"declared total of {total}"
        )
        del pending_uploads[filename]
        return web.json_response({"error_code": 9000}, status=200)

    # Last chunk landed: verify the MD5 of the whole file, as the printer does.
    del pending_uploads[filename]
    actual = hashlib.md5(bytes(state["data"])).hexdigest()
    if md5 and actual != md5:
        print(
            f"❌ Upload of {filename} refused: MD5 mismatch "
            f"(sent {md5}, computed {actual})"
        )
        return web.json_response({"error_code": CC2_ERROR_MD5_FAILED}, status=200)

    printer_files.append(
        {
            "filename": filename,
            "size": received,
            "create_time": get_timestamp(),
            "layer": 0,
            "print_time": 0,
            "total_filament_used": 0.0,
            "color_map": [],
            "type": "file",
        }
    )
    print(f"✅ Uploaded {filename} ({received} bytes); {len(printer_files)} files listed")
    return web.json_response({"error_code": CC2_ERROR_SUCCESS}, status=200)


async def run_upload_server(stop_event):
    """Serve ``PUT /upload`` on the configured port alongside MQTT and discovery."""
    # aiohttp's default request body ceiling is exactly 1 MiB, which is the size
    # of one real upload chunk (``upload.CHUNK_SIZE``), so with the default the
    # simulator answers a full-size chunk HTTP 413 with an HTML body — a failure
    # shape the real printer never produces. Allow the whole body and cap it
    # ourselves in the handler, where the refusal is a CC2 ``error_code``.
    app = web.Application(client_max_size=MAX_UPLOAD_BYTES + 64 * 1024)
    app.router.add_put("/upload", handle_upload)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, BIND_HOST, UPLOAD_PORT)
    await site.start()
    print(f"🌐 HTTP upload server listening on {BIND_HOST}:{UPLOAD_PORT} (PUT /upload)")

    await stop_event.wait()

    await runner.cleanup()
    print("✅ HTTP upload server stopped")


# =============================================================================
# MQTT BROKER AND CLIENT
# =============================================================================


async def run_mqtt_broker(stop_event):
    """Run the embedded MQTT broker."""
    config = {
        "listeners": {
            "default": {
                "type": "tcp",
                "bind": f"{BROKER_HOST}:{MQTT_PORT}",
            },
        },
    }

    broker = Broker(config)

    print(f"🔌 Starting MQTT broker on {BROKER_HOST}:{MQTT_PORT}...")
    await broker.start()
    print("✅ MQTT broker started")

    # Wait for stop signal
    await stop_event.wait()

    print("🛑 Stopping MQTT broker...")
    await broker.shutdown()
    print("✅ MQTT broker stopped")


async def run_mqtt_client(stop_event):
    """Run the MQTT client that handles messages."""
    # Wait a bit for broker to start
    await asyncio.sleep(1)

    try:
        async with aiomqtt.Client(
            hostname=PRINTER_IP,
            port=MQTT_PORT,
            username=MQTT_USERNAME,
            password=MQTT_PASSWORD,
        ) as mqtt_client:
            print("✅ Internal MQTT client connected")

            # Subscribe to topics
            await mqtt_client.subscribe(f"elegoo/{SERIAL_NUMBER}/api_register")
            await mqtt_client.subscribe(f"elegoo/{SERIAL_NUMBER}/+/api_request")
            print(f"📡 Subscribed to: elegoo/{SERIAL_NUMBER}/api_register")
            print(f"📡 Subscribed to: elegoo/{SERIAL_NUMBER}/+/api_request")

            # Start background tasks
            handler_task = asyncio.create_task(
                mqtt_message_handler(mqtt_client, stop_event)
            )
            status_task = asyncio.create_task(
                status_publisher(mqtt_client, stop_event)
            )
            print_task = asyncio.create_task(simulate_printing(stop_event))
            temp_task = asyncio.create_task(simulate_temperatures(stop_event))

            print(f"\n{'='*70}")
            print(f"📡 CC2 Printer ready!")
            print(f"   Serial: {SERIAL_NUMBER}")
            print(f"   MQTT Broker: {PRINTER_IP}:{MQTT_PORT}")
            print(f"   Discovery: UDP {UDP_DISCOVERY_PORT}")
            print(f"   HTTP:      PUT :{UPLOAD_PORT}/upload")
            print(f"{'='*70}\n")

            # Wait for stop
            await stop_event.wait()

            # Cleanup
            for task in [handler_task, status_task, print_task, temp_task]:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    except (OSError, TimeoutError) as e:
        print(f"⚠️  MQTT client error: {e}")


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================


async def main():
    """Main entry point."""
    global UPLOAD_PORT

    parser = argparse.ArgumentParser(
        description="CC2 test printer simulator (MQTT broker + discovery + HTTP upload)."
    )
    parser.add_argument(
        "--idle",
        action="store_true",
        help="start the printer IDLE with no file loaded, instead of mid-print "
        "(the 1047 delete probe refuses to run against a printing printer)",
    )
    parser.add_argument(
        "--upload-port",
        type=int,
        default=UPLOAD_PORT,
        help=f"port for the HTTP PUT /upload endpoint (default {UPLOAD_PORT}; "
        "the real printer uses 80, which needs root)",
    )
    args = parser.parse_args()

    UPLOAD_PORT = args.upload_port

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    # Set up signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    if args.idle:
        start_idle()

    print("=" * 70)
    print("🖨️  CC2 Test Printer Simulator")
    print("=" * 70)
    print(f"Printer Name:  {PRINTER_NAME}")
    print(f"Model:         {PRINTER_MODEL}")
    print(f"Serial:        {SERIAL_NUMBER}")
    print(f"Firmware:      {FIRMWARE_VERSION}")
    print(f"IP Address:    {PRINTER_IP}")
    print(f"MQTT Port:     {MQTT_PORT}")
    print(f"Discovery:     UDP {UDP_DISCOVERY_PORT}")
    print(f"HTTP Upload:   PUT :{UPLOAD_PORT}/upload (token {MQTT_PASSWORD})")
    print(f"Machine:       {machine_status_label()}")
    print(f"1047 accepts:  {DELETE_PARAM_KEY!r} (1003 without it, 1007 if unnamed)")
    print(f"File list:     {len(printer_files)} entries seeded fresh")
    print("=" * 70)
    print()

    # Start all services
    udp_task = asyncio.create_task(udp_discovery_server(stop_event))
    broker_task = asyncio.create_task(run_mqtt_broker(stop_event))
    client_task = asyncio.create_task(run_mqtt_client(stop_event))
    upload_task = asyncio.create_task(run_upload_server(stop_event))

    try:
        await asyncio.gather(udp_task, broker_task, client_task, upload_task)
    except asyncio.CancelledError:
        pass
    finally:
        print("\n🛑 Shutting down CC2 simulator...")
        stop_event.set()

        for task in [udp_task, broker_task, client_task, upload_task]:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        print("✅ CC2 simulator stopped")


if __name__ == "__main__":
    asyncio.run(main())
