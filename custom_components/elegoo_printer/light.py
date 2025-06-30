from typing import Any

from homeassistant.components.light import ATTR_RGB_COLOR, LightEntity
from homeassistant.components.light.const import ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.elegoo_printer.const import CONF_CENTAURI_CARBON, LOGGER
from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.data import ElegooPrinterConfigEntry
from custom_components.elegoo_printer.definitions import (
    PRINTER_FDM_LIGHTS,
    ElegooPrinterLightEntityDescription,
)
from custom_components.elegoo_printer.elegoo_sdcp.client import ElegooPrinterClient
from custom_components.elegoo_printer.elegoo_sdcp.models.status import LightStatus
from custom_components.elegoo_printer.entity import ElegooPrinterEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Asynchronously sets up Elegoo printer light entities for an FDM printer configuration entry.

    Adds light entities to Home Assistant if the printer is identified as an FDM model supporting lights.
    """
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator
    fdm_printer: bool = coordinator.config_entry.data.get(CONF_CENTAURI_CARBON, False)

    # Check if the printer supports lights before adding entities
    if fdm_printer:
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
        Initialize an Elegoo printer light entity with the provided coordinator and entity description.

        Sets up unique identification, display name, and supported color modes based on the light type (RGB or On/Off).
        """
        super().__init__(coordinator)
        self.entity_description = description
        self._elegoo_printer_client: ElegooPrinterClient = (
            coordinator.config_entry.runtime_data.client._elegoo_printer
        )
        # Set a unique ID and a friendly name for the entity
        self._attr_unique_id = coordinator.generate_unique_id(
            self.entity_description.key
        )
        self._attr_name = f"{description.name}"

        # Configure color modes based on what this entity represents (from its description)
        if self.entity_description.key == "rgb_light":
            self._attr_supported_color_modes = {ColorMode.RGB}
            self._attr_color_mode = ColorMode.RGB
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

    @property
    def light_status(self) -> LightStatus:
        """
        Returns the current light status from the latest printer data.
        """
        return self._elegoo_printer_client.printer_data.status.light_status

    @property
    def is_on(self) -> bool | None:
        """
        Indicates whether the light entity is currently on.

        Returns:
            True if the light is on, False if off, or None if the light status is unavailable.
        """
        # For the standard on/off light
        return bool(self.entity_description.value_fn(self.light_status))

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """
        Returns the current RGB color of the light as a tuple if the entity represents an RGB light and status is available; otherwise returns None.
        """
        if self.entity_description.key == "rgb_light" and self.light_status:
            rgb = self.light_status.rgb_light
            if isinstance(rgb, list) and len(rgb) == 3:
                return (rgb[0], rgb[1], rgb[2])
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """
        Asynchronously turns the light on.

        For RGB lights, sets the color to the specified RGB value or defaults to white if none is provided. For on/off lights, enables the light. Updates the printer with the new light status and requests a state refresh.
        """
        # Get the current status of ALL lights to avoid overriding the other light's state
        light_status = self.light_status

        if self.entity_description.key == "rgb_light":
            # If a color is specified in the service call (e.g., from a color wheel), use it.
            # Otherwise, when just turning "on", default to white.
            rgb_color = kwargs.get(ATTR_RGB_COLOR, (255, 255, 255))
            light_status.rgb_light = list(rgb_color)
        else:  # This is the on/off light
            light_status.second_light = True

        LOGGER.debug("Turning on light '%s'", self.name)
        self._elegoo_printer_client.set_light_status(light_status)
        # Request a refresh from the coordinator to get the updated state from the printer
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """
        Asynchronously turns off the printer light.

        For RGB lights, sets all color components to zero. For on/off lights, disables the light. Updates the printer with the new state and requests a data refresh.
        """
        light_status = self.light_status

        if self.entity_description.key == "rgb_light":
            light_status.rgb_light = [0, 0, 0]
        else:  # This is the on/off light
            light_status.second_light = False

        LOGGER.debug("Turning off light '%s'", self.name)
        self._elegoo_printer_client.set_light_status(light_status)
        await self.coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        """
        Return whether the light entity is currently available.

        If the entity description provides an availability function, uses it with the current light status; otherwise, falls back to the base class availability check.
        """
        if self.entity_description.available_fn is not None:
            return self.entity_description.available_fn(self.light_status)
        return super().available
