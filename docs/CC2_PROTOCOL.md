# Centauri Carbon 2 (CC2) Protocol Documentation

> Community documentation for Elegoo CC2 printer protocols

This document is the authoritative reference for the Elegoo Centauri Carbon 2 (CC2) communication protocol. It is intended for developers building integrations, tools, or applications that communicate with CC2-based FDM printers.

**Sources**: Derived from analysis of the [elegoo-link](https://github.com/ELEGOO-3D/elegoo-link) open-source SDK (v1.3.6), packet captures of ElegooSlicer traffic, port scanning and endpoint probing of stock firmware, and community reverse engineering efforts.

## elegoo-link SDK

Elegoo provides an official open-source C++ SDK — [elegoo-link](https://github.com/ELEGOO-3D/elegoo-link) — that implements the full CC2 protocol. This is the same library that ElegooSlicer uses internally.

## ⚠️ Firmware Compatibility

This document covers **two firmware variants** that behave differently. Sections are labeled accordingly:

| Firmware | Description | How to Identify |
|----------|-------------|-----------------|
| **Stock** | Factory Elegoo firmware. Limited HTTP surface. | Default on all new printers. No "OC" in firmware version string. |
| **OpenCentauri** | Community firmware with extended HTTP API. | Firmware version contains "OC" or "O" suffix. |

Where behavior differs, sections are tagged with **[Stock]**, **[OpenCentauri]**, or **[Both]**. Unmarked content applies to both firmwares unless noted otherwise.

---

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Network Architecture](#network-architecture)
4. [Discovery Protocol](#discovery-protocol)
5. [MQTT Connection](#mqtt-connection)
6. [Registration Protocol](#registration-protocol)
7. [Heartbeat Protocol](#heartbeat-protocol)
8. [Command Protocol](#command-protocol)
9. [Method Reference](#method-reference)
10. [Status Codes Reference](#status-codes-reference)
11. [Data Structures](#data-structures)
12. [Delta Status Updates](#delta-status-updates)
13. [HTTP API](#http-api)
14. [File Operations](#file-operations)
15. [Video Streaming](#video-streaming)
16. [Canvas/AMS System](#canvasams-system)
17. [Print Job Lifecycle](#print-job-lifecycle)
18. [Error Handling](#error-handling)
19. [Security Considerations](#security-considerations)
20. [Firmware Variations](#firmware-variations)
21. [CC1 vs CC2 Comparison](#cc1-vs-cc2-comparison)
22. [Implementation Checklist](#implementation-checklist)
23. [Troubleshooting](#troubleshooting)
24. [Glossary](#glossary)
25. [References](#references)
26. [G-Code File Structure](#g-code-file-structure)
27. [Addendum: Implementation Findings](#addendum-implementation-findings)

---

## Overview

The Centauri Carbon 2 (CC2) is Elegoo's second-generation FDM printer communication protocol. It uses an **inverted MQTT architecture** where the printer itself runs the MQTT broker, and clients (like slicers, apps, or home automation systems) connect to it.

### Key Characteristics

| Feature | Description |
|---------|-------------|
| Transport | MQTT 3.1.1 over TCP (port 1883) and WebSocket (port 9001) |
| Broker | Runs on the printer |
| Discovery | UDP broadcast (port 52700) |
| Authentication | Username/password + optional access code |
| Status Updates | Delta-based (incremental) |
| Max Clients | ~4 concurrent connections |
| Heartbeat | Required every 10 seconds |

### Supported Printers

The CC2 protocol is used by:
- Elegoo Centauri Carbon 2
- Elegoo Cura (some models)
- Other Elegoo FDM printers with CC2 firmware

### What Makes CC2 Different

Unlike traditional printer protocols where a central server (like OctoPrint) manages connections:

1. **The printer IS the server** - It runs an MQTT broker that other systems connect to.
2. **Clients connect TO the printer** - Not vice versa. Slicers, integrations, and web interfaces all connect as MQTT clients.
3. **Registration is mandatory** - Must register before sending commands.
4. **Connection health monitoring** - Heartbeat mechanism required.
5. **Bandwidth optimization** - Uses delta status updates.

### ⚠️ Important: LAN-Only Mode Required

**This integration currently supports LAN-only mode connections only.**

CC2 printers have two network modes:
- **LAN-Only Mode** (`lan_status: 1`) - ✅ **Supported** - Direct local MQTT connection
- **Cloud Mode** (`lan_status: 0`) - ❌ **Not Supported** - Requires Elegoo cloud authentication

**To use CC2 printers with this integration:**
1. On your printer: **Settings → Network → LAN Only Mode**
2. Enable **LAN Only Mode**
3. Save and restart printer if needed

**Why Cloud Mode isn't supported:**
Cloud mode uses a completely different architecture with RTM (Real-Time Messaging) relay through Elegoo's cloud servers, requiring OAuth2 authentication, cloud API integration, and token management. This would require significant development and is not currently planned.

---

## Quick Start

Here's the minimum flow to connect and receive status updates:

### Step 1: Discover the Printer

```python
import socket
import json

# Send UDP discovery broadcast
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock.settimeout(5.0)

message = json.dumps({"id": 0, "method": 7000}).encode()
sock.sendto(message, ('255.255.255.255', 52700))

# Receive response
data, addr = sock.recvfrom(1024)
response = json.loads(data)
printer_ip = addr[0]
serial_number = response['result']['sn']
```

### Step 2: Connect via MQTT

```python
import paho.mqtt.client as mqtt
import time
import random

# Generate client_id matching web interface format
# Format: "0cli" + 5_hex_timestamp + random_hex, truncated to 10 chars
timestamp_hex = format(int(time.time() * 1000), "x")[-5:]
random_hex = format(random.randint(0, 4095), "x")
client_id = f"0cli{timestamp_hex}{random_hex}"[:10]

# Generate request_id matching web interface format
# Format: UUID-like string + timestamp
uuid_part = "".join(
    format(random.randint(0, 15) if c == "x" else (random.randint(0, 3) + 8), "x")
    for c in "xxxxxxxxxxxxxxxx"
)
timestamp_hex_long = format(int(time.time() * 1000), "x")
request_id = f"{uuid_part}{timestamp_hex_long}"

client = mqtt.Client(client_id=client_id)
client.username_pw_set("elegoo", "123456")  # or access code
client.connect(printer_ip, 1883, keepalive=60)
```

### Step 3: Register

```python
# Subscribe to registration response
client.subscribe(f"elegoo/{serial_number}/{request_id}/register_response")

# Send registration
client.publish(
    f"elegoo/{serial_number}/api_register",
    json.dumps({"client_id": client_id, "request_id": request_id})
)

# Wait for "ok" response before proceeding
```

### Step 4: Subscribe to Status Updates

```python
# Subscribe to status updates and command responses
client.subscribe(f"elegoo/{serial_number}/api_status")
client.subscribe(f"elegoo/{serial_number}/{client_id}/api_response")
```

### Step 5: Start Heartbeat

```python
import threading
import time

def heartbeat():
    while connected:
        client.publish(
            f"elegoo/{serial_number}/{client_id}/api_request",
            json.dumps({"type": "PING"})
        )
        time.sleep(10)

threading.Thread(target=heartbeat, daemon=True).start()
```

---

## Network Architecture

### Port Map by Firmware

| Port | Protocol | Stock Firmware | OpenCentauri Firmware |
|------|----------|---------------|----------------------|
| **52700** | UDP | Discovery (broadcast) | Discovery (broadcast) |
| **80** | TCP/HTTP | `PUT /upload` only (libhv/1.3.4). Returns 404 for all other paths. | Unknown |
| **1883** | TCP/MQTT | MQTT broker (TCP). Used by the elegoo-link C++ library (ElegooSlicer, HA integrations). | MQTT broker |
| **9001** | TCP/WS | MQTT broker (WebSocket). Used by the Device page's bundled JavaScript (`lan_service_web`) for real-time status, file lists, and printer control. | MQTT broker (WebSocket) |
| **8080** | TCP/HTTP | Camera MJPEG stream **only**. Returns `multipart/x-mixed-replace` for every request regardless of path, query, or auth headers. | Full HTTP API server (files, system info, download, camera) |

**Ports confirmed closed on stock firmware:** 21, 22, 23, 3030, 3031, 8888, 34952 (Klipper webhooks), 54780 (SDCP WebSocket).

> **Two MQTT endpoints:** The CC2 exposes MQTT on two ports: **1883** (standard MQTT over TCP) and **9001** (MQTT over WebSocket). The elegoo-link C++ library connects on 1883 for registration, status polling, and commands. The slicer's Device page (`lan_service_web/index.html`) loads as a local `file://` URL and creates its own mqtt.js client connecting to `ws://{printer_ip}:9001` for real-time events, auto-report subscriptions, and heartbeats. Both ports must be accessible for the full Device page to function.

### Stock Firmware Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Local Network                             │
│                                                                     │
│  ┌──────────────────┐              ┌───────────────────────────────┐│
│  │  Client App      │              │      CC2 Printer              ││
│  │  (Slicer or      │              │      (Stock Firmware)         ││
│  │   HA Integration)│              │                               ││
│  │                  │  UDP:52700   │  ┌─────────────────────────┐  ││
│  │  Discovery       │◄────────────►│  │ Discovery (UDP)         │  ││
│  │                  │              │  └─────────────────────────┘  ││
│  │                  │  TCP:1883    │  ┌─────────────────────────┐  ││
│  │  MQTT (TCP)      │◄────────────►│  │ MQTT Broker (TCP)       │  ││
│  │  (C++ library:   │              │  │ (status, commands,      │  ││
│  │   commands,      │              │  │  registration)          │  ││
│  │   status)        │              │  └─────────────────────────┘  ││
│  │                  │  TCP:9001    │  ┌─────────────────────────┐  ││
│  │  MQTT (WebSocket)│◄────────────►│  │ MQTT Broker (WebSocket) │  ││
│  │  (Device page JS:│              │  │ (real-time events,      │  ││
│  │   file lists,    │              │  │  auto-report, control)  │  ││
│  │   live status)   │              │  └─────────────────────────┘  ││
│  │                  │  TCP:80      │  ┌─────────────────────────┐  ││
│  │  HTTP PUT        │─────────────►│  │ libhv/1.3.4             │  ││
│  │  (gcode upload   │              │  │ (PUT /upload ONLY)      │  ││
│  │   from slicer)   │              │  └─────────────────────────┘  ││
│  │                  │  TCP:8080    │  ┌─────────────────────────┐  ││
│  │  MJPEG           │◄─────────────│  │ Camera Stream           │  ││
│  │  (view only)     │              │  │ (MJPEG, no file ops)    │  ││
│  └──────────────────┘              │  └─────────────────────────┘  ││
│                                    └───────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```


### Data Flow

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Connection Lifecycle                            │
│                                                                        │
│  ┌─────────┐   ┌───────────┐   ┌──────────┐   ┌─────────┐   ┌─────────┐│
│  │Discovery│──►│MQTT       │──►│Register  │──►│Subscribe│──►│Heartbeat││
│  │(UDP)    │   │Connect    │   │          │   │Topics   │   │Loop     ││
│  └─────────┘   └───────────┘   └──────────┘   └─────────┘   └─────────┘│
│                                                                        │
│  After connection established:                                         │
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                     Main Event Loop                             │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │   │
│  │  │ Receive      │  │ Send         │  │ Periodic Heartbeat     │ │   │
│  │  │ Status       │◄─┤ Commands     │  │ (every 10 seconds)     │ │   │
│  │  │ Updates      │  │              │  │                        │ │   │
│  │  └──────────────┘  └──────────────┘  └────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Discovery Protocol

Discovery allows clients to find CC2 printers on the local network.

### Configuration

| Parameter | Value |
|-----------|-------|
| Port | 52700 (UDP) |
| Method | Broadcast or directed UDP |
| Timeout | 10 seconds recommended (broadcast), 3 seconds (directed) |

### Discovery Request

Send to `255.255.255.255:52700` (broadcast) or `<printer_ip>:52700` (direct):

```json
{
  "id": 0,
  "method": 7000
}
```

### Discovery Response

The printer responds with its configuration:

```json
{
  "id": 0,
  "result": {
    "host_name": "Centauri Carbon 2",
    "machine_model": "Centauri Carbon 2",
    "sn": "CC2ABCD1234567890",
    "token_status": 0,
    "lan_status": 1
  }
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `host_name` | string | User-configured printer name (can be changed in settings) |
| `machine_model` | string | Hardware model identifier |
| `sn` | string | Serial number (unique, used in MQTT topics) |
| `token_status` | int | Authentication mode (see below) |
| `lan_status` | int | Network mode (see below) |

### Token Status Values

| Value | Meaning | Action Required |
|-------|---------|-----------------|
| 0 | No access code | Use default password `123456` |
| 1 | Access code set | Use access code as MQTT password |

### LAN Status Values

| Value | Meaning |
|-------|---------|
| 0 | Cloud mode (printer may be cloud-connected) |
| 1 | LAN-only mode |

### Implementation Notes

- The printer may take 1-2 seconds to respond
- Multiple printers on the network will all respond
- Serial number format: Usually starts with printer model prefix
- Discovery works even when another client is connected

---

## MQTT Connection

After discovering the printer, establish an MQTT connection.

### Connection Parameters

| Parameter | Value |
|-----------|-------|
| Host | Printer IP address |
| Port | 1883 |
| Protocol | MQTT 3.1.1 |
| Keep-alive | 60 seconds |
| Clean Session | true |
| Username | `elegoo` (always) |
| Password | See auth modes below |

#### MQTT Authentication Modes

The elegoo-link SDK supports multiple authentication modes. The username is always `elegoo`.

| Auth Mode | Password Value | When Used |
|-----------|---------------|-----------|
| `basic` | `123456` (default) | No access code set (`token_status=0`) |
| `accessCode` | User-configured access code | Access code set (`token_status=1`, LAN mode) |
| `token` | Token string | Token-based auth |
| `pinCode` | PIN code | Cloud mode (`lan_status=0`) |

For LAN-only integrations, you only need to handle `basic` (default password `123456`) and `accessCode` (user-set password). The `pinCode` mode is used when the printer is in cloud mode and typically requires pairing via the Elegoo app.

### Client ID Format

**IMPORTANT**: Client ID must match the official web interface format.

Generate a unique client ID:

```text
"0cli" + timestamp_hex[-5:] + random_hex
```

- **Prefix**: `"0cli"` (constant, 4 characters)
- **Timestamp**: Last 5 hex characters of current timestamp in milliseconds
- **Random**: Random hex digits (0-fff)
- **Length**: Exactly 10 characters (truncated if longer)

**Examples**:
- `0clib9137a`
- `0clic1361f`
- `0cli8f3a2b`

**Python Implementation**:
```python
import time
import random

timestamp_hex = format(int(time.time() * 1000), "x")[-5:]  # Last 5 hex chars
random_hex = format(random.randint(0, 4095), "x")  # Random 0-fff
client_id = f"0cli{timestamp_hex}{random_hex}"[:10]  # Truncate to exactly 10
```

**Alternative Format** (used by official elegoo-link SDK):
- `1_PC_<number>` — where `<number>` is a random 4-digit integer (1000-9999)
- This is the format used by the [elegoo-link](https://github.com/ELEGOO-3D/elegoo-link) SDK (current v1.3.6) and by extension ElegooSlicer
- The `0cli` format above is used by the web interface
- Both formats work. Implementations should support both

### Request ID Format

Used for registration (UUID + timestamp format):

```text
<uuid_part> + timestamp_hex
```

- **UUID part**: 16 hex characters (UUID4-like)
- **Timestamp**: Current timestamp in hex

**Example**: `a3f8b2c4d5e6f7a819c422c1361`

**Python Implementation**:
```python
import time
import random

uuid_part = "".join(
    format(random.randint(0, 15) if c == "x" else (random.randint(0, 3) + 8), "x")
    for c in "xxxxxxxxxxxxxxxx"
)
timestamp_hex = format(int(time.time() * 1000), "x")
request_id = f"{uuid_part}{timestamp_hex}"
```

**Alternative Format** (used by official elegoo-link SDK):
- `1_PC_<number>_req` — the SDK simply appends `_req` to the client ID
- Example: client ID `1_PC_4521` → request ID `1_PC_4521_req`
- This is the current format used by the elegoo-link SDK (v1.3.6)
- Both formats work. The UUID + timestamp format shown above is used by the web interface

### Connection Error Handling

| Error | Cause | Solution |
|-------|-------|----------|
| Connection refused | Wrong IP/port | Verify discovery response |
| Authentication failed | Wrong password | Check `token_status`, use access code |
| Connection closed | Too many clients | Wait for slot, implement reconnection |
| Timeout | Network issue | Retry with backoff |

---

## Registration Protocol

Registration **must** be completed before sending any commands. The printer needs to know which client IDs are valid.

### Registration Flow

```
┌────────┐                              ┌────────────┐
│ Client │                              │  Printer   │
└───┬────┘                              └─────┬──────┘
    │                                         │
    │ 1. Subscribe to register_response       │
    │────────────────────────────────────────►│
    │                                         │
    │ 2. Publish registration request         │
    │────────────────────────────────────────►│
    │                                         │
    │ 3. Receive registration response        │
    │◄────────────────────────────────────────│
    │                                         │
    │ 4. If "ok", proceed with commands       │
    │                                         │
```

### Topics

| Action | Topic |
|--------|-------|
| Subscribe (response) | `elegoo/<sn>/<request_id>/register_response` |
| Publish (request) | `elegoo/<sn>/api_register` |

### Registration Request

```json
{
  "client_id": "0clib9137a",
  "request_id": "a3f8b2c4d5e6f7a819c422c1361"
}
```

### Registration Response - Success

```json
{
  "client_id": "0clib9137a",
  "error": "ok"
}
```

### Registration Response - Failure

```json
{
  "client_id": "0clib9137a",
  "error": "too many clients"
}
```

### Error Values

| Error | Meaning | Action |
|-------|---------|--------|
| `ok` | Success | Proceed with subscriptions |
| `fail` | General failure | Retry after delay |
| `too many clients` | Max connections reached | Wait or disconnect another client |

### Timing

| Parameter | Value |
|-----------|-------|
| Timeout | 3 seconds |
| Max Clients | ~4 (may vary by firmware) |
| Retry Delay | 5-10 seconds recommended |

---

## Heartbeat Protocol

The heartbeat mechanism keeps the connection alive and allows the printer to detect disconnected clients.

### Configuration

| Parameter | Value |
|-----------|-------|
| Interval | 10 seconds |
| Timeout | 65 seconds (printer disconnects if no heartbeat) |

### Heartbeat Request

Publish to: `elegoo/<sn>/<client_id>/api_request`

```json
{
  "type": "PING"
}
```

### Heartbeat Response

Received on: `elegoo/<sn>/<client_id>/api_response`

```json
{
  "type": "PONG"
}
```

### Implementation Notes

- **Start heartbeat immediately** after successful registration
- **Don't wait for PONG** before sending next PING (fire-and-forget pattern)
- **Missing PONGs** may indicate connection issues
- If connection drops, re-establish from discovery or MQTT connect

### Heartbeat State Machine

```
┌─────────────────────────────────────────────────────────────┐
│                    Heartbeat Manager                         │
│                                                              │
│  ┌──────────┐    10s    ┌──────────┐   PONG    ┌──────────┐│
│  │  IDLE    │──────────►│PING_SENT │──────────►│   OK     ││
│  └──────────┘           └──────────┘           └──────────┘│
│       ▲                      │                      │       │
│       │                      │ 65s timeout          │       │
│       │                      ▼                      │       │
│       │                ┌──────────┐                 │       │
│       │                │DISCONNECT│                 │       │
│       │                └──────────┘                 │       │
│       │                                             │       │
│       └─────────────────────────────────────────────┘       │
│                         10s                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Command Protocol

Commands are JSON messages sent to control the printer.

### Command Message Format

```json
{
  "id": <sequence_number>,
  "method": <method_code>,
  "params": { ... }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Request sequence number (for matching responses) |
| `method` | int | Command method code |
| `params` | object | Command-specific parameters |

### Response Message Format

```json
{
  "id": <sequence_number>,
  "method": <method_code>,
  "result": {
    "error_code": 0,
    ...
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Matches request `id` |
| `method` | int | Matches request `method` |
| `result` | object | Response data including `error_code` |

### Topics

| Action | Topic |
|--------|-------|
| Send command | `elegoo/<sn>/<client_id>/api_request` |
| Receive response | `elegoo/<sn>/<client_id>/api_response` |

### Request ID Management

- Use incrementing integers starting from 1
- Track pending requests by ID
- Implement timeout for responses (5-10 seconds)
- The same ID should not be reused for different requests

---

## Method Reference

### Query Methods (Read)

| Code | Name | Description | Parameters |
|------|------|-------------|------------|
| 1001 | GET_ATTRIBUTES | Get printer info | None |
| 1002 | GET_STATUS | Get full status | None |
| 1036 | PRINT_TASK_LIST | Get print history | `{"page": 1, "page_size": 10}` |
| 1037 | PRINT_TASK_DETAIL | Get task details | `{"uuid": "..."}` |
| 1044 | GET_FILE_LIST | List files | `{"storage_media": "local", "path": "/"}` |
| 1045 | GET_FILE_THUMBNAIL | Get file thumbnail | `{"storage_media": "local", "filename": "..."}` |
| 1046 | GET_FILE_DETAIL | Get file info | `{"storage_media": "local", "filename": "..."}` |
| 1048 | GET_DISK_INFO | Get storage info | `{"storage_media": "local"}` |
| 2005 | GET_CANVAS_STATUS | Get AMS status | None |

### Print Control Methods

| Code | Name | Description | Parameters |
|------|------|-------------|------------|
| 1020 | START_PRINT | Start print job | See [Start Print](#start-print) |
| 1021 | PAUSE_PRINT | Pause print | None |
| 1022 | STOP_PRINT | Stop/cancel print | None |
| 1023 | RESUME_PRINT | Resume print | None |

### Motion Methods

| Code | Name | Description | Parameters |
|------|------|-------------|------------|
| 1026 | HOME_AXES | Home axes | `{"homed_axes": "xyz"}` |
| 1027 | MOVE_AXES | Move axes | `{"axes": "z", "distance": 10.0}` |

### Temperature Methods

| Code | Name | Description | Parameters |
|------|------|-------------|------------|
| 1028 | SET_TEMPERATURE | Set temps | `{"extruder": 220, "heater_bed": 60}` |

### Peripheral Methods

| Code | Name | Description | Parameters |
|------|------|-------------|------------|
| 1029 | SET_LIGHT | Set LED | `{"brightness": 255}` |
| 1030 | SET_FAN_SPEED | Set fans | `{"fan": 255, "aux_fan": 128}` |
| 1031 | SET_PRINT_SPEED | Set speed mode | `{"mode": 1}` |
| 1042 | VIDEO_STREAM | Toggle camera | `{"enable": true}` |

### File Methods

| Code | Name | Description | Parameters |
|------|------|-------------|------------|
| 1047 | DELETE_FILE | Delete file | `{"storage_media": "local", "filename": "..."}` |
| 1057 | SET_PRINTER_DOWNLOAD_FILE | Tell printer to download from URL | See [File Operations](#file-operations). Cloud/remote feature. |
| 1058 | CANCEL_PRINTER_DOWNLOAD_FILE | Cancel printer download | `{"taskID": "..."}` |

### System Methods

| Code | Name | Description | Parameters |
|------|------|-------------|------------|
| 1038 | DELETE_PRINT_TASK | Delete history | `{"uuid": "..."}` |
| 1043 | UPDATE_PRINTER_NAME | Rename printer | `{"name": "New Name"}` |
| 2004 | SET_AUTO_REFILL | Toggle auto-refill | `{"enable": true}` |

### Event Methods (Printer → All Connected MQTT Clients)

| Code | Name | Description |
|------|------|-------------|
| 6000 | ON_PRINTER_STATUS | Delta status update |
| 6008 | ON_PRINTER_ATTRIBUTES | Attributes changed |

---

## Status Codes Reference

### Machine Status Codes

The primary status indicates what the printer is currently doing.

| Code | Name | Description |
|------|------|-------------|
| 0 | INITIALIZING | Printer booting up |
| 1 | IDLE | Ready, waiting for commands |
| 2 | PRINTING | Print job in progress |
| 3 | FILAMENT_OPERATING | Loading/unloading filament (type 1) |
| 4 | FILAMENT_OPERATING_2 | Loading/unloading filament (type 2) |
| 5 | AUTO_LEVELING | Automatic bed leveling |
| 6 | PID_CALIBRATING | PID auto-tune running |
| 7 | RESONANCE_TESTING | Input shaper calibration |
| 8 | SELF_CHECKING | Hardware self-test |
| 9 | UPDATING | Firmware update in progress |
| 10 | HOMING | Axes homing |
| 11 | FILE_TRANSFERRING | File upload/download active |
| 12 | VIDEO_COMPOSING | Creating timelapse video |
| 13 | EXTRUDER_OPERATING | Extruder maintenance operation |
| 14 | EMERGENCY_STOP | E-stop triggered |
| 15 | POWER_LOSS_RECOVERY | Recovering after power loss |

### Sub-Status Codes

Sub-status provides additional detail within the main status.

#### Printing Sub-Status (status=2)

| Code | Name | Description |
|------|------|-------------|
| 0 | NONE | No specific sub-status |
| 1041 | IDLE_IN_PRINT | Idle within print context |
| 1045 | EXTRUDER_PREHEATING | Nozzle heating up |
| 1096 | EXTRUDER_PREHEATING_2 | Nozzle heating (variant) |
| 1405 | BED_PREHEATING | Bed heating up |
| 1906 | BED_PREHEATING_2 | Bed heating (variant) |
| 2075 | PRINTING | Actively laying down material |
| 2077 | PRINTING_COMPLETED | Print finished successfully |
| 2401 | RESUMING | Resuming from pause |
| 2402 | RESUMING_COMPLETED | Resume complete |
| 2501 | PAUSING | Pause in progress |
| 2502 | PAUSED | Print paused |
| 2505 | PAUSED_2 | Paused (variant) |
| 2503 | STOPPING | Stop in progress |
| 2504 | STOPPED | Print stopped/cancelled |
| 2801 | HOMING | Homing during print |
| 2802 | HOMING_COMPLETED | Mid-print homing done |
| 2901 | AUTO_LEVELING | Leveling during print |
| 2902 | AUTO_LEVELING_COMPLETED | Mid-print leveling done |

#### Filament Operating Sub-Status (status=3,4)

| Code | Name | Description |
|------|------|-------------|
| 1133 | FILAMENT_LOADING | Loading filament |
| 1134 | FILAMENT_LOADING_2 | Loading (phase 2) |
| 1135 | FILAMENT_LOADING_3 | Loading (phase 3) |
| 1136 | FILAMENT_LOADING_COMPLETED | Loading complete |
| 1143 | FILAMENT_PRE_UNLOAD | Preparing to unload |
| 1144 | FILAMENT_UNLOADING | Unloading filament |
| 1145 | FILAMENT_UNLOADING_COMPLETED | Unloading complete |

#### Auto Leveling Sub-Status (status=5)

| Code | Name | Description |
|------|------|-------------|
| 2901 | AL_AUTO_LEVELING | Probing in progress |
| 2902 | AL_AUTO_LEVELING_COMPLETED | Leveling complete |

#### PID Calibrating Sub-Status (status=6)

| Code | Name | Description |
|------|------|-------------|
| 1503 | PID_CALIBRATING | Calibration running |
| 1504 | PID_CALIBRATING_2 | Calibration (phase 2) |
| 1505 | PID_CALIBRATING_COMPLETED | Calibration successful |
| 1506 | PID_CALIBRATING_FAILED | Calibration failed |

#### Resonance Testing Sub-Status (status=7)

| Code | Name | Description |
|------|------|-------------|
| 5934 | RESONANCE_TEST | Test running |
| 5935 | RESONANCE_TEST_COMPLETED | Test successful |
| 5936 | RESONANCE_TEST_FAILED | Test failed |

#### Updating Sub-Status (status=9)

| Code | Name | Description |
|------|------|-------------|
| 2061 | UPDATING_INIT | Update initializing |
| 2071 | UPDATING_1 | Update phase 1 |
| 2072 | UPDATING_2 | Update phase 2 |
| 2073 | UPDATING_3 | Update phase 3 |
| 2074 | UPDATING_COMPLETED | Update successful |
| 2075 | UPDATING_FAILED | Update failed |

#### Homing Sub-Status (status=10)

| Code | Name | Description |
|------|------|-------------|
| 2801 | H_HOMING | Homing in progress |
| 2802 | H_HOMING_COMPLETED | Homing successful |
| 2803 | H_HOMING_FAILED | Homing failed |

#### File Transferring Sub-Status (status=11)

| Code | Name | Description |
|------|------|-------------|
| 3000 | UPLOADING_FILE | Upload in progress |
| 3001 | UPLOADING_FILE_COMPLETED | Upload complete |

#### Extruder Operating Sub-Status (status=13)

| Code | Name | Description |
|------|------|-------------|
| 1061 | EXTRUDER_LOADING | Extruder filament load |
| 1062 | EXTRUDER_UNLOADING | Extruder filament unload |
| 1063 | EXTRUDER_LOADING_COMPLETED | Load complete |
| 1064 | EXTRUDER_UNLOADING_COMPLETED | Unload complete |

### Speed Modes

| Code | Name | Speed Multiplier |
|------|------|------------------|
| 0 | Silent | 50% |
| 1 | Balanced | 100% (default) |
| 2 | Sport | 150% |
| 3 | Ludicrous | 200% |

---

## Data Structures

### Full Status Response

This is the complete status structure returned by method 1002 or event 6000 (first full update).

```json
{
  "id": 1,
  "method": 6000,
  "result": {
    "error_code": 0,

    "machine_status": {
      "status": 2,
      "sub_status": 2075,
      "exception_status": [],
      "progress": 45
    },

    "print_status": {
      "filename": "benchy.gcode",
      "uuid": "b52af24c-764e-4092-8a50-00e5f8f02b46",
      "current_layer": 225,
      "total_layer": 500,
      "print_duration": 3600,
      "total_duration": 8000,
      "remaining_time_sec": 4400,
      "progress": 45
    },

    "extruder": {
      "temperature": 215.0,
      "target": 220,
      "filament_detect_enable": 1,
      "filament_detected": 1
    },

    "heater_bed": {
      "temperature": 58.5,
      "target": 60
    },

    "ztemperature_sensor": {
      "temperature": 33.0,
      "measured_max_temperature": 0,
      "measured_min_temperature": 0
    },

    "fans": {
      "fan": {
        "speed": 255,
        "rpm": 5000
      },
      "aux_fan": {
        "speed": 178,
        "rpm": 3500
      },
      "box_fan": {
        "speed": 25,
        "rpm": 800
      },
      "heater_fan": {
        "speed": 255,
        "rpm": 4500
      },
      "controller_fan": {
        "speed": 255,
        "rpm": 4000
      }
    },

    "led": {
      "status": 1
    },

    "gcode_move_inf": {
      "x": 88.148,
      "y": 139.946,
      "z": 1.6,
      "e": 138.87,
      "speed": 9019,
      "speed_mode": 1
    },

    "toolhead": {
      "homed_axes": "xyz"
    },

    "external_device": {
      "camera": true,
      "u_disk": false,
      "type": "0303"
    }
  }
}
```

### Field Descriptions

#### machine_status

| Field | Type | Description |
|-------|------|-------------|
| `status` | int | Machine status code (see [Machine Status Codes](#machine-status-codes)) |
| `sub_status` | int | Sub-status code (see [Sub-Status Codes](#sub-status-codes)) |
| `exception_status` | array | List of active error codes |
| `progress` | int | Print progress 0-100 (also in print_status) |

#### print_status

| Field | Type | Description |
|-------|------|-------------|
| `filename` | string | Current print file name |
| `uuid` | string | Unique print task identifier |
| `current_layer` | int | Current layer being printed |
| `total_layer` | int | Total layers in print |
| `print_duration` | int | Elapsed time in seconds |
| `total_duration` | int | Estimated total time in seconds |
| `remaining_time_sec` | int | Estimated remaining time in seconds |
| `progress` | int | Progress percentage 0-100 |

#### extruder

| Field | Type | Description |
|-------|------|-------------|
| `temperature` | float | Current nozzle temperature (°C) |
| `target` | float | Target nozzle temperature (°C) |
| `filament_detect_enable` | int | 1=sensor enabled, 0=disabled |
| `filament_detected` | int | 1=filament present, 0=no filament |

#### heater_bed

| Field | Type | Description |
|-------|------|-------------|
| `temperature` | float | Current bed temperature (°C) |
| `target` | float | Target bed temperature (°C) |

#### ztemperature_sensor (Chamber/Box)

| Field | Type | Description |
|-------|------|-------------|
| `temperature` | float | Current chamber temperature (°C) |
| `measured_max_temperature` | float | Max recorded temp |
| `measured_min_temperature` | float | Min recorded temp |

#### fans

Each fan object contains:

| Field | Type | Description |
|-------|------|-------------|
| `speed` | int | PWM value 0-255 (NOT percentage) |
| `rpm` | int | Actual measured RPM |

Fan types:
- `fan` - Part cooling fan (model fan)
- `aux_fan` - Auxiliary/chamber circulation fan
- `box_fan` - Enclosure exhaust fan
- `heater_fan` - Hotend heatsink fan
- `controller_fan` - Electronics cooling fan

**Converting speed to percentage:**
```
percentage = round(speed / 255 * 100)
```

#### led

| Field | Type | Description |
|-------|------|-------------|
| `status` | int | 0=off, 1=on (may also be brightness 0-255) |

#### gcode_move_inf

| Field | Type | Description |
|-------|------|-------------|
| `x` | float | X axis position (mm) |
| `y` | float | Y axis position (mm) |
| `z` | float | Z axis position (mm) |
| `e` | float | Extruder position (mm of filament) |
| `speed` | int | Current move speed (mm/min) |
| `speed_mode` | int | Speed mode 0-3 (see [Speed Modes](#speed-modes)) |

#### toolhead

| Field | Type | Description |
|-------|------|-------------|
| `homed_axes` | string | Which axes are homed ("", "x", "xy", "xyz") |

#### external_device

| Field | Type | Description |
|-------|------|-------------|
| `camera` | bool | Camera connected |
| `u_disk` | bool | USB drive connected |
| `type` | string | Device type identifier |

### Attributes Response

```json
{
  "id": 1,
  "method": 1001,
  "result": {
    "error_code": 0,
    "hostname": "My Printer",
    "machine_model": "Centauri Carbon 2",
    "sn": "CC2ABCD1234567890",
    "ip": "192.168.1.100",
    "mac": "AA:BB:CC:DD:EE:FF",
    "protocol_version": "1.0.0",
    "hardware_version": "1.0",
    "software_version": {
      "ota_version": "1.0.5.2",
      "mcu_version": "00.00.00.00",
      "soc_version": ""
    },
    "resolution": "1920x1080",
    "xyz_size": "220x220x250",
    "network_type": "wifi",
    "usb_connected": false,
    "camera_connected": true,
    "remaining_memory": 1073741824,
    "max_video_connections": 1,
    "video_connections": 0
  }
}
```

### Field Name Variations

Different firmware versions may use different field names. Implementations should handle both:

| Official Name | Alternative | Notes |
|---------------|-------------|-------|
| `gcode_move_inf` | `gcode_move` | Position/speed data |
| `gcode_move_inf.e` | `gcode_move.extruder` | Extruder position |
| `toolhead` | `tool_head` | Toolhead info |
| `ztemperature_sensor` | `chamber` | Chamber temperature |

---

## Delta Status Updates

CC2 uses delta updates to minimize bandwidth. Only changed fields are sent.

### How Delta Updates Work

1. **Initial Full Status**: On connection or explicit request (method 1002), printer sends complete status
2. **Incremental Updates**: Subsequent event 6000 messages contain only changed fields
3. **Client Merging**: Client must merge delta into cached full status
4. **Continuity Tracking**: Track message IDs to detect missed updates

### Delta Update Example

Full status has been received. Then printer sends:

```json
{
  "id": 42,
  "method": 6000,
  "result": {
    "error_code": 0,
    "machine_status": {
      "progress": 46
    },
    "print_status": {
      "current_layer": 230,
      "print_duration": 3650
    },
    "extruder": {
      "temperature": 219.5
    }
  }
}
```

Only `progress`, `current_layer`, `print_duration`, and `extruder.temperature` changed.

### Deep Merge Algorithm

```python
def deep_merge(base: dict, update: dict) -> dict:
    """Recursively merge update into base."""
    result = base.copy()
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result

# Usage
cached_status = deep_merge(cached_status, delta_update["result"])
```

### Continuity Checking

Track the `id` field to detect missed updates:

```python
class StatusManager:
    def __init__(self):
        self.last_id = None
        self.non_continuous_count = 0
        self.MAX_NON_CONTINUOUS = 5

    def process_update(self, message):
        current_id = message.get("id")

        if self.last_id is not None:
            if current_id != self.last_id + 1:
                self.non_continuous_count += 1
                if self.non_continuous_count >= self.MAX_NON_CONTINUOUS:
                    self.request_full_status()
                    self.non_continuous_count = 0
            else:
                self.non_continuous_count = 0

        self.last_id = current_id
```

### When to Request Full Status

- After 5+ non-continuous ID gaps
- After reconnection
- If critical fields appear missing
- Periodically (every 5-10 minutes) as safety measure

---

## HTTP API

> ⚠️ **Firmware Compatibility:** Most HTTP endpoints in this section are only available on **OpenCentauri firmware**. Stock Elegoo firmware exposes only `PUT /upload` on port 80 and the camera MJPEG stream on port 8080. Use the equivalent MQTT methods for stock firmware operations (file listing, file details, etc.).

| HTTP Endpoint | Stock Firmware | OpenCentauri | MQTT Alternative |
|---------------|---------------|--------------|------------------|
| `GET /system/info` | ❌ 404 (see note) | ✅ Port 8080 | Method 1001 (GET_ATTRIBUTES) |
| `GET /files` | ❌ 404 | ✅ Port 8080 | Method 1044 (GET_FILE_LIST) |
| `GET /download` | ❌ 404 | ✅ Port 8080 | N/A (1057 is a different operation — see below) |
| `PUT /upload` | ✅ Port 80 | ✅ Port 8080 | N/A (always HTTP) |

> **Note on `/system/info`:** The elegoo-link SDK (v1.3.6) attempts `GET /system/info?X-Token=...` to retrieve the printer's serial number when it isn't already known. On current stock firmware this returns 404, and the SDK falls back to using the serial number obtained from UDP discovery. This suggests the endpoint may be added to stock firmware in a future update, or it may only exist on OpenCentauri. The SDK handles the 404 gracefully.

### Stock Firmware Capabilities Summary

Based on port scanning, HTTP endpoint probing, and MQTT protocol testing on stock firmware:

**What IS possible:**
- Full printer status monitoring (temps, position, progress, fans) via MQTT
- Query file metadata: name, size, layers, total filament used, print time (MQTT method 1046)
- Retrieve file thumbnails as base64 PNG (MQTT method 1045)
- List files on the printer (MQTT method 1044)
- Upload G-code files (HTTP `PUT /upload` on port 80)
- Start, pause, resume, stop prints (MQTT)
- Control temperatures, fans, lights, speed (MQTT)
- View camera stream (MJPEG on port 8080)

**What IS NOT possible:**
- Download G-code file content via HTTP (no endpoint exists on any port)
- Download G-code file content via MQTT (method 1057 is not a download method — see below)
- HTTP Range requests (not supported on any port)
- SSH or shell access (port 22 closed)
- Klipper webhooks access (port 34952 closed)

**Implication for per-extruder filament tracking:** The per-slot filament usage breakdown (needed for multi-material Spoolman integration) only exists inside the G-code file itself. Since file content cannot be retrieved from the printer, it must be captured at upload time — for example, via a [local network proxy](#g-code-file-structure) between the slicer and printer.

### File Upload — Stock Firmware [Stock]

On stock firmware, file uploads go to **port 80** with minimal headers. No authentication is required.

**Endpoint:** `PUT http://<printer_ip>:80/upload`

**Request (from packet capture of ElegooSlicer 1.3.2.9, which uses ElegooLink SDK v1.0.1):**
```http
PUT /upload HTTP/1.1
Host: <printer_ip>
User-Agent: ElegooLink/1.0.1
Accept: application/json
Accept-Encoding:
Content-Range: bytes 0-85255/85256
Content-Type: application/octet-stream
Content-Length: 85256

<raw gcode bytes>
```

The `User-Agent` header follows the pattern `ElegooLink/<sdk_version>` (defined as `ELEGOO_LINK_USER_AGENT` in the elegoo-link SDK source). Newer slicer versions will show a higher version number here.

| Header | Value | Notes |
|--------|-------|-------|
| `Host` | Printer IP | Standard HTTP/1.1 |
| `User-Agent` | `ElegooLink/1.0.1` | Identifies the slicer's network library (see version note below) |
| `Accept` | `application/json` | Expects JSON response |
| `Accept-Encoding` | (empty) | Explicitly empty — no compression |
| `Content-Range` | `bytes <start>-<end>/<total>` | Supports chunked/resumable uploads |
| `Content-Type` | `application/octet-stream` | Raw binary body |
| `Content-Length` | File size in bytes | Standard |

> **ElegooLink SDK version note:** The packet capture above was from ElegooSlicer 1.3.2.9, which bundles **ElegooLink v1.0.1**. This older SDK version does NOT send `X-Token`, `X-File-Name`, or `X-File-MD5` headers.
>
> However, the current [elegoo-link](https://github.com/ELEGOO-3D/elegoo-link) open-source SDK (**v1.3.6**) **always sends** these headers for CC2 uploads — including `X-File-Name`, `X-File-MD5`, and `X-Token` (defaulting to `123456` if no access code is set). Future slicer versions using the newer SDK will send these headers even to stock firmware.
>
> Stock firmware appears to accept uploads regardless of whether these headers are present — older clients omit them and uploads succeed, while newer clients include them and uploads also succeed. The headers are simply ignored when not needed.

**Response:**
```json
{
  "error_code": 0,
  "offset": 85255
}
```

| Field | Type | Meaning |
|-------|------|---------|
| `error_code` | int | 0 = success |
| `offset` | int | Last byte offset received (0-indexed). For a complete upload, equals `Content-Length - 1`. |

**Chunked upload behavior:**
- Small files may be sent in a single PUT (observed: 85KB file in one request)
- Large files are sent in multiple PUT requests with sequential `Content-Range` offsets
- The firmware source code references `?offset=<byte_offset>` query parameter and `.cbdtmp` temp file suffix during transfer

### File Upload — OpenCentauri [OpenCentauri]

On OpenCentauri firmware, uploads go to **port 8080** with additional authentication and verification headers.

**Endpoint:** `PUT http://<printer_ip>:8080/upload`

```http
PUT /upload HTTP/1.1
Content-Type: application/octet-stream
Content-Range: bytes 0-1048575/5242880
X-File-Name: model.gcode
X-File-MD5: abc123def456...
X-Token: <token>

<binary chunk data>
```

| Header | Description |
|--------|-------------|
| `Content-Range` | Byte range being uploaded |
| `X-File-Name` | Target filename |
| `X-File-MD5` | MD5 hash of complete file |
| `X-Token` | Authentication token |

**Response:**
```json
{
  "error_code": 0,
  "received": 1048576,
  "total": 5242880
}
```

**Important:**
- Maximum chunk size: 1 MB (1048576 bytes)
- Calculate MD5 before starting upload
- Use persistent HTTP connections for efficiency
- Last chunk completes the upload

### Upload Protocol Comparison

| Aspect | Stock Firmware | OpenCentauri |
|--------|---------------|--------------|
| Port | 80 | 8080 |
| Auth header (`X-Token`) | Not required (accepted but ignored) | Required |
| Filename header (`X-File-Name`) | Not sent by ElegooLink ≤1.0.1; sent by ≥1.3.6 | Sent |
| MD5 header (`X-File-MD5`) | Not sent by ElegooLink ≤1.0.1; sent by ≥1.3.6 | Sent |
| Response field | `"offset"` | `"received"` / `"total"` |
| Max chunk | 1 MB (from elegoo-link SDK) | 1 MB |

### OpenCentauri HTTP API [OpenCentauri]

The following endpoints are only available on OpenCentauri firmware. Base URL: `http://<printer_ip>:8080`

**Authentication:** All requests require `X-Token` header or `?X-Token=<access_code>` query parameter. If no access code is set, use `123456`. The elegoo-link SDK sends both the header and query parameter simultaneously.

#### Get System Info

```
GET /system/info?X-Token=<token>
```

Response:
```json
{
  "error_code": 0,
  "system_info": {
    "sn": "CC2ABCD1234567890"
  }
}
```

#### List Files

```
GET /files?storage_media=local&path=/&X-Token=<token>
```

Response:
```json
{
  "error_code": 0,
  "files": [
    {
      "name": "benchy.gcode",
      "size": 1234567,
      "modified": 1706900000,
      "type": "file"
    },
    {
      "name": "models",
      "type": "directory"
    }
  ]
}
```

#### Download File

```
GET /download?file_name=<path>&X-Token=<token>
GET /download/sdcard?file_name=<path>&X-Token=<token>
GET /download/udisk?file_name=<path>&X-Token=<token>
```

Returns raw file contents.

> ⚠️ **Stock firmware:** These download endpoints do not exist. Port 80 returns 404 for all paths except `/upload`. Port 8080 always returns the camera MJPEG stream. G-code file content **cannot be downloaded** from a CC2 on stock firmware. Only metadata and thumbnails are available via MQTT.
>
> For G-code file structure and parsing (e.g. for upload capture), see [G-Code File Structure](#g-code-file-structure).

---

## File Operations

### Storage Media Types

| Value | Description |
|-------|-------------|
| `local` | Internal storage |
| `u-disk` | USB drive |
| `sd-card` | SD card |

### MQTT File Methods - Stock Firmware Compatibility

| Method | Code | Stock Firmware | Notes |
|--------|------|---------------|-------|
| GET_FILE_LIST | 1044 | ✅ Works | Returns file names, sizes |
| GET_FILE_THUMBNAIL | 1045 | ✅ Works | Returns base64 PNG thumbnail |
| GET_FILE_DETAIL | 1046 | ✅ Works | Returns metadata: size, layers, filament used, print time, color map |
| DELETE_FILE | 1047 | Untested | — |
| GET_DISK_INFO | 1048 | Untested | — |
| SET_PRINTER_DOWNLOAD_FILE | 1057 | Untested (cloud feature) | Tells printer to download from URL — NOT for retrieving files (see below) |
| CANCEL_PRINTER_DOWNLOAD_FILE | 1058 | Untested | Cancels a 1057 download task |

### File List via MQTT [Both]

```json
{
  "id": 1,
  "method": 1044,
  "params": {
    "storage_media": "local",
    "path": "/",
    "page": 1,
    "page_size": 50
  }
}
```

Response:
```json
{
  "id": 1,
  "method": 1044,
  "result": {
    "error_code": 0,
    "total": 25,
    "files": [
      {
        "name": "benchy.gcode",
        "size": 1234567,
        "modified": 1706900000
      }
    ]
  }
}
```

### File Detail via MQTT [Both]

Returns metadata about a specific file, including filament usage data and color mapping.

```json
{
  "id": 1,
  "method": 1046,
  "params": {
    "storage_media": "local",
    "filename": "CC2_temperature_tower.gcode"
  }
}
```

Response (from stock firmware testing):
```json
{
  "id": 1,
  "method": 1046,
  "result": {
    "error_code": 0,
    "filename": "CC2_temperature_tower.gcode",
    "size": 5712759,
    "layer": 722,
    "print_time": 4690,
    "total_filament_used": 24.8,
    "color_map": [
      {"color": "#0B6283", "name": "PLA", "t": 3}
    ],
    "create_time": 1772820155,
    "last_print_time": 0,
    "total_print_times": 0
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `filename` | string | File name |
| `size` | int | File size in bytes |
| `layer` | int | Total layers (also reported as `TotalLayers` in some firmware) |
| `print_time` | int | Estimated print time in seconds |
| `total_filament_used` | float | Total filament usage for the print |
| `color_map` | array | Per-extruder/slot filament info (see below) |
| `create_time` | int | Unix timestamp of file creation |
| `last_print_time` | int | Unix timestamp of last print (0 = never printed) |
| `total_print_times` | int | Number of times this file has been printed |

**`color_map` entries:**

| Field | Type | Description |
|-------|------|-------------|
| `color` | string | Hex color code with `#` prefix |
| `name` | string | Filament type name (e.g., "PLA", "PETG") |
| `t` | int | Canvas tray index used for this slot |

For multi-material prints, `color_map` contains one entry per extruder/slot used. The `t` field maps to the Canvas tray index (0-based). The `total_filament_used` field is a single total value — per-extruder weight breakdown is not available via this method (it exists only in the G-code file comments).

### Method 1057 - SET_PRINTER_DOWNLOAD_FILE

> **Important:** Method 1057 is NOT for downloading file content FROM the printer to the client. It instructs the **printer** to download a file FROM a given URL to its own storage. This is a cloud/remote feature used when the printer needs to pull a file from cloud storage or a remote server.

The elegoo-link SDK maps this as `SET_PRINTER_DOWNLOAD_FILE` with parameters:

```json
{
  "id": 1,
  "method": 1057,
  "params": {
    "filename": "model.gcode",
    "url": "https://cloud.example.com/files/model.gcode",
    "md5": "abc123def456...",
    "taskID": "unique-task-id"
  }
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `filename` | string | Target filename to save as on the printer |
| `url` | string | URL for the printer to download the file from |
| `md5` | string | MD5 hash for verification after download |
| `taskID` | string | Unique task identifier (used for cancellation with method 1058) |

#### How to actually download files FROM the printer

To download a file from the printer to the client, use the **HTTP API** — not MQTT method 1057. The elegoo-link SDK uses `GET /download?file_name=<path>&X-Token=<token>` with storage-specific paths:

| Storage | HTTP Endpoint |
|---------|---------------|
| `local` | `GET /download?file_name=<path>` |
| `sdcard` | `GET /download/sdcard?file_name=<path>` |
| `udisk` | `GET /download/udisk?file_name=<path>` |

All require `X-Token` authentication (query parameter or header). See [Download File](#download-file) in the OpenCentauri HTTP API section.

> ⚠️ **Stock firmware:** These HTTP download endpoints do not exist. There is no MQTT alternative for downloading file content either. On stock firmware, G-code file content is **not accessible by any method** — only metadata (method 1046) and thumbnails (method 1045) are available.

### Delete File [Both]

```json
{
  "id": 1,
  "method": 1047,
  "params": {
    "storage_media": "local",
    "filename": "old_model.gcode"
  }
}
```

### Disk Info [Both]

```json
{
  "id": 1,
  "method": 1048,
  "params": {
    "storage_media": "local"
  }
}
```

Response:
```json
{
  "id": 1,
  "method": 1048,
  "result": {
    "error_code": 0,
    "total_bytes": 8589934592,
    "free_bytes": 4294967296,
    "used_bytes": 4294967296
  }
}
```

---

## Video Streaming

The CC2 camera provides MJPEG streaming on port 8080.

### Enable Video Stream

```json
{
  "id": 1,
  "method": 1042,
  "params": {
    "enable": true
  }
}
```

### Stream URL

```
http://<printer_ip>:8080/?action=stream
```

> **Note [Stock]:** On stock firmware, port 8080 serves the camera MJPEG stream for **every request** regardless of path, query parameters, or headers. The URL path and `?action=stream` query have no effect — any HTTP request to port 8080 returns the camera stream. There is no `Accept-Ranges` header, `Range` headers are ignored, and `X-Token` has no effect.

### Stream Characteristics

- Format: MJPEG (Motion JPEG), `Content-Type: multipart/x-mixed-replace; boundary=--frame_boundary`
- Resolution: Varies by camera
- Max connections: Usually 1 (check `max_video_connections` in attributes)
- No authentication required for stream itself

### Example: Display Stream

```python
import cv2

stream_url = f"http://{printer_ip}:8080/?action=stream"
cap = cv2.VideoCapture(stream_url)

while True:
    ret, frame = cap.read()
    if ret:
        cv2.imshow('Printer Camera', frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
```

---

## Canvas/AMS System

The Canvas is Elegoo's Automatic Material System (similar to Bambu Lab AMS).

### Get Canvas Status

```json
{
  "id": 1,
  "method": 2005,
  "params": {}
}
```

### Canvas Status Response

**Real-world example from CC2 printer with Canvas:**

```json
{
  "id": 1,
  "method": 2005,
  "result": {
    "error_code": 0,
    "canvas_info": {
      "active_canvas_id": 0,
      "active_tray_id": 3,
      "auto_refill": false,
      "canvas_list": [
        {
          "canvas_id": 0,
          "connected": 1,
          "tray_list": [
            {
              "tray_id": 0,
              "brand": "ELEGOO",
              "filament_type": "PLA",
              "filament_name": "PLA",
              "filament_color": "#2850DF",
              "min_nozzle_temp": 190,
              "max_nozzle_temp": 230,
              "status": 1
            },
            {
              "tray_id": 1,
              "brand": "ELEGOO",
              "filament_type": "PLA",
              "filament_name": "PLA Basic",
              "filament_color": "#FFFFFF",
              "min_nozzle_temp": 190,
              "max_nozzle_temp": 230,
              "status": 1
            },
            {
              "tray_id": 2,
              "brand": "ELEGOO",
              "filament_type": "PLA",
              "filament_name": "PLA Silk",
              "filament_color": "#F32FF8",
              "min_nozzle_temp": 190,
              "max_nozzle_temp": 230,
              "status": 1
            },
            {
              "tray_id": 3,
              "brand": "ELEGOO",
              "filament_type": "PLA",
              "filament_name": "PLA",
              "filament_color": "#000000",
              "min_nozzle_temp": 190,
              "max_nozzle_temp": 230,
              "status": 2
            }
          ]
        }
      ]
    }
  }
}
```

### Canvas Fields

| Field | Type | Description |
|-------|------|-------------|
| `active_canvas_id` | int | Currently selected Canvas unit (0-based, 0 = first unit) |
| `active_tray_id` | int | Currently selected tray (0-based, 0 = first tray) |
| `auto_refill` | bool | Auto-switch when filament runs out |
| `canvas_id` | int | Canvas unit ID (0-based: 0, 1, 2...) |
| `connected` | int | 1=connected, 0=disconnected |
| `tray_id` | int | Tray slot ID (0-based: 0, 1, 2, 3 for 4-tray system) |
| `brand` | string | Filament manufacturer |
| `filament_type` | string | Material type (PLA, PETG, ABS, etc.) |
| `filament_name` | string | Specific filament name |
| `filament_color` | string | Hex color code **with # prefix** (e.g., "#FF0000") |
| `min_nozzle_temp` | int | Minimum nozzle temperature (°C) |
| `max_nozzle_temp` | int | Maximum nozzle temperature (°C) |
| `status` | int | 1=filament present, 2=currently active, 0=empty |

**Important Notes:**
- All IDs are **0-based** (canvas_id: 0 = first Canvas, tray_id: 0 = first tray)
- Color codes include `#` prefix (e.g., `#FF0000` not `FF0000`)
- Bed temperature fields (`bed_temp_min`, `bed_temp_max`) are **not present** in Canvas API
- Temperature fields use correct names: `min_nozzle_temp` / `max_nozzle_temp` (not `nozzle_temp_min`)
- `status` values: 0=empty, 1=loaded, 2=currently active during print

### Set Auto Refill

```json
{
  "id": 1,
  "method": 2004,
  "params": {
    "enable": true
  }
}
```

### Printing with Canvas

When starting a print with Canvas, include slot mapping:

```json
{
  "id": 1,
  "method": 1020,
  "params": {
    "storage_media": "local",
    "filename": "multicolor.gcode",
    "config": {
      "slot_map": [
        {"slot": 1, "canvas_id": 1, "tray_id": 1},
        {"slot": 2, "canvas_id": 1, "tray_id": 2}
      ]
    }
  }
}
```

---

## Print Job Lifecycle

### Print State Machine

```
                          ┌─────────────┐
                          │    IDLE     │
                          └──────┬──────┘
                                 │ START_PRINT
                                 ▼
                ┌────────────────────────────────┐
                │           PRINTING             │
                │  ┌──────────────────────────┐  │
                │  │  PREHEATING              │  │
                │  │    ↓                     │  │
                │  │  HOMING (optional)       │  │
                │  │    ↓                     │  │
                │  │  LEVELING (optional)     │  │
                │  │    ↓                     │  │
                │  │  PRINTING ◄──────────┐   │  │
                │  │    │                 │   │  │
                │  │    ├── PAUSING ──► PAUSED│  │
                │  │    │                 │   │  │
                │  │    │        RESUMING─┘   │  │
                │  └────┼─────────────────────┘  │
                │       │                        │
                └───────┼────────────────────────┘
                        │
          ┌─────────────┼─────────────┐
          │             │             │
          ▼             ▼             ▼
    ┌──────────┐  ┌──────────┐  ┌──────────┐
    │ COMPLETE │  │ STOPPING │  │  ERROR   │
    └──────────┘  └────┬─────┘  └──────────┘
                       │
                       ▼
                 ┌──────────┐
                 │ STOPPED  │
                 └──────────┘
```

### Upload-Then-Print Flow [Stock]

This section documents the complete sequence of events when ElegooSlicer sends a print job to the CC2 on stock firmware. Derived from packet capture of ElegooSlicer 1.3.2.9 and confirmed by the elegoo-link SDK source.

**Participants:**
- **Slicer**: ElegooSlicer
- **Printer**: CC2 with stock firmware

| Time | Direction | Protocol | What Happens |
|------|-----------|----------|-------------|
| 0-3s | Printer → Slicer | MQTT | Status pushes on `api_status` (~1/sec) |
| 3.9s | Slicer → Printer | MQTT | api_request (get_status or get_attributes) |
| 3.9s | Printer → Slicer | MQTT | api_response |
| 10.6s | Slicer → Printer | MQTT | api_request (get_file_list, larger response ~1KB) |
| 10.7s | Printer → Slicer | MQTT | api_response (~1KB, file list) |
| 14.3s | Slicer → Printer | MQTT | api_request (status check) |
| 14.3s | Printer → Slicer | MQTT | api_response |
| 20.5s | Slicer → Printer | MQTT | api_request (~155 bytes, pre-upload notification) |
| 20.5s | Printer → Slicer | MQTT | api_response (~1KB) |
| **20.5s** | **Slicer → Printer** | **HTTP** | **TCP SYN to port 80, then `PUT /upload` (85KB)** |
| **20.9s** | **Printer → Slicer** | **HTTP** | **200 OK, `{"error_code": 0, "offset": 85255}`** |
| 20.9s | Slicer → Printer | HTTP | TCP FIN (connection closes) |
| 21.9s | Slicer → Printer | MQTT | api_request (file detail query for uploaded file) |
| 22.0s | Printer → Slicer | MQTT | api_response (~1KB, file metadata) |
| 22.1s | Slicer → Printer | MQTT | api_request (another query) |
| 22.2s | Printer → Slicer | MQTT | api_response |
| 22.2s | Slicer → Printer | MQTT | api_request (365 bytes, start_print command with config) |
| 22.4s | Printer → Slicer | MQTT | api_response (acknowledgment) |
| 22.8s+ | Printer → Slicer | MQTT | Status updates shift to printing state |

**Key observations:**
- The HTTP upload is a single isolated event. TCP connect, PUT, response, TCP close, completing in under half a second. This was a small file, larger files may be sent in chunks.
- Everything before and after the upload is MQTT
- The slicer queries file detail (method 1046) for the just-uploaded file before issuing start_print
- The G-code body starts with `; HEADER_BLOCK_START` and contains the full file structure (header, thumbnail, parameters, executable G-code, filament usage summary, CONFIG_BLOCK)

### Start Print

```json
{
  "id": 1,
  "method": 1020,
  "params": {
    "storage_media": "local",
    "filename": "benchy.gcode",
    "config": {
      "delay_video": false,
      "printer_check": true,
      "print_layout": "A",
      "bedlevel_force": false,
      "slot_map": []
    }
  }
}
```

#### Config Options

| Field | Type | Description |
|-------|------|-------------|
| `delay_video` | bool | Delay start until video streaming active |
| `printer_check` | bool | Run pre-print checks |
| `print_layout` | string | Layout option (model-specific) |
| `bedlevel_force` | bool | Force bed leveling before print |
| `slot_map` | array | Canvas/AMS filament mapping |

### Pause Print

```json
{
  "id": 1,
  "method": 1021,
  "params": {}
}
```

### Resume Print

```json
{
  "id": 1,
  "method": 1023,
  "params": {}
}
```

### Stop Print

```json
{
  "id": 1,
  "method": 1022,
  "params": {}
}
```

### Print Progress Monitoring

Monitor these fields during printing:

| Field | Location | Description |
|-------|----------|-------------|
| `status` | `machine_status.status` | Should be 2 (PRINTING) |
| `sub_status` | `machine_status.sub_status` | Detailed state |
| `progress` | `machine_status.progress` or `print_status.progress` | 0-100% |
| `current_layer` | `print_status.current_layer` | Current layer |
| `remaining_time_sec` | `print_status.remaining_time_sec` | ETA in seconds |

---

## Error Handling

### Error Code Reference

| Code | Name | Description | Recovery |
|------|------|-------------|----------|
| 0 | SUCCESS | Operation completed | N/A |
| 109 | FILAMENT_RUNOUT | No filament detected | Load filament, resume |
| 1000 | TOKEN_FAILED | Authentication failed | Check access code |
| 1001 | UNKNOWN_INTERFACE | Unknown command | Check method code |
| 1002 | FOLDER_OPEN_FAILED | Cannot access folder | Check path |
| 1003 | INVALID_PARAMETER | Bad parameter | Check params |
| 1004 | FILE_WRITE_FAILED | Cannot write file | Check disk space |
| 1005 | TOKEN_UPDATE_FAILED | Token refresh failed | Re-authenticate |
| 1006 | MOS_UPDATE_FAILED | MOS update failed | Retry |
| 1007 | FILE_DELETE_FAILED | Cannot delete file | Check file exists |
| 1008 | RESPONSE_EMPTY | No data returned | Retry |
| 1009 | PRINTER_BUSY | Printer occupied | Wait, retry |
| 1010 | NOT_PRINTING | No active print | Check state first |
| 1011 | FILE_COPY_FAILED | Copy failed | Check disk space |
| 1012 | TASK_NOT_FOUND | Print task missing | Refresh task list |
| 1013 | DATABASE_FAILED | DB error | Internal error |
| 1021 | PRINT_FILE_NOT_FOUND | File doesn't exist | Upload file |
| 1026 | MISSING_BED_LEVELING | No mesh data | Run leveling |
| 9000 | FILE_OFFSET_MISMATCH | Upload offset wrong | Restart upload |
| 9001 | FILE_OPEN_FAILED | Cannot open file | Check filename |
| 9002 | FILE_WRITE_ERROR | Write error | Check disk |
| 9003 | FILE_SEEK_FAILED | Seek error | Retry |
| 9004 | MD5_FAILED | Checksum mismatch | Re-upload |
| 9005 | CANCEL_NOT_NEEDED | Nothing to cancel | Ignore |
| 9006 | CANCEL_FAILED | Cancel failed | Force retry |
| 9007 | PATH_NOT_EXISTS | Path not found | Check path |
| 9008 | MD5_SYSTEM_ERROR | System MD5 error | Retry |
| 9009 | MD5_READ_ERROR | File read error | Retry |
| 9999 | UNKNOWN_ERROR | Unclassified error | Check logs |

### Exception Status

The `machine_status.exception_status` array contains active errors:

```json
{
  "machine_status": {
    "exception_status": [109, 1026]
  }
}
```

This indicates both filament runout (109) and missing bed leveling (1026).

### Error Recovery Patterns

#### Filament Runout
1. Detect error 109 in exception_status
2. Notify user
3. User loads filament
4. Clear exception (may require print resume)
5. Issue RESUME_PRINT command

#### Authentication Error
1. Detect error 1000
2. Re-prompt user for access code
3. Reconnect with new credentials

#### Printer Busy
1. Detect error 1009
2. Wait 5-10 seconds
3. Retry command
4. After 3 failures, check printer status

---

## Security Considerations

### Access Code

- When `token_status=1` in discovery, an access code is required
- The access code replaces the default password `123456`
- Access codes are set through the printer's touchscreen
- Store access codes securely (not in plain text logs)

### Network Security

- CC2 uses **unencrypted** MQTT and HTTP
- All traffic is visible on the local network
- Recommend keeping printers on isolated IoT network
- Do not expose ports 1883 or 8080 to the internet

### MQTT Connection Limits

- Maximum ~4 concurrent MQTT connections (shared across slicer, HA integration, web interface, and any other connected applications)
- All connection slots can be exhausted by legitimate use (slicer + HA integration + web interface = 3 slots)
- A local network proxy's MQTT pass-through counts as one connection per slicer session
- Malicious clients could DoS by filling all slots
- Implement clean disconnection on application exit to free slots

### Best Practices

1. **Never hardcode access codes** - prompt user or use secure storage
2. **Use network isolation** - separate VLAN for IoT devices
3. **Implement connection timeouts** - don't hold connections indefinitely
4. **Handle disconnection gracefully** - free up client slots

---

## Firmware Variations

Different firmware versions may behave slightly differently.

### Firmware Architecture [Stock]

The CC2 stock firmware is built on the following components:

| Component | Details |
|-----------|---------|
| Base system | **Klipper** (G-Code command structure, virtual SD card) |
| CPU | **Allwinner R528** (ARM Cortex-A7) |
| HTTP server | **libhv/1.3.4** (port 80, minimal endpoints) |
| Upload handler | **Mongoose HTTP library** (chunked uploads with offset tracking) |
| Temp file suffix | `.cbdtmp` during upload, renamed on completion |

#### Open Source vs Proprietary Components

The [CentauriCarbon](https://github.com/ELEGOO-3D/CentauriCarbon) open-source repository includes:
- Klipper core (motion control, G-Code parsing)
- File management (list, metadata, thumbnails)
- OTA update mechanism
- Hardware drivers

The following components are **proprietary** and not in the open-source code:
- HTTP server (libhv on port 80)
- Camera streaming (port 8080 MJPEG)
- MQTT command handlers and broker
- Web interface

This distinction is important: the open-source Klipper code shows file storage paths and upload handling, but the network-facing services (HTTP endpoints, MQTT protocol, camera) are closed-source. This limits the ability to predict or modify network behavior without packet-level analysis.

### Known Variations

| Feature | v1.0.x | v1.1.x | Notes |
|---------|--------|--------|-------|
| Position field | `gcode_move_inf` | `gcode_move_inf` | Some early firmware used `gcode_move` |
| Extruder field | `e` | `e` | Some used `extruder` |
| Fan RPM | Present | Present | May be 0 if not supported |
| Canvas support | Partial | Full | Varies by model |

### Defensive Coding

```python
# Handle field variations
def get_position(status):
    pos = status.get("gcode_move_inf") or status.get("gcode_move", {})
    return {
        "x": pos.get("x", 0),
        "y": pos.get("y", 0),
        "z": pos.get("z", 0),
        "e": pos.get("e") or pos.get("extruder", 0)
    }
```

### Version Detection

Check firmware version in attributes response:

```python
def get_firmware_version(attrs):
    sw = attrs.get("software_version", {})
    return sw.get("ota_version", "unknown")
```

---

## CC1 vs CC2 Comparison

| Feature | CC1 (Centauri Carbon) | CC2 (Centauri Carbon 2) |
|---------|----------------------|-------------------------|
| **Discovery** | | |
| Port | 3000 | 52700 |
| Message | `M99999` | `{"id":0,"method":7000}` |
| Protocol | TCP | UDP |
| **Communication** | | |
| Transport | WebSocket | MQTT |
| Broker Location | External (HA) | **On Printer** |
| Home Assistant Role | MQTT Broker (server) | **MQTT Client (connects to printer)** |
| **Authentication** | | |
| Registration | Not required | **Required** |
| Password | None | `123456` or access code |
| **Connection** | | |
| Heartbeat | Not required | **Every 10s** |
| Max Clients | Unlimited | **~4** |
| Timeout | None | **65 seconds** |
| **Status** | | |
| Update Type | Full | **Delta** |
| Message Format | Proprietary | JSON |
| **Features** | | |
| Canvas/AMS | No | Yes |
| Video Stream | Varies | MJPEG on :8080 |

---

## Implementation Checklist

> **C++ developers:** Consider using the [elegoo-link](https://github.com/ELEGOO-3D/elegoo-link) SDK directly instead of implementing the protocol from scratch. The checklist below is for developers building custom implementations in other languages.

Use this checklist when implementing CC2 support:

### Discovery
- [ ] UDP broadcast to port 52700
- [ ] Handle multiple printer responses
- [ ] Parse discovery response fields
- [ ] Store serial number for topics
- [ ] Handle token_status for auth

### Connection
- [ ] MQTT 3.1.1 client
- [ ] Generate unique client_id
- [ ] Handle authentication (default + access code)
- [ ] Connection error handling
- [ ] Automatic reconnection

### Registration
- [ ] Subscribe to register_response topic
- [ ] Publish registration request
- [ ] Handle success/failure responses
- [ ] Timeout handling (3 seconds)
- [ ] Retry logic for "too many clients"

### Heartbeat
- [ ] 10-second interval timer
- [ ] PING message publishing
- [ ] PONG response handling
- [ ] 65-second timeout detection
- [ ] Reconnection on timeout

### Status
- [ ] Subscribe to api_status topic
- [ ] Parse full status structure
- [ ] Implement delta merge
- [ ] Track message IDs for continuity
- [ ] Request full status on gaps

### Commands
- [ ] Command request formatting
- [ ] Response matching by ID
- [ ] Error code handling
- [ ] Timeout handling

### File Operations
- [ ] File listing (MQTT method 1044, or HTTP on OpenCentauri)
- [ ] File upload (stock: `PUT /upload` on port 80; OpenCentauri: port 8080 with auth)
- [ ] File detail/metadata (MQTT method 1046)
- [ ] File thumbnail (MQTT method 1045)
- [ ] File download (OpenCentauri only: HTTP `GET /download`; stock firmware: not available via any method)

### Peripheral Control
- [ ] Temperature control
- [ ] Fan speed control (0-255)
- [ ] Light control
- [ ] Speed mode control

### Print Control
- [ ] Start print
- [ ] Pause/Resume
- [ ] Stop
- [ ] Progress monitoring

### Advanced
- [ ] Canvas/AMS status
- [ ] Video streaming
- [ ] Print history
- [ ] Firmware version handling

---

## Troubleshooting

### Connection Issues

**Problem**: Discovery times out
- Check printer is powered on and connected to network
- Verify you're on the same network/subnet
- Check firewall allows UDP port 52700
- Try direct IP instead of broadcast

**Problem**: MQTT connection refused
- Verify printer IP is correct
- Check port 1883 is not blocked
- Verify credentials (check token_status)
- Check another client isn't blocking

**Problem**: Registration fails with "too many clients"
- Disconnect other clients (slicer, app)
- Wait 65 seconds for timeout to expire
- Restart printer to clear all connections

**Problem**: Heartbeat timeout
- Check network stability
- Verify PING messages being sent
- Check for PONG responses
- Reduce heartbeat interval if needed

### Data Issues

**Problem**: Missing status fields
- Request full status (method 1002)
- Check firmware version
- Handle field variations defensively

**Problem**: Delta updates not applying
- Verify deep merge implementation
- Check for ID continuity
- Request full status periodically

**Problem**: Wrong temperature/progress values
- Verify you're merging deltas correctly
- Check field paths in nested structure

### Print Issues

**Problem**: Start print fails with "file not found"
- Verify filename is correct
- Check storage_media value
- Upload file first

**Problem**: Print doesn't resume after pause
- Check printer isn't in error state
- Verify sub_status is PAUSED
- Clear any exception_status errors first

---

## Glossary

| Term | Definition |
|------|------------|
| **AMS** | Automatic Material System (multi-filament) |
| **Canvas** | Elegoo's AMS product name |
| **CC1** | Centauri Carbon (first generation protocol) |
| **CC2** | Centauri Carbon 2 (this protocol) |
| **Delta Update** | Status message containing only changed fields |
| **elegoo-link** | Elegoo's official network communication library |
| **FDM** | Fused Deposition Modeling (3D printing technology) |
| **Heartbeat** | Periodic message to maintain connection |
| **MQTT** | Message Queuing Telemetry Transport protocol |
| **OTA** | Over-The-Air (firmware update) |
| **PID** | Proportional-Integral-Derivative (temperature control) |
| **PWM** | Pulse Width Modulation (fan speed control) |
| **SDCP** | Elegoo's resin printer protocol |
| **Serial Number (SN)** | Unique printer identifier used in topics |
| **Sub-status** | Detailed state within main status |
| **Token** | Access code for authentication |

---

## References

### Official Sources
- [elegoo-link](https://github.com/ELEGOO-3D/elegoo-link) - **Official C++ SDK** (v1.3.6). Primary source for protocol details. Implements discovery, MQTT, HTTP upload/download, and all printer commands. Used internally by ElegooSlicer.
- [CentauriCarbon](https://github.com/ELEGOO-3D/CentauriCarbon) - Open-source Klipper firmware components (file management, OTA, hardware drivers). Network services (HTTP, MQTT, camera) are proprietary and not included.
- [ElegooSlicer](https://github.com/ELEGOO-3D/ElegooSlicer) - Official slicer application (uses elegoo-link SDK)
- [elegoo-fdm-web](https://github.com/ELEGOO-3D/elegoo-fdm-web) - Web interface releases

### Community Projects
- [elegoo-homeassistant](https://github.com/danielcherubini/elegoo-homeassistant) - Home Assistant integration
- [OpenCentauri](https://opencentauri.org) - Community documentation project

### Testing Methodology

Protocol documentation in this document is derived from two complementary approaches:

**Primary source — elegoo-link SDK source code:**
1. **[elegoo-link](https://github.com/ELEGOO-3D/elegoo-link) v1.3.6** — Elegoo's official C++ SDK. Method codes, message formats, error codes, auth modes, connection flow, upload/download protocol, discovery, and state machine are all from this source.
2. **[CentauriCarbon](https://github.com/ELEGOO-3D/CentauriCarbon)** — Open-source Klipper firmware (Mongoose HTTP library, file transfer handlers, storage paths)

**Stock firmware validation — manual testing:**

The SDK documents the protocol as designed, but doesn't tell you what actually works on stock firmware vs. OpenCentauri. The following manual testing established stock firmware capabilities:

3. **Packet capture** of ElegooSlicer 1.3.2.9 "Upload and Print" session (confirmed upload flow, header behavior, timing)
4. **Port scanning** of CC2 stock firmware (15+ ports tested including 21, 22, 23, 80, 1883, 3030, 3031, 8080, 8888, 34952, 54780)
5. **HTTP endpoint probing** on ports 80 and 8080 (20+ paths tested: `/`, `/download`, `/files`, `/api`, `/system/info`, etc., with and without `X-Token` auth headers)
6. **MQTT protocol testing** with direct broker connection (methods 1001, 1002, 1044, 1045, 1046)

Manual testing performed on stock Elegoo firmware. The SDK source was cross-referenced to verify findings and fill in gaps (e.g., the SDK always sends auth headers for uploads, but stock firmware used an older SDK version that did not).

### Related Documentation
- [MQTT 3.1.1 Specification](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html)
- [Paho MQTT Python](https://eclipse.dev/paho/files/paho.mqtt.python/html/index.html)

---

## Contributing

Found an error or have additional information? Contributions welcome!

- **GitHub Issues**: Report errors or missing information
- **Pull Requests**: Submit documentation improvements
- **Discord/Forums**: Discuss findings with the community

When contributing:
1. Cite your source (firmware version, elegoo-link version, etc.)
2. Include example data where possible
3. Note any firmware-specific behavior

---

## G-Code File Structure

ElegooSlicer (based on OrcaSlicer) produces G-code files with a well-defined block structure. Metadata is concentrated at the head and tail of the file, so parsers can extract everything without reading the multi-megabyte body. The filament usage data at the end is particularly relevant for tracking per-spool consumption with multi-material Canvas setups.

### File Layout

```
; HEADER_BLOCK_START
; generated by ElegooSlicer 1.3.2.9 on 2026-01-11 at 11:11:11
; total layer number: 111
; filament_density: 1.26,1.26,1.26,1.25
; filament_diameter: 1.75,1.75,1.75,1.75
; max_z_height: 56.04
; HEADER_BLOCK_END

; THUMBNAIL_BLOCK_START
; thumbnail begin 144x144 <byte_count>
; <base64-encoded PNG, split across comment lines>
; thumbnail end
; THUMBNAIL_BLOCK_END

<print parameter comments — extrusion widths, etc.>

; EXECUTABLE_BLOCK_START
<G-code moves — bulk of the file>
; EXECUTABLE_BLOCK_END

; filament used [mm] = 0.00, 11111.11, 0.00, 0.00   <--- Per-slot filament usage in millimeters
; filament used [cm3] = 0.00, 11111.11, 0.00, 0.00  <--- Per-slot filament usage in cubic centimeters
; filament used [g] = 0.00, 11111.11, 0.00, 0.00    <--- Per-slot filament usage in grams
; total filament used [g] = 11111.11                <--- Total filament usage in grams
; total filament cost = 11.11
; total layers count = 111
; estimated printing time (normal mode) = 1h 11m 11s

; CONFIG_BLOCK_START
; <full slicer settings as "; key = value" lines>
; filename_format = <name>.gcode     <--- Whatever custom name format the user set in the slicer
; input_filename_base = my_model     <--- May be absent in some versions
; CONFIG_BLOCK_END
```

**Key observations:**

- **Per-slot filament data** (4 values for Canvas/AMS slots) appears between
  `EXECUTABLE_BLOCK_END` and `CONFIG_BLOCK_START`.
- **CONFIG_BLOCK** contains all slicer settings as `; key = value` comment lines.
  For multi-material profiles this block has been observed to be around 26 KB.


### Filament Usage Comments

The per-slot filament data uses comma-separated values, one per Canvas slot (A1, A2, A3, A4):

```gcode
; filament used [mm] = 0.00, 0.00, 0.00, 916.48
; filament used [cm3] = 0.00, 0.00, 0.00, 2.20
; filament used [g] = 0.00, 0.00, 0.00, 2.76
; total filament used [g] = 2.76
; total filament cost = 0.00
```

The four values correspond to Canvas slots 0-3. Only slots with non-zero values were used for the print.

### CONFIG_BLOCK Filament Settings

The CONFIG_BLOCK at the end contains slicer profile settings with semicolon-separated per-extruder values:

```gcode
; filament_settings_id = My Custom PLA Profile;My Custom PETG Profile
; filament_type = PLA;PETG
; filament_density = 1.24;1.27
```

> **Note:** This per-slot data is only accessible from the G-code file itself, not from the printer's MQTT status or file detail responses. The MQTT file detail (method 1046) provides `total_filament_used` as a single number and `color_map` with filament types, but not the per-slot weight breakdown.
>
> On stock firmware, G-code file content cannot be downloaded from the printer (see [Method 1057](#method-1057--download_file)). To access per-slot filament data, the file may be captured at upload time using a local network proxy between the slicer and the printer.
>
> **Proxy approach:** A local network proxy intercepts the slicer's `PUT /upload` request on port 80, parses the G-code file, and forwards the request to the printer. The slicer is pointed at the proxy's IP instead of the printer's IP and can query it for per-slot filament data. The proxy also provides TCP pass-through on ports 1883 (MQTT over TCP), 9001 (MQTT over WebSocket), and 8080 (camera) so the slicer's Device page continues to work normally. The Device page's JavaScript creates its own MQTT-over-WebSocket client on port 9001 for file lists, live status, and controls. The HA integration connects directly to the printer and does not need to go through the proxy.
>
> **Home Assistant:** CC2 integration options store the capture-proxy **base URL** (`http://` or `https://`, host, optional port). The config flow normalizes input (for example repeated `http://` prefixes) before saving and before calling the proxy health endpoint.

---

## Addendum: Implementation Findings

This section documents discrepancies and additional findings discovered during real-world implementation that differ from or extend the main documentation.

### Light Control (Method 1029)

**Documentation states**: `{"brightness": 255}` parameter

**Actual behavior**: The web interface uses `power` parameter:
```json
{
  "id": 1,
  "method": 1029,
  "params": {
    "power": 1
  }
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `power` | int | 0 = off, 1 = on |

The `brightness` parameter may work on some firmware versions, but `power` is what the official web interface uses.

### Total Layers Not in Delta Updates

**Issue**: The `total_layer` field in `print_status` is often missing from delta status updates (method 6000).

**Explanation**: Since `total_layer` doesn't change during printing, the CC2 protocol omits it from delta updates to save bandwidth. The field is only reliably present in:
1. Full status response (method 1002) at print start
2. File details response (method 1046)

**Workaround**: When `total_layer` is missing from `print_status`, fetch file details:

```json
{
  "id": 1,
  "method": 1046,
  "params": {
    "storage_media": "local",
    "filename": "benchy.gcode"
  }
}
```

Response includes:
```json
{
  "id": 1,
  "method": 1046,
  "result": {
    "TotalLayers": 500,
    "layer": 500,
    ...
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `TotalLayers` | int | Total layers in the gcode file |
| `layer` | int | Same as TotalLayers (alternate field name) |

### Field Name Variations in File Details

The file details response (method 1046) may use different field names across firmware versions:

| Standard Name | Variations |
|--------------|------------|
| `TotalLayers` | `layer`, `total_layer` |

Always check for multiple field names when parsing file details.

### File Thumbnail (Method 1045)

Method 1045 (`GET_FILE_THUMBNAIL`) retrieves a thumbnail image for a print file. This is used to display a preview of the current print job.

**Request:**
```json
{
  "id": 1,
  "method": 1045,
  "params": {
    "storage_media": "local",
    "filename": "benchy.gcode"
  }
}
```

**Response:**
```json
{
  "id": 1,
  "method": 1045,
  "result": {
    "error_code": 0,
    "thumbnail": "<base64-encoded PNG image data>"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `storage_media` | string | Storage location (`local`, `u-disk`, `sd-card`) |
| `filename` | string | Name of the gcode file |
| `thumbnail` | string | Base64-encoded image data (typically PNG) |

**Notes:**
- The `thumbnail` field contains raw base64 data (not a data URI)
- If the file has no embedded thumbnail, the response may omit the `thumbnail` field or return an error
- Thumbnails are typically embedded in gcode files by the slicer
- Source: `elegoo-fdm-web` LAN web interface (`25-app-components.js`)

### Begin Time / End Time

**Issue**: `begin_time` and `end_time` are not provided in live print status.

**Available fields in print_status**:
- `print_duration` - Elapsed time in seconds
- `remaining_time_sec` - Estimated remaining time in seconds
- `total_duration` - Estimated total print time in seconds

**Calculating begin_time**: `current_time - print_duration`
**Calculating end_time**: `current_time + remaining_time_sec`

Note: `begin_time` and `end_time` ARE provided in historical task data (method 1036/1037) but not in live status.

---

## Changelog

### 2026-03-16 - Port 9001 Discovery
- Added port 9001 (MQTT over WebSocket) to port map and network architecture
- The CC2 exposes MQTT on two ports: 1883 (TCP, used by the elegoo-link C++ library)
  and 9001 (WebSocket, used by the slicer's Device page JavaScript). Discovered via
  analysis of the bundled `lan_service_web/index.html` which connects `ws://{ip}:9001`
  using mqtt.js. Port confirmed open on stock firmware via port scan.
- Updated architecture diagram with MQTT-WS port
- Updated proxy approach section to include port 9001 relay requirement

### 2026-03-08 - Stock Firmware Documentation Updates
- Added elegoo-link SDK section to introduction: explains the SDK is available as a usable C++ library, not just a reference
- Restructured Testing Methodology: separated SDK source analysis (primary) from manual stock firmware validation
- Updated References: expanded elegoo-link and CentauriCarbon descriptions
- Added SDK note to Implementation Checklist
- Added firmware compatibility context throughout document (stock vs OpenCentauri labels)
- Updated Network Architecture with correct port table (added port 80) and stock firmware diagram
- Documented stock firmware upload protocol (`PUT /upload` on port 80, `offset` response)
- Clarified upload header behavior across ElegooLink SDK versions: v1.0.1 omits auth headers, v1.3.6 always sends `X-Token`, `X-File-Name`, `X-File-MD5`. Stock firmware accepts uploads either way.
- Fixed chunk size: 1 MB for both stock and OpenCentauri (from elegoo-link SDK source)
- Added firmware compatibility banner to HTTP API section; noted most HTTP endpoints are OpenCentauri-only
- **Corrected Method 1057**: Renamed from DOWNLOAD_FILE to SET_PRINTER_DOWNLOAD_FILE. This method tells the printer to download a file FROM a URL (cloud feature), not for downloading files from the printer. Added correct params: `filename`, `url`, `md5`, `taskID`.
- Fixed Client ID section: `1_PC_<1000-9999>` format is the current elegoo-link SDK format (not deprecated). The `0cli` format is from the web interface.
- Fixed Request ID section: `1_PC_<number>_req` is the current elegoo-link SDK format (not legacy)
- Added MQTT authentication modes table (basic, accessCode, token, pinCode) from elegoo-link SDK
- Added note about elegoo-link SDK attempting `GET /system/info` (404 on current stock firmware, handled gracefully)
- Clarified port 8080 behavior on stock firmware (camera MJPEG only, no file operations)
- Added Upload-Then-Print flow section with packet-capture-derived timeline
- Documented full GET_FILE_DETAIL (1046) response including `total_filament_used` and `color_map`
- Added G-Code File Structure section documenting per-slot filament usage comments and CONFIG_BLOCK
- Updated Video Streaming section with stock firmware behavior notes
- Added Firmware Architecture subsection (Klipper base, Allwinner R528, storage paths, open-source vs proprietary component split)
- Added Stock Firmware Capabilities Summary (what IS and IS NOT possible on stock firmware)
- Added reference to local network proxy approach for G-code file capture
- Added Testing Methodology to References section

### 2026-02-02 - Initial Release
- Complete protocol documentation based on elegoo-link v1.0.0
- All method codes from COMMAND_MAPPING_TABLE
- All status and sub-status codes from elegoo_fdm_cc2_message_adapter.cpp
- All error codes
- Delta status update mechanism
- Command examples with parameters
- Canvas/AMS documentation
- HTTP API documentation
- Print lifecycle documentation
- Field name variation handling
- Implementation checklist
- Troubleshooting guide

---

*Last updated: 2026-03-16*
