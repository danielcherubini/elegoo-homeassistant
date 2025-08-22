"""Platform for light integration."""

from typing import TYPE_CHECKING, Any

from homeassistant.components.light import LightEntity
from homeassistant.components.light.const import ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.data import ElegooPrinterConfigEntry
from custom_components.elegoo_printer.definitions import (
    PRINTER_FDM_LIGHTS,
    ElegooPrinterLightEntityDescription,
)
from custom_components.elegoo_printer.entity import ElegooPrinterEntity
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.status import LightStatus

from .const import LOGGER

if TYPE_CHECKING:
    from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    config_entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Asynchronously sets up Elegoo printer light entities for FDM printers in Home Assistant.

    Creates and adds a light entity for each supported FDM light type if the connected printer is identified as an FDM model.
    """  # noqa: E501
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator
    printer_type = coordinator.config_entry.runtime_data.api.printer.printer_type

    # Check if the printer supports lights before adding entities
    if printer_type == PrinterType.FDM:
        LOGGER.debug(f"Adding {len(PRINTER_FDM_LIGHTS)} light entities")
        for light in PRINTER_FDM_LIGHTS:
            async_add_entities(
                [ElegooLight(coordinator, light)], update_before_add=True
            )


class ElegooLight(ElegooPrinterEntity, LightEntity):
    """Representation of an Elegoo printer light (either On/Off or RGB)."""

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterLightEntityDescription,
    ) -> None:
        """
        Initialize an Elegoo printer light entity with the given data coordinator and entity description.

        Configures the entity's unique ID, display name, and supported color modes based on whether it represents an RGB or on/off light.
        """  # noqa: E501
        super().__init__(coordinator)
        self.entity_description: ElegooPrinterLightEntityDescription = description
        self._elegoo_printer_client: ElegooPrinterClient = (
            coordinator.config_entry.runtime_data.api.client
        )
        # Set a unique ID and a friendly name for the entity
        self._attr_unique_id = coordinator.generate_unique_id(
            self.entity_description.key
        )
        self._attr_name = f"{description.name}"

        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF

    @property
    def light_status(self) -> LightStatus:
        """Returns the current light status from the latest printer data."""
        return self._elegoo_printer_client.printer_data.status.light_status

    @property
    def is_on(self) -> bool | None:
        """
        Indicates whether the light is currently on.

        Returns:
            True if the light is on, False if it is off, or None if the light status is unavailable.

        """  # noqa: E501
        # For the standard on/off light
        return self.entity_description.value_fn(self.light_status)

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """
        Asynchronously turns the light on.

        For RGB lights, sets the color to the specified RGB value
        or defaults to white if none is provided. For on/off lights, enables the light.
        Updates the printer with the new light status and requests a state refresh.
        """
        light_status = self.light_status
        light_status.second_light = True
        light_status.rgb_light = [255, 255, 255]
        await self._elegoo_printer_client.set_light_status(light_status)

        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """
        Asynchronously turns off the printer light.

        For RGB lights, sets all color channels to zero.
        For on/off lights, turns the light off.
        Updates the printer with the new state and requests a data refresh.
        """
        light_status = self.light_status
        light_status.second_light = False
        light_status.rgb_light = [0, 0, 0]
        await self._elegoo_printer_client.set_light_status(light_status)

        await self.coordinator.async_request_refresh()
