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
    """
    Generate a unique identifier string for a printer entity based on machine name, id, and key.
    
    If the machine name is empty or None, returns a string combining id and key separated by an underscore. Otherwise, returns the machine name (spaces replaced by underscores and in lowercase) concatenated with the key.
    
    Returns:
        str: The generated unique identifier.
    """
    if not machine_name or machine_name == "":
        return id + "_" + key
    else:
        return machine_name.replace(" ", "_").lower() + "_" + key


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up Elegoo Printer camera entities for a configuration entry in Home Assistant.
    
    Adds camera entities based on the printer's configuration and supported features.
    """
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
        """
        Initialize an Elegoo MJPEG camera entity for Home Assistant.
        
        Creates a camera entity with a unique ID and sets up the MJPEG stream URL using the printer's IP address. The entity description and printer client are stored for later use.
        """
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = coordinator.generate_unique_id(
            self.entity_description.key
        )
        self.printer_client: ElegooPrinterClient = (
            coordinator.config_entry.runtime_data.client._elegoo_printer
        )
        _mjpeg_url = f"http://{self.printer_client.ip_address}:3031/video"
        MjpegCamera.__init__(self, mjpeg_url=_mjpeg_url, still_image_url=_mjpeg_url)

    @property
    def available(self) -> bool:
        """
        Indicates whether the camera entity is currently available.
        
        If an availability function is defined in the entity description, it is called with the printer client to determine the entity's availability.
        """
        if (
            hasattr(self, "entity_description")
            and self.entity_description.available_fn is not None
        ):
            self._attr_available = self.entity_description.available_fn(
                self.printer_client
            )

        return super().available
