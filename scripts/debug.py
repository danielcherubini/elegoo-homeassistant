"""Debug file for testing elegoo printer."""

import asyncio
import os
import sys

from loguru import logger

# Add project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from custom_components.elegoo_printer.elegoo_sdcp.client import ElegooPrinterClient
from custom_components.elegoo_printer.elegoo_sdcp.const import DEBUG

LOG_LEVEL = "DEBUG"
PRINTER_IP = os.getenv("PRINTER_IP", "10.0.0.212")

logger.remove()
logger.add(sys.stdout, colorize=DEBUG, level=LOG_LEVEL)


async def main() -> None:
    """
    Asynchronously discovers, connects to, and monitors an Elegoo printer for debugging.

    Discovers available printers at the configured IP address, connects to the first discovered printer, retrieves printer attributes, enables the video stream, prints the video status if available, and enters a polling loop until interrupted.
    """
    stop_event = asyncio.Event()
    try:
        elegoo_printer = ElegooPrinterClient(ip_address=PRINTER_IP, logger=logger)
        printer = elegoo_printer.discover_printer(PRINTER_IP)
        if printer:
            logger.debug(f"PrinterType: {printer[0].printer_type}")
            logger.debug(f"Model Reported from Printer: {printer[0].model}")
            # server = ElegooPrinterServer(printer[0], logger=logger)
            # printer = server.get_printer()

            logger.debug(
                "Connecting to printer: %s at %s with proxy enabled: %s",
                printer[0].name,
                printer[0].ip_address,
                printer[0].proxy_enabled,
            )
            connected = await elegoo_printer.connect_printer(
                printer[0], printer[0].proxy_enabled
            )
            if connected:
                logger.debug("Polling Started")
                await asyncio.sleep(2)
                # elegoo_printer.get_printer_attributes()
                while not stop_event.is_set():  # noqa: ASYNC110
                    # elegoo_printer.get_printer_status()
                    await asyncio.sleep(2)
        else:
            logger.exception("No printers discovered.")
    except asyncio.CancelledError:
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(main())
