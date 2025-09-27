"""Debug file for testing elegoo printer."""

import asyncio
import os
import sys
from typing import Any

import aiohttp
from loguru import logger

from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient
from custom_components.elegoo_printer.sdcp.const import DEBUG

LOG_LEVEL = "DEBUG"
PRINTER_IP = os.getenv("PRINTER_IP", "10.0.0.184")

logger.remove()
logger.add(sys.stdout, colorize=DEBUG, level=LOG_LEVEL)


async def monitor_printer(
    printer: Any, session: aiohttp.ClientSession, stop_event: asyncio.Event
):
    """Monitor a single printer."""
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
                video = await elegoo_printer.get_printer_video(enable=True)
                if video:
                    logger.info(f"[{printer.name}] Video URL: {video.video_url}")
                
                # Optionally get status
                # printer_data = await elegoo_printer.get_printer_status()
                # print_info = printer_data.status.print_info
                # logger.info(f"[{printer.name}] Remaining: {print_info.remaining_ticks}")
                
                await asyncio.sleep(4)
            except Exception as e:
                logger.error(f"Error monitoring {printer.name}: {e}")
                await asyncio.sleep(10)  # Wait longer on error
    else:
        logger.error(f"❌ Failed to connect to {printer.name}")


async def main() -> None:
    """
    Asynchronously discovers and monitors all Elegoo printers on the network.

    Discovers all available printers, connects to each one, and monitors them concurrently.
    """
    stop_event = asyncio.Event()
    try:
        async with aiohttp.ClientSession() as session:
            # Create client with the printer IP for discovery
            elegoo_printer = ElegooPrinterClient(
                ip_address=PRINTER_IP, session=session, logger=logger
            )

            # Test ping functionality first if specific IP provided
            logger.info(f"Testing ping to printer at {PRINTER_IP}...")
            ping_result = await elegoo_printer.ping_printer(ping_timeout=5.0)
            if ping_result:
                logger.info("✓ Ping successful - printer WebSocket is reachable")
            else:
                logger.warning("✗ Ping failed - printer WebSocket is not reachable")
                logger.info("This is expected if printer is off or WebSocket service not running")

            # Discover specific printer first
            logger.info(f"🔍 Discovering printer at {PRINTER_IP}...")
            discovered_printer = elegoo_printer.discover_printer(PRINTER_IP)
            if discovered_printer:
                printer = discovered_printer[0]
                logger.info(f"✓ Found printer: {printer.name} ({printer.model})")
                logger.debug(f"PrinterType: {printer.printer_type}")
                logger.debug(f"Model Reported from Printer: {printer.model}")

                logger.debug(
                    "Connecting to printer: %s at %s with proxy enabled: %s",
                    printer.name,
                    printer.ip_address,
                    printer.proxy_enabled,
                )
                connected = await elegoo_printer.connect_printer(
                    printer, proxy_enabled=printer.proxy_enabled
                )
                if connected:
                    logger.debug("Polling Started")
                    await asyncio.sleep(2)
                    await elegoo_printer.async_get_printer_current_task()
                    await elegoo_printer.async_get_printer_historical_tasks()
                    await elegoo_printer.get_printer_attributes()
                    while not stop_event.is_set():  # noqa: ASYNC110
                        printer_data = await elegoo_printer.get_printer_status()
                        print_info = printer_data.status.print_info

                        current_task = await elegoo_printer.async_get_printer_current_task()
                        logger.info(current_task)
                        logger.info(
                            f"remaining_ticks: {print_info.remaining_ticks} total_ticks: {print_info.total_ticks} current_ticks: {print_info.current_ticks}"
                        )
                        await asyncio.sleep(4)
                else:
                    logger.error(f"❌ Failed to connect to {printer.name}")

            # Also discover all printers on network (broadcast discovery)
            logger.info("🔍 Discovering all printers on network...")
            discovered_printers = elegoo_printer.discover_printer()

            if discovered_printers:
                logger.info(f"🎯 Found {len(discovered_printers)} printer(s) total:")
                for i, printer in enumerate(discovered_printers):
                    logger.info(
                        f"  {i+1}. {printer.name} ({printer.model}) at {printer.ip_address}"
                    )

                # Create monitoring tasks for all printers
                monitor_tasks = []
                for printer in discovered_printers:
                    task = asyncio.create_task(
                        monitor_printer(printer, session, stop_event)
                    )
                    monitor_tasks.append(task)

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
                logger.warning("⚠️  No additional printers discovered on the network")

    except KeyboardInterrupt:
        logger.info("🛑 Received interrupt signal")
        stop_event.set()
    except asyncio.CancelledError:
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(main())
