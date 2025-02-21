"""Image platform."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import async_timeout
from homeassistant.components.image import Image, ImageEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.elegoo_printer.definitions import (
    ElegooPrinterSensorEntityDescription,
)
from custom_components.elegoo_printer.elegoo_sdcp.elegoo_printer import (
    ElegooPrinterClientWebsocketError,
)
from custom_components.elegoo_printer.entity import ElegooPrinterEntity

if TYPE_CHECKING:
    import aiohttp
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator

COVER_IMAGE_SENSOR = ElegooPrinterSensorEntityDescription(
    key="cover_image",
    translation_key="cover_image",
    value_fn=lambda self: "",
    exists_fn=lambda coordinator: True,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Everything but the Kitchen Sink config entry."""
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator

    if COVER_IMAGE_SENSOR.exists_fn(coordinator):
        cover_image = CoverImage(hass, coordinator, COVER_IMAGE_SENSOR)
        async_add_entities([cover_image])


def _verify_response_or_raise(response: aiohttp.ClientResponse) -> None:
    """Verify that the response is valid."""
    if response.status in (401, 403):
        msg = "Invalid credentials"
        raise ElegooPrinterClientWebsocketError(
            msg,
        )
    response.raise_for_status()


class IntegrationBlueprintApiClientError(Exception):
    """Exception to indicate a general API error."""


class IntegrationBlueprintApiClientCommunicationError(
    IntegrationBlueprintApiClientError,
):
    """Exception to indicate a communication error."""


class IntegrationBlueprintApiClientAuthenticationError(
    IntegrationBlueprintApiClientError,
):
    """Exception to indicate an authentication error."""


class CoverImage(ImageEntity, ElegooPrinterEntity):
    """Representation of an image entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """Initialize the image entity."""
        super().__init__(hass=hass)
        super(ElegooPrinterEntity, self).__init__(coordinator=coordinator)
        self.coordinator = coordinator
        self.entity_description = description
        self._attr_content_type = "image/bmp"
        self._image_filename = "cover_image"
        self._session = async_get_clientsession(hass)
        self._attr_unique_id = self.entity_description.key

    async def get_image(self) -> bytes | None:
        """Update the image from the URL."""
        url = "http://10.0.0.212:3030/media/mmcblk0p1/history_image/6c4336e6-eecb-11ef-ae89-40f4c926a324.bmp"
        print(url)
        if response := await self._api_wrapper("get", url):
            print(response)
            image = Image(content_type="image/bmp", content=response.content)
            self._cached_image = image
            self._attr_content_type = image.content_type
            return image.content
        return None

    @property
    async def async_image(self) -> bytes | None:
        """Return image."""
        return await self.get_image()

    # @property
    # def image_last_updated(self) -> datetime | None:
    #     """The time when the image was last updated."""
    #     return self.coordinator.get_model().cover_image.get_last_update_time()

    async def _api_wrapper(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        headers: dict | None = None,
    ) -> Any:
        """Get information from the API."""
        try:
            async with async_timeout.timeout(10):
                response = await self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data,
                )
                _verify_response_or_raise(response)
                return await response.json()

        except TimeoutError as exception:
            msg = f"Timeout error fetching information - {exception}"
            raise IntegrationBlueprintApiClientCommunicationError(
                msg,
            ) from exception
        except Exception as exception:  # pylint: disable=broad-except
            msg = f"Something really wrong happened! - {exception}"
            raise IntegrationBlueprintApiClientError(
                msg,
            ) from exception
