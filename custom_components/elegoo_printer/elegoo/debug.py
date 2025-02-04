"""Debug file for testing elegoo printer."""

import time

from .elegoo_printer import ElegooPrinterClient


def main() -> None:  # noqa: D103
    elegoo_printer = ElegooPrinterClient("10.0.0.212")
    printer = elegoo_printer.discover_printer()
    if printer:
        connected = elegoo_printer.connect_printer()
        if connected:
            print("Polling Started")  # noqa: T201
            time.sleep(2)
            elegoo_printer.get_printer_attributes()
            time.sleep(10)
            # while True:
            #     time.sleep(2)  # noqa: ERA001
            #     elegoo_printer.get_printer_attributes()  # noqa: ERA001
    else:
        print("No printers discovered.")  # noqa: T201


if __name__ == "__main__":
    main()
