import time

from .printer import ElegooPrinterClient


def main():
    elegoo_printer = ElegooPrinterClient("10.0.0.212")
    printer = elegoo_printer.discover_printer()
    if printer:
        connected = elegoo_printer.connect_printer()
        if connected:
            # loop = asyncio.new_event_loop()
            # asyncio.set_event_loop(loop)
            # loop.create_task(elegoo_printer.poll_printer_status())
            # loop.run_forever()
            print("Polling Started")
            while True:
                time.sleep(2)
                print(elegoo_printer.get_printer_status())
    else:
        print("No printers discovered.")


if __name__ == "__main__":
    main()
