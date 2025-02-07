"""Debug file for testing elegoo printer."""

import asyncio

from .elegoo_printer import ElegooPrinterClient


async def main() -> None:  # noqa: D103
    elegoo_printer = ElegooPrinterClient("10.0.0.212")
    printer = elegoo_printer.discover_printer()
    if printer:
        connected = await elegoo_printer.connect_printer()
        if connected:
            print("Polling Started")  # noqa: T201
            elegoo_printer.set_printer_video_stream(toggle=True)
            while True:
                await asyncio.sleep(2)
                elegoo_printer.get_printer_status()
    else:
        print("No printers discovered.")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
