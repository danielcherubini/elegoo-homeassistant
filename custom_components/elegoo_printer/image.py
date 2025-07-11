"""Image platform."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.image import ImageEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from propcache.api import cached_property

from custom_components.elegoo_printer.definitions import (
    ElegooPrinterSensorEntityDescription,
)
from custom_components.elegoo_printer.elegoo_sdcp.models.enums import (
    ElegooMachineStatus,
)
from custom_components.elegoo_printer.entity import ElegooPrinterEntity

from .const import LOGGER
from .definitions import PRINTER_IMAGES

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Asynchronously sets up Elegoo Printer image entities for a Home Assistant config entry.

    Creates and adds a CoverImage entity for each supported printer image, ensuring each entity is updated before being added to the platform.
    """
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

        Sets up the entity with the provided Home Assistant instance, data coordinator, and entity description. Assigns a unique ID, sets the content type to BMP, and records the initial image update timestamp.
        """
        super().__init__(coordinator)
        ImageEntity.__init__(self, hass=hass)
        self.coordinator = coordinator
        self._attr_content_type = "image/bmp"
        self._image_filename = None
        self.entity_description = description
        unique_id = coordinator.generate_unique_id(self.entity_description.key)
        self._attr_unique_id = unique_id
        self._attr_image_last_updated = dt_util.now()
        self.api = coordinator.config_entry.runtime_data.api

    async def async_image(self) -> bytes | None:
        """Return bytes of an image."""
        task = await self.api.async_get_task(include_last_task=False)
        if task and task.thumbnail != self.image_url:
            if thumnail_image := await self.api.async_get_thumbnail_image(task=task):
                self._attr_image_last_updated = thumnail_image.get_last_update_time()
                self._cached_image = thumnail_image.get_image()
                self.image_url = task.thumbnail
                return thumnail_image.get_bytes()

        elif self._cached_image:
            return self._cached_image.content

        return None

    @cached_property
    def content_type(self) -> str:
        """Image content type."""
        return "image/png"

    @cached_property
    def available(self) -> bool:
        """Return if entity is not available"""
        if not super().available:
            return False
        return (
            self.api.printer_data.status.print_info.status
            == ElegooMachineStatus.PRINTING
        )
