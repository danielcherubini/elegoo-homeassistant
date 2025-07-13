from homeassistant.components.mjpeg.camera import MjpegCamera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.elegoo_printer.const import CONF_PROXY_ENABLED, LOGGER
from custom_components.elegoo_printer.data import ElegooPrinterConfigEntry
from custom_components.elegoo_printer.definitions import (
    PRINTER_MJPEG_CAMERAS,
    ElegooPrinterSensorEntityDescription,
)
from custom_components.elegoo_printer.elegoo_sdcp.client import ElegooPrinterClient
from custom_components.elegoo_printer.elegoo_sdcp.models.enums import (
    ElegooVideoStatus,
    PrinterType,
)
from custom_components.elegoo_printer.elegoo_sdcp.models.video import ElegooVideo
from custom_components.elegoo_printer.entity import ElegooPrinterEntity

from .coordinator import ElegooDataUpdateCoordinator


def normalize_video_url(video_object: ElegooVideo) -> ElegooVideo:
    """
    Checks if video_object.video_url starts with 'http://' and adds it if missing.
    Modifies the video_object in place.
    """
    if not video_object.video_url.startswith("http://"):
        video_object.video_url = "http://" + video_object.video_url

    return video_object

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Asynchronously sets up Elegoo MJPEG camera entities for a printer configuration entry in Home Assistant.

    Adds camera entities for FDM-type printers and enables the printer's video stream.
    """
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator
    printer_type = coordinator.config_entry.runtime_data.api.printer.printer_type
    printer_client: ElegooPrinterClient = (
        coordinator.config_entry.runtime_data.api.client
    )

    await printer_client.set_printer_video_stream(toggle=True)

    if printer_type == PrinterType.FDM:
        LOGGER.debug(f"Adding {len(PRINTER_MJPEG_CAMERAS)} Camera entities")
        for camera in PRINTER_MJPEG_CAMERAS:
            async_add_entities(
                [ElegooMjpegCamera(hass, coordinator, camera)], update_before_add=True
            )


class ElegooMjpegCamera(ElegooPrinterEntity, MjpegCamera):
    """Representation of an MjpegCamera"""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """
        Initialize an Elegoo MJPEG camera entity for a 3D printer.

        Creates the camera entity with a unique ID, assigns its description, and stores a reference to the printer client for accessing video streams.
        """
        MjpegCamera.__init__(
            self,
            name=f"{description.name}",
            mjpeg_url="http://127.0.0.1:3031/video",
            still_image_url=None,  # This camera does not have a separate still image URL
            unique_id=coordinator.generate_unique_id(description.key),
        )

        ElegooPrinterEntity.__init__(self, coordinator)
        self.entity_description = description
        self._printer_client: ElegooPrinterClient = (
            coordinator.config_entry.runtime_data.api.client
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """
        Asynchronously retrieves the current MJPEG stream URL for the printer camera.

        If the printer video stream is successfully enabled, returns either a local proxy URL or the direct printer video URL based on configuration. Otherwise, returns the last known MJPEG URL.

        Returns:
            str: The MJPEG stream URL for the camera.
        """
        video = await self._printer_client.get_printer_video(toggle=True)
        if video.status and video.status == ElegooVideoStatus.SUCCESS:
            LOGGER.debug("stream_source: Video is OK, getting stream source")
            if self.coordinator.config_entry.data.get(CONF_PROXY_ENABLED, False):
                LOGGER.debug("stream_source: Proxy is enabled using local video")
                self._mjpeg_url = "http://127.0.0.1:3031/video"
            else:
                LOGGER.debug(
                    f"stream_source: Proxy is disabled using printer video url: {video.video_url}"
                )

                self._mjpeg_url = normalize_video_url(video).video_url 

        else:
            LOGGER.error(f"stream_source: Failed to get video stream: {video.status}")
        return await super().async_camera_image(width=width, height=height)

    @property
    def available(self) -> bool:
        """
        Return whether the camera entity is currently available.

        If the entity description specifies an availability function, this function is used to determine availability based on the printer's video data. Otherwise, falls back to the default availability check.
        """
        return super().available and self.entity_description.available_fn(
            self._printer_client.printer_data.video
        )
