"""
MQTT Test Printer Simulator.

This simulates an Elegoo printer that communicates via MQTT protocol.
It responds to commands on MQTT topics and publishes status updates.
"""

import asyncio
import json
import random
import signal
import socket
import time
import uuid

import aiomqtt

# Printer configuration
MQTT_HOST = "localhost"
MQTT_PORT = 1883
MAINBOARD_ID = "4c851c540107103d00000c0000000000"
PRINTER_IP = "127.0.0.1"
PRINTER_NAME = "Saturn 3 MQTT"
UDP_PORT = 3000
HOST = "0.0.0.0"

# Printer state
print_history = {
    "b9a8b8f8-8b8b-4b8b-8b8b-8b8b8b8b8b8b": {
        "TaskId": "b9a8b8f8-8b8b-4b8b-8b8b-8b8b8b8b8b8b",
        "TaskName": "test_print_1.gcode",
        "BeginTime": 1678886400,
        "EndTime": 1678890000,
        "TaskStatus": 9,
        "Thumbnail": f"http://{PRINTER_IP}:8000/thumb1.jpg",
        "SliceInformation": {},
        "AlreadyPrintLayer": 500,
        "MD5": "d41d8cd98f00b204e9800998ecf8427e",
        "CurrentLayerTalVolume": 15.5,
        "TimeLapseVideoStatus": 1,
        "TimeLapseVideoUrl": f"http://{PRINTER_IP}:8000/video1.mp4",
        "ErrorStatusReason": 0,
    },
}

printer_attributes = {
    "Name": PRINTER_NAME,
    "MachineName": "Saturn 3",
    "BrandName": "ELEGOO",
    "ProtocolVersion": "V1.0.0",  # V1.x indicates MQTT protocol
    "FirmwareVersion": "V1.1.29",
    "XYZsize": "300x300x400",
    "MainboardIP": PRINTER_IP,
    "MainboardID": MAINBOARD_ID,
    "NumberOfVideoStreamConnected": 0,
    "MaximumVideoStreamAllowed": 1,
    "NumberOfCloudSDCPServicesConnected": 0,
    "MaximumCloudSDCPSercicesAllowed": 1,
    "NetworkStatus": "wlan",
    "MainboardMAC": "00:11:22:33:44:55",
    "UsbDiskStatus": 1,
    "Capabilities": ["FILE_TRANSFER", "PRINT_CONTROL", "VIDEO_STREAM"],
    "SupportFileType": ["GCODE"],
    "DevicesStatus": {
        "ZMotorStatus": 1,
        "YMotorStatus": 1,
        "XMotorStatus": 1,
        "ExtruderMotorStatus": 1,
        "RelaseFilmState": 1,
    },
    "CameraStatus": 1,
    "RemainingMemory": 5 * 1024 * 1024 * 1024,  # 5GB
    "SDCPStatus": 1,
}

printer_status = {
    "CurrentStatus": [0],  # Idle
    "PreviousStatus": 0,
    "TempOfNozzle": 25,
    "TempTargetNozzle": 0,
    "TempOfHotbed": 25,
    "TempTargetHotbed": 0,
    "TempOfBox": 25,
    "TempTargetBox": 0,
    "CurrenCoord": "0.0,0.0,0.0",
    "CurrentFanSpeed": {
        "ModelFan": 0,
        "ModeFan": 0,
        "AuxiliaryFan": 0,
        "BoxFan": 0,
    },
    "LightStatus": {"SecondLight": 0},
    "RgbLight": [255, 255, 255],
    "ZOffset": 0.0,
    "PrintSpeed": 100,
    "PrintInfo": {
        "Status": 0,
        "CurrentLayer": 0,
        "TotalLayer": 0,
        "CurrentTicks": 0,
        "TotalTicks": 0,
        "Filename": "",
        "ErrorNumber": 0,
        "TaskId": "",
        "PrintSpeed": 100,
    },
}


def get_timestamp():
    """Get current timestamp in seconds."""
    return int(time.time())


async def udp_discovery_server(stop_event):
    """Handle UDP discovery requests."""
    loop = asyncio.get_running_loop()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1)
        sock.bind((HOST, UDP_PORT))
        print(f"UDP discovery server listening on {HOST}:{UDP_PORT}")

        while not stop_event.is_set():
            try:
                data, addr = await loop.run_in_executor(None, sock.recvfrom, 1024)
                if data == b"M99999":
                    print(f"Received discovery request from {addr}")
                    response = {
                        "Id": str(uuid.uuid4()),
                        "Data": {
                            "Name": PRINTER_NAME,
                            "MachineName": "Saturn 3",
                            "BrandName": printer_attributes["BrandName"],
                            "MainboardIP": PRINTER_IP,
                            "MainboardID": MAINBOARD_ID,
                            "ProtocolVersion": printer_attributes["ProtocolVersion"],
                            "FirmwareVersion": printer_attributes["FirmwareVersion"],
                        },
                    }
                    sock.sendto(json.dumps(response).encode("utf-8"), addr)
            except socket.timeout:
                continue
            except OSError as e:
                if not stop_event.is_set():
                    print(f"UDP error: {e}")

    print("UDP discovery server shut down")


async def publish_status(mqtt_client):
    """Publish printer status to MQTT."""
    status_message = {
        **printer_status,
        "MainboardID": MAINBOARD_ID,
        "TimeStamp": get_timestamp(),
    }
    topic = f"sdcp/status/{MAINBOARD_ID}"
    await mqtt_client.publish(topic, json.dumps(status_message))


async def publish_attributes(mqtt_client):
    """Publish printer attributes to MQTT."""
    attributes_message = {
        **printer_attributes,
        "MainboardID": MAINBOARD_ID,
        "TimeStamp": get_timestamp(),
    }
    topic = f"sdcp/attributes/{MAINBOARD_ID}"
    await mqtt_client.publish(topic, json.dumps(attributes_message))


def create_response(request_data, data):
    """Create a response message to a request."""
    return {
        "Id": request_data.get("Id", str(uuid.uuid4())),
        "Data": {
            "Cmd": request_data["Data"]["Cmd"],
            "Data": data,
            "RequestID": request_data["Data"]["RequestID"],
            "MainboardID": MAINBOARD_ID,
            "TimeStamp": get_timestamp(),
        },
    }


async def handle_request(mqtt_client, request):
    """Handle incoming MQTT request."""
    if "Data" not in request:
        print(f"Invalid request: {request}")
        return

    cmd = request["Data"]["Cmd"]
    request_id = request["Data"].get("RequestID", "unknown")
    print(f"Handling command: {cmd} (RequestID: {request_id})")

    response_topic = f"sdcp/response/{MAINBOARD_ID}"

    if cmd == 0:  # Request Status Refresh
        response = create_response(request, {"Ack": 0})
        await mqtt_client.publish(response_topic, json.dumps(response))
        await publish_status(mqtt_client)

    elif cmd == 1:  # Request Attributes
        response = create_response(request, {"Ack": 0})
        await mqtt_client.publish(response_topic, json.dumps(response))
        await publish_attributes(mqtt_client)

    elif cmd == 128:  # Start Print
        filename = request["Data"]["Data"].get("Filename", "unknown.gcode")
        print(f"Starting print for file: {filename}")
        printer_status["CurrentStatus"] = [1]  # Printing
        printer_status["PrintInfo"]["Status"] = 1  # Homing
        printer_status["PrintInfo"]["Filename"] = filename
        printer_status["PrintInfo"]["TaskId"] = str(uuid.uuid4())
        printer_status["PrintInfo"]["TotalLayer"] = random.randint(100, 1000)
        printer_status["PrintInfo"]["TotalTicks"] = (
            printer_status["PrintInfo"]["TotalLayer"] * random.randint(10, 20)
        )
        response = create_response(request, {"Ack": 0})
        await mqtt_client.publish(response_topic, json.dumps(response))
        await publish_status(mqtt_client)

    elif cmd == 129:  # Pause Print
        print("Pausing print")
        printer_status["PrintInfo"]["Status"] = 7  # Paused
        response = create_response(request, {"Ack": 0})
        await mqtt_client.publish(response_topic, json.dumps(response))
        await publish_status(mqtt_client)

    elif cmd == 130:  # Stop Print
        print("Stopping print")
        printer_status["CurrentStatus"] = [0]  # Idle
        printer_status["PrintInfo"]["Status"] = 8  # Stopped
        response = create_response(request, {"Ack": 0})
        await mqtt_client.publish(response_topic, json.dumps(response))
        await publish_status(mqtt_client)

    elif cmd == 131:  # Continue Print
        print("Resuming print")
        printer_status["PrintInfo"]["Status"] = 3  # Printing
        response = create_response(request, {"Ack": 0})
        await mqtt_client.publish(response_topic, json.dumps(response))
        await publish_status(mqtt_client)

    elif cmd == 320:  # Request History Task List
        history_data = {"Ack": 0, "HistoryData": list(print_history.keys())}
        response = create_response(request, history_data)
        await mqtt_client.publish(response_topic, json.dumps(response))

    elif cmd == 321:  # Request History Task Detail Information
        task_ids = request["Data"]["Data"].get("Id", [])
        history_details = [
            print_history[task_id] for task_id in task_ids if task_id in print_history
        ]
        response_data = {"Ack": 0, "HistoryDetailList": history_details}
        response = create_response(request, response_data)
        await mqtt_client.publish(response_topic, json.dumps(response))

    elif cmd == 386:  # Set Video Stream
        enable = request["Data"]["Data"].get("Enable", 0)
        print(f"Setting video stream: {enable}")
        response_data = {"Ack": 0, "VideoUrl": f"http://{PRINTER_IP}:3031/video"}
        response = create_response(request, response_data)
        await mqtt_client.publish(response_topic, json.dumps(response))

    elif cmd == 16:  # Control Device (lights, fans, temps, etc)
        control_data = request["Data"]["Data"]
        print(f"Control device command: {control_data}")

        # Update printer state based on control data
        if "LightStatus" in control_data:
            printer_status["LightStatus"].update(control_data["LightStatus"])
        if "TargetFanSpeed" in control_data:
            printer_status["CurrentFanSpeed"].update(control_data["TargetFanSpeed"])
        if "TempTargetNozzle" in control_data:
            printer_status["TempTargetNozzle"] = control_data["TempTargetNozzle"]
        if "TempTargetHotbed" in control_data:
            printer_status["TempTargetHotbed"] = control_data["TempTargetHotbed"]
        if "PrintSpeedPct" in control_data:
            printer_status["PrintSpeed"] = control_data["PrintSpeedPct"]

        response = create_response(request, {"Ack": 0})
        await mqtt_client.publish(response_topic, json.dumps(response))
        await publish_status(mqtt_client)

    else:
        print(f"Unknown command: {cmd}")
        response = create_response(request, {"Ack": 1})  # Generic error
        await mqtt_client.publish(response_topic, json.dumps(response))


async def status_publisher(mqtt_client, stop_event):
    """Periodically publish status updates."""
    while not stop_event.is_set():
        try:
            await asyncio.sleep(5)  # Publish every 5 seconds
            if not stop_event.is_set():
                await publish_status(mqtt_client)
        except asyncio.CancelledError:
            break
        except (OSError, TimeoutError):
            print("Error publishing status, will retry")


async def mqtt_message_handler(mqtt_client, stop_event):
    """Handle incoming MQTT messages."""
    request_topic = f"sdcp/request/{MAINBOARD_ID}"
    await mqtt_client.subscribe(request_topic)
    print(f"Subscribed to {request_topic}")

    try:
        async for message in mqtt_client.messages:
            if stop_event.is_set():
                break

            try:
                payload = json.loads(message.payload.decode())
                if message.topic.matches(request_topic):
                    await handle_request(mqtt_client, payload)
            except json.JSONDecodeError:
                print(f"Invalid JSON received: {message.payload}")
            except (OSError, TimeoutError, KeyError, ValueError) as e:
                print(f"Error handling message: {e}")
    except asyncio.CancelledError:
        print("Message handler cancelled")


async def main():
    """Main entry point."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    # Set up signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    print(f"Starting MQTT test printer: {PRINTER_NAME}")
    print(f"Mainboard ID: {MAINBOARD_ID}")
    print(f"IP Address: {PRINTER_IP}")
    print(f"Connecting to MQTT broker at {MQTT_HOST}:{MQTT_PORT}")

    # Start UDP discovery server
    udp_task = asyncio.create_task(udp_discovery_server(stop_event))

    try:
        async with aiomqtt.Client(hostname=MQTT_HOST, port=MQTT_PORT) as mqtt_client:
            print("Connected to MQTT broker")

            # Publish initial state
            await publish_attributes(mqtt_client)
            await publish_status(mqtt_client)

            # Start background tasks
            status_task = asyncio.create_task(
                status_publisher(mqtt_client, stop_event)
            )
            handler_task = asyncio.create_task(
                mqtt_message_handler(mqtt_client, stop_event)
            )

            print(f"\nMQTT Printer ready and listening on topics:")
            print(f"  - sdcp/request/{MAINBOARD_ID}")
            print(f"  - Publishing to sdcp/status/{MAINBOARD_ID}")
            print(f"  - Publishing to sdcp/attributes/{MAINBOARD_ID}")
            print(f"  - Publishing to sdcp/response/{MAINBOARD_ID}")
            print("\nPress Ctrl+C to stop\n")

            # Wait for stop signal
            await stop_event.wait()

            # Clean shutdown
            print("\nShutting down...")
            status_task.cancel()
            handler_task.cancel()

            try:
                await asyncio.gather(status_task, handler_task)
            except asyncio.CancelledError:
                pass

            print("MQTT printer shut down gracefully")

    except (OSError, TimeoutError) as e:
        print(f"Failed to connect to MQTT broker: {e}")
        print(f"Make sure an MQTT broker is running on {MQTT_HOST}:{MQTT_PORT}")
        print("You can start one with: mosquitto -v")
    finally:
        udp_task.cancel()
        try:
            await udp_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nPrinter shutting down.")
