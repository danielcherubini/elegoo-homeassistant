#!/usr/bin/env python3
"""
Centauri Carbon 2 Raw Data Extraction Script

This script connects to a Centauri Carbon 2 printer and captures RAW WebSocket
data without using any data models. All messages (sent and received) are saved
to a JSONL file for analysis.

This ensures we capture ALL data even if the printer has different/new fields
that the existing models don't know about.

Usage:
    make extract                          # Interactive selection
    make extract PRINTER_IP=192.168.1.100 # Direct IP
"""

import asyncio
import json
import os
import secrets
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.elegoo_printer.const import (
    DEFAULT_BROADCAST_ADDRESS,
    DISCOVERY_MESSAGE,
    DISCOVERY_PORT,
    DISCOVERY_TIMEOUT,
    WEBSOCKET_PORT,
)
from custom_components.elegoo_printer.sdcp.const import (
    CMD_AMS_GET_MAPPING_INFO,
    CMD_AMS_GET_SLOT_LIST,
    CMD_EXPORT_TIME_LAPSE,
    CMD_GET_FILE_INFO,
    CMD_REQUEST_ATTRIBUTES,
    CMD_REQUEST_STATUS_REFRESH,
    CMD_RETRIEVE_FILE_LIST,
    CMD_RETRIEVE_HISTORICAL_TASKS,
    CMD_RETRIEVE_TASK_DETAILS,
    CMD_SET_TIME_LAPSE_PHOTOGRAPHY,
    CMD_SET_VIDEO_STREAM,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer

# Configure logging
logger.remove()
logger.add(sys.stdout, colorize=True, level="INFO")


class RawDataExtractor:
    """Captures raw WebSocket data without any model parsing."""

    def __init__(self, output_file: Path):
        """Initialize the raw data extractor."""
        self.output_file = output_file
        self.mainboard_id: str | None = None
        self.connection_id: str | None = None
        self.websocket: aiohttp.ClientWebSocketResponse | None = None
        self.session: aiohttp.ClientSession | None = None
        self.message_count = {"sent": 0, "received": 0}
        self._response_events: dict[str, asyncio.Event] = {}
        self._response_lock = asyncio.Lock()
        self._listener_task: asyncio.Task | None = None

        # Create output file with header
        with open(self.output_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "extraction_start": datetime.now().isoformat(),
                        "format": "jsonl",
                        "description": "Raw WebSocket data capture from Elegoo printer",
                    }
                )
                + "\n"
            )

    def discover_printers(
        self, target_ip: str | None = None
    ) -> list[Printer]:
        """
        Discover printers via UDP broadcast.

        Returns list of Printer objects.
        """
        discovered_printers: list[Printer] = []
        logger.info("🔍 Discovering printers on network...")

        if target_ip:
            logger.info(f"   Targeting printer at {target_ip}")
            broadcast_address = target_ip
        else:
            broadcast_address = DEFAULT_BROADCAST_ADDRESS

        msg = DISCOVERY_MESSAGE.encode()
        with socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP
        ) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(DISCOVERY_TIMEOUT)

            try:
                sock.sendto(msg, (broadcast_address, DISCOVERY_PORT))
                while True:
                    try:
                        data, addr = sock.recvfrom(8192)
                        logger.debug(f"Discovery response from {addr}")
                        printer_info = data.decode("utf-8")
                        printer = Printer(printer_info)
                        discovered_printers.append(printer)
                        logger.info(
                            f"   Found: {printer.name} @ {printer.ip_address}"
                        )
                    except TimeoutError:
                        break
            except OSError as e:
                logger.error(f"Socket error during discovery: {e}")
                return []

        return discovered_printers

    async def connect_websocket(self, printer: Printer) -> bool:
        """Connect to printer's WebSocket using raw aiohttp connection."""
        url = f"ws://{printer.ip_address}:{WEBSOCKET_PORT}/websocket"
        logger.info(f"🔌 Connecting to {url}...")

        # Extract IDs from printer
        self.mainboard_id = printer.id
        self.connection_id = printer.connection

        if not self.mainboard_id or not self.connection_id:
            logger.error("❌ Missing MainboardID or Connection ID from discovery!")
            return False

        logger.debug(f"   MainboardID: {self.mainboard_id}")
        logger.debug(f"   Connection ID: {self.connection_id}")

        try:
            self.session = aiohttp.ClientSession()
            self.websocket = await self.session.ws_connect(
                url, timeout=aiohttp.ClientWSTimeout(), heartbeat=30
            )
            logger.info("✅ Connected!")
            return True
        except Exception as e:
            logger.error(f"❌ Connection failed: {e}")
            if self.session:
                await self.session.close()
            return False

    def save_message(self, direction: str, message: dict[str, Any]) -> None:
        """
        Save a raw message to JSONL file.

        Args:
            direction: "send" or "recv"
            message: Raw JSON message
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "direction": direction,
            "message": message,
        }

        with open(self.output_file, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

        self.message_count[direction.replace("send", "sent").replace("recv", "received")] += 1

        # Log to console with color
        topic = message.get("Topic", "NO_TOPIC")
        if direction == "send":
            logger.info(f"📤 SEND: {topic}")
        else:
            logger.info(f"📥 RECV: {topic}")

        logger.debug(json.dumps(message, indent=2, default=str))

        # If this is a response, check if anyone is waiting for it
        if direction == "recv":
            inner_data = message.get("Data", {})
            request_id = inner_data.get("RequestID")
            if request_id:
                asyncio.create_task(self._set_response_event(request_id))

    async def _set_response_event(self, request_id: str) -> None:
        """Signal that a response with this RequestID was received."""
        async with self._response_lock:
            if event := self._response_events.get(request_id):
                event.set()
            else:
                logger.debug(f"Received response for RequestID={request_id} (no waiter)")

    async def _listen_background(self) -> None:
        """Background task to continuously listen for messages."""
        if not self.websocket:
            return

        try:
            async for msg in self.websocket:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    message = json.loads(msg.data)
                    self.save_message("recv", message)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {self.websocket.exception()}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.warning("WebSocket closed by server")
                    break
        except asyncio.CancelledError:
            logger.debug("Background listener cancelled")
        except Exception as e:
            logger.error(f"Background listener error: {e}")

    async def send_raw_command(
        self, cmd: int, cmd_name: str, data: dict[str, Any] | None = None, timeout: float = 10.0
    ) -> bool:
        """
        Send a raw SDCP command and wait for the response.

        Args:
            cmd: Command ID from const.py
            cmd_name: Human-readable command name for logging
            data: Command data payload (optional)
            timeout: How long to wait for response in seconds

        Returns:
            True if response received, False if timeout
        """
        if not self.websocket:
            logger.error("❌ Not connected!")
            return False

        request_id = secrets.token_hex(8)
        payload = {
            "Id": self.connection_id,
            "Data": {
                "Cmd": cmd,
                "Data": data or {},
                "RequestID": request_id,
                "MainboardID": self.mainboard_id,
                "TimeStamp": int(time.time()),
                "From": 0,
            },
            "Topic": f"sdcp/request/{self.mainboard_id}",
        }

        # Create event to wait for response
        event = asyncio.Event()
        async with self._response_lock:
            self._response_events[request_id] = event

        try:
            logger.info(f"  → Sending: {cmd_name} (Cmd={cmd})")
            self.save_message("send", payload)
            await self.websocket.send_str(json.dumps(payload, default=str))

            # Wait for response
            logger.debug(f"  ⏳ Waiting for response (RequestID={request_id})...")
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
                logger.debug(f"  ✓ Response received for {cmd_name}")
                return True
            except asyncio.TimeoutError:
                logger.warning(f"  ⏱ Timeout waiting for response to {cmd_name}")
                return False

        finally:
            # Clean up event
            async with self._response_lock:
                self._response_events.pop(request_id, None)

    async def listen_for_additional_messages(self, duration: int) -> None:
        """
        Continue listening for additional messages (status updates, etc.).

        The background listener is already running, so we just sleep.

        Args:
            duration: How long to listen in seconds
        """
        logger.info(f"👂 Listening for additional messages for {duration} seconds...")
        logger.info("   (Capturing status updates, notices, etc.)")

        try:
            await asyncio.sleep(duration)
        except KeyboardInterrupt:
            logger.info("\n🛑 Interrupted by user")

    async def run_extraction(self, printer: Printer) -> None:
        """
        Run the complete data extraction process.

        Args:
            printer: The printer to extract data from
        """
        logger.info("=" * 80)
        logger.info(f"Starting RAW Data Extraction: {printer.name}")
        logger.info("=" * 80)

        # Connect
        if not await self.connect_websocket(printer):
            return

        # Start background listener
        self._listener_task = asyncio.create_task(self._listen_background())

        # Wait for initial connection to stabilize
        await asyncio.sleep(2)

        logger.info("\n📊 Sending READ-ONLY Commands...")
        logger.info("-" * 80)

        # Core status and attributes
        await self.send_raw_command(
            CMD_REQUEST_STATUS_REFRESH, "Status Refresh"
        )
        await asyncio.sleep(5)

        await self.send_raw_command(CMD_REQUEST_ATTRIBUTES, "Attributes")
        await asyncio.sleep(5)

        # File management
        await self.send_raw_command(CMD_RETRIEVE_FILE_LIST, "File List")
        await asyncio.sleep(5)

        await self.send_raw_command(
            CMD_GET_FILE_INFO, "Get File Info (CC2)", {"FileName": "test.gcode"}
        )
        await asyncio.sleep(5)

        # History
        await self.send_raw_command(
            CMD_RETRIEVE_HISTORICAL_TASKS, "Historical Tasks"
        )
        await asyncio.sleep(5)
        # Note: Task details would need a specific task ID, skip for now

        # Video stream
        await self.send_raw_command(
            CMD_SET_VIDEO_STREAM, "Video Stream ON", {"Enable": 1}
        )
        await asyncio.sleep(5)

        await self.send_raw_command(
            CMD_SET_VIDEO_STREAM, "Video Stream OFF", {"Enable": 0}
        )
        await asyncio.sleep(5)

        # Time-lapse
        await self.send_raw_command(
            CMD_SET_TIME_LAPSE_PHOTOGRAPHY, "Time-Lapse ON", {"Enable": 1}
        )
        await asyncio.sleep(5)

        await self.send_raw_command(
            CMD_SET_TIME_LAPSE_PHOTOGRAPHY, "Time-Lapse OFF", {"Enable": 0}
        )
        await asyncio.sleep(5)

        # NEW Centauri Carbon 2 commands
        logger.info("\n🎨 Testing NEW Centauri Carbon 2 Commands...")
        logger.info("-" * 80)

        await self.send_raw_command(
            CMD_AMS_GET_SLOT_LIST, "AMS Get Slot List (CC2)"
        )
        await asyncio.sleep(5)

        await self.send_raw_command(
            CMD_AMS_GET_MAPPING_INFO, "AMS Get Mapping Info (CC2)"
        )
        await asyncio.sleep(5)
        # Export time-lapse would need a task ID, skip for now

        logger.info("\n⚠️  Skipped Commands (Potentially Destructive):")
        logger.info("   - XYZ Move/Home (could move axes)")
        logger.info("   - File rename/delete operations")
        logger.info("   - Print control (pause/stop/resume)")
        logger.info("   - Delete history")
        logger.info("   - AMS loading/unloading")

        # Listen for status updates and any delayed responses
        logger.info("\n" + "=" * 80)
        await self.listen_for_additional_messages(duration=30)

        # Cleanup
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        if self.websocket:
            await self.websocket.close()
        if self.session:
            await self.session.close()

        logger.info("\n" + "=" * 80)
        logger.info("✅ RAW Data Extraction Complete!")
        logger.info(f"📁 Saved to: {self.output_file}")
        logger.info(
            f"📊 Captured {self.message_count['sent']} sent, "
            f"{self.message_count['received']} received messages"
        )
        logger.info("=" * 80)


async def main() -> None:
    """Main entry point."""
    # Get printer IP from command line or environment
    printer_ip = sys.argv[1] if len(sys.argv) > 1 else os.getenv("PRINTER_IP")

    # Create output directory and file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).parent.parent / "cc2_extractions"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / f"cc2_raw_extraction_{timestamp}.jsonl"

    logger.info(f"📁 Output will be saved to: {output_file}")

    extractor = RawDataExtractor(output_file)

    try:
        # Discover printers
        discovered = extractor.discover_printers(printer_ip)

        if not discovered:
            logger.error("❌ No printers found!")
            return

        # Select printer
        if len(discovered) == 1 or printer_ip:
            # Auto-select if only one printer or specific IP given
            selected_printer = discovered[0]
            logger.info(
                f"✅ Found: {selected_printer.name} ({selected_printer.model})"
            )
            logger.info(f"   IP: {selected_printer.ip_address}")
            logger.info(f"   ID: {selected_printer.id}")
        else:
            # Multiple printers - let user choose
            logger.info(f"🎯 Found {len(discovered)} printer(s):")
            logger.info("=" * 80)

            for i, printer in enumerate(discovered, start=1):
                proxy_suffix = " (Proxy)" if printer.is_proxy else ""
                logger.info(
                    f"  {i}. {printer.name}{proxy_suffix} - "
                    f"{printer.model} @ {printer.ip_address}"
                )

            logger.info("=" * 80)

            # Get user selection
            while True:
                try:
                    choice = input(
                        f"Enter printer number (1-{len(discovered)}): "
                    )
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

        # Run extraction
        await extractor.run_extraction(selected_printer)

    except KeyboardInterrupt:
        logger.info("\n🛑 Interrupted by user")
    except Exception as e:
        logger.exception(f"❌ Fatal error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
