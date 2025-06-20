from homeassistant.components.mjpeg.camera import MjpegCamera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.elegoo_printer import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.const import CONF_CENTAURI_CARBON
from custom_components.elegoo_printer.data import ElegooPrinterConfigEntry
from custom_components.elegoo_printer.definitions import (
    PRINTER_CAMERAS,
    ElegooPrinterSensorEntityDescription,
)
from custom_components.elegoo_printer.elegoo_sdcp.elegoo_printer import (
    ElegooPrinterClient,
)
from custom_components.elegoo_printer.entity import ElegooPrinterEntity


def generate_unique_id(machine_name: str, id: str, key: str) -> str:
    if not machine_name or machine_name == "":
        return id + "_" + key
    else:
        return machine_name.replace(" ", "_").lower() + "_" + key


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add Elegoo Printer Camera entities."""
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator

    for camera in PRINTER_CAMERAS:
        if camera.exists_fn(
            coordinator.config_entry.data.get(CONF_CENTAURI_CARBON, False)
        ):
            async_add_entities([ElegooMjpegCamera(hass, coordinator, camera)])


class ElegooMjpegCamera(ElegooPrinterEntity, MjpegCamera):
    """Representation of an MjpegCamera"""

    printer_client: ElegooPrinterClient

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """Initialize the MjpegCamera entity"""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = coordinator.generate_unique_id(
            self.entity_description.key
        )
        self.printer_client: ElegooPrinterClient = (
            coordinator.config_entry.runtime_data.client._elegoo_printer
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if (
            hasattr(self, "entity_description")
            and self.entity_description.available_fn is not None
        ):
            self._attr_available = self.entity_description.available_fn(
                self.printer_client
            )

        return super().available

    @property
    def stream_source(self) -> str:
        return f"http://{self.printer_client.ip_address}:3031/video"
