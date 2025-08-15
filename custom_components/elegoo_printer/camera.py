from http import HTTPStatus

import homeassistant
from aiohttp import web
from haffmpeg.camera import CameraMjpeg
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import (
    DOMAIN,
    async_get_image,
)
from homeassistant.components.mjpeg.camera import MjpegCamera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_aiohttp_proxy_stream
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.elegoo_printer.const import (
    CONF_PROXY_ENABLED,
    LOGGER,
    PROXY_HOST,
    VIDEO_ENDPOINT,
    VIDEO_PORT,
)
from custom_components.elegoo_printer.data import ElegooPrinterConfigEntry
from custom_components.elegoo_printer.definitions import (
    PRINTER_FFMPEG_CAMERAS,
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
    """Checks if video_object.video_url starts with 'http://' and adds it if missing.

    Args:
        video_object: The video object to normalize.
    """
    if not video_object.video_url.startswith("http://"):
        video_object.video_url = "http://" + video_object.video_url

    return video_object


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Asynchronously sets up Elegoo camera entities."""
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator
    printer_type = coordinator.config_entry.runtime_data.api.printer.printer_type
    printer_client: ElegooPrinterClient = (
        coordinator.config_entry.runtime_data.api.client
    )
    if printer_type == PrinterType.FDM:
        LOGGER.debug(f"Adding {len(PRINTER_MJPEG_CAMERAS)} Camera entities")
        for camera in PRINTER_MJPEG_CAMERAS:
            async_add_entities(
                [ElegooMjpegCamera(hass, coordinator, camera)], update_before_add=True
            )
    elif printer_type == PrinterType.RESIN:
        await printer_client.get_printer_video(toggle=True)
        LOGGER.debug(f"Adding {len(PRINTER_FFMPEG_CAMERAS)} Camera entities")
        for camera in PRINTER_FFMPEG_CAMERAS:
            async_add_entities(
                [ElegooStreamCamera(hass, coordinator, camera)],
                update_before_add=True,
            )


class ElegooStreamCamera(ElegooPrinterEntity, Camera):
    """Representation of a camera that streams from an Elegoo printer."""

    def __init__(
        self,
        hass: homeassistant,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """Initialize an Elegoo stream camera entity."""
        Camera.__init__(self)
        ElegooPrinterEntity.__init__(self, coordinator)

        self.entity_description = description
        self._printer_client: ElegooPrinterClient = (
            coordinator.config_entry.runtime_data.api.client
        )
        self._attr_name = description.name
        self._attr_unique_id = coordinator.generate_unique_id(description.key)
        self._attr_supported_features = CameraEntityFeature.STREAM

        # For HLS stream worker
        self.stream_options = {
            "ffmpeg_options": {
                "rtsp_transport": "udp",
                "err_detect": "ignore_err",
                "fflags": "nobuffer",
                "analyzeduration": "10M",
                "probesize": "5M",
            }
        }
        # For MJPEG stream
        self._extra_ffmpeg_arguments = "-rtsp_transport udp"

    @property
    def supported_features(self) -> CameraEntityFeature:
        """Return supported features."""
        return self._attr_supported_features

    async def _get_stream_url(self) -> str | None:
        """Get the stream URL, from cache if recent."""
        # if self._printer_client.printer_data.attributes.num_video_stream_connected > 2:
        #     return None
        #
        # if (
        #     self._printer_client.printer_data.video is not None
        #     and self._printer_client.printer_data.video.video_url is not None
        # ):
        #     return self._printer_client.printer_data.video.video_url

        video = await self._printer_client.get_printer_video(toggle=True)
        if video.status and video.status == ElegooVideoStatus.SUCCESS:
            LOGGER.debug(
                f"stream_source: Video is OK, using printer video url: {video.video_url}"
            )
            return video.video_url

        LOGGER.error(f"stream_source: Failed to get video stream: {video.status}")
        return None

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse:
        """Generate an HTTP MJPEG stream from the camera."""
        stream_url = await self._get_stream_url()
        if not stream_url:
            return web.Response(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="Stream URL not available",
            )

        ffmpeg_manager = self.hass.data[DOMAIN]
        stream = CameraMjpeg(ffmpeg_manager.binary)
        await stream.open_camera(stream_url, extra_cmd=self._extra_ffmpeg_arguments)

        try:
            stream_reader = await stream.get_reader()
            return await async_aiohttp_proxy_stream(
                self.hass,
                request,
                stream_reader,
                ffmpeg_manager.ffmpeg_stream_content_type,
            )
        finally:
            await stream.close()

    async def stream_source(self) -> str | None:
        """Return the source of the stream."""
        return await self._get_stream_url()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera."""
        stream_url = await self._get_stream_url()
        if not stream_url:
            return None

        try:
            return await async_get_image(
                self.hass,
                input_source=stream_url,
            )
        except Exception as e:
            LOGGER.error(
                "Failed to get camera image via ffmpeg (ffmpeg may be missing): %s", e
            )
            return None

    @property
    def available(self) -> bool:
        """Return whether the camera entity is currently available."""
        LOGGER.debug(
            f"ElegooStreamCamera.available: {self._printer_client.printer_data.video.video_url}"
        )
        return (
            super().available
            and self._printer_client.printer_data.attributes.num_video_stream_connected
            <= 2
        )


class ElegooMjpegCamera(ElegooPrinterEntity, MjpegCamera):
    """Representation of an MjpegCamera."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """Initialize an Elegoo MJPEG camera entity.

        Args:
            hass: The Home Assistant instance.
            coordinator: The data update coordinator.
            description: The entity description.
        """
        MjpegCamera.__init__(
            self,
            name=f"{description.name}",
            mjpeg_url=f"http://{PROXY_HOST}:{VIDEO_PORT}/{VIDEO_ENDPOINT}",
            still_image_url=None,  # This camera does not have a separate still image URL
            unique_id=coordinator.generate_unique_id(description.key),
        )

        ElegooPrinterEntity.__init__(self, coordinator)
        self.entity_description = description
        self._printer_client: ElegooPrinterClient = (
            coordinator.config_entry.runtime_data.api.client
        )

    async def _update_stream_url(self) -> None:
        """Update the MJPEG stream URL."""
        video = await self._printer_client.get_printer_video(toggle=True)
        if video.status and video.status == ElegooVideoStatus.SUCCESS:
            LOGGER.debug("stream_source: Video is OK, getting stream source")
            if self.coordinator.config_entry.data.get(CONF_PROXY_ENABLED, False):
                LOGGER.debug("stream_source: Proxy is enabled using local video")
                self._mjpeg_url = f"http://{PROXY_HOST}:{VIDEO_PORT}/{VIDEO_ENDPOINT}"
            else:
                LOGGER.debug(
                    "stream_source: Proxy is disabled using printer video url: %s",
                    video.video_url,
                )
                self._mjpeg_url = normalize_video_url(video).video_url
        else:
            LOGGER.error("stream_source: Failed to get video stream: %s", video.status)
            self._mjpeg_url = None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Asynchronously retrieves the current MJPEG stream URL for the printer camera."""
        await self._update_stream_url()
        if not self._mjpeg_url:
            return None
        return await super().async_camera_image(width=width, height=height)

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse:
        """Generate an HTTP MJPEG stream from the camera."""
        await self._update_stream_url()
        return await super().handle_async_mjpeg_stream(request)

    @property
    def available(self) -> bool:
        """Return whether the camera entity is currently available.

        If the entity description specifies an availability function, this function is
        used to determine availability based on the printer's video data. Otherwise,
        falls back to the default availability check.
        """
        return super().available and self.entity_description.available_fn(
            self._printer_client.printer_data.video
        )
