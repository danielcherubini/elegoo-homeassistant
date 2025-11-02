#!/usr/bin/env python3
"""
Centauri Carbon 2 Data Extraction Script

This script connects to a Centauri Carbon 2 printer and extracts all available
data by running all known SDCP commands. The output is saved to a timestamped
log file for analysis.

Usage:
    python scripts/extract_cc2_data.py [PRINTER_IP]

If PRINTER_IP is not provided, the script will discover printers on the network.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

# Add parent directory to path so we can import the custom component
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.elegoo_printer.sdcp.const import (
    CMD_AMS_GET_MAPPING_INFO,
    CMD_AMS_GET_SLOT_LIST,
    CMD_BATCH_DELETE_FILES,
    CMD_CONTINUE_PRINT,
    CMD_CONTROL_DEVICE,
    CMD_DELETE_HISTORY,
    CMD_EXPORT_TIME_LAPSE,
    CMD_GET_FILE_INFO,
    CMD_PAUSE_PRINT,
    CMD_RENAME_FILE,
    CMD_REQUEST_ATTRIBUTES,
    CMD_REQUEST_STATUS_REFRESH,
    CMD_RETRIEVE_FILE_LIST,
    CMD_RETRIEVE_HISTORICAL_TASKS,
    CMD_RETRIEVE_TASK_DETAILS,
    CMD_SET_TIME_LAPSE_PHOTOGRAPHY,
    CMD_SET_VIDEO_STREAM,
    CMD_STOP_PRINT,
    CMD_XYZ_HOME_CONTROL,
    CMD_XYZ_MOVE_CONTROL,
)
from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient


class DataExtractor:
    """Extracts all data from a Centauri Carbon 2 printer."""

    def __init__(self, output_file: Path):
        """Initialize the data extractor."""
        self.output_file = output_file
        self.data = {
            "extraction_time": datetime.now().isoformat(),
            "printer_info": {},
            "commands": {},
            "errors": [],
        }

    def log_and_save(self, message: str, level: str = "info") -> None:
        """Log a message and save it to the output file."""
        if level == "info":
            logger.info(message)
        elif level == "warning":
            logger.warning(message)
        elif level == "error":
            logger.error(message)

    async def save_command_response(
        self, cmd_name: str, cmd_id: int, response: Any, error: str | None = None
    ) -> None:
        """Save a command response to the data structure."""
        self.data["commands"][cmd_name] = {
            "command_id": cmd_id,
            "response": response,
            "error": error,
            "timestamp": datetime.now().isoformat(),
        }
        # Write to file immediately to preserve data even if script crashes
        await self.write_to_file()

    async def write_to_file(self) -> None:
        """Write the current data to the output file."""
        with open(self.output_file, "w") as f:
            json.dump(self.data, f, indent=2, default=str)

    async def extract_all_data(self, client: ElegooPrinterClient) -> None:
        """Extract all data from the printer."""
        logger.info("=" * 80)
        logger.info("Starting Centauri Carbon 2 Data Extraction")
        logger.info("=" * 80)

        # Save printer info
        self.data["printer_info"] = {
            "name": client.printer.name,
            "model": client.printer.model,
            "brand": client.printer.brand,
            "ip_address": client.printer.ip_address,
            "id": client.printer.id,
            "connection": client.printer.connection,
            "protocol": client.printer.protocol,
            "firmware": client.printer.firmware,
            "printer_type": client.printer.printer_type.value
            if client.printer.printer_type
            else None,
        }
        await self.write_to_file()

        # Test all READ commands (safe to run on any printer)
        logger.info("\n📊 Testing READ Commands...")

        await self._test_command(
            client, "Status Refresh", CMD_REQUEST_STATUS_REFRESH, {}
        )
        await self._test_command(client, "Attributes", CMD_REQUEST_ATTRIBUTES, {})
        await self._test_command(
            client, "Historical Tasks", CMD_RETRIEVE_HISTORICAL_TASKS, {}
        )
        await self._test_command(client, "File List", CMD_RETRIEVE_FILE_LIST, {})

        # Test video commands
        logger.info("\n📹 Testing Video Commands...")
        await self._test_command(
            client, "Video Stream Enable", CMD_SET_VIDEO_STREAM, {"Enable": 1}
        )
        await asyncio.sleep(1)
        await self._test_command(
            client, "Video Stream Disable", CMD_SET_VIDEO_STREAM, {"Enable": 0}
        )

        # Test NEW Centauri Carbon 2 commands
        logger.info("\n🆕 Testing NEW Centauri Carbon 2 Commands...")

        # AMS (Automatic Material System) Commands
        logger.info("\n🎨 Testing AMS Commands...")
        await self._test_command(
            client, "AMS Get Slot List", CMD_AMS_GET_SLOT_LIST, {}
        )
        await self._test_command(
            client, "AMS Get Mapping Info", CMD_AMS_GET_MAPPING_INFO, {}
        )

        # File info (if we have files)
        if "File List" in self.data["commands"]:
            file_list_response = self.data["commands"]["File List"].get("response")
            if file_list_response and isinstance(file_list_response, dict):
                # Try to get info on first file if available
                logger.info("\n📄 Testing File Info Command...")
                await self._test_command(
                    client,
                    "Get File Info",
                    CMD_GET_FILE_INFO,
                    {"FileName": "test.gcode"},
                )

        # Time-lapse commands
        logger.info("\n🎬 Testing Time-Lapse Commands...")
        await self._test_command(
            client,
            "Time Lapse Enable",
            CMD_SET_TIME_LAPSE_PHOTOGRAPHY,
            {"Enable": 1},
        )
        await asyncio.sleep(1)
        await self._test_command(
            client,
            "Time Lapse Disable",
            CMD_SET_TIME_LAPSE_PHOTOGRAPHY,
            {"Enable": 0},
        )

        # Export time-lapse (if available)
        if client.printer_data.print_history:
            task_ids = list(client.printer_data.print_history.keys())
            if task_ids:
                logger.info("\n📹 Testing Export Time-Lapse...")
                await self._test_command(
                    client, "Export Time Lapse", CMD_EXPORT_TIME_LAPSE, {"Id": task_ids[0]}
                )

        # NOTE: We skip these commands as they could affect the printer state:
        # - CMD_XYZ_MOVE_CONTROL (could move axes)
        # - CMD_XYZ_HOME_CONTROL (could home axes)
        # - CMD_RENAME_FILE (would modify files)
        # - CMD_DELETE_HISTORY (would delete history)
        # - CMD_BATCH_DELETE_FILES (would delete files)
        # - CMD_PAUSE_PRINT, CMD_STOP_PRINT, CMD_CONTINUE_PRINT (print control)
        # - CMD_CONTROL_DEVICE (device control)

        logger.info("\n⚠️  Skipping potentially destructive commands:")
        logger.info("  - XYZ Move/Home (could move axes)")
        logger.info("  - File operations (rename, delete)")
        logger.info("  - Print control (pause, stop, resume)")
        logger.info("  - Delete history")
        logger.info("\nIf you want to test these, please do so manually.")

        logger.info("\n" + "=" * 80)
        logger.info("✅ Data Extraction Complete!")
        logger.info(f"📁 Data saved to: {self.output_file}")
        logger.info("=" * 80)

    async def _test_command(
        self, client: ElegooPrinterClient, name: str, cmd: int, data: dict
    ) -> None:
        """Test a single command and save the response."""
        logger.info(f"  Testing: {name} (Cmd={cmd})")
        try:
            # Send command
            await client._send_printer_cmd(cmd, data)
            # Wait a bit for response to be processed
            await asyncio.sleep(0.5)

            # Try to get response data from printer_data
            response_data = None
            if cmd == CMD_REQUEST_STATUS_REFRESH:
                response_data = client.printer_data.status.to_dict()
            elif cmd == CMD_REQUEST_ATTRIBUTES:
                response_data = client.printer_data.attributes.to_dict()
            elif cmd == CMD_RETRIEVE_HISTORICAL_TASKS:
                response_data = {
                    k: v.to_dict() if v else None
                    for k, v in client.printer_data.print_history.items()
                }
            elif cmd == CMD_SET_VIDEO_STREAM:
                response_data = client.printer_data.video.to_dict()

            await self.save_command_response(name, cmd, response_data, None)
            logger.success(f"    ✓ {name} succeeded")

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"    ✗ {name} failed: {error_msg}")
            await self.save_command_response(name, cmd, None, error_msg)
            self.data["errors"].append(
                {"command": name, "error": error_msg, "timestamp": datetime.now().isoformat()}
            )


async def main() -> None:
    """Main entry point."""
    # Get printer IP from command line or environment
    printer_ip = sys.argv[1] if len(sys.argv) > 1 else os.getenv("PRINTER_IP")

    # Create output file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).parent.parent / "cc2_extractions"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / f"cc2_extraction_{timestamp}.json"

    logger.info(f"📁 Output will be saved to: {output_file}")

    extractor = DataExtractor(output_file)

    try:
        async with aiohttp.ClientSession() as session:
            # Create client for discovery
            client = ElegooPrinterClient(
                ip_address=printer_ip or "localhost", session=session, logger=logger
            )

            # Discover printers
            logger.info("🔍 Discovering printers on network...")
            if printer_ip:
                logger.info(f"   Targeting printer at {printer_ip}")
                discovered = client.discover_printer(printer_ip)
            else:
                discovered = client.discover_printer()

            if not discovered:
                logger.error("❌ No printers found!")
                return

            # If only one printer or specific IP provided, use it
            if len(discovered) == 1 or printer_ip:
                selected_printer = discovered[0]
                logger.info(f"✅ Found: {selected_printer.name} ({selected_printer.model})")
                logger.info(f"   IP: {selected_printer.ip_address}")
                logger.info(f"   ID: {selected_printer.id}")
            else:
                # Multiple printers found - let user choose
                logger.info(f"🎯 Found {len(discovered)} printer(s):")
                logger.info("=" * 80)

                # Show printer list
                for i, printer in enumerate(discovered, start=1):
                    proxy_suffix = " (Proxy)" if printer.is_proxy else ""
                    logger.info(
                        f"  {i}. {printer.name}{proxy_suffix} - {printer.model} @ {printer.ip_address}"
                    )

                logger.info("=" * 80)

                # Get user selection
                while True:
                    try:
                        choice = input(f"Enter printer number (1-{len(discovered)}): ")
                        printer_index = int(choice) - 1
                        if 0 <= printer_index < len(discovered):
                            selected_printer = discovered[printer_index]
                            break
                        logger.error(
                            f"Please enter a number between 1 and {len(discovered)}"
                        )
                    except ValueError:
                        logger.error("Please enter a valid number")
                    except KeyboardInterrupt:
                        logger.info("\n🛑 Cancelled by user")
                        return

                logger.info(f"📍 Selected: {selected_printer.name}")

            # Connect
            logger.info("🔌 Connecting to printer...")
            connected = await client.connect_printer(selected_printer, proxy_enabled=False)

            if not connected:
                logger.error("❌ Failed to connect!")
                return

            logger.info("✅ Connected!")

            # Wait for initial status
            await asyncio.sleep(2)

            # Extract all data
            await extractor.extract_all_data(client)

            # Disconnect
            await client.disconnect()

    except KeyboardInterrupt:
        logger.info("\n🛑 Interrupted by user")
    except Exception as e:
        logger.exception(f"❌ Error: {e}")
        extractor.data["errors"].append(
            {
                "error": f"Fatal error: {str(e)}",
                "timestamp": datetime.now().isoformat(),
            }
        )
        await extractor.write_to_file()


if __name__ == "__main__":
    asyncio.run(main())
