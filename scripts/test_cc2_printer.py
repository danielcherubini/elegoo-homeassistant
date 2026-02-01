"""
CC2 Test Printer Simulator.

This simulates a Centauri Carbon 2 printer that runs its own MQTT broker.
The CC2 uses an inverted architecture where:
- The printer runs the MQTT broker (on port 1883)
- Home Assistant connects TO the printer
- Clients must register before sending commands
- Uses heartbeat/ping-pong mechanism
- Sends delta status updates
"""

import asyncio
import json
import random
import signal
import socket
import time
import uuid
from contextlib import suppress

import aiomqtt
from amqtt.broker import Broker

# Printer configuration
SERIAL_NUMBER = "CC2TEST1234567890"
PRINTER_IP = "127.0.0.1"
PRINTER_NAME = "Centauri Carbon 2 Test"
PRINTER_MODEL = "Centauri Carbon 2"

# Discovery settings
UDP_DISCOVERY_PORT = 52700
# Use 0.0.0.0 for discovery and MQTT broker to accept from any interface
DISCOVERY_HOST = "0.0.0.0"
BROKER_HOST = "0.0.0.0"

# MQTT broker settings (printer runs the broker)
MQTT_PORT = 1883
MQTT_USERNAME = "elegoo"
MQTT_PASSWORD = "123456"  # noqa: S105

# CC2 command IDs
CC2_CMD_GET_ATTRIBUTES = 1001
CC2_CMD_GET_STATUS = 1002
CC2_CMD_START_PRINT = 1020
CC2_CMD_PAUSE_PRINT = 1021
CC2_CMD_STOP_PRINT = 1022
CC2_CMD_RESUME_PRINT = 1023
CC2_CMD_SET_TEMPERATURE = 1028
CC2_CMD_SET_FAN_SPEED = 1030
CC2_CMD_SET_LIGHT = 1031
CC2_CMD_SET_VIDEO_STREAM = 1050

# CC2 event IDs
CC2_EVENT_STATUS = 6000

# Registered clients
registered_clients: dict[str, str] = {}

# Printer state
printer_status = {
    "status": 1,  # Idle
    "sub_status": 0,
    "temp_extruder": 25.5,
    "temp_extruder_target": 0,
    "temp_heater_bed": 24.0,
    "temp_heater_bed_target": 0,
    "temp_box": 22.0,
    "temp_box_target": 0,
    "fan_speeds": {
        "fan": 0,
        "aux_fan": 0,
        "box_fan": 0,
    },
    "light_status": {
        "enabled": 1,
        "rgb": [255, 255, 255],
    },
    "position": {"x": 0, "y": 0, "z": 0},
    "z_offset": 0.0,
    "print_speed": 100,
    "print_job": {
        "file_name": None,
        "task_id": None,
        "current_layer": 0,
        "total_layers": 0,
        "print_time": 0,
        "total_time": 0,
        "progress": 0,
        "error_code": 0,
    },
    "sequence": 0,
}

printer_attributes = {
    "host_name": PRINTER_NAME,
    "machine_model": PRINTER_MODEL,
    "sn": SERIAL_NUMBER,
    "firmware_version": "V1.0.0",
    "resolution": "1920x1080",
    "xyz_size": "220x220x250",
    "ip": PRINTER_IP,
    "mac": "00:11:22:33:44:55",
    "network_type": "wifi",
    "usb_connected": False,
    "camera_connected": True,
    "remaining_memory": 1073741824,
    "video_connections": 0,
    "max_video_connections": 2,
}


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
    """Create a status event (delta update)."""
    printer_status["sequence"] += 1
    return {
        "id": 0,
        "method": CC2_EVENT_STATUS,
        "result": printer_status.copy(),
    }


async def handle_registration(mqtt_client, client_id: str, request_id: str):
    """Handle client registration."""
    print(f"📋 Registration request from client: {client_id}")

    if len(registered_clients) >= 4:
        # Too many clients
        response = {
            "client_id": client_id,
            "error": "too many clients",
        }
    else:
        registered_clients[client_id] = request_id
        response = {
            "client_id": client_id,
            "error": "ok",
        }

    topic = f"elegoo/{SERIAL_NUMBER}/{request_id}/register_response"
    await mqtt_client.publish(topic, json.dumps(response))
    print(f"✅ Sent registration response to {client_id}: {response['error']}")


async def handle_command(mqtt_client, client_id: str, payload: dict):
    """Handle a command from a registered client."""
    request_id = payload.get("id", 0)
    method = payload.get("method", 0)
    params = payload.get("params", {})

    print(f"📨 Command from {client_id}: method={method}, id={request_id}")

    response_topic = f"elegoo/{SERIAL_NUMBER}/{client_id}/api_response"
    result = {"error_code": 0}

    if method == CC2_CMD_GET_ATTRIBUTES:
        result = {**printer_attributes, "error_code": 0}

    elif method == CC2_CMD_GET_STATUS:
        result = {**printer_status, "error_code": 0}

    elif method == CC2_CMD_START_PRINT:
        filename = params.get("filename", "unknown.gcode")
        print(f"🚀 Starting print: {filename}")
        printer_status["status"] = 2  # Printing
        printer_status["sub_status"] = 2075  # Printing
        printer_status["print_job"]["file_name"] = filename
        printer_status["print_job"]["task_id"] = str(uuid.uuid4())
        printer_status["print_job"]["current_layer"] = 0
        printer_status["print_job"]["total_layers"] = random.randint(100, 500)
        printer_status["print_job"]["print_time"] = 0
        total_layers = printer_status["print_job"]["total_layers"]
        printer_status["print_job"]["total_time"] = total_layers * random.randint(5, 15)
        printer_status["print_job"]["progress"] = 0

    elif method == CC2_CMD_PAUSE_PRINT:
        print("⏸️  Pausing print")
        printer_status["sub_status"] = 2502  # Paused

    elif method == CC2_CMD_STOP_PRINT:
        print("⏹️  Stopping print")
        printer_status["status"] = 1  # Idle
        printer_status["sub_status"] = 2504  # Stopped

    elif method == CC2_CMD_RESUME_PRINT:
        print("▶️  Resuming print")
        printer_status["sub_status"] = 2075  # Printing

    elif method == CC2_CMD_SET_TEMPERATURE:
        if "extruder" in params:
            printer_status["temp_extruder_target"] = params["extruder"]
            print(f"🌡️  Target nozzle temp: {params['extruder']}°C")
        if "heater_bed" in params:
            printer_status["temp_heater_bed_target"] = params["heater_bed"]
            print(f"🌡️  Target bed temp: {params['heater_bed']}°C")

    elif method == CC2_CMD_SET_FAN_SPEED:
        for fan_key in ["fan", "aux_fan", "box_fan"]:
            if fan_key in params:
                printer_status["fan_speeds"][fan_key] = params[fan_key]
                print(f"💨 Set {fan_key}: {params[fan_key]}%")

    elif method == CC2_CMD_SET_LIGHT:
        if "enabled" in params:
            printer_status["light_status"]["enabled"] = params["enabled"]
        if "rgb" in params:
            printer_status["light_status"]["rgb"] = params["rgb"]
        print(f"💡 Light: {printer_status['light_status']}")

    elif method == CC2_CMD_SET_VIDEO_STREAM:
        enable = params.get("enable", 0)
        print(f"📹 Video stream: {'enabled' if enable else 'disabled'}")
        result = {
            "error_code": 0,
            "url": f"http://{PRINTER_IP}:8000/video" if enable else "",
        }

    else:
        print(f"⚠️  Unknown command: {method}")
        result = {"error_code": 1001}  # Unknown interface

    response = create_response(request_id, method, result)
    await mqtt_client.publish(response_topic, json.dumps(response))


async def handle_heartbeat(mqtt_client, client_id: str, payload: dict):
    """Handle heartbeat ping/pong."""
    if payload.get("type") == "PING":
        response_topic = f"elegoo/{SERIAL_NUMBER}/{client_id}/api_response"
        await mqtt_client.publish(response_topic, json.dumps({"type": "PONG"}))


async def mqtt_message_handler(mqtt_client, stop_event):
    """Handle incoming MQTT messages."""
    # Subscribe to all relevant topics
    topics = [
        f"elegoo/{SERIAL_NUMBER}/api_register",
        f"elegoo/{SERIAL_NUMBER}/+/api_request",
    ]
    for topic in topics:
        await mqtt_client.subscribe(topic)
        print(f"📡 Subscribed to: {topic}")

    try:
        async for message in mqtt_client.messages:
            if stop_event.is_set():
                break

            try:
                payload = json.loads(message.payload.decode())
                topic = str(message.topic)

                # Handle registration
                if "api_register" in topic:
                    client_id = payload.get("client_id", "")
                    request_id = payload.get("request_id", "")
                    await handle_registration(mqtt_client, client_id, request_id)

                # Handle commands
                elif "api_request" in topic:
                    # Extract client_id from topic
                    # Format: elegoo/<sn>/<client_id>/api_request
                    parts = topic.split("/")
                    if len(parts) >= 4:
                        client_id = parts[2]

                        # Check for heartbeat
                        if "type" in payload:
                            await handle_heartbeat(mqtt_client, client_id, payload)
                        else:
                            await handle_command(mqtt_client, client_id, payload)

            except json.JSONDecodeError:
                print(f"⚠️  Invalid JSON: {message.payload}")
            except (OSError, TimeoutError, KeyError, ValueError) as e:
                print(f"⚠️  Error handling message: {e}")

    except asyncio.CancelledError:
        print("📡 Message handler cancelled")


async def status_publisher(mqtt_client, stop_event):
    """Periodically publish status updates (delta format)."""
    while not stop_event.is_set():
        try:
            await asyncio.sleep(5)
            if not stop_event.is_set() and registered_clients:
                status_event = create_status_event()
                topic = f"elegoo/{SERIAL_NUMBER}/api_status"
                await mqtt_client.publish(topic, json.dumps(status_event))
        except asyncio.CancelledError:
            break
        except (OSError, TimeoutError):
            print("⚠️  Error publishing status")


async def simulate_printing(stop_event):
    """Simulate printing progress."""
    while not stop_event.is_set():
        await asyncio.sleep(2)

        if printer_status["status"] == 2:  # Printing
            job = printer_status["print_job"]
            if job["current_layer"] < job["total_layers"]:
                job["current_layer"] += 1
                job["print_time"] += random.randint(5, 15)
                job["progress"] = int(job["current_layer"] / job["total_layers"] * 100)
                print(
                    f"📊 Layer {job['current_layer']}/{job['total_layers']} "
                    f"({job['progress']}%)"
                )
            else:
                print("✅ Print completed!")
                printer_status["status"] = 1
                printer_status["sub_status"] = 2077  # Completed


async def simulate_temperatures(stop_event):
    """Simulate temperature changes."""
    while not stop_event.is_set():
        await asyncio.sleep(1)

        # Simulate nozzle heating
        target = printer_status["temp_extruder_target"]
        current = printer_status["temp_extruder"]
        if target > current:
            printer_status["temp_extruder"] = min(target, current + 2.0)
        elif target < current and current > 25:
            printer_status["temp_extruder"] = max(25, current - 1.0)

        # Simulate bed heating
        target = printer_status["temp_heater_bed_target"]
        current = printer_status["temp_of_bed"] = printer_status["temp_heater_bed"]
        if target > current:
            printer_status["temp_heater_bed"] = min(target, current + 1.0)
        elif target < current and current > 24:
            printer_status["temp_heater_bed"] = max(24, current - 0.5)


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

    print("✅ UDP discovery server shut down")


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

            # Start background tasks
            handler_task = asyncio.create_task(
                mqtt_message_handler(mqtt_client, stop_event)
            )
            status_task = asyncio.create_task(
                status_publisher(mqtt_client, stop_event)
            )
            print_task = asyncio.create_task(simulate_printing(stop_event))
            temp_task = asyncio.create_task(simulate_temperatures(stop_event))

            print(f"\n📡 CC2 Printer ready!")
            print(f"   Serial: {SERIAL_NUMBER}")
            print(f"   MQTT Broker: {PRINTER_IP}:{MQTT_PORT}")
            print(f"   Discovery: UDP {UDP_DISCOVERY_PORT}")

            # Wait for stop
            await stop_event.wait()

            # Cleanup
            for task in [handler_task, status_task, print_task, temp_task]:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    except (OSError, TimeoutError) as e:
        print(f"⚠️  MQTT client error: {e}")


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
        await stop_event.wait()
    except KeyboardInterrupt:
        print("\n\n⚠️  Received interrupt signal")
        stop_event.set()
    finally:
        print("\n🛑 Shutting down CC2 simulator...")

        for task in [udp_task, broker_task, client_task]:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        print("✅ CC2 simulator shut down\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
