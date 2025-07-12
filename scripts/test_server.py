import asyncio
import http.server
import json
import os
import random
import signal
import socket
import socketserver
import time
import uuid

import websockets

# Server configuration
HOST = "0.0.0.0"
HTTP_PORT = 8000
WS_PORT = 3030
UDP_PORT = 3000
MAINBOARD_ID = "000000000001d354"
PRINTER_IP = "127.0.0.1"


# Printer state
print_history = {
    "b9a8b8f8-8b8b-4b8b-8b8b-8b8b8b8b8b8b": {
        "TaskId": "b9a8b8f8-8b8b-4b8b-8b8b-8b8b8b8b8b8b",
        "TaskName": "test_print_1.gcode",
        "BeginTime": 1678886400,
        "EndTime": 1678890000,
        "TaskStatus": 9,
        "Thumbnail": f"http://{PRINTER_IP}:{HTTP_PORT}/thumb1.jpg",
        "SliceInformation": {},
        "AlreadyPrintLayer": 500,
        "MD5": "d41d8cd98f00b204e9800998ecf8427e",
        "CurrentLayerTalVolume": 15.5,
        "TimeLapseVideoStatus": 1,
        "TimeLapseVideoUrl": f"http://{PRINTER_IP}:{HTTP_PORT}/video1.mp4",
        "ErrorStatusReason": 0,
    },
    "c9a8b8f8-8b8b-4b8b-8b8b-8b8b8b8b8b8b": {
        "TaskId": "c9a8b8f8-8b8b-4b8b-8b8b-8b8b8b8b8b8b",
        "TaskName": "test_print_2.gcode",
        "BeginTime": 1678972800,
        "EndTime": 1678976400,
        "TaskStatus": 9,
        "Thumbnail": f"http://{PRINTER_IP}:{HTTP_PORT}/thumb2.jpg",
        "SliceInformation": {},
        "AlreadyPrintLayer": 800,
        "MD5": "e4d909c290d0fb1ca068ffaddf22cbd0",
        "CurrentLayerTalVolume": 25.0,
        "TimeLapseVideoStatus": 0,
        "TimeLapseVideoUrl": "",
        "ErrorStatusReason": 0,
    },
    "d9a8b8f8-8b8b-4b8b-8b8b-8b8b8b8b8b8b": {
        "TaskId": "d9a8b8f8-8b8b-4b8b-8b8b-8b8b8b8b8b8b",
        "TaskName": "failed_print.gcode",
        "BeginTime": 1679059200,
        "EndTime": 1679061000,
        "TaskStatus": 8,  # Stopped
        "Thumbnail": f"http://{PRINTER_IP}:{HTTP_PORT}/thumb3.jpg",
        "SliceInformation": {},
        "AlreadyPrintLayer": 150,
        "MD5": "a3cca2b2aa1e3b5b3b5b3b5b3b5b3b5b",
        "CurrentLayerTalVolume": 5.2,
        "TimeLapseVideoStatus": 0,
        "TimeLapseVideoUrl": "",
        "ErrorStatusReason": 1,
    },
}
printer_attributes = {
    "Name": "Centauri Carbon Test",
    "MachineName": "Centauri Carbon",
    "BrandName": "Centauri",
    "ProtocolVersion": "V3.0.0",
    "FirmwareVersion": "V1.0.0",
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
        "TaskId": "b9a8b8f8-8b8b-4b8b-8b8b-8b8b8b8b8b8b",
        "PrintSpeed": 100,
    },
}

connected_clients = set()


def get_timestamp():
    return int(time.time())


def create_response(request_data, data):
    return {
        "Id": str(uuid.uuid4()),
        "Data": {
            "Cmd": request_data["Data"]["Cmd"],
            "Data": data,
            "RequestID": request_data["Data"]["RequestID"],
            "MainboardID": MAINBOARD_ID,
            "TimeStamp": get_timestamp(),
        },
        "Topic": f"sdcp/response/{MAINBOARD_ID}",
    }


def create_push_message(topic, data):
    return {
        "Id": str(uuid.uuid4()),
        "Data": data,
        "Topic": topic,
    }


async def send_status_update(websocket):
    status_data = {
        "Status": printer_status,
        "MainboardID": MAINBOARD_ID,
        "TimeStamp": get_timestamp(),
        "Topic": f"sdcp/status/{MAINBOARD_ID}",
    }
    print("Sending status update")
    await websocket.send(json.dumps(status_data))


async def send_attributes_update(websocket):
    attributes_data = {
        "Attributes": printer_attributes,
        "MainboardID": MAINBOARD_ID,
        "TimeStamp": get_timestamp(),
    }
    message = create_push_message(f"sdcp/attributes/{MAINBOARD_ID}", attributes_data)
    await websocket.send(json.dumps(message))


async def send_history_update(websocket):
    history_data = {
        "Ack": 0,
        "HistoryData": list(print_history.keys()),
    }
    inner_message = {
        "Cmd": 320,
        "Data": history_data,
        "RequestID": 0,
        "MainboardID": MAINBOARD_ID,
        "TimeStamp": get_timestamp(),
    }
    message = create_push_message(f"sdcp/response/{MAINBOARD_ID}", inner_message)
    await websocket.send(json.dumps(message))


async def send_history_detail(websocket, request_data):
    task_ids = request_data["Data"]["Data"]["Id"]
    history_details = [
        print_history[task_id] for task_id in task_ids if task_id in print_history
    ]
    response_data = {"Ack": 0, "HistoryDetailList": history_details}
    response = create_response(request_data, response_data)
    await websocket.send(json.dumps(response))


async def simulate_printing():
    print("Starting print simulation")
    pi = printer_status["PrintInfo"]
    while pi["CurrentLayer"] < pi["TotalLayer"] and printer_status["CurrentStatus"] == [
        1
    ]:
        await asyncio.sleep(5)
        pi["CurrentLayer"] += 1
        pi["CurrentTicks"] += random.randint(10, 20)
        printer_status["CurrenCoord"] = (
            f"{random.uniform(0, 300):.1f},{random.uniform(0, 300):.1f},{pi['CurrentLayer'] * 0.2:.2f}"
        )

    if printer_status["CurrentStatus"] == [1]:
        print("Print simulation finished")
        printer_status["CurrentStatus"] = [0]  # Idle
        printer_status["PrintInfo"]["Status"] = 9  # Complete
        task_id = printer_status["PrintInfo"]["TaskId"]
        print_history[task_id] = {
            "TaskId": task_id,
            "TaskName": printer_status["PrintInfo"]["Filename"],
            "BeginTime": get_timestamp() - printer_status["PrintInfo"]["TotalTicks"],
            "EndTime": get_timestamp(),
            "TaskStatus": 9,
        }


async def handler(websocket):
    print(f"Client connected from {websocket.remote_address}")
    connected_clients.add(websocket)
    try:
        # On connect, send initial status and attributes
        await send_attributes_update(websocket)
        await send_status_update(websocket)
        await send_history_update(websocket)

        async for message in websocket:
            if message == "ping":
                await websocket.send("pong")
                continue

            try:
                data = json.loads(message)
                if data.get("Topic", "").startswith("sdcp/request"):
                    await handle_request(websocket, data)
            except json.JSONDecodeError:
                print(f"Invalid JSON received: {message}")
            except Exception as e:
                print(f"Error processing message: {e}")

    except websockets.exceptions.ConnectionClosed:
        print(f"Client disconnected: {websocket.remote_address}")
    finally:
        connected_clients.remove(websocket)


async def handle_request(websocket, request):
    if "Data" not in request:
        print(f"Invalid request: {request}")
        return
    cmd = request["Data"]["Cmd"]
    print(f"Handling command: {cmd}")

    if cmd == 0:  # Request Status Refresh
        response = create_response(request, {"Ack": 0})
        await websocket.send(json.dumps(response))
        await send_status_update(websocket)
    elif cmd == 1:  # Request Attributes
        response = create_response(request, {"Ack": 0})
        await websocket.send(json.dumps(response))
        await send_attributes_update(websocket)
    elif cmd == 128:  # Start Print
        filename = request["Data"]["Data"]["Filename"]
        print(f"Starting print for file: {filename}")
        printer_status["CurrentStatus"] = [1]  # Printing
        printer_status["PrintInfo"]["Status"] = 1  # Homing
        printer_status["PrintInfo"]["Filename"] = filename
        printer_status["PrintInfo"]["TaskId"] = str(uuid.uuid4())
        printer_status["PrintInfo"]["TotalLayer"] = random.randint(100, 1000)
        printer_status["PrintInfo"]["TotalTicks"] = printer_status["PrintInfo"][
            "TotalLayer"
        ] * random.randint(10, 20)
        response = create_response(request, {"Ack": 0})
        await websocket.send(json.dumps(response))
        # Simulate printing progress
        asyncio.create_task(simulate_printing())
    elif cmd == 130:  # Stop Print
        print("Stopping print")
        printer_status["CurrentStatus"] = [0]  # Idle
        printer_status["PrintInfo"]["Status"] = 8  # Stopped
        response = create_response(request, {"Ack": 0})
        await websocket.send(json.dumps(response))
    elif cmd == 320:  # Request History Task List
        response = create_response(request, {"Ack": 0})
        await websocket.send(json.dumps(response))
        await send_history_update(websocket)
    elif cmd == 321:  # Request History Task Detail Information
        response = create_response(request, {"Ack": 0})
        await websocket.send(json.dumps(response))
        await send_history_detail(websocket, request)
    elif cmd == 322:  # Delete History Task
        task_ids = request["Data"]["Data"]["Id"]
        for task_id in task_ids:
            if task_id in print_history:
                del print_history[task_id]
        response = create_response(request, {"Ack": 0})
        await websocket.send(json.dumps(response))
    else:
        print(f"Unknown command: {cmd}")
        response = create_response(request, {"Ack": 1})  # Generic error
        await websocket.send(json.dumps(response))


async def udp_server(stop_event):
    loop = asyncio.get_running_loop()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(1)
        s.bind((HOST, UDP_PORT))
        print(f"UDP server listening on {HOST}:{UDP_PORT}")
        while not stop_event.is_set():
            try:
                data, addr = await loop.run_in_executor(None, s.recvfrom, 1024)
                if data == b"M99999":
                    print(f"Received discovery request from {addr}")
                    response = {
                        "Id": str(uuid.uuid4()),
                        "Data": {
                            "Name": printer_attributes["Name"],
                            "MachineName": printer_attributes["MachineName"],
                            "BrandName": printer_attributes["BrandName"],
                            "MainboardIP": PRINTER_IP,
                            "MainboardID": MAINBOARD_ID,
                            "ProtocolVersion": printer_attributes["ProtocolVersion"],
                            "FirmwareVersion": printer_attributes["FirmwareVersion"],
                        },
                    }
                    s.sendto(json.dumps(response).encode("utf-8"), addr)
            except socket.timeout:
                continue
    print("UDP server shut down.")


async def http_server(stop_event):
    # Serve files from the script's directory without changing cwd
    script_dir = os.path.dirname(os.path.abspath(__file__))

    class CustomHTTPHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=script_dir, **kwargs)

    handler = CustomHTTPHandler
    loop = asyncio.get_running_loop()
    with socketserver.TCPServer(("", HTTP_PORT), handler) as httpd:
        print(f"HTTP server listening on {HOST}:{HTTP_PORT}")
        httpd.timeout = 1
        while not stop_event.is_set():
            await loop.run_in_executor(None, httpd.handle_request)
    print("HTTP server shut down.")


async def main():
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()

    # Set up signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # Start UDP and HTTP servers in separate tasks
    udp_task = asyncio.create_task(udp_server(stop_event))
    http_task = asyncio.create_task(http_server(stop_event))

    # Grab the first task from history to be the "current" print
    first_task_id = next(iter(print_history))
    first_task = print_history[first_task_id]

    # Set initial printing status
    printer_status["CurrentStatus"] = [1]  # 1 = Printing
    printer_status["PrintInfo"]["Status"] = 3  # 3 = Exposuring
    printer_status["PrintInfo"]["Filename"] = first_task["TaskName"]
    printer_status["PrintInfo"]["TaskId"] = first_task["TaskId"]
    printer_status["PrintInfo"]["TotalLayer"] = 500
    printer_status["PrintInfo"]["CurrentLayer"] = 100
    printer_status["PrintInfo"]["TotalTicks"] = (
        first_task["EndTime"] - first_task["BeginTime"]
    ) * 1000
    printer_status["PrintInfo"]["CurrentTicks"] = 1000

    # Start WebSocket server
    server = await websockets.serve(handler, HOST, WS_PORT)
    print(f"WebSocket server started on {HOST}:{WS_PORT}")
    # Simulate printing progress in the background
    simulation_task = asyncio.create_task(simulate_printing())

    # Wait for the stop event
    await stop_event.wait()

    # Cleanly shut down all tasks
    simulation_task.cancel()
    server.close()
    await server.wait_closed()
    print("WebSocket server shut down.")
    await udp_task
    await http_task
    print("All servers shut down gracefully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Server shutting down.")
