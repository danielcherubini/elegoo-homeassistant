from homeassistant.components.mjpeg.camera import MjpegCamera
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import PlatformNotReady
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

        runtime_data = coordinator.config_entry.runtime_data
        if not runtime_data or not runtime_data.client:
            raise PlatformNotReady("Printer client not yet available")
        self.printer_client: ElegooPrinterClient = runtime_data.client._elegoo_printer
        if not self.printer_client.ip_address:
            raise PlatformNotReady("Printer IP address not available")
        _mjpeg_url = f"http://{self.printer_client.ip_address}:3031/video"
        self.entity_description.value_fn(_mjpeg_url)
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
            return self.entity_description.available_fn(self.printer_client)
        return super().available
