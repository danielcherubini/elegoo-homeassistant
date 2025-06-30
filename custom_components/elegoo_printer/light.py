from typing import Any

from homeassistant.components.light import ATTR_RGB_COLOR, LightEntity
from homeassistant.components.light.const import ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from propcache.api import cached_property

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
    """Set up the Elegoo printer light entities from a config entry."""
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator

    FDM_PRINTER: bool = coordinator.config_entry.data.get(CONF_CENTAURI_CARBON, False)

    # Check if the printer supports lights before adding entities
    if FDM_PRINTER:
        entities = [
            ElegooLight(coordinator, description) for description in PRINTER_FDM_LIGHTS
        ]
        async_add_entities(entities)


class ElegooLight(ElegooPrinterEntity, LightEntity):
    """Representation of an Elegoo printer light (either On/Off or RGB)."""

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterLightEntityDescription,
    ) -> None:
        """Initialize the light entity."""
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
        """Helper property to get the light status from the latest coordinator data."""
        return self._elegoo_printer_client.printer_data.status.light_status

    @cached_property
    def is_on(self) -> bool | None:
        """Return true if the light is on."""
        if not self.light_status:
            return None

        if self.entity_description.key == "rgb_light":
            # The RGB light is on if any of its color components have a value > 0
            return any(c > 0 for c in self.light_status.rgb_light)

        # For the standard on/off light
        return bool(self.light_status.second_light)

    @cached_property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the current RGB color value."""
        if self.entity_description.key == "rgb_light" and self.light_status:
            rgb = self.light_status.rgb_light
            if isinstance(rgb, list) and len(rgb) == 3:
                return (rgb[0], rgb[1], rgb[2])
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
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
        """Turn the light off."""
        light_status = self.light_status

        if self.entity_description.key == "rgb_light":
            light_status.rgb_light = [0, 0, 0]
        else:  # This is the on/off light
            light_status.second_light = False

        LOGGER.debug("Turning off light '%s'", self.name)
        self._elegoo_printer_client.set_light_status(light_status)
        await self.coordinator.async_request_refresh()

    @cached_property
    def available(self) -> bool:
        """
        Return whether the camera entity is currently available.

        If the entity description specifies an availability function, this function is used to determine availability based on the printer's video data. Otherwise, falls back to the default availability check.
        """
        if self.entity_description.available_fn is not None:
            return self.entity_description.available_fn(self.light_status)
        return super().available
