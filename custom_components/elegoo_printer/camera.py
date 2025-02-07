"""Elegoo Camera."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.camera.const import StreamType

from .definitions import ElegooPrinterSensorEntityDescription
from .entity import ElegooPrinterEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import ElegooDataUpdateCoordinator
    from .data import ElegooPrinterConfigEntry


CAMERA_SENSOR = [
    ElegooPrinterSensorEntityDescription(
        key="camera_image",
        name="Camera Image",
        value_fn=lambda self: self.coordinator.data.camera_image,
        available_fn=lambda self: self.coordinator.data.attributes.num_video_stream_connected  # noqa: E501
        < self.coordinator.data.attributes.max_video_stream_allowed,
        icon="mdi:camera",
    )
]


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 Unused function argument: `hass`
    entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Camera platform."""
    for entity_description in CAMERA_SENSOR:
        async_add_entities(
            [
                ElegooCamera(
                    coordinator=entry.runtime_data.coordinator,
                    entity_description=entity_description,
                )
            ],
            update_before_add=False,
        )


class ElegooCamera(ElegooPrinterEntity, Camera):
    """elegoo_printer Camera class."""

    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_frontend_stream_type = StreamType.HLS

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        entity_description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """Initialize the sensor class."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = self.entity_description.key
        Camera.__init__(self)

    @property
    def is_streaming(self) -> bool:
        """Return if stream is available."""
        return self.available

    @property
    def is_recording(self) -> bool:
        """Return if recording."""
        return False

    @property
    def use_stream_for_stills(self) -> bool:
        """Return stream for stills."""
        return False

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.entity_description.available_fn(self)

    async def stream_source(self) -> str | None:
        """Return the stream source."""
        mainboard_ip = self.coordinator.data.attributes.mainboard_ip
        return f"rtsp://{mainboard_ip}:554/video"
