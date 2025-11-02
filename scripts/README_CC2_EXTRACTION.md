# Centauri Carbon 2 Data Extraction Tool

This script helps gather diagnostic data from Centauri Carbon 2 printers to improve compatibility with this Home Assistant integration.

## Purpose

The `extract_cc2_data.py` script connects to your Centauri Carbon 2 printer and runs various SDCP commands to extract information about:
- Printer capabilities and attributes
- Current status
- Print history
- AMS (Automatic Material System) configuration
- Video stream capabilities
- File management features
- Time-lapse features

All data is saved to a JSON file that can be shared with developers to improve support.

## Requirements

- Python 3.11+
- Network access to your Centauri Carbon 2 printer
- The printer must be powered on and connected to your network

## Installation

1. Clone this repository:
```bash
git clone https://github.com/your-repo/elegoo_homeassistant.git
cd elegoo_homeassistant
```

2. Install dependencies:
```bash
pip install -r requirements.txt
# OR using uv:
uv sync
```

## Usage

### Option 1: Let the script discover your printer

```bash
python scripts/extract_cc2_data.py
```

The script will:
1. Search your network for Elegoo printers
2. Display all discovered printers
3. Let you select which one to test

### Option 2: Specify your printer's IP address

```bash
python scripts/extract_cc2_data.py 192.168.1.100
```

Or set the `PRINTER_IP` environment variable:
```bash
export PRINTER_IP=192.168.1.100
python scripts/extract_cc2_data.py
```

## Output

The script creates a timestamped JSON file in the `cc2_extractions/` directory:

```
cc2_extractions/cc2_extraction_20250102_143052.json
```

This file contains:
- Printer information (model, firmware, IP, etc.)
- Responses from all supported SDCP commands
- Any errors encountered during extraction
- Timestamps for each operation

## Sharing Data

To help improve Centauri Carbon 2 support:

1. Run the extraction script on your printer
2. Locate the JSON file in `cc2_extractions/`
3. Create a GitHub issue at: https://github.com/your-repo/elegoo_homeassistant/issues
4. Attach the JSON file to the issue
5. Include any additional context about your printer (model, firmware version, any issues you've encountered)

## Safety

The script only runs **read-only** commands and will NOT:
- ❌ Move printer axes
- ❌ Delete files
- ❌ Modify printer settings
- ❌ Start, stop, or pause prints
- ❌ Delete print history

All commands are safe to run on a printer that is idle or actively printing.

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

### Using with uv

```bash
uv run scripts/extract_cc2_data.py
```

### Collecting data from multiple printers

Run the script multiple times, once for each printer. Each run creates a separate timestamped file.

## Support

If you encounter issues with the extraction script itself:
1. Check the console output for error messages
2. Look at the generated JSON file for captured errors
3. Create a GitHub issue with the error details
