"""Image platform."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.image import Image, ImageEntity

from custom_components.elegoo_printer.entity import ElegooPrinterEntity
from custom_components.elegoo_printer.sdcp.models.enums import (
    ElegooMachineStatus,
)

from .const import LOGGER
from .definitions import PRINTER_IMAGES

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator
    from custom_components.elegoo_printer.definitions import (
        ElegooPrinterSensorEntityDescription,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Asynchronously sets up Elegoo Printer image entities for a Home Assistant config entry.

    Creates and adds a CoverImage entity for each supported printer image, ensuring each entity is updated before being added to the platform.
    """  # noqa: E501
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator

    LOGGER.debug(f"Adding {len(PRINTER_IMAGES)} image entities")
    for image in PRINTER_IMAGES:
        async_add_entities(
            [CoverImage(hass, coordinator, image)],
            update_before_add=True,
        )


class CoverImage(ElegooPrinterEntity, ImageEntity):
    """Representation of an image entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """
        Initialize a CoverImage entity for the Elegoo Printer.

        Sets up the entity with the provided Home Assistant instance, data coordinator, and entity description.
        Assigns a unique ID, sets the content type to PNG, and records the initial image update timestamp.
        """  # noqa: E501
        super().__init__(coordinator)
        ImageEntity.__init__(self, hass=hass)
        self.coordinator = coordinator
        self._image_filename = None
        self.image_url = None
        self._cached_image: Image | None = None
        self.entity_description = description
        unique_id = coordinator.generate_unique_id(self.entity_description.key)
        self._attr_unique_id = unique_id
        self._attr_image_last_updated: datetime | None = None
        self.api = coordinator.config_entry.runtime_data.api

    async def async_image(self) -> bytes | None:
        """Return bytes of an image."""
        task = await self.api.async_get_task(include_last_task=False)
        if task and task.thumbnail != self.image_url:
            if thumbnail_image := await self.api.async_get_thumbnail_image(task=task):
                self._attr_image_last_updated = thumbnail_image.get_last_update_time()
                self._cached_image = thumbnail_image.get_image()
                self.image_url = task.thumbnail
                self._attr_content_type = thumbnail_image.get_content_type()
                return thumbnail_image.get_bytes()

        elif self._cached_image:
            return self._cached_image.content

        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            super().available
            and self.coordinator.data
            and self.coordinator.data.status
            and self.coordinator.data.status.current_status
            == ElegooMachineStatus.PRINTING
        )
