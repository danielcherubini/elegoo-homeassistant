from typing import Any

from homeassistant.components.light import LightEntity
from homeassistant.components.light.const import ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from propcache.api import cached_property

from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.data import ElegooPrinterConfigEntry
from custom_components.elegoo_printer.definitions import (
    PRINTER_FDM_LIGHTS,
    ElegooPrinterLightEntityDescription,
)
from custom_components.elegoo_printer.elegoo_sdcp.client import ElegooPrinterClient
from custom_components.elegoo_printer.elegoo_sdcp.models.light import LightStatus
from custom_components.elegoo_printer.entity import ElegooPrinterEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator
    for light in PRINTER_FDM_LIGHTS:
        async_add_entities([ElegooLight(hass, coordinator, light)])


class ElegooLight(ElegooPrinterEntity, LightEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterLightEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
        self._elegoo_printer_client: ElegooPrinterClient = (
            coordinator.config_entry.runtime_data.client._elegoo_printer
        )

        self.entity_description = description

        unique_id = coordinator.generate_unique_id(self.entity_description.key)
        self._attr_unique_id = unique_id

        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF

    @property
    def available(self) -> bool:
        """
        Return whether the camera entity is currently available.

        If the entity description specifies an availability function, this function is used to determine availability based on the printer's video data. Otherwise, falls back to the default availability check.
        """
        if self.entity_description.available_fn is not None:
            return self.entity_description.available_fn(
                self._elegoo_printer_client.printer_data.status.light_status.second_light
            )
        return super().available

    @cached_property
    def is_on(self) -> bool:
        """Return true if the light is on.

        This property reads the state from the coordinator's data.
        You'll need to adjust `self.coordinator.data.status.light_on`
        to the actual attribute path in your data structure.
        """
        return bool(
            self._elegoo_printer_client.printer_data.status.light_status.second_light
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""

        light_status = LightStatus(False, [0, 0, 0])
        self._elegoo_printer_client.set_light_status(light_status)
        self._attr_is_on = light_status.second_light
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        light_status = LightStatus(False, [0, 0, 0])
        self._elegoo_printer_client.set_light_status(light_status)

        self._attr_is_on = light_status.second_light
        self.async_write_ha_state()
