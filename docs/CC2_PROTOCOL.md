# Centauri Carbon 2 (CC2) Protocol Documentation

This document describes the protocol differences between the Centauri Carbon (CC1) and Centauri Carbon 2 (CC2) printers, based on analysis of Elegoo's open-source [elegoo-link](https://github.com/ELEGOO-3D/elegoo-link) library.

## Overview

The CC2 uses a fundamentally different communication architecture compared to CC1 and other Elegoo printers. While CC1 uses WebSocket communication similar to Saturn 4 series printers, CC2 uses MQTT with an **inverted broker architecture**.

## Discovery Protocol

### CC1 (Current Implementation)

| Aspect | Value |
|--------|-------|
| Port | 3000 |
| Message | `M99999` |
| Response Format | `Data.Attributes.*` |
| Response Fields | `Name`, `MachineName`, `MainboardIP`, `MainboardID`, `ProtocolVersion`, `FirmwareVersion` |

### CC2 (New)

| Aspect | Value |
|--------|-------|
| Port | **52700** |
| Message | `{"id": 0, "method": 7000}` |
| Response Format | `result.*` |
| Response Fields | `host_name`, `machine_model`, `sn`, `token_status`, `lan_status` |

### CC2 Discovery Response Example

```json
{
  "id": 0,
  "result": {
    "host_name": "My Printer",
    "machine_model": "Centauri Carbon 2",
    "sn": "ABCD1234567890",
    "token_status": 1,
    "lan_status": 1
  }
}
```

**Fields:**
- `host_name`: User-configured printer name
- `machine_model`: Printer model string
- `sn`: Serial number (also used as MainboardID)
- `token_status`: 0 = no auth required, 1 = accessCode required
- `lan_status`: 0 = cloud mode (WAN), 1 = LAN only mode

## MQTT Architecture

### CC1/Legacy Resin Printers

```
┌─────────────────┐         ┌─────────────────┐
│  Home Assistant │◄────────│     Printer     │
│  MQTT Broker    │         │  (connects to   │
│  (port 18830)   │         │   HA broker)    │
└─────────────────┘         └─────────────────┘
```

- Home Assistant runs an MQTT broker on port 18830
- Printer is instructed via M66666 command to connect to HA's broker
- HA subscribes to printer topics

### CC2 (Inverted Architecture)

```
┌─────────────────┐         ┌─────────────────┐
│  Home Assistant │────────►│     Printer     │
│  (MQTT Client)  │         │  MQTT Broker    │
│                 │         │  (port 1883)    │
└─────────────────┘         └─────────────────┘
```

- **Printer runs its own MQTT broker** on port 1883
- Home Assistant connects TO the printer as an MQTT client
- Client must register before sending commands

## CC2 MQTT Protocol Details

### Authentication

| Mode | Username | Password | When Used |
|------|----------|----------|-----------|
| Default | `elegoo` | `123456` | No auth required (token_status=0) |
| accessCode | `elegoo` | `<user_code>` | Auth required (token_status=1) |
| pinCode | `elegoo` | `<pin_code>` | Cloud mode (lan_status=0) |

### Topic Structure

```
elegoo/<serial_number>/api_register              # Registration request
elegoo/<sn>/<request_id>/register_response       # Registration acknowledgment
elegoo/<sn>/<client_id>/api_request              # Command channel (publish)
elegoo/<sn>/<client_id>/api_response             # Response channel (subscribe)
elegoo/<sn>/api_status                           # Status updates (subscribe)
```

### Registration Protocol

Before sending any commands, the client must register with the printer.

**1. Subscribe to topics:**
```
elegoo/<sn>/<client_id>/api_response
elegoo/<sn>/api_status
elegoo/<sn>/<request_id>/register_response
```

**2. Send registration request:**
```json
// Topic: elegoo/<sn>/api_register
{
  "client_id": "1_PC_1234",
  "request_id": "1_PC_1234_req"
}
```

**3. Wait for response:**
```json
// Topic: elegoo/<sn>/<request_id>/register_response
{
  "client_id": "1_PC_1234",
  "error": "ok"
}
```

**Possible errors:**
- `"ok"` - Registration successful
- `"fail"` - Registration failed
- `"too many clients"` - Connection limit exceeded (max ~4 clients)

**Timeout:** 3 seconds

### Heartbeat/Keep-alive

| Parameter | Value |
|-----------|-------|
| Interval | 10 seconds |
| Timeout | 65 seconds |
| Request | `{"type":"PING"}` |
| Response | `{"type":"PONG"}` |
| Topic | Same as command topic |

### Command Message Format

```json
{
  "id": 12345,
  "method": 1002,
  "params": {}
}
```

- `id`: Request ID (integer, used to match responses)
- `method`: Command code (see table below)
- `params`: Command-specific parameters

### Response Message Format

```json
{
  "id": 12345,
  "method": 1002,
  "result": {
    "error_code": 0,
    ...
  }
}
```

## Command Reference

### Command IDs

| Command | CC1 ID | CC2 ID | Description |
|---------|--------|--------|-------------|
| GET_PRINTER_ATTRIBUTES | 1 | **1001** | Get printer info (version negotiation) |
| GET_PRINTER_STATUS | 0 | **1002** | Get current status |
| START_PRINT | 128 | **1020** | Start a print job |
| PAUSE_PRINT | 129 | **1021** | Pause current print |
| STOP_PRINT | 130 | **1022** | Stop current print |
| UPDATE_PRINTER_NAME | 192 | **1043** | Set printer hostname |
| GET_CANVAS_STATUS | N/A | **2005** | Get AMS/Canvas module status |
| SET_AUTO_REFILL | N/A | **2004** | Enable/disable auto refill |
| SET_PRINTER_DOWNLOAD_FILE | N/A | **1057** | Queue remote file download |
| CANCEL_PRINTER_DOWNLOAD_FILE | N/A | **1058** | Cancel file download |

### Event IDs (Push notifications)

| Event | ID | Description |
|-------|-----|-------------|
| ON_PRINTER_STATUS | **6000** | Printer status update (delta) |
| ON_PRINTER_ATTRIBUTES | **6008** | Printer attributes changed |

### START_PRINT Parameters

```json
{
  "id": 1,
  "method": 1020,
  "params": {
    "storage_media": "local",
    "filename": "model.gcode",
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

**storage_media values:** `"local"`, `"u-disk"`, `"sd-card"`

### SET_TEMPERATURE Parameters

```json
{
  "id": 1,
  "method": 1028,
  "params": {
    "heater_bed": 60,
    "extruder": 200
  }
}
```

### SET_FAN_SPEED Parameters

```json
{
  "id": 1,
  "method": 1030,
  "params": {
    "fan": 100,
    "box_fan": 50,
    "aux_fan": 0
  }
}
```

## Delta Status Updates

CC2 sends incremental status updates via method 6000. The client must:

1. Request full status with method 1002 on connection
2. Cache the full status JSON
3. Merge delta updates (method 6000) into the cached status
4. Track sequence IDs for continuity checking
5. Re-request full status if sequence gaps detected (after 5 non-continuous events)

## HTTP REST API

CC2 also exposes an HTTP API for certain operations.

### Get System Info

Used to retrieve serial number if not provided during discovery.

```
GET /system/info?X-Token=<accessCode>
Header: X-Token: <accessCode>
```

**Response:**
```json
{
  "error_code": 0,
  "system_info": {
    "sn": "ABCD1234567890"
  }
}
```

### File Upload (Chunked)

```
PUT /upload
Headers:
  Content-Type: application/octet-stream
  Content-Range: bytes 0-1048575/5242880
  X-File-Name: model.gcode
  X-File-MD5: abc123...
  X-Token: <accessCode>
Body: <binary chunk data>
```

- Maximum chunk size: 1MB (1048576 bytes)
- Uses persistent HTTP connections for efficiency
- MD5 hash calculated before upload for integrity

**Response:**
```json
{
  "error_code": 0
}
```

### File Download

```
GET /download?file_name=<path>&X-Token=<accessCode>
GET /download/sdcard?file_name=<path>&X-Token=<accessCode>
GET /download/udisk?file_name=<path>&X-Token=<accessCode>
```

## Error Codes

| Code | Description |
|------|-------------|
| 0 | Success |
| 1000 | Token validation failed |
| 1001 | Unknown interface |
| 1002 | Failed to open folder |
| 1003 | Invalid parameter |
| 1004 | File write failed |
| 1005 | Failed to update token |
| 1009 | Printer is busy |
| 1010 | Printer is not in printing state |
| 1012 | Task not found |
| 9000 | File offset mismatch |
| 9004 | MD5 checksum failed |

## Printer States

### Machine Status (status field)

| Code | State |
|------|-------|
| 0 | Initializing |
| 1 | Idle |
| 2 | Printing |
| 3-4 | Filament Operating |
| 5 | Auto Leveling |
| 6 | PID Calibrating |
| 7 | Resonance Testing |
| 8 | Self Checking |
| 9 | Updating |
| 10 | Homing |
| 11 | File Transferring |
| 12 | Video Composing |
| 13 | Extruder Operating |
| 14 | Emergency Stop |
| 15 | Power Loss Recovery |

### Print Sub-Status (sub_status field)

| Code | State |
|------|-------|
| 1045, 1096 | Extruder Preheating |
| 1405, 1906 | Heated Bed Preheating |
| 2075 | Printing |
| 2077 | Printing Completed |
| 2501 | Pausing |
| 2502, 2505 | Paused |
| 2401 | Resuming |
| 2503 | Stopping |
| 2504 | Stopped |
| 2801-2802 | Homing |
| 2901-2902 | Auto Leveling |

## Implementation Checklist

To support CC2 in this integration, the following components need to be implemented:

- [ ] New discovery strategy for port 52700 with JSON message
- [ ] CC2 MQTT client (connect TO printer's broker)
- [ ] Registration protocol
- [ ] Heartbeat mechanism
- [ ] New command ID mapping
- [ ] Delta status merging
- [ ] HTTP API for serial number retrieval
- [ ] HTTP file upload/download
- [ ] Authentication flow (accessCode/pinCode)
- [ ] Canvas/AMS support

## References

- [elegoo-link](https://github.com/ELEGOO-3D/elegoo-link) - Elegoo's open-source printer communication library (AGPL-3.0)
- [GitHub Issue #315](https://github.com/danielcherubini/elegoo-homeassistant/issues/315) - CC2 support discussion
