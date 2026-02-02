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
"""

import asyncio
import json
import random
import signal
import socket
import time
import uuid
from contextlib import suppress
from copy import deepcopy

import aiomqtt
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
MQTT_PORT = 1883
MQTT_USERNAME = "elegoo"
MQTT_PASSWORD = "123456"  # noqa: S105

# =============================================================================
# CC2 PROTOCOL CONSTANTS
# =============================================================================

# Command IDs (requests from client)
CC2_CMD_GET_ATTRIBUTES = 1001
CC2_CMD_GET_STATUS = 1002
CC2_CMD_START_PRINT = 1020
CC2_CMD_PAUSE_PRINT = 1021
CC2_CMD_STOP_PRINT = 1022
CC2_CMD_RESUME_PRINT = 1023  # Not in elegoo-link but logical
CC2_CMD_HOME_AXES = 1026
CC2_CMD_MOVE_AXES = 1027
CC2_CMD_SET_TEMPERATURE = 1028
CC2_CMD_SET_FAN_SPEED = 1030
CC2_CMD_SET_PRINT_SPEED = 1031
CC2_CMD_UPDATE_PRINTER_NAME = 1043
CC2_CMD_SET_VIDEO_STREAM = 1050
CC2_CMD_FILE_DOWNLOAD = 1057
CC2_CMD_CANCEL_FILE_DOWNLOAD = 1058
CC2_CMD_GET_CANVAS_STATUS = 2005
CC2_CMD_SET_AUTO_REFILL = 2004

# Event IDs (push notifications from printer)
CC2_EVENT_STATUS = 6000
CC2_EVENT_ATTRIBUTES = 6008

# Machine status codes
STATUS_INITIALIZING = 0
STATUS_IDLE = 1
STATUS_PRINTING = 2
STATUS_FILAMENT_OPERATING = 3
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

# Print sub-status codes
SUB_STATUS_NONE = 0
SUB_STATUS_EXTRUDER_PREHEATING = 1045
SUB_STATUS_BED_PREHEATING = 1405
SUB_STATUS_PRINTING = 2075
SUB_STATUS_PRINTING_COMPLETED = 2077
SUB_STATUS_PAUSING = 2501
SUB_STATUS_PAUSED = 2502
SUB_STATUS_RESUMING = 2401
SUB_STATUS_STOPPING = 2503
SUB_STATUS_STOPPED = 2504
SUB_STATUS_HOMING = 2801
SUB_STATUS_AUTO_LEVELING = 2901

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
    "gcode_move": {
        "extruder": 138.87,
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
# HELPER FUNCTIONS
# =============================================================================


def get_timestamp():
    """Get current timestamp in seconds."""
    return int(time.time())


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
        printer_status["gcode_move"]["speed_mode"] = mode
        mode_names = {0: "Silent", 1: "Balanced", 2: "Sport", 3: "Ludicrous"}
        print(f"⚡ Speed mode: {mode_names.get(mode, 'Unknown')}")

    elif method == CC2_CMD_SET_VIDEO_STREAM:
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
        printer_status["toolhead"]["homed_axes"] = axes
        print(f"🏠 Homing axes: {axes}")

    elif method == CC2_CMD_GET_CANVAS_STATUS:
        # Return empty canvas (no AMS connected)
        result = {
            "error_code": 0,
            "canvas_info": {
                "active_canvas_id": 0,
                "active_tray_id": 0,
                "auto_refill": False,
                "canvas_list": [],
            },
        }
        print("📦 Sending canvas status (no AMS)")

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
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    # Set up signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

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
    print("=" * 70)
    print()

    # Start all services
    udp_task = asyncio.create_task(udp_discovery_server(stop_event))
    broker_task = asyncio.create_task(run_mqtt_broker(stop_event))
    client_task = asyncio.create_task(run_mqtt_client(stop_event))

    try:
        await asyncio.gather(udp_task, broker_task, client_task)
    except asyncio.CancelledError:
        pass
    finally:
        print("\n🛑 Shutting down CC2 simulator...")
        stop_event.set()

        for task in [udp_task, broker_task, client_task]:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        print("✅ CC2 simulator stopped")


if __name__ == "__main__":
    asyncio.run(main())
