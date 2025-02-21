"""Debug file for testing elegoo printer."""

import asyncio
import sys

from loguru import logger

from custom_components.elegoo_printer.elegoo_sdcp.const import DEBUG
from custom_components.elegoo_printer.elegoo_sdcp.elegoo_printer import (
    ElegooPrinterClient,
)

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
            elegoo_printer.set_printer_video_stream(toggle=False)
            # logger.debug(elegoo_printer.get_current_print_thumbnail())
            while True:
                await asyncio.sleep(0)
                # elegoo_printer.get_printer_status()
                # elegoo_printer.get_printer_attributes()
    else:
        logger.exception("No printers discovered.")


if __name__ == "__main__":
    asyncio.run(main())
