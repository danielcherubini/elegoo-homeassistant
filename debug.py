"""Debug file for testing elegoo printer."""

import asyncio
import json
import os
import sys
from typing import Any

import aiohttp
from loguru import logger

from custom_components.elegoo_printer.mqtt.client import ElegooMqttClient
from custom_components.elegoo_printer.mqtt.server import ElegooMQTTBroker
from custom_components.elegoo_printer.sdcp.const import DEBUG
from custom_components.elegoo_printer.sdcp.models.enums import TransportType
from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient

LOG_LEVEL = "INFO"
PRINTER_IP = os.getenv("PRINTER_IP", "localhost")
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
# Default to embedded broker port (18830), not standard MQTT port (1883)
MQTT_PORT = int(os.getenv("MQTT_PORT", "18830"))

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
    logger.info(f"Transport Type:   {printer.transport_type.value if printer.transport_type else 'Unknown'}")
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
    # Create appropriate client based on transport type
    if printer.transport_type == TransportType.MQTT:
        logger.info(f"🔌 Using MQTT transport for {printer.name}")
        elegoo_printer = ElegooMqttClient(
            mqtt_host=MQTT_HOST,
            mqtt_port=MQTT_PORT,
            logger=logger,
            printer=printer,
        )
    else:
        logger.info(f"🔌 Using WebSocket transport for {printer.name}")
        elegoo_printer = ElegooPrinterClient(
            ip_address=printer.ip_address, session=session, logger=logger
        )

    logger.info(f"Connecting to printer: {printer.name} at {printer.ip_address}")
    # MQTT client doesn't accept proxy_enabled parameter
    if printer.transport_type == TransportType.MQTT:
        connected = await elegoo_printer.connect_printer(printer)
    else:
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
                if print_info:
                    logger.info(f"[{printer.name}] Progress: {print_info.percent_complete}% | Remaining: {print_info.remaining_ticks}ms")
                else:
                    logger.info(f"[{printer.name}] Status received (no print_info yet)")

                # Try to get video for WebSocket printers
                if printer.transport_type != TransportType.MQTT:
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

                # Filter out proxy servers for monitoring
                non_proxy_printers = [
                    p for p in discovered_printers if not p.is_proxy
                ]

                if not non_proxy_printers:
                    logger.warning("⚠️  No printers to monitor (all were proxy servers)")
                    return

                # Ask user which printer to monitor
                logger.info("=" * 80)
                logger.info("Select a printer to monitor:")
                for i, printer in enumerate(non_proxy_printers, start=1):
                    logger.info(
                        f"  {i}. {printer.name} ({printer.model}) - {printer.transport_type.value}"
                    )
                logger.info("=" * 80)

                # Get user selection
                while True:
                    try:
                        choice = input(f"Enter printer number (1-{len(non_proxy_printers)}): ")
                        printer_index = int(choice) - 1
                        if 0 <= printer_index < len(non_proxy_printers):
                            selected_printer = non_proxy_printers[printer_index]
                            break
                        logger.error(f"Please enter a number between 1 and {len(non_proxy_printers)}")
                    except ValueError:
                        logger.error("Please enter a valid number")
                    except KeyboardInterrupt:
                        logger.info("\n🛑 Cancelled by user")
                        return

                logger.info(f"📍 Selected: {selected_printer.name}")

                # Start MQTT broker if selected printer uses MQTT
                mqtt_broker = None
                if selected_printer.transport_type == TransportType.MQTT:
                    logger.info("🚀 Starting embedded MQTT broker for MQTT printer...")
                    mqtt_broker = await ElegooMQTTBroker.get_instance()
                    logger.info(f"✅ MQTT broker started on port {mqtt_broker.port}")

                # Monitor the selected printer
                logger.info("=" * 80)
                logger.info(f"Starting monitoring of {selected_printer.name}...")
                logger.info("Press Ctrl+C to stop monitoring")
                logger.info("=" * 80)

                # Create monitoring task for selected printer only
                monitor_tasks = [
                    asyncio.create_task(
                        monitor_printer(selected_printer, session, stop_event)
                    )
                ]

                if monitor_tasks:
                    # Monitor the selected printer

                    # Wait for all tasks to complete
                    try:
                        await asyncio.gather(*monitor_tasks)
                    except asyncio.CancelledError:
                        logger.info("🛑 Monitoring cancelled, cleaning up...")
                        for task in monitor_tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*monitor_tasks, return_exceptions=True)
                    finally:
                        # Stop MQTT broker if it was started
                        if mqtt_broker:
                            logger.info("🛑 Stopping MQTT broker...")
                            await ElegooMQTTBroker.release_instance()
            else:
                logger.warning("⚠️  No printers discovered on the network")

    except KeyboardInterrupt:
        logger.info("🛑 Received interrupt signal")
        stop_event.set()
    except asyncio.CancelledError:
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(main())
