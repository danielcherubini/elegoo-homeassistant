"""Debug file for testing elegoo printer."""  # noqa: INP001

import time

from .printer import ElegooPrinterClient


def main() -> None:  # noqa: D103
    elegoo_printer = ElegooPrinterClient("10.0.0.212")
    printer = elegoo_printer.discover_printer()
    if printer:
        connected = elegoo_printer.connect_printer()
        if connected:
            print("Polling Started")  # noqa: T201
            while True:
                time.sleep(2)
                print(elegoo_printer.get_printer_status())  # noqa: T201
    else:
        print("No printers discovered.")  # noqa: T201


if __name__ == "__main__":
    main()
