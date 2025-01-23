import logging
import datetime

from .printer import ElegooPrinter
from homeassistant.core import HomeAssistant
from .models import PrinterSensor

DOMAIN = "elegoo_home_assistant"

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Elegoo Home Assistant Add-on."""
    printer_ip = "10.0.0.212"
    _LOGGER = logging.getLogger(__name__)
    _LOGGER.info("Elegoo Home Assistant Add-on is starting")

    # Create entities with updated entity IDs
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

    # Initial update of entities
    elegoo_printer = ElegooPrinter(hass, printer_ip, entities)
    printer = elegoo_printer.discover_printer()
    if printer:
        connected = elegoo_printer.connect_printer()
        if connected:
            # Set up a timer to update entities periodically
            hass.helpers.event.async_track_time_interval(
                elegoo_printer.get_printer_status(), datetime.timedelta(seconds=30)
            )
    else:
        _LOGGER.error("No printers discovered.")

    return True
