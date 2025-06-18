"""Image platform."""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from homeassistant.components.image import ImageEntity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from PIL import Image

from custom_components.elegoo_printer.definitions import (
    ElegooPrinterSensorEntityDescription,
)
from custom_components.elegoo_printer.entity import ElegooPrinterEntity

from .definitions import PRINTER_IMAGES

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Elegoo Printer image platform from config entry."""
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator

    for image in PRINTER_IMAGES:
        async_add_entities(
            [CoverImage(hass, coordinator, image)],
            update_before_add=True,
        )


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
        self._attr_image_last_updated = dt_util.now()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if (
            hasattr(self, "entity_description")
            and self.entity_description.available_fn is not None
        ):
            _printer = self.coordinator.config_entry.runtime_data.client._elegoo_printer
            self._attr_available = self.entity_description.available_fn(_printer)

        return super().available

    async def async_image(self) -> bytes | None:
        """Return bytes of an image."""
        if (
            hasattr(self, "entity_description")
            and self.entity_description.value_fn is not None
        ):
            _printer = self.coordinator.config_entry.runtime_data.client._elegoo_printer
            image_url = await self.entity_description.value_fn(_printer)
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
