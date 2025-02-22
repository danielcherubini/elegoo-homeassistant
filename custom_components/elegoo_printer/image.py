"""Image platform."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.image import ImageEntity

from custom_components.elegoo_printer.definitions import (
    ElegooPrinterSensorEntityDescription,
)
from custom_components.elegoo_printer.entity import ElegooPrinterEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator

COVER_IMAGE_SENSOR = ElegooPrinterSensorEntityDescription(
    key="cover_image",
    translation_key="cover_image",
    value_fn=lambda self: self.coordinator.async_get_current_print_thumbnail(),
    exists_fn=lambda coordinator: coordinator.async_get_current_print_thumbnail()
    is not None,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Elegoo Printer image platform from config entry."""
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator

    if COVER_IMAGE_SENSOR.exists_fn(coordinator):
        cover_image = CoverImage(hass, coordinator, COVER_IMAGE_SENSOR)
        async_add_entities([cover_image])


class CoverImage(ImageEntity, ElegooPrinterEntity):
    """Representation of an image entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """Initialize the image entity."""
        ImageEntity.__init__(self, hass=hass)
        ElegooPrinterEntity.__init__(self, coordinator=coordinator)
        self.coordinator = coordinator
        self._attr_content_type = "image/bmp"
        self._image_filename = None
        self.entity_description = description
        self._attr_unique_id = self.entity_description.key

    @property
    async def async_image(self) -> bytes | None:
        """Return bytes of an image."""
        image_url = await self.coordinator.async_get_current_print_thumbnail()
        if image_url:
            return await self.coordinator.async_get_image(image_url)
        return None
