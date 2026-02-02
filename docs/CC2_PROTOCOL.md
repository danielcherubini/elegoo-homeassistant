# Centauri Carbon 2 (CC2) Protocol Documentation

This document is the authoritative reference for the Elegoo Centauri Carbon 2 (CC2) communication protocol, derived from analysis of the [elegoo-link](https://github.com/ELEGOO-3D/elegoo-link) open-source library.

## Overview

The CC2 uses an **inverted MQTT architecture** compared to traditional printer integrations:

- **The printer runs the MQTT broker** (on port 1883)
- **Clients connect TO the printer** (not vice versa)
- **Clients must register** before sending commands
- **Uses heartbeat/ping-pong mechanism** for connection health
- **Sends delta status updates** to minimize bandwidth

## Network Architecture

```
┌─────────────────┐                    ┌─────────────────┐
│   Home          │    UDP Discovery   │    CC2 Printer  │
│   Assistant     │◄──────────────────►│                 │
│                 │    Port 52700      │                 │
│                 │                    │                 │
│                 │    MQTT Connection │   MQTT Broker   │
│   (Client)      │◄──────────────────►│   Port 1883     │
└─────────────────┘                    └─────────────────┘
```

---

## Discovery Protocol

### Port and Method
- **Port**: 52700 (UDP)
- **Method**: Broadcast JSON message

### Discovery Request
```json
{
  "id": 0,
  "method": 7000
}
```

### Discovery Response
```json
{
  "id": 0,
  "result": {
    "host_name": "Centauri Carbon 2",
    "machine_model": "Centauri Carbon 2",
    "sn": "CC2SERIALNUMBER",
    "token_status": 0,
    "lan_status": 1
  }
}
```

### Response Fields
| Field | Type | Description |
|-------|------|-------------|
| `host_name` | string | User-configured printer name |
| `machine_model` | string | Printer model identifier |
| `sn` | string | Serial number (used for MQTT topics) |
| `token_status` | int | 0=No auth required, 1=Access code required |
| `lan_status` | int | 0=Cloud mode, 1=LAN-only mode |

---

## MQTT Connection

### Connection Parameters
| Parameter | Value |
|-----------|-------|
| Port | 1883 |
| Protocol | MQTT 3.1.1 |
| Username | `elegoo` |
| Default Password | `123456` (or access code if `token_status=1`) |
| Keep-alive | 60 seconds |

### Client ID Format
```
1_PC_<random 4 digits>
```
Example: `1_PC_4521`

### Request ID Format
```
<client_id>_req
```
Example: `1_PC_4521_req`

---

## MQTT Topics

### Topic Structure
All topics follow the pattern: `elegoo/<serial_number>/...`

### Subscribe Topics (Client listens)
| Topic | Purpose |
|-------|---------|
| `elegoo/<sn>/<client_id>/api_response` | Command responses |
| `elegoo/<sn>/api_status` | Status updates (events) |
| `elegoo/<sn>/<request_id>/register_response` | Registration acknowledgment |

### Publish Topics (Client sends)
| Topic | Purpose |
|-------|---------|
| `elegoo/<sn>/api_register` | Registration request |
| `elegoo/<sn>/<client_id>/api_request` | Commands |

---

## Registration Protocol

Registration is **required** before sending any commands.

### Registration Request
Publish to: `elegoo/<sn>/api_register`
```json
{
  "client_id": "1_PC_4521",
  "request_id": "1_PC_4521_req"
}
```

### Registration Response
Received on: `elegoo/<sn>/<request_id>/register_response`

**Success:**
```json
{
  "client_id": "1_PC_4521",
  "error": "ok"
}
```

**Failure (too many clients):**
```json
{
  "client_id": "1_PC_4521",
  "error": "too many clients"
}
```

### Registration Timeout
- Timeout: 3 seconds
- Max clients: 4 (typical)

---

## Heartbeat Protocol

The heartbeat mechanism ensures connection health.

### Configuration
| Parameter | Value |
|-----------|-------|
| Interval | 10 seconds |
| Timeout | 65 seconds |

### Heartbeat Request
Publish to command topic:
```json
{
  "type": "PING"
}
```

### Heartbeat Response
```json
{
  "type": "PONG"
}
```

---

## Command Protocol

### Command Message Format
```json
{
  "id": <request_id>,
  "method": <method_code>,
  "params": { ... }
}
```

### Response Message Format
```json
{
  "id": <request_id>,
  "method": <method_code>,
  "result": {
    "error_code": 0,
    ...
  }
}
```

---

## Method Codes

### Command Methods (Client → Printer)
| Code | Method | Description |
|------|--------|-------------|
| 1001 | GET_PRINTER_ATTRIBUTES | Get printer info |
| 1002 | GET_PRINTER_STATUS | Get full status |
| 1020 | START_PRINT | Start a print job |
| 1021 | PAUSE_PRINT | Pause current print |
| 1022 | STOP_PRINT | Stop/cancel print |
| 1023 | RESUME_PRINT | Resume paused print |
| 1026 | HOME_AXES | Home specified axes |
| 1027 | MOVE_AXES | Move axes by distance |
| 1028 | SET_TEMPERATURE | Set nozzle/bed temp |
| 1029 | SET_LIGHT | Set light brightness |
| 1030 | SET_FAN_SPEED | Set fan speeds |
| 1031 | SET_PRINT_SPEED | Set speed mode |
| 1036 | PRINT_TASK_LIST | Get print history |
| 1037 | PRINT_TASK_DETAIL | Get task details |
| 1038 | DELETE_PRINT_TASK | Delete from history |
| 1042 | VIDEO_STREAM | Enable/disable camera |
| 1043 | UPDATE_PRINTER_NAME | Change printer name |
| 1044 | GET_FILE_LIST | List files on storage |
| 1046 | GET_FILE_DETAIL | Get file info |
| 1047 | DELETE_FILE | Delete a file |
| 1048 | GET_DISK_INFO | Get storage capacity |
| 1057 | DOWNLOAD_FILE | Download file to printer |
| 1058 | CANCEL_DOWNLOAD | Cancel file download |
| 2004 | SET_AUTO_REFILL | Enable/disable auto refill |
| 2005 | GET_CANVAS_STATUS | Get AMS/Canvas status |

### Event Methods (Printer → Client)
| Code | Method | Description |
|------|--------|-------------|
| 6000 | ON_PRINTER_STATUS | Delta status update |
| 6008 | ON_PRINTER_ATTRIBUTES | Attributes changed |

---

## Machine Status Codes

| Code | Status | Description |
|------|--------|-------------|
| 0 | INITIALIZING | Printer starting up |
| 1 | IDLE | Ready, not printing |
| 2 | PRINTING | Print in progress |
| 3 | FILAMENT_OPERATING | Loading/unloading filament |
| 4 | FILAMENT_OPERATING_2 | Filament operation variant |
| 5 | AUTO_LEVELING | Bed leveling in progress |
| 6 | PID_CALIBRATING | PID tuning in progress |
| 7 | RESONANCE_TESTING | Input shaper calibration |
| 8 | SELF_CHECKING | Device self-test |
| 9 | UPDATING | Firmware update |
| 10 | HOMING | Axes homing |
| 11 | FILE_TRANSFERRING | File upload in progress |
| 12 | VIDEO_COMPOSING | Creating timelapse |
| 13 | EXTRUDER_OPERATING | Extruder maintenance |
| 14 | EMERGENCY_STOP | E-stop triggered |
| 15 | POWER_LOSS_RECOVERY | Recovering from power loss |

---

## Sub-Status Codes

Sub-status provides detailed state within the main status.

### Printing Sub-Status (status=2)
| Code | Sub-Status | Description |
|------|------------|-------------|
| 0 | NONE | No sub-status |
| 1041 | NONE | Variant |
| 1045 | EXTRUDER_PREHEATING | Nozzle heating |
| 1096 | EXTRUDER_PREHEATING_2 | Nozzle heating variant |
| 1405 | BED_PREHEATING | Bed heating |
| 1906 | BED_PREHEATING_2 | Bed heating variant |
| 2075 | PRINTING | Actively printing |
| 2077 | PRINTING_COMPLETED | Print finished |
| 2401 | RESUMING | Resuming from pause |
| 2402 | RESUMING_COMPLETED | Resume complete |
| 2501 | PAUSING | Pause in progress |
| 2502 | PAUSED | Print paused |
| 2505 | PAUSED_2 | Paused variant |
| 2503 | STOPPING | Stop in progress |
| 2504 | STOPPED | Print stopped |
| 2801 | HOMING | Homing during print |
| 2802 | HOMING_COMPLETED | Homing complete |
| 2901 | AUTO_LEVELING | Leveling during print |
| 2902 | AUTO_LEVELING_COMPLETED | Leveling complete |

### Filament Operating Sub-Status (status=3,4)
| Code | Sub-Status | Description |
|------|------------|-------------|
| 1133 | FILAMENT_LOADING | Loading filament |
| 1134 | FILAMENT_LOADING_2 | Loading variant |
| 1135 | FILAMENT_LOADING_3 | Loading variant |
| 1136 | FILAMENT_LOADING_COMPLETED | Load complete |
| 1143 | NONE | Pre-unload state |
| 1144 | FILAMENT_UNLOADING | Unloading filament |
| 1145 | FILAMENT_UNLOADING_COMPLETED | Unload complete |

### Auto Leveling Sub-Status (status=5)
| Code | Sub-Status | Description |
|------|------------|-------------|
| 2901 | AL_AUTO_LEVELING | Leveling in progress |
| 2902 | AL_AUTO_LEVELING_COMPLETED | Leveling complete |

### PID Calibrating Sub-Status (status=6)
| Code | Sub-Status | Description |
|------|------------|-------------|
| 1503 | PID_CALIBRATING | PID tuning |
| 1504 | PID_CALIBRATING_2 | PID tuning variant |
| 1505 | PID_CALIBRATING_COMPLETED | PID complete |
| 1506 | PID_CALIBRATING_FAILED | PID failed |

### Resonance Testing Sub-Status (status=7)
| Code | Sub-Status | Description |
|------|------------|-------------|
| 5934 | RESONANCE_TEST | Test in progress |
| 5935 | RESONANCE_TEST_COMPLETED | Test complete |
| 5936 | RESONANCE_TEST_FAILED | Test failed |

### Updating Sub-Status (status=9)
| Code | Sub-Status | Description |
|------|------------|-------------|
| 2061 | UPDATING | Update starting |
| 2071 | UPDATING_2 | Updating |
| 2072 | UPDATING_3 | Updating |
| 2073 | UPDATING_4 | Updating |
| 2074 | UPDATING_COMPLETED | Update complete |
| 2075 | UPDATING_FAILED | Update failed |

### Homing Sub-Status (status=10)
| Code | Sub-Status | Description |
|------|------------|-------------|
| 2801 | H_HOMING | Homing in progress |
| 2802 | H_HOMING_COMPLETED | Homing complete |
| 2803 | H_HOMING_FAILED | Homing failed |

### File Transferring Sub-Status (status=11)
| Code | Sub-Status | Description |
|------|------------|-------------|
| 3000 | UPLOADING_FILE | File upload in progress |
| 3001 | UPLOADING_FILE_COMPLETED | Upload complete |

### Extruder Operating Sub-Status (status=13)
| Code | Sub-Status | Description |
|------|------------|-------------|
| 1061 | EXTRUDER_LOADING | Extruder loading |
| 1062 | EXTRUDER_UNLOADING | Extruder unloading |
| 1063 | EXTRUDER_LOADING_COMPLETED | Load complete |
| 1064 | EXTRUDER_UNLOADING_COMPLETED | Unload complete |

---

## Speed Modes

| Code | Mode | Percentage |
|------|------|------------|
| 0 | Silent | 50% |
| 1 | Balanced | 100% |
| 2 | Sport | 150% |
| 3 | Ludicrous | 200% |

---

## Error Codes

| Code | Error | Description |
|------|-------|-------------|
| 0 | SUCCESS | Operation successful |
| 109 | FILAMENT_RUNOUT | Filament detected empty |
| 1000 | TOKEN_FAILED | Authentication failed |
| 1001 | UNKNOWN_INTERFACE | Unknown command |
| 1002 | FOLDER_OPEN_FAILED | Cannot open folder |
| 1003 | INVALID_PARAMETER | Bad parameter value |
| 1004 | FILE_WRITE_FAILED | Cannot write file |
| 1005 | TOKEN_UPDATE_FAILED | Token refresh failed |
| 1006 | MOS_UPDATE_FAILED | MOS update failed |
| 1007 | FILE_DELETE_FAILED | Cannot delete file |
| 1008 | RESPONSE_EMPTY | No data in response |
| 1009 | PRINTER_BUSY | Printer is busy |
| 1010 | NOT_PRINTING | No active print |
| 1011 | FILE_COPY_FAILED | Copy operation failed |
| 1012 | TASK_NOT_FOUND | Print task not found |
| 1013 | DATABASE_FAILED | DB operation failed |
| 1021 | PRINT_FILE_NOT_FOUND | Print file missing |
| 1026 | MISSING_BED_LEVELING | No leveling data |
| 9000 | FILE_OFFSET_MISMATCH | Resume offset wrong |
| 9001 | FILE_OPEN_FAILED | Cannot open file |
| 9002 | FILE_WRITE_ERROR | Write error |
| 9003 | FILE_SEEK_FAILED | Seek error |
| 9004 | MD5_FAILED | Checksum mismatch |
| 9005 | CANCEL_NOT_NEEDED | Nothing to cancel |
| 9006 | CANCEL_FAILED | Cancel failed |
| 9007 | PATH_NOT_EXISTS | Path not found |
| 9008 | MD5_SYSTEM_ERROR | System MD5 error |
| 9009 | MD5_READ_ERROR | File read error |
| 9999 | UNKNOWN_ERROR | Other error |

---

## Status Data Structure

### Full Status Response (method 1002 / event 6000)

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
      "filename": "model.gcode",
      "uuid": "b52af24c-764e-4092-8a50-00e5f8f02b46",
      "current_layer": 225,
      "total_layer": 500,
      "print_duration": 3600,
      "total_duration": 8000,
      "remaining_time_sec": 4400
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
      "fan": {"speed": 255, "rpm": 5000},
      "aux_fan": {"speed": 178, "rpm": 3500},
      "box_fan": {"speed": 25, "rpm": 800},
      "heater_fan": {"speed": 255, "rpm": 4500},
      "controller_fan": {"speed": 255, "rpm": 4000}
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

### Key Status Fields

| Field | Type | Description |
|-------|------|-------------|
| `machine_status.status` | int | Machine status code |
| `machine_status.sub_status` | int | Sub-status code |
| `machine_status.progress` | int | Print progress (0-100) |
| `machine_status.exception_status` | array | Active error codes |
| `print_status.filename` | string | Current print filename |
| `print_status.uuid` | string | Unique task identifier |
| `print_status.current_layer` | int | Current layer number |
| `print_status.total_layer` | int | Total layers |
| `print_status.print_duration` | int | Elapsed time (seconds) |
| `print_status.remaining_time_sec` | int | Remaining time (seconds) |
| `extruder.temperature` | float | Current nozzle temp (°C) |
| `extruder.target` | float | Target nozzle temp (°C) |
| `heater_bed.temperature` | float | Current bed temp (°C) |
| `heater_bed.target` | float | Target bed temp (°C) |
| `fans.*.speed` | int | Fan speed (0-255) |
| `led.status` | int | Light state (0=off, 1=on) |
| `gcode_move_inf.x/y/z` | float | Current position (mm) |
| `gcode_move_inf.speed_mode` | int | Speed mode (0-3) |

### Field Name Variations

The CC2 firmware may use different field names depending on firmware version:

| Official (elegoo-link) | Alternative | Description |
|------------------------|-------------|-------------|
| `gcode_move_inf` | `gcode_move` | Position/speed data |
| `gcode_move_inf.e` | `gcode_move.extruder` | Extruder position |
| `toolhead` | `tool_head` | Toolhead info |

The integration supports both variants.

---

## Delta Status Updates

The CC2 sends incremental (delta) status updates to minimize bandwidth.

### Delta Update Mechanism
1. Full status is sent on connection/request (method 1002)
2. Subsequent updates (method 6000) contain only changed fields
3. Client must merge delta with cached full status
4. Updates include incrementing `id` for continuity checking

### Continuity Checking
- Track the `id` field in status events
- IDs should increment by 1 each update
- If 5+ non-continuous events occur, request full status refresh
- Reset counter after receiving full status

### Example Delta Update
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

---

## Attributes Data Structure

### Attributes Response (method 1001)

```json
{
  "id": 1,
  "method": 1001,
  "result": {
    "error_code": 0,
    "hostname": "Centauri Carbon 2",
    "machine_model": "Centauri Carbon 2",
    "sn": "CC2SERIALNUMBER",
    "ip": "192.168.1.100",
    "protocol_version": "1.0.0",
    "hardware_version": "",
    "software_version": {
      "ota_version": "1.0.5.2",
      "mcu_version": "00.00.00.00",
      "soc_version": ""
    }
  }
}
```

---

## Command Examples

### Start Print
```json
{
  "id": 100,
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

**Storage Media Values:**
- `"local"` - Internal storage
- `"u-disk"` - USB drive
- `"sd-card"` - SD card

### Set Temperature
```json
{
  "id": 101,
  "method": 1028,
  "params": {
    "extruder": 220,
    "heater_bed": 60
  }
}
```

### Set Fan Speed
Fan speeds are 0-255 (not percentage).
```json
{
  "id": 102,
  "method": 1030,
  "params": {
    "fan": 255,
    "box_fan": 128,
    "aux_fan": 178
  }
}
```

### Home Axes
```json
{
  "id": 103,
  "method": 1026,
  "params": {
    "homed_axes": "xyz"
  }
}
```

### Move Axes
```json
{
  "id": 104,
  "method": 1027,
  "params": {
    "axes": "z",
    "distance": 10.0
  }
}
```

### Set Light
```json
{
  "id": 105,
  "method": 1029,
  "params": {
    "brightness": 255
  }
}
```

### Set Print Speed Mode
```json
{
  "id": 106,
  "method": 1031,
  "params": {
    "mode": 1
  }
}
```

### Get Canvas/AMS Status
```json
{
  "id": 107,
  "method": 2005,
  "params": {}
}
```

**Response:**
```json
{
  "id": 107,
  "method": 2005,
  "result": {
    "error_code": 0,
    "canvas_info": {
      "active_canvas_id": 0,
      "active_tray_id": 0,
      "auto_refill": false,
      "canvas_list": [
        {
          "canvas_id": 1,
          "connected": 1,
          "tray_list": [
            {
              "tray_id": 1,
              "brand": "ELEGOO",
              "filament_type": "PLA",
              "filament_name": "Generic PLA",
              "filament_color": "FFFFFF",
              "status": 1
            }
          ]
        }
      ]
    }
  }
}
```

---

## HTTP API (Supplementary)

The CC2 also provides an HTTP API for certain operations.

### Get System Info
```
GET /system/info?X-Token=<access_code>
Header: X-Token: <access_code>
```

Response:
```json
{
  "error_code": 0,
  "system_info": {
    "sn": "CC2SERIALNUMBER"
  }
}
```

### Video Stream
```
GET /?action=stream
Port: 8080
```

Returns MJPEG video stream.

### File Upload (Chunked)
```
PUT /upload
Headers:
  Content-Type: application/octet-stream
  Content-Range: bytes 0-1048575/5242880
  X-File-Name: model.gcode
  X-File-MD5: abc123...
  X-Token: <access_code>
Body: <binary chunk data>
```

- Maximum chunk size: 1MB (1048576 bytes)
- Uses persistent HTTP connections for efficiency
- MD5 hash calculated before upload for integrity

### File Download
```
GET /download?file_name=<path>&X-Token=<access_code>
GET /download/sdcard?file_name=<path>&X-Token=<access_code>
GET /download/udisk?file_name=<path>&X-Token=<access_code>
```

---

## Comparison: CC1 vs CC2

| Feature | CC1 (Centauri Carbon) | CC2 (Centauri Carbon 2) |
|---------|----------------------|-------------------------|
| Discovery Port | 3000 | 52700 |
| Discovery Message | `M99999` | `{"id":0,"method":7000}` |
| Communication | WebSocket | MQTT |
| Broker Location | Home Assistant | **Printer** |
| Client Role | Server | **Client** |
| Registration | Not required | **Required** |
| Heartbeat | Not required | **Required** (10s) |
| Status Updates | Full | **Delta** |
| Max Clients | Unlimited | ~4 |

---

## Implementation Status

### Completed
- [x] Discovery protocol (port 52700, JSON)
- [x] MQTT client connection
- [x] Registration protocol
- [x] Heartbeat mechanism
- [x] Status mapping
- [x] Command ID mapping
- [x] Delta status support

### In Progress
- [ ] HTTP file upload/download
- [ ] Canvas/AMS full support
- [ ] All command implementations

---

## References

- [elegoo-link](https://github.com/ELEGOO-3D/elegoo-link) - Official Elegoo network library (source of this documentation)
- [ElegooSlicer](https://github.com/ELEGOO-3D/ElegooSlicer) - Official Elegoo slicer
- [elegoo-fdm-web](https://github.com/ELEGOO-3D/elegoo-fdm-web) - Web interface releases
- [GitHub Issue #315](https://github.com/danielcherubini/elegoo-homeassistant/issues/315) - CC2 support discussion

---

## Changelog

- **2026-02-02**: Complete rewrite based on elegoo-link v1.0.0 source code analysis
  - Added all method codes from COMMAND_MAPPING_TABLE
  - Added all status and sub-status codes from elegoo_fdm_cc2_message_adapter.cpp
  - Added all error codes
  - Documented delta status update mechanism
  - Added command examples
  - Added Canvas/AMS documentation
  - Added field name variations
