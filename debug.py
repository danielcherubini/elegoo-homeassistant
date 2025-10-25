"""Debug file for testing elegoo printer."""

import asyncio
import json
import os
import sys
from typing import Any

import aiohttp
from loguru import logger

from custom_components.elegoo_printer.mqtt.client import ElegooMqttClient
from custom_components.elegoo_printer.sdcp.const import DEBUG
from custom_components.elegoo_printer.sdcp.models.enums import ProtocolType
from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient

LOG_LEVEL = "INFO"
PRINTER_IP = os.getenv("PRINTER_IP", "localhost")
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

logger.remove()
logger.add(sys.stdout, colorize=DEBUG, level=LOG_LEVEL)


def print_printer_info(printer: Any, index: int | None = None) -> None:
    """Print detailed printer information in a copy-paste friendly format."""
    prefix = f"  {index}. " if index is not None else ""

    logger.info("=" * 80)
    logger.info(f"{prefix}Discovered Printer Information:")
    logger.info("=" * 80)
    logger.info(f"Name:             {printer.name}")
    logger.info(f"Model:            {printer.model}")
    logger.info(f"Brand:            {printer.brand}")
    logger.info(f"IP Address:       {printer.ip_address}")
    logger.info(f"Printer ID:       {printer.id}")
    logger.info(f"Connection ID:    {printer.connection}")
    logger.info(f"Protocol Version: {printer.protocol}")
    logger.info(f"Protocol Type:    {printer.protocol_type.value if printer.protocol_type else 'Unknown'}")
    logger.info(f"Firmware:         {printer.firmware}")
    logger.info(f"Printer Type:     {printer.printer_type.value if printer.printer_type else 'Unknown'}")
    logger.info(f"Is Proxy:         {printer.is_proxy}")
    logger.info("-" * 80)
    logger.info("JSON Representation (for GitHub issues):")
    logger.info("-" * 80)
    printer_dict = printer.to_dict_safe()
    logger.info(json.dumps(printer_dict, indent=2))
    logger.info("=" * 80)


async def monitor_printer(
    printer: Any, session: aiohttp.ClientSession, stop_event: asyncio.Event
):
    """Monitor a single printer."""
    # Create appropriate client based on protocol type
    if printer.protocol_type == ProtocolType.MQTT:
        logger.info(f"🔌 Using MQTT protocol for {printer.name}")
        elegoo_printer = ElegooMqttClient(
            mqtt_host=MQTT_HOST,
            mqtt_port=MQTT_PORT,
            mqtt_username=MQTT_USERNAME,
            mqtt_password=MQTT_PASSWORD,
            logger=logger,
            printer=printer,
        )
    else:
        logger.info(f"🔌 Using WebSocket/SDCP protocol for {printer.name}")
        elegoo_printer = ElegooPrinterClient(
            ip_address=printer.ip_address, session=session, logger=logger
        )

    logger.info(f"Connecting to printer: {printer.name} at {printer.ip_address}")
    connected = await elegoo_printer.connect_printer(
        printer, proxy_enabled=printer.proxy_enabled
    )

    if connected:
        logger.info(f"✅ Connected to {printer.name} ({printer.model})")
        await asyncio.sleep(2)

        # Get initial data
        try:
            await elegoo_printer.async_get_printer_current_task()
            await elegoo_printer.async_get_printer_historical_tasks()
        except Exception as e:
            logger.warning(f"Failed to get initial data for {printer.name}: {e}")

        # Monitor loop
        while not stop_event.is_set():
            try:
                # Get status
                printer_data = await elegoo_printer.get_printer_status()
                print_info = printer_data.status.print_info
                logger.info(f"[{printer.name}] Progress: {print_info.percent_complete}% | Remaining: {print_info.remaining_ticks}ms")

                # Try to get video for WebSocket printers
                if printer.protocol_type != ProtocolType.MQTT:
                    try:
                        video = await elegoo_printer.get_printer_video(enable=True)
                        if video:
                            logger.info(f"[{printer.name}] Video URL: {video.video_url}")
                    except Exception:
                        pass  # Video not critical

                await asyncio.sleep(4)
            except Exception as e:
                logger.error(f"Error monitoring {printer.name}: {e}")
                await asyncio.sleep(10)  # Wait longer on error
    else:
        logger.error(f"❌ Failed to connect to {printer.name}")

    # Cleanup
    await elegoo_printer.disconnect()


async def main() -> None:
    """
    Asynchronously discovers and monitors all Elegoo printers on the network.

    Discovers all available printers, connects to each one, and monitors them concurrently.
    Supports both WebSocket/SDCP and MQTT protocols.
    """
    stop_event = asyncio.Event()
    try:
        async with aiohttp.ClientSession() as session:
            # Create client for discovery
            elegoo_printer = ElegooPrinterClient(
                ip_address=PRINTER_IP, session=session, logger=logger
            )

            # Discover specific printer first if IP provided
            logger.info(f"🔍 Discovering printer at {PRINTER_IP}...")
            discovered_printer = elegoo_printer.discover_printer(PRINTER_IP)
            if discovered_printer:
                printer = discovered_printer[0]
                logger.info(f"✓ Found printer: {printer.name} ({printer.model})")

                # Print detailed information for GitHub issues
                print_printer_info(printer)

            # Also discover all printers on network (broadcast discovery)
            logger.info("🔍 Discovering all printers on network...")
            discovered_printers = elegoo_printer.discover_printer()

            if discovered_printers:
                logger.info(f"🎯 Found {len(discovered_printers)} printer(s) total:")

                # Print detailed info for each printer
                for i, printer in enumerate(discovered_printers, start=1):
                    print_printer_info(printer, index=i)

                # Ask user if they want to monitor all printers
                logger.info("=" * 80)
                logger.info("Starting monitoring of all discovered printers...")
                logger.info("Press Ctrl+C to stop monitoring")
                logger.info("=" * 80)

                # Create monitoring tasks for all printers
                monitor_tasks = []
                for printer in discovered_printers:
                    # Skip proxy servers
                    if printer.is_proxy:
                        logger.info(f"⏭️  Skipping proxy server: {printer.name}")
                        continue

                    task = asyncio.create_task(
                        monitor_printer(printer, session, stop_event)
                    )
                    monitor_tasks.append(task)

                if monitor_tasks:
                    logger.info("🚀 Starting concurrent monitoring of all printers...")

                    # Wait for all tasks to complete
                    try:
                        await asyncio.gather(*monitor_tasks)
                    except asyncio.CancelledError:
                        logger.info("🛑 Monitoring cancelled, cleaning up...")
                        for task in monitor_tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*monitor_tasks, return_exceptions=True)
                else:
                    logger.warning("⚠️  No printers to monitor (all were proxy servers)")
            else:
                logger.warning("⚠️  No printers discovered on the network")

    except KeyboardInterrupt:
        logger.info("🛑 Received interrupt signal")
        stop_event.set()
    except asyncio.CancelledError:
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(main())
