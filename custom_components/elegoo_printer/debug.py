import asyncio
import time

from .models import PrinterSensor
from .printer import ElegooPrinterClient


def main():
    hass = None
    entities = [
        PrinterSensor(
            hass,
            "elegoo_printer_uvled_temperature",
            "°C",
            "uv_temperature",
            "mdi:led-variant-on",
        ),
        PrinterSensor(
            hass,
            "elegoo_printer_time_total",
            "milliseconds",
            "time_total",
            "mdi:timer-clock-outline",
        ),
        PrinterSensor(
            hass,
            "elegoo_printer_time_printing",
            "milliseconds",
            "time_printing",
            "mdi:timer-sand",
        ),
        PrinterSensor(
            hass,
            "elegoo_printer_time_remaining",
            "milliseconds",
            "time_remaining",
            "mdi:timer-outline",
        ),
        PrinterSensor(hass, "elegoo_printer_filename", None, "filename", "mdi:file"),
        PrinterSensor(
            hass, "elegoo_printer_current_layer", None, "current_layer", "mdi:layers"
        ),
        PrinterSensor(
            hass,
            "elegoo_printer_total_layers",
            None,
            "total_layers",
            "mdi:layers-triple",
        ),
        PrinterSensor(
            hass,
            "elegoo_printer_remaining_layers",
            None,
            "remaining_layers",
            "mdi:layers-minus",
        ),
    ]
    elegoo_printer = ElegooPrinterClient("10.0.0.212", entities)
    printer = elegoo_printer.discover_printer()
    if printer:
        connected = elegoo_printer.connect_printer()
        if connected:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.create_task(elegoo_printer.poll_printer_status())
            loop.run_forever()
            print("Polling Started")
            while True:
                time.sleep(2)
                print(elegoo_printer.get_entities())
    else:
        print("No printers discovered.")


if __name__ == "__main__":
    main()
