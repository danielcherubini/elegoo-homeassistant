"""Debug file for testing elegoo printer."""

import asyncio
import os
import sys

from loguru import logger

from custom_components.elegoo_printer.elegoo_sdcp.client import ElegooPrinterClient
from custom_components.elegoo_printer.elegoo_sdcp.const import DEBUG
from custom_components.elegoo_printer.elegoo_sdcp.server import ElegooPrinterServer

LOG_LEVEL = "DEBUG"
PRINTER_IP = os.getenv("PRINTER_IP", "10.0.0.212")

logger.remove()
logger.add(sys.stdout, colorize=DEBUG, level=LOG_LEVEL)


async def main() -> None:
    """
    Asynchronously discovers, connects to, and monitors an Elegoo printer for debugging purposes.
    
    Discovers a printer at the specified IP address, attempts to connect, enables the printer's video stream, and periodically polls until interrupted. Prints the video status if available.
    """
    stop_event = asyncio.Event()
    try:
        elegoo_printer = ElegooPrinterClient(ip_address=PRINTER_IP, logger=logger)
        printer = elegoo_printer.discover_printer(PRINTER_IP)
        if printer:
            server = ElegooPrinterServer(printer, logger=logger)
            printer = server.get_printer()
            connected = await elegoo_printer.connect_printer(printer)
            if connected:
                logger.debug("Polling Started")
                await asyncio.sleep(2)
                # elegoo_printer.get_printer_attributes()
                video = await elegoo_printer.get_printer_video(toggle=True)
                if video.status:
                    print(video.status.name)
                while not stop_event.is_set():  # noqa: ASYNC110
                    # elegoo_printer.get_printer_status()
                    await asyncio.sleep(2)
        else:
            logger.exception("No printers discovered.")
    except asyncio.CancelledError:
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(main())
