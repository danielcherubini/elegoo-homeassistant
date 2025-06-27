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
    Run a debug polling loop to discover, connect to, and monitor an Elegoo printer.

    Attempts to discover a printer at the specified IP address, connect to it, retrieve its attributes, and periodically poll its status until interrupted.
    """
    stop_event = asyncio.Event()
    try:
        elegoo_printer = ElegooPrinterClient(
            ip_address=PRINTER_IP, centauri_carbon=False, logger=logger, ws_server=True
        )
        printer = elegoo_printer.discover_printer(PRINTER_IP)
        if printer:
            server = ElegooPrinterServer(printer, logger=logger)
            proxied_printer = server.get_printer()
            connected = await elegoo_printer.connect_printer(proxied_printer)
            if connected:
                logger.debug("Polling Started")
                await asyncio.sleep(2)
                elegoo_printer.get_printer_attributes()
                while not stop_event.is_set():  # noqa: ASYNC110
                    elegoo_printer.get_printer_status()
                    await asyncio.sleep(2)
        else:
            logger.exception("No printers discovered.")
    except asyncio.CancelledError:
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(main())
