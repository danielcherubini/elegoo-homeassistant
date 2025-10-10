# Testing Instructions for MQTT Support (Beta)

This guide will help you test the MQTT support feature currently in development.

## Prerequisites

- Python 3.13 or higher
- Git
- `uv` package manager (will be installed automatically if using `make setup`)

## Setup Instructions

### 1. Clone the Repository

```bash
git clone https://github.com/danielcherubini/elegoo-homeassistant.git
cd elegoo-homeassistant
```

### 2. Checkout the MQTT Support Branch

```bash
git checkout feature/mqtt-support-clean
```

### 3. Install Dependencies

```bash
make setup
```

This command will:
- Install `uv` if not already installed
- Create a Python virtual environment
- Install all required dependencies

## Running the Debug Script

### For WebSocket/SDCP Printers (Default)

If you have a newer printer that uses WebSocket/SDCP protocol (e.g., Saturn 4 Ultra, Mars 5 Ultra, Centauri Carbon):

```bash
PRINTER_IP=<your-printer-ip> make debug
```

**Example:**
```bash
PRINTER_IP=10.0.0.212 make debug
```

### For MQTT Printers

If you have an older printer that uses MQTT protocol (e.g., Saturn 3 Ultra, Mars 4 Ultra):

1. **Ensure you have an MQTT broker running** (e.g., Mosquitto)

2. **Run the debug script with MQTT settings:**

```bash
PRINTER_IP=<your-printer-ip> MQTT_HOST=<mqtt-broker-ip> MQTT_PORT=1883 make debug
```

**Example:**
```bash
PRINTER_IP=10.0.0.212 MQTT_HOST=localhost MQTT_PORT=1883 make debug
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PRINTER_IP` | IP address of your printer | `10.0.0.212` |
| `MQTT_HOST` | MQTT broker hostname/IP | `localhost` |
| `MQTT_PORT` | MQTT broker port | `1883` |

## What to Expect

The debug script will:

1. **Discover your printer** and display detailed information:
   - Name, Model, Brand
   - IP Address, Printer ID
   - Protocol Version and Type (MQTT or SDCP)
   - Firmware Version
   - Printer Type (FDM or Resin)
   - JSON representation (perfect for copying to GitHub issues!)

2. **Connect to your printer** using the appropriate protocol

3. **Monitor your printer** in real-time:
   - Print progress percentage
   - Time remaining
   - Video feed URL (WebSocket printers only)
   - Status updates every 4 seconds

4. **Press Ctrl+C** to stop monitoring

## Sample Output

```
================================================================================
  1. Discovered Printer Information:
================================================================================
Name:             Saturn 3 Ultra
Model:            Saturn 3 Ultra
Brand:            ELEGOO
IP Address:       10.0.0.212
Printer ID:       ABC123DEF456
Connection ID:    xyz789
Protocol Version: V1.0.0
Protocol Type:    mqtt
Firmware:         V1.4.2
Printer Type:     resin
Is Proxy:         False
--------------------------------------------------------------------------------
JSON Representation (for GitHub issues):
--------------------------------------------------------------------------------
{
  "connection": "xyz789",
  "name": "Saturn 3 Ultra",
  "model": "Saturn 3 Ultra",
  "brand": "ELEGOO",
  "ip_address": "10.0.0.212",
  "protocol": "V1.0.0",
  "protocol_type": "mqtt",
  "firmware": "V1.4.2",
  "id": "ABC123DEF456",
  "printer_type": "resin",
  "proxy_enabled": false,
  "camera_enabled": false,
  "proxy_websocket_port": null,
  "proxy_video_port": null,
  "is_proxy": false
}
================================================================================
```

## Reporting Issues

If you encounter any issues:

1. **Copy the JSON representation** from the debug output
2. **Open a GitHub issue** at: https://github.com/danielcherubini/elegoo-homeassistant/issues
3. **Include:**
   - The JSON printer information
   - Any error messages
   - Your MQTT broker setup (if using MQTT)
   - Steps to reproduce the issue

## Supported Printers

### MQTT Protocol (V1.x.x)
- Saturn 3 Ultra
- Mars 4 Ultra
- Mars 5 Ultra (with older firmware)

### WebSocket/SDCP Protocol (V3.x.x)
- Saturn 4, Saturn 4 Ultra
- Mars 5, Mars 5 Ultra
- Centauri, Centauri Carbon
- Neptune 4 series

## Common Issues

### MQTT Connection Failed

**Problem:** Cannot connect to MQTT broker

**Solution:**
- Ensure your MQTT broker is running: `sudo systemctl status mosquitto`
- Check firewall settings allow port 1883
- Verify MQTT_HOST points to the correct broker IP

### Printer Not Discovered

**Problem:** No printers found during discovery

**Solution:**
- Ensure printer is on the same network
- Check printer has network connectivity enabled
- Try specifying the exact IP with `PRINTER_IP=x.x.x.x`
- Check your firewall allows UDP port 3000

### Permission Denied

**Problem:** Permission errors when running make commands

**Solution:**
```bash
chmod +x scripts/*
```

## Need Help?

Join the discussion: https://github.com/danielcherubini/elegoo-homeassistant/discussions
