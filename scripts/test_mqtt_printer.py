"""
MQTT Test Printer Simulator.

This simulates an Elegoo printer that communicates via MQTT protocol.
It responds to UDP discovery and M66666 MQTT connection commands,
then connects to MQTT broker and publishes status updates.
"""

import asyncio
import json
import os
import random
import signal
import socket
import time
import uuid

import aiomqtt

# Printer configuration
MAINBOARD_ID = "4c851c540107103d00000c0000000000"
PRINTER_IP = "127.0.0.1"
PRINTER_NAME = "Saturn 3 MQTT"
UDP_PORT = 3000
HOST = "0.0.0.0"

# MQTT credentials (can be overridden by environment variables)
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")

# Printer state
print_history = {
    "b9a8b8f8-8b8b-4b8b-8b8b-8b8b8b8b8b8b": {
        "TaskId": "b9a8b8f8-8b8b-4b8b-8b8b-8b8b8b8b8b8b",
        "TaskName": "test_print_1.gcode",
        "BeginTime": int(time.time()) - 200,  # Started 200 seconds ago
        "EndTime": 0,  # Not ended yet (currently printing)
        "TaskStatus": 3,  # Printing
        "Thumbnail": f"http://{PRINTER_IP}:8000/thumb1.jpg",
        "SliceInformation": {},
        "AlreadyPrintLayer": 100,
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
    "ProtocolVersion": "V1.0.0",  # V1.x indicates MQTT protocol
    "FirmwareVersion": "V1.1.29",
    "Resolution": "11520x5120",
    "MainboardIP": PRINTER_IP,
    "MainboardID": MAINBOARD_ID,
    "SDCPStatus": 1,
    "LocalSDCPAddress": f"tcp://{PRINTER_IP}:18830",
    "SDCPAddress": "",
    "Capabilities": ["FILE_TRANSFER", "PRINT_CONTROL"],
}

printer_status = {
    "CurrentStatus": 1,  # Printing (MQTT format: integer, not list)
    "PreviousStatus": 0,
    "PrintInfo": {
        "Status": 3,  # 3 = Printing (Exposuring)
        "CurrentLayer": 100,
        "TotalLayer": 500,
        "CurrentTicks": 2000,
        "TotalTicks": 10000,
        "Filename": "test_print_1.gcode",
        "ErrorNumber": 0,
    },
    "FileTransferInfo": {
        "Status": 0,
        "DownloadOffset": 0,
        "CheckOffset": 0,
        "FileTotalSize": 0,
        "Filename": "",
    },
}


def get_timestamp():
    """Get current timestamp in seconds."""
    return int(time.time())


async def publish_status(mqtt_client):
    """Publish printer status to MQTT."""
    # Real MQTT printers send status nested under "Data" (not like WebSocket)
    status_message = {
        "Id": str(uuid.uuid4()),
        "Data": {
            "Status": printer_status,
            "MainboardID": MAINBOARD_ID,
            "TimeStamp": get_timestamp(),
        },
    }
    topic = f"/sdcp/status/{MAINBOARD_ID}"
    await mqtt_client.publish(topic, json.dumps(status_message))


async def publish_attributes(mqtt_client):
    """Publish printer attributes to MQTT."""
    # Real MQTT printers send attributes nested under "Data" (not like WebSocket)
    attributes_message = {
        "Id": str(uuid.uuid4()),
        "Data": {
            "Attributes": printer_attributes,
            "MainboardID": MAINBOARD_ID,
            "TimeStamp": get_timestamp(),
        },
    }
    topic = f"/sdcp/attributes/{MAINBOARD_ID}"
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


async def handle_request(mqtt_client, request, disconnect_event=None):
    """Handle incoming MQTT request."""
    if "Data" not in request:
        print(f"Invalid request: {request}")
        return

    cmd = request["Data"]["Cmd"]
    request_id = request["Data"].get("RequestID", "unknown")
    print(f"Handling command: {cmd} (RequestID: {request_id})")

    response_topic = f"/sdcp/response/{MAINBOARD_ID}"

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
        print(f"🚀 Starting print for file: {filename}")
        printer_status["CurrentStatus"] = 1  # Printing
        printer_status["PrintInfo"]["Status"] = 3  # Printing/Exposuring
        printer_status["PrintInfo"]["Filename"] = filename
        printer_status["PrintInfo"]["TaskId"] = str(uuid.uuid4())
        printer_status["PrintInfo"]["CurrentLayer"] = 0
        printer_status["PrintInfo"]["TotalLayer"] = random.randint(100, 500)
        printer_status["PrintInfo"]["CurrentTicks"] = 0
        printer_status["PrintInfo"]["TotalTicks"] = printer_status["PrintInfo"][
            "TotalLayer"
        ] * random.randint(10, 20)
        response = create_response(request, {"Ack": 0})
        await mqtt_client.publish(response_topic, json.dumps(response))
        await publish_status(mqtt_client)
        # Note: simulation runs continuously in background, will pick up the change

    elif cmd == 129:  # Pause Print
        print("Pausing print")
        printer_status["PrintInfo"]["Status"] = 7  # Paused
        response = create_response(request, {"Ack": 0})
        await mqtt_client.publish(response_topic, json.dumps(response))
        await publish_status(mqtt_client)

    elif cmd == 130:  # Stop Print
        print("Stopping print")
        printer_status["CurrentStatus"] = 0  # Idle
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

    elif cmd == 512:  # Set Status Update Period
        time_period = request["Data"]["Data"].get("TimePeriod", 5000)
        print(f"📡 Setting auto-push status update period: {time_period}ms")
        # Acknowledge the command
        response = create_response(request, {"Ack": 0})
        await mqtt_client.publish(response_topic, json.dumps(response))
        # Note: The actual periodic publishing is handled by status_publisher()

    elif cmd == 64:  # Disconnect
        print("🔌 Received disconnect command from client")
        response = create_response(request, {"Ack": 0})
        await mqtt_client.publish(response_topic, json.dumps(response))
        # Signal disconnection to close the MQTT connection
        if disconnect_event:
            print("🛑 Initiating disconnect from MQTT broker...")
            disconnect_event.set()

    elif cmd == 16:  # Control Device (lights, fans, temps, etc)
        control_data = request["Data"]["Data"]
        print(f"Control device command: {control_data}")
        # MQTT resin printers don't support control device commands
        # Just acknowledge the request without updating any state
        response = create_response(request, {"Ack": 0})
        await mqtt_client.publish(response_topic, json.dumps(response))

    else:
        print(f"Unknown command: {cmd}")
        response = create_response(request, {"Ack": 1})  # Generic error
        await mqtt_client.publish(response_topic, json.dumps(response))


async def simulate_printing(stop_event):
    """Simulate printing progress by updating printer status."""
    print("🔧 Starting print simulation...")
    pi = printer_status["PrintInfo"]

    while not stop_event.is_set() and pi["CurrentLayer"] < pi["TotalLayer"]:
        if printer_status["CurrentStatus"] != 1:  # Not printing
            break

        await asyncio.sleep(2)  # Update every 2 seconds

        # Increment progress
        pi["CurrentLayer"] += 1
        pi["CurrentTicks"] += random.randint(15, 25)

        print(
            f"📊 Layer {pi['CurrentLayer']}/{pi['TotalLayer']} "
            f"({pi['CurrentLayer'] / pi['TotalLayer'] * 100:.1f}%)"
        )

    if not stop_event.is_set() and pi["CurrentLayer"] >= pi["TotalLayer"]:
        print("✅ Print simulation completed")
        printer_status["CurrentStatus"] = 0  # Idle
        printer_status["PrintInfo"]["Status"] = 16  # Complete
        # Update history
        task_id = printer_status["PrintInfo"]["TaskId"]
        if task_id in print_history:
            print_history[task_id]["EndTime"] = get_timestamp()
            print_history[task_id]["TaskStatus"] = 9  # Complete


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


async def mqtt_message_handler(mqtt_client, stop_event, disconnect_event):
    """Handle incoming MQTT messages."""
    request_topic = f"/sdcp/request/{MAINBOARD_ID}"
    await mqtt_client.subscribe(request_topic)
    print(f"Subscribed to {request_topic}")

    try:
        async for message in mqtt_client.messages:
            if stop_event.is_set() or disconnect_event.is_set():
                break

            try:
                payload = json.loads(message.payload.decode())
                topic_str = str(message.topic)
                if topic_str == request_topic:
                    await handle_request(mqtt_client, payload, disconnect_event)
            except json.JSONDecodeError:
                print(f"Invalid JSON received: {message.payload}")
            except (OSError, TimeoutError, KeyError, ValueError) as e:
                print(f"Error handling message: {e}")
    except asyncio.CancelledError:
        print("Message handler cancelled")


async def mqtt_connection_manager(mqtt_connect_event, mqtt_broker_info, stop_event):
    """Manage MQTT connection after receiving M66666 command."""
    # Wait for M66666 command to trigger connection
    await mqtt_connect_event.wait()

    if stop_event.is_set():
        return

    broker_host = mqtt_broker_info.get("host")
    broker_port = mqtt_broker_info.get("port")
    broker_username = mqtt_broker_info.get("username") or MQTT_USERNAME
    broker_password = mqtt_broker_info.get("password") or MQTT_PASSWORD

    print(f"\n🔌 Connecting to MQTT broker at {broker_host}:{broker_port}...")

    # Create disconnect event to handle graceful disconnect on CMD_DISCONNECT
    disconnect_event = asyncio.Event()

    try:
        # Build MQTT client configuration
        mqtt_kwargs = {
            "hostname": broker_host,
            "port": broker_port,
        }
        if broker_username:
            mqtt_kwargs["username"] = broker_username
        if broker_password:
            mqtt_kwargs["password"] = broker_password

        async with aiomqtt.Client(**mqtt_kwargs) as mqtt_client:
            if broker_username:
                print(
                    f"✅ Connected to MQTT broker (authenticated as {broker_username})"
                )
            else:
                print("✅ Connected to MQTT broker")

            # Publish initial state
            await publish_attributes(mqtt_client)
            await publish_status(mqtt_client)

            # Start background tasks
            status_task = asyncio.create_task(status_publisher(mqtt_client, stop_event))
            handler_task = asyncio.create_task(
                mqtt_message_handler(mqtt_client, stop_event, disconnect_event)
            )
            simulation_task = asyncio.create_task(simulate_printing(stop_event))

            print(f"\n📡 MQTT Printer ready and listening on topics:")
            print(f"  - /sdcp/request/{MAINBOARD_ID}")
            print(f"  - Publishing to /sdcp/status/{MAINBOARD_ID}")
            print(f"  - Publishing to /sdcp/attributes/{MAINBOARD_ID}")
            print(f"  - Publishing to /sdcp/response/{MAINBOARD_ID}")

            # Wait for stop signal or disconnect command
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(stop_event.wait()),
                    asyncio.create_task(disconnect_event.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel pending waits
            for task in pending:
                task.cancel()

            if disconnect_event.is_set():
                print("\n🔌 Disconnect command received, closing MQTT connection...")
            else:
                print("\n🛑 Stop signal received, shutting down MQTT connection...")

            # Clean shutdown
            print("\n🛑 Shutting down MQTT connection...")
            status_task.cancel()
            handler_task.cancel()
            simulation_task.cancel()

            try:
                await asyncio.gather(status_task, handler_task, simulation_task)
            except asyncio.CancelledError:
                pass

            print("✅ MQTT connection shut down gracefully")

            # If disconnected by command (not stop signal), wait for new M66666 to reconnect
            if disconnect_event.is_set() and not stop_event.is_set():
                print("\n⏳ Waiting for new M66666 command to reconnect...")
                mqtt_connect_event.clear()  # Reset the connect event
                # Recursively call to handle reconnection
                await mqtt_connection_manager(
                    mqtt_connect_event, mqtt_broker_info, stop_event
                )

    except (OSError, TimeoutError) as e:
        print(f"❌ Failed to connect to MQTT broker: {e}")
        print(f"💡 Make sure an MQTT broker is running on {broker_host}:{broker_port}")
        print("   You can start one with: mosquitto -v")


async def udp_discovery_server(mqtt_connect_event, mqtt_broker_info, stop_event):
    """Handle UDP discovery requests and MQTT connection commands."""
    loop = asyncio.get_running_loop()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1)
        sock.bind((HOST, UDP_PORT))
        print(f"📡 UDP discovery server listening on {HOST}:{UDP_PORT}")

        while not stop_event.is_set():
            try:
                data, addr = await loop.run_in_executor(None, sock.recvfrom, 1024)
                message = data.decode("utf-8")

                if message == "M99999":
                    print(f"🔍 Received discovery request from {addr}")
                    # Using legacy Saturn format for compatibility
                    # MQTT printers don't send BrandName, so it's omitted
                    response = {
                        "Id": str(uuid.uuid4()),
                        "Data": {
                            "Attributes": {
                                "Name": PRINTER_NAME,
                                "MachineName": "Saturn 3",
                                "MainboardIP": PRINTER_IP,
                                "MainboardID": MAINBOARD_ID,
                                "ProtocolVersion": printer_attributes[
                                    "ProtocolVersion"
                                ],
                                "FirmwareVersion": printer_attributes[
                                    "FirmwareVersion"
                                ],
                            },
                            "Status": {
                                "CurrentStatus": [printer_status["CurrentStatus"]],
                                "PrintInfo": printer_status["PrintInfo"],
                                "FileTransferInfo": {
                                    "Status": 0,
                                    "DownloadOffset": 0,
                                    "FileTotalSize": 0,
                                    "Filename": "",
                                },
                            },
                        },
                    }
                    sock.sendto(json.dumps(response).encode("utf-8"), addr)
                    print(f"✅ Sent discovery response to {addr}")

                elif message.startswith("M66666"):
                    # MQTT connection command: M66666 <host> <port> [username] [password]
                    # Also supports legacy format: M66666 <port> (uses source IP as host)
                    parts = message.split()
                    if len(parts) >= 3:
                        # New format: M66666 <host> <port> [username] [password]
                        mqtt_host = parts[1]
                        try:
                            mqtt_port = int(parts[2])
                            if not (1 <= mqtt_port <= 65535):
                                raise ValueError("port out of range")
                        except ValueError:
                            print(f"⚠️  Invalid port in M66666 from {addr}: {parts[2]}")
                            continue
                        mqtt_username = parts[3] if len(parts) > 3 else None
                        mqtt_password = parts[4] if len(parts) > 4 else None

                        print(f"\n🎯 Received M66666 command from {addr}")
                        print(f"   Broker: {mqtt_host}:{mqtt_port}")
                        if mqtt_username:
                            print(f"   Username: {mqtt_username}")

                        mqtt_broker_info["host"] = mqtt_host
                        mqtt_broker_info["port"] = mqtt_port
                        mqtt_broker_info["username"] = mqtt_username
                        mqtt_broker_info["password"] = mqtt_password
                        mqtt_connect_event.set()
                    elif len(parts) == 2:
                        # Legacy format: M66666 <port> (use source IP)
                        try:
                            mqtt_port = int(parts[1])
                            if not (1 <= mqtt_port <= 65535):
                                raise ValueError("port out of range")
                        except ValueError:
                            print(
                                f"⚠️  Invalid port in legacy M66666 from {addr}: {parts[1]}"
                            )
                            continue
                        print(
                            f"\n🎯 Received M66666 command from {addr} (legacy format)"
                        )
                        print(f"   Broker: {addr[0]}:{mqtt_port}")
                        mqtt_broker_info["host"] = addr[0]  # Use source IP
                        mqtt_broker_info["port"] = mqtt_port
                        mqtt_broker_info["username"] = None
                        mqtt_broker_info["password"] = None
                        mqtt_connect_event.set()
                    else:
                        print(
                            f"⚠️  Received M66666 command from {addr} (invalid format)"
                        )

            except (socket.timeout, UnicodeDecodeError):
                continue
            except OSError as e:
                if not stop_event.is_set():
                    print(f"❌ UDP error: {e}")

    print("✅ UDP discovery server shut down")


async def main():
    """Main entry point."""
    stop_event = asyncio.Event()
    mqtt_connect_event = asyncio.Event()
    mqtt_broker_info = {}  # Shared dict to store broker info from M66666
    loop = asyncio.get_running_loop()

    # Set up signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    print("=" * 70)
    print(f"🖨️  MQTT Test Printer Simulator")
    print("=" * 70)
    print(f"Printer Name:  {PRINTER_NAME}")
    print(f"Mainboard ID:  {MAINBOARD_ID}")
    print(f"IP Address:    {PRINTER_IP}")
    print(f"UDP Port:      {UDP_PORT}")
    print("=" * 70)
    print("\n⏳ Waiting for M66666 command to connect to MQTT broker...")
    print("💡 Tip: Run discovery or send M66666 command to trigger MQTT connection\n")

    # Start UDP discovery server
    udp_task = asyncio.create_task(
        udp_discovery_server(mqtt_connect_event, mqtt_broker_info, stop_event)
    )

    # Start MQTT connection manager (waits for M66666 command)
    mqtt_task = asyncio.create_task(
        mqtt_connection_manager(mqtt_connect_event, mqtt_broker_info, stop_event)
    )

    try:
        # Wait for stop signal
        await stop_event.wait()
    except KeyboardInterrupt:
        print("\n\n⚠️  Received interrupt signal")
        stop_event.set()
    finally:
        print("\n🛑 Shutting down printer simulator...")

        # Cancel tasks
        udp_task.cancel()
        mqtt_task.cancel()

        try:
            await asyncio.gather(udp_task, mqtt_task, return_exceptions=True)
        except asyncio.CancelledError:
            pass

        print("✅ Printer simulator shut down\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
