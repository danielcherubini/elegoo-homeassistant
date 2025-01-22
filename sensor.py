import logging
import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

DOMAIN = "elegoo_home_assistant"

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Elegoo Home Assistant Add-on."""
    _LOGGER = logging.getLogger(__name__)
    _LOGGER.info("Elegoo Home Assistant Add-on is starting")

    # --- Replace with your actual Elegoo printer integration logic ---

    def get_printer_data():
        """Fetch data from your Elegoo printer."""
        # Replace this with your actual data retrieval logic
        return {
            "uv_temperature": 35.5,
            "time_total": 21600000,  # in milliseconds (6 hours)
            "time_printing": 3600000,  # in milliseconds (1 hour)
            "time_remaining": 18000000,  # in milliseconds (5 hours)
            "filename": "my_resin_model.stl",
            "current_layer": 45,
            "total_layers": 200,
            "remaining_layers": 155,
        }

    # --- End of Elegoo printer integration logic ---

    async def update_entities():
        """Update the values of the entities."""
        printer_data = get_printer_data()

        for entity in entities:
            entity.update_data(printer_data)
            await entity.async_update_ha_state()

    # Create entities with updated entity IDs
    entities = [
        PrinterSensor(hass, "elegoo_printer_uvled_temperature",
                      "°C", "uv_temperature", "mdi:led-variant-on"),
        PrinterSensor(hass, "elegoo_printer_time_total",
                      "milliseconds", "time_total", "mdi:timer-clock-outline"),
        PrinterSensor(hass, "elegoo_printer_time_printing",
                      "milliseconds", "time_printing", "mdi:timer-sand"),
        PrinterSensor(hass, "elegoo_printer_time_remaining",
                      "milliseconds", "time_remaining", "mdi:timer-outline"),
        PrinterSensor(hass, "elegoo_printer_filename",
                      None, "filename", "mdi:file"),
        PrinterSensor(hass, "elegoo_printer_current_layer",
                      None, "current_layer", "mdi:layers"),
        PrinterSensor(hass, "elegoo_printer_total_layers", None,
                      "total_layers", "mdi:layers-triple"),
        PrinterSensor(hass, "elegoo_printer_remaining_layers",
                      None, "remaining_layers", "mdi:layers-minus"),
    ]

    # Initial update of entities
    await update_entities()

    # Set up a timer to update entities periodically
    hass.helpers.event.async_track_time_interval(
        update_entities, datetime.timedelta(seconds=30))

    return True


class PrinterSensor(Entity):
    """Representation of an Elegoo printer sensor."""

    def __init__(self, hass, entity_id, unit, data_key, icon):
        """Initialize the sensor."""
        self.hass = hass
        # The 'sensor.' prefix is important
        self._entity_id = f"sensor.{entity_id}"
        self._unit_of_measurement = unit
        self._data_key = data_key
        self._icon = icon
        self._state = None

    @property
    def entity_id(self):
        """Return the entity ID."""
        return self._entity_id

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def icon(self):
        """Return the icon."""
        return self._icon

    def update_data(self, printer_data):
        """Update the sensor data."""
        self._state = printer_data.get(self._data_key)
