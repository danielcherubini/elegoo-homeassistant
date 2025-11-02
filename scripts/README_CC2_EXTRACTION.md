# Centauri Carbon 2 Data Extraction Tool

This script helps gather diagnostic data from Centauri Carbon 2 printers to improve compatibility with this Home Assistant integration.

## Purpose

The `extract_cc2_data.py` script connects to your Centauri Carbon 2 printer and captures **RAW WebSocket data** by running various SDCP commands.

**What it captures:**
- Printer capabilities and attributes
- Current status
- Print history
- AMS (Automatic Material System) configuration
- Video stream capabilities
- File management features
- Time-lapse features

**Important:** This script captures the **raw JSON messages** sent and received over WebSocket, without any data parsing or model processing. This ensures we capture ALL data even if the printer has new/different fields that existing code doesn't know about.

All data is saved to a **JSONL (JSON Lines) file** that can be shared with developers to improve support.

## Requirements

- Python 3.11+
- Network access to your Centauri Carbon 2 printer
- The printer must be powered on and connected to your network

## Installation

1. Clone this repository:
```bash
git clone https://github.com/your-repo/elegoo_homeassistant.git
cd elegoo_homeassistant
git checkout feature/cc2
```

2. Set up the development environment:
```bash
make setup
```

This will install `uv` (if needed) and set up all dependencies automatically.

## Usage

### Option 1: Interactive selection (recommended)

```bash
make extract
```

The script will:
1. Search your network for Elegoo printers
2. Display all discovered printers
3. **Ask you to select which printer to test** (if multiple are found)
4. Run all diagnostic commands on the selected printer

### Option 2: Specify your printer's IP address (skip selection)

```bash
make extract PRINTER_IP=192.168.1.100
```

This skips the interactive selection and goes straight to the specified printer.

## Output

The script creates a timestamped **JSONL (JSON Lines) file** in the `cc2_extractions/` directory:

```
cc2_extractions/cc2_raw_extraction_20250102_143052.jsonl
```

### JSONL Format

Each line is a complete JSON object representing one WebSocket message:

```json
{"timestamp": "2025-01-02T14:30:52.123456", "direction": "send", "message": {...}}
{"timestamp": "2025-01-02T14:30:52.456789", "direction": "recv", "message": {...}}
{"timestamp": "2025-01-02T14:30:53.789012", "direction": "recv", "message": {...}}
```

**Fields:**
- `timestamp`: When the message was sent/received
- `direction`: Either `"send"` (we sent to printer) or `"recv"` (printer sent to us)
- `message`: The complete raw JSON WebSocket message

**Benefits of JSONL:**
- ✅ Easy to parse line-by-line
- ✅ Won't break if one message is malformed
- ✅ Can grep/filter for specific commands
- ✅ Preserves exact send/receive order
- ✅ No data is lost or transformed by models

## Sharing Data

To help improve Centauri Carbon 2 support:

1. Run the extraction script on your printer
2. Locate the **JSONL file** in `cc2_extractions/`
3. Create a GitHub issue at: https://github.com/your-repo/elegoo_homeassistant/issues
4. Attach the JSONL file to the issue
5. Include any additional context about your printer:
   - Exact model name
   - Firmware version
   - Any accessories (AMS, enclosure, etc.)
   - Any issues you've encountered

**Privacy Note:** The JSONL file contains printer information like IP address, printer name, and file names. You may want to review it before sharing if you have sensitive file names.

## Safety

The script only runs **read-only** commands and will NOT:
- ❌ Move printer axes (skips XYZ move/home commands)
- ❌ Delete files (skips file delete commands)
- ❌ Modify printer settings (skips rename/modify commands)
- ❌ Start, stop, or pause prints (skips print control)
- ❌ Delete print history (skips history delete)
- ❌ Load/unload filament (skips AMS loading commands)

All commands are safe to run on a printer that is idle or actively printing.

**What it DOES send:**
- Status requests (read current state)
- Attribute requests (read printer info)
- File list requests (read available files)
- History requests (read print history)
- AMS slot/mapping info requests (read multi-material config)
- Video/time-lapse enable/disable (temporary, reverts after capture)

## Troubleshooting

### "No printer found"
- Ensure your printer is powered on
- Check that the printer is connected to your network
- Verify you can ping the printer's IP address
- Try specifying the IP address manually

### "Failed to connect"
- Ensure port 3030 is accessible on your network
- Check if your firewall is blocking the connection
- Verify the printer's web interface is accessible

### Script crashes or hangs
- The script saves data after each command, so partial data is preserved
- Check the JSON file for any errors that were captured
- Try running again with a specific IP address

## Advanced Usage

### Running directly with uv (without Makefile)

```bash
uv run scripts/extract_cc2_data.py
# Or with specific IP:
uv run scripts/extract_cc2_data.py 192.168.1.100
```

### Collecting data from multiple printers

Run the script multiple times, once for each printer. Each run creates a separate timestamped file:

```bash
make extract PRINTER_IP=192.168.1.100
make extract PRINTER_IP=192.168.1.101
make extract PRINTER_IP=192.168.1.102
```

## Support

If you encounter issues with the extraction script itself:
1. Check the console output for error messages
2. Look at the generated JSON file for captured errors
3. Create a GitHub issue with the error details
