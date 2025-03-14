"""Elegoo Camera."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.helpers.reload import async_setup_reload_service

from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator

from .definitions import ElegooPrinterSensorEntityDescription
from .entity import ElegooPrinterEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import ElegooPrinterConfigEntry


_LOGGER = logging.getLogger(__name__)

CAMERA_SENSOR: list[ElegooPrinterSensorEntityDescription] = [
    ElegooPrinterSensorEntityDescription(
        key="camera_image",
        name="Camera Image",
        value_fn=lambda self: self.coordinator.data.camera_image,
        available_fn=lambda self: (
            self.coordinator.data.attributes.num_video_stream_connected
            < self.coordinator.data.attributes.max_video_stream_allowed
        ),
        icon="mdi:camera",
    )
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Camera platform."""
    # Reload the integration if any changes are detected in the config.
    await async_setup_reload_service(hass, "elegoo_printer", ["camera"])

    for entity_description in CAMERA_SENSOR:
        async_add_entities(
            [
                ElegooCamera(
                    hass=hass,
                    coordinator=entry.runtime_data.coordinator,
                    entity_description=entity_description,
                )
            ],
            update_before_add=False,
        )


class ElegooCamera(ElegooPrinterEntity, Camera):
    """elegoo_printer Camera class."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ElegooDataUpdateCoordinator,
        entity_description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """Initialize the camera."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._name = "camera"
        self._unique_id: str = self.entity_description.key
        self.hass: HomeAssistant = hass
        self._rtsp_url = "rtsp://10.0.0.212:554/video"
        self._process = None

    @property
    def name(self):
        """Return the name of the camera."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique ID of the camera."""
        return self._unique_id

    @property
    def supported_features(self):
        """Return supported features."""
        return CameraEntityFeature.STREAM

    async def async_stream_source(self):
        """Return the source of the stream."""
        return self._rtsp_url

    async def async_added_to_hass(self):
        """Start the ffmpeg process when entity is added."""
        command = [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-allowed_media_types",
            "video",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-timeout",
            "5000000",
            "-i",
            self._rtsp_url,
            "-c:v",
            "copy",
            "-an",
            "-user_agent",
            "hacs/elegoo_printer",
            "-rtsp_transport",
            "udp",
        ]
        _LOGGER.debug(f"Starting ffmpeg with command: {' '.join(command)}")
        try:
            self._process = await self.hass.async_add_executor_job(
                subprocess.Popen, command, stderr=subprocess.PIPE
            )
        except Exception as ex:
            _LOGGER.error(f"Error starting ffmpeg: {ex}")

    async def async_will_remove_from_hass(self):
        """Stop the ffmpeg process when entity is removed."""
        if self._process and self._process.poll() is None:
            _LOGGER.debug("Stopping ffmpeg process.")
            self._process.terminate()
            await self.hass.async_add_executor_job(self._process.wait)
            _LOGGER.debug("ffmpeg process stopped.")
