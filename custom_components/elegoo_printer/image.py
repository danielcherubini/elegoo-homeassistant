"""Image platform."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from homeassistant.components.image import ImageEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from PIL import Image

from custom_components.elegoo_printer.api import ElegooPrinterApiClient
from custom_components.elegoo_printer.definitions import (
    ElegooPrinterSensorEntityDescription,
)
from custom_components.elegoo_printer.entity import ElegooPrinterEntity

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

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            self.coordinator.config_entry.runtime_data.client._elegoo_printer.get_current_print_thumbnail()
            is not None
        )

    async def async_image(self) -> bytes | None:
        """Return bytes of an image."""
        if (
            hasattr(self, "entity_description")
            and self.entity_description.value_fn is not None
        ):
            _printer_client: ElegooPrinterApiClient = (
                self.coordinator.config_entry.runtime_data.client
            )
            thumbnail = await _printer_client.async_get_current_thumbnail()
            image_url = self.entity_description.value_fn(thumbnail)
            if image_url != self.image_url:
                self._cached_image = None
                self._attr_image_url = image_url
                self._attr_image_last_updated = dt_util.now()

        return await super().async_image()

    async def _fetch_url(self, url: str):
        """Fetch a URL.

        Chitubox provides 'text/plain' as content type
        so this is a hack to provide a correct image content type
        to Home Assistant.
        """

        response = await super()._fetch_url(url)
        if response:
            response.headers["content-type"] = "image/bmp"

        return response

    async def _async_load_image_from_url(self, url: str):
        """Load an image by url

        Chitubox thumbnail is bitmap, which is no longer/not supported
        by many browsers. This converts the bitmap into png, which is
        widely supported."
        """

        image = await super()._async_load_image_from_url(url)
        if image is not None:
            new_image = Image.open(io.BytesIO(image.content))
            buffer = io.BytesIO()
            new_image.save(buffer, "PNG")
            image.content = buffer.getvalue()
            image.content_type = "image/png"

        return image
