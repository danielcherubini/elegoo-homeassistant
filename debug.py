"""Debug file for testing elegoo printer."""

import asyncio
import os
import sys

from loguru import logger

from custom_components.elegoo_printer.elegoo_sdcp.const import DEBUG
from custom_components.elegoo_printer.elegoo_sdcp.elegoo_printer import (
    ElegooPrinterClient,
)

LOG_LEVEL = "DEBUG"
logger.remove()
logger.add(sys.stdout, colorize=DEBUG, level=LOG_LEVEL)


async def main() -> None:
    """Declare Main function for debugging purposes."""
    stop_event = asyncio.Event()
    try:
        printer_ip = os.getenv("PRINTER_IP", "10.0.0.212")
        elegoo_printer = ElegooPrinterClient(
            ip_address=printer_ip, use_seconds=True, logger=logger
        )
        printer = elegoo_printer.discover_printer()
        if printer:
            connected = await elegoo_printer.connect_printer()
            if connected:
                logger.debug("Polling Started")
                await asyncio.sleep(2)
                elegoo_printer.get_printer_attributes()
                while not stop_event.is_set():  # noqa: ASYNC110
                    # elegoo_printer.get_printer_status()
                    await asyncio.sleep(2)
        else:
            logger.exception("No printers discovered.")
    except asyncio.CancelledError:
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(main())
