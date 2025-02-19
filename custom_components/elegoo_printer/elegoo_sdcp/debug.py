"""Debug file for testing elegoo printer."""

import asyncio
import sys

from loguru import logger

from .const import DEBUG
from .elegoo_printer import ElegooPrinterClient

LOG_LEVEL = "DEBUG"
logger.remove()
logger.add(sys.stdout, colorize=DEBUG, level=LOG_LEVEL)


async def main() -> None:  # noqa: D103
    elegoo_printer = ElegooPrinterClient("10.0.0.212", logger)
    printer = elegoo_printer.discover_printer()
    if printer:
        connected = await elegoo_printer.connect_printer()
        if connected:
            logger.debug("Polling Started")
            await asyncio.sleep(2)
            logger.debug(elegoo_printer.get_current_print_thumbnail())
            while True:
                await asyncio.sleep(0)
                # elegoo_printer.get_printer_status()
                # elegoo_printer.get_printer_attributes()
    else:
        print("No printers discovered.")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
