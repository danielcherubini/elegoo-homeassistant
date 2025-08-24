"""Debug file for testing elegoo printer."""

import asyncio
import os
import sys

import aiohttp
from loguru import logger

from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient
from custom_components.elegoo_printer.sdcp.const import DEBUG

LOG_LEVEL = "INFO"
PRINTER_IP = os.getenv("PRINTER_IP", "10.0.0.114")

logger.remove()
logger.add(sys.stdout, colorize=DEBUG, level=LOG_LEVEL)


async def monitor_printer(printer, session, stop_event):
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
            elegoo_printer = ElegooPrinterClient(
                ip_address="0.0.0.0", session=session, logger=logger
            )
            
            # Discover all printers (broadcast discovery)
            logger.info("🔍 Discovering printers on network...")
            discovered_printers = elegoo_printer.discover_printer()
            
            if discovered_printers:
                logger.info(f"🎯 Found {len(discovered_printers)} printer(s):")
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
                logger.warning("⚠️  No printers discovered on the network")
                
    except KeyboardInterrupt:
        logger.info("🛑 Received interrupt signal")
        stop_event.set()
    except asyncio.CancelledError:
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(main())
