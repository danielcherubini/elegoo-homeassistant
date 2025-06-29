from homeassistant.components.mjpeg.camera import MjpegCamera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.elegoo_printer import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.const import CONF_CENTAURI_CARBON
from custom_components.elegoo_printer.data import ElegooPrinterConfigEntry
from custom_components.elegoo_printer.definitions import (
    PRINTER_MJPEG_CAMERAS,
    ElegooPrinterSensorEntityDescription,
)
from custom_components.elegoo_printer.elegoo_sdcp.client import ElegooPrinterClient
from custom_components.elegoo_printer.elegoo_sdcp.models.enums import ElegooVideoStatus
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

    for camera in PRINTER_MJPEG_CAMERAS:
        if coordinator.config_entry.data.get(CONF_CENTAURI_CARBON, False):
            async_add_entities([ElegooMjpegCamera(hass, coordinator, camera)])

    printer_client: ElegooPrinterClient = (
        coordinator.config_entry.runtime_data.client._elegoo_printer
    )
    printer_client.set_printer_video_stream(toggle=True)


class ElegooMjpegCamera(ElegooPrinterEntity, MjpegCamera):
    """Representation of an MjpegCamera"""

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
        self._mjpeg_url = ""
        self._printer_client: ElegooPrinterClient = (
            coordinator.config_entry.runtime_data.client._elegoo_printer
        )

    @property
    async def stream_source(self) -> str:
        # if self.coordinator.config_entry.data.get(CONF_PROXY_ENABLED, False):
        #     return "http://127.0.0.1:3031/video"

        video = await self._printer_client.get_printer_video(toggle=True)
        if video.status and video.status == ElegooVideoStatus.SUCCESS:
            self._mjpeg_url = video.video_url

        return self._mjpeg_url

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
            return self.entity_description.available_fn(
                self._printer_client.printer_data.video
            )
        return super().available
