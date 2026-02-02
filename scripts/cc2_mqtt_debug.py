#!/usr/bin/env python3
"""
CC2 MQTT Debug Script - Standalone Data Extraction

This script connects to a Centauri Carbon 2 printer via MQTT and captures
all protocol messages. It has MINIMAL dependencies - no Home Assistant required.

Dependencies (install with pip):
    pip install paho-mqtt

Usage:
    python cc2_mqtt_debug.py <printer_ip>
    python cc2_mqtt_debug.py 192.168.1.100

The script will:
1. Discover the CC2 via UDP (port 52700)
2. Connect to the printer's MQTT broker (port 1883)
3. Register as a client
4. Send status/attribute queries
5. Save all messages to a JSON file

Output is saved to: cc2_debug_<timestamp>.json
"""

import asyncio
import json
import secrets
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

# Check for paho-mqtt
try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("ERROR: paho-mqtt is required. Install with:")
    print("  pip install paho-mqtt")
    sys.exit(1)


# =============================================================================
# CC2 PROTOCOL CONSTANTS
# =============================================================================

# Discovery
CC2_DISCOVERY_PORT = 52700
CC2_DISCOVERY_MESSAGE = '{"id": 0, "method": 7000}'
CC2_DISCOVERY_TIMEOUT = 10

# MQTT
CC2_MQTT_PORT = 1883
CC2_MQTT_USERNAME = "bblp"
CC2_MQTT_PASSWORD = ""  # Empty by default, or access code

# Command IDs
CC2_CMD_GET_ATTRIBUTES = 1001
CC2_CMD_GET_STATUS = 1002
CC2_CMD_SET_VIDEO_STREAM = 1050
CC2_CMD_SET_FAN_SPEED = 1030
CC2_CMD_SET_LIGHT = 1029


# =============================================================================
# CC2 DISCOVERY
# =============================================================================

def discover_cc2(target_ip: str | None = None) -> dict | None:
    """Discover CC2 printer via UDP."""
    broadcast_addr = target_ip or "255.255.255.255"

    print(f"[DISCOVERY] Sending to {broadcast_addr}:{CC2_DISCOVERY_PORT}")
    print(f"[DISCOVERY] Message: {CC2_DISCOVERY_MESSAGE}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(CC2_DISCOVERY_TIMEOUT)

    try:
        sock.sendto(CC2_DISCOVERY_MESSAGE.encode(), (broadcast_addr, CC2_DISCOVERY_PORT))

        while True:
            try:
                data, addr = sock.recvfrom(8192)
                print(f"[DISCOVERY] Response from {addr[0]}:{addr[1]}")

                try:
                    response = json.loads(data.decode("utf-8"))
                    print(json.dumps(response, indent=2))

                    # Extract printer info
                    result = response.get("result", {})
                    return {
                        "ip": addr[0],
                        "port": addr[1],
                        "host_name": result.get("host_name"),
                        "machine_model": result.get("machine_model"),
                        "serial_number": result.get("sn"),
                        "protocol_version": result.get("protocol_version"),
                        "raw_response": response,
                    }
                except json.JSONDecodeError:
                    print(f"[DISCOVERY] Non-JSON response: {data}")

            except socket.timeout:
                break

    except OSError as e:
        print(f"[DISCOVERY] Socket error: {e}")
    finally:
        sock.close()

    return None


# =============================================================================
# CC2 MQTT CLIENT
# =============================================================================

class CC2MQTTDebugger:
    """Debug client for CC2 MQTT protocol."""

    def __init__(self, printer_ip: str, serial_number: str, output_file: Path):
        self.printer_ip = printer_ip
        self.serial_number = serial_number
        self.output_file = output_file

        # Generate unique client ID
        self.client_id = f"debug_{secrets.token_hex(4)}"
        self.request_id = f"{self.client_id}_req"

        # MQTT client
        self.client = mqtt.Client(
            client_id=self.client_id,
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )

        # Message tracking
        self.messages: list[dict] = []
        self.request_counter = 0
        self.registered = False
        self.registration_event = asyncio.Event()

        # Set up callbacks
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        # Initialize output file
        self._init_output_file()

    def _init_output_file(self):
        """Initialize the output file with metadata."""
        metadata = {
            "timestamp": datetime.now().isoformat(),
            "printer_ip": self.printer_ip,
            "serial_number": self.serial_number,
            "client_id": self.client_id,
            "messages": [],
        }
        with open(self.output_file, "w") as f:
            json.dump(metadata, f, indent=2)

    def _save_message(self, direction: str, topic: str, payload: dict):
        """Save a message to the output file."""
        msg = {
            "timestamp": datetime.now().isoformat(),
            "direction": direction,
            "topic": topic,
            "payload": payload,
        }
        self.messages.append(msg)

        # Print to console
        arrow = ">>>" if direction == "send" else "<<<"
        print(f"\n{arrow} [{direction.upper()}] {topic}")
        print(json.dumps(payload, indent=2))

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        """Handle MQTT connection."""
        print(f"\n[MQTT] Connected with result code: {reason_code}")

        # Subscribe to topics
        sn = self.serial_number
        topics = [
            (f"elegoo/{sn}/{self.client_id}/api_response", 0),
            (f"elegoo/{sn}/api_status", 0),
            (f"elegoo/{sn}/{self.request_id}/register_response", 0),
        ]

        for topic, qos in topics:
            client.subscribe(topic)
            print(f"[MQTT] Subscribed to: {topic}")

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT message."""
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            self._save_message("recv", msg.topic, payload)

            # Check for registration response
            if "register_response" in msg.topic:
                error = payload.get("error", "")
                if error == "ok":
                    print("\n[REGISTER] Successfully registered!")
                    self.registered = True
                else:
                    print(f"\n[REGISTER] Failed: {error}")

            # Check for PONG
            if payload.get("type") == "PONG":
                print("[HEARTBEAT] Received PONG")

        except json.JSONDecodeError:
            print(f"[MQTT] Non-JSON message on {msg.topic}: {msg.payload}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        """Handle MQTT disconnection."""
        print(f"\n[MQTT] Disconnected with result code: {reason_code}")

    def connect(self, username: str = CC2_MQTT_USERNAME, password: str = CC2_MQTT_PASSWORD) -> bool:
        """Connect to the CC2 MQTT broker."""
        print(f"\n[MQTT] Connecting to {self.printer_ip}:{CC2_MQTT_PORT}")
        print(f"[MQTT] Username: {username}, Password: {'***' if password else '(empty)'}")

        try:
            self.client.username_pw_set(username, password)
            self.client.connect(self.printer_ip, CC2_MQTT_PORT, keepalive=60)
            self.client.loop_start()
            time.sleep(2)  # Wait for connection
            return self.client.is_connected()
        except Exception as e:
            print(f"[MQTT] Connection failed: {e}")
            return False

    def register(self) -> bool:
        """Register with the CC2 printer."""
        topic = f"elegoo/{self.serial_number}/api_register"
        payload = {
            "client_id": self.client_id,
            "request_id": self.request_id,
        }

        print(f"\n[REGISTER] Sending registration...")
        self._save_message("send", topic, payload)
        self.client.publish(topic, json.dumps(payload))

        # Wait for registration response
        for _ in range(50):  # 5 second timeout
            if self.registered:
                return True
            time.sleep(0.1)

        print("[REGISTER] Timeout waiting for registration response")
        return False

    def send_command(self, method: int, params: dict | None = None) -> bool:
        """Send a command to the CC2."""
        if not self.client.is_connected():
            print("[MQTT] Not connected!")
            return False

        self.request_counter += 1
        topic = f"elegoo/{self.serial_number}/{self.client_id}/api_request"
        payload = {
            "id": self.request_counter,
            "method": method,
            "params": params or {},
        }

        self._save_message("send", topic, payload)
        self.client.publish(topic, json.dumps(payload))
        return True

    def send_ping(self):
        """Send heartbeat PING."""
        topic = f"elegoo/{self.serial_number}/{self.client_id}/api_request"
        payload = {"type": "PING"}

        self._save_message("send", topic, payload)
        self.client.publish(topic, json.dumps(payload))

    def save_output(self):
        """Save all messages to the output file."""
        output = {
            "timestamp": datetime.now().isoformat(),
            "printer_ip": self.printer_ip,
            "serial_number": self.serial_number,
            "client_id": self.client_id,
            "total_messages": len(self.messages),
            "messages": self.messages,
        }

        with open(self.output_file, "w") as f:
            json.dump(output, f, indent=2)

        print(f"\n[OUTPUT] Saved {len(self.messages)} messages to: {self.output_file}")

    def disconnect(self):
        """Disconnect from the broker."""
        self.client.loop_stop()
        self.client.disconnect()


# =============================================================================
# MAIN
# =============================================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python cc2_mqtt_debug.py <printer_ip> [mqtt_password]")
        print("Example: python cc2_mqtt_debug.py 192.168.1.100")
        print("Example: python cc2_mqtt_debug.py 192.168.1.100 mypassword")
        sys.exit(1)

    printer_ip = sys.argv[1]
    mqtt_password = sys.argv[2] if len(sys.argv) > 2 else CC2_MQTT_PASSWORD

    print("=" * 60)
    print("CC2 MQTT Debug Script")
    print("=" * 60)

    # Step 1: Discover printer
    print("\n[STEP 1] Discovering CC2 printer...")
    printer_info = discover_cc2(printer_ip)

    if not printer_info:
        print("\nERROR: Could not discover CC2 printer.")
        print("Make sure:")
        print("  - The printer is powered on")
        print("  - You're on the same network")
        print("  - The IP address is correct")
        sys.exit(1)

    serial_number = printer_info.get("serial_number")
    if not serial_number:
        print("\nERROR: Could not get serial number from discovery response.")
        sys.exit(1)

    print(f"\nPrinter found:")
    print(f"  Name: {printer_info.get('host_name')}")
    print(f"  Model: {printer_info.get('machine_model')}")
    print(f"  Serial: {serial_number}")

    # Create output file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = Path(f"cc2_debug_{timestamp}.json")

    # Step 2: Connect to MQTT
    print("\n[STEP 2] Connecting to MQTT broker...")
    debugger = CC2MQTTDebugger(printer_ip, serial_number, output_file)

    # Try different credential combinations
    credentials = [
        (CC2_MQTT_USERNAME, mqtt_password),
        ("admin", mqtt_password),
        ("elegoo", mqtt_password),
        (CC2_MQTT_USERNAME, ""),
    ]

    connected = False
    for username, password in credentials:
        print(f"\n  Trying: username='{username}', password='{'***' if password else '(empty)'}'")
        if debugger.connect(username, password):
            connected = True
            break
        time.sleep(1)

    if not connected:
        print("\nERROR: Could not connect to MQTT broker.")
        print("Try providing the password from Elegoo Slicer logs:")
        print(f"  python {sys.argv[0]} {printer_ip} <password>")
        sys.exit(1)

    # Step 3: Register
    print("\n[STEP 3] Registering with printer...")
    if not debugger.register():
        print("\nWARNING: Registration may have failed, continuing anyway...")

    # Step 4: Send queries
    print("\n[STEP 4] Sending queries...")
    time.sleep(1)

    print("\n  Requesting attributes (method 1001)...")
    debugger.send_command(CC2_CMD_GET_ATTRIBUTES)
    time.sleep(2)

    print("\n  Requesting status (method 1002)...")
    debugger.send_command(CC2_CMD_GET_STATUS)
    time.sleep(2)

    print("\n  Requesting video stream info (method 1050)...")
    debugger.send_command(CC2_CMD_SET_VIDEO_STREAM, {"enable": 1})
    time.sleep(2)

    print("\n  Sending heartbeat PING...")
    debugger.send_ping()
    time.sleep(2)

    # Step 5: Listen for status updates
    print("\n[STEP 5] Listening for status updates (30 seconds)...")
    print("  (Press Ctrl+C to stop early)")
    try:
        for i in range(30):
            time.sleep(1)
            if i % 10 == 0 and i > 0:
                print(f"  ... {30-i} seconds remaining")
                debugger.send_ping()
    except KeyboardInterrupt:
        print("\n  Stopped by user.")

    # Save and disconnect
    print("\n[STEP 6] Saving output...")
    debugger.save_output()
    debugger.disconnect()

    print("\n" + "=" * 60)
    print("Done!")
    print(f"Output saved to: {output_file}")
    print("=" * 60)


if __name__ == "__main__":
    # Windows compatibility
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
