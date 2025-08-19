"""Fan platform for Elegoo printers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.elegoo_printer.sdcp.models.enums import (
    ElegooFan,
    PrinterType,
)

from .definitions import FANS, ElegooPrinterFanEntityDescription
from .entity import ElegooPrinterEntity

if TYPE_CHECKING:
    from .coordinator import ElegooDataUpdateCoordinator
    from .data import ElegooPrinterConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Elegoo printer fan entities."""
    coordinator = entry.runtime_data.coordinator
    printer_type = coordinator.config_entry.runtime_data.api.printer.printer_type

    if printer_type == PrinterType.FDM:
        async_add_entities(
            ElegooPrinterFan(coordinator, description) for description in FANS
        )


class ElegooPrinterFan(ElegooPrinterEntity, FanEntity):
    """Representation of an Elegoo printer fan."""

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterFanEntityDescription,
    ) -> None:
        """Initialize the fan entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = coordinator.generate_unique_id(description.key)

    @property
    def is_on(self) -> bool:
        """Return true if the fan is on."""
        if self.coordinator.data:
            return self.entity_description.value_fn(self.coordinator.data)
        return False

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on."""
        if percentage is None:
            percentage = 100  # Default to 100% if no percentage is specified
        await self.coordinator.config_entry.runtime_data.api.set_fan_speed(
            percentage, ElegooFan.from_key(self.entity_description.key)
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        await self.coordinator.config_entry.runtime_data.api.set_fan_speed(
            0, ElegooFan.from_key(self.entity_description.key)
        )
        await self.coordinator.async_request_refresh()

    @property
    def supported_features(self) -> FanEntityFeature:
        """Return the list of supported features."""
        return self.entity_description.supported_features

    @property
    def percentage(self) -> int | None:
        """Return the current speed."""
        if self.coordinator.data:
            return self.entity_description.percentage_fn(self.coordinator.data)
        return None

    @property
    def percentage_step(self) -> int:
        """Return the step for percentage."""
        return 1

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        percentage = self.percentage
        if percentage == 0:
            return "Off"
        elif percentage == 100:
            return "On"
        return None

    @property
    def preset_modes(self) -> list[str] | None:
        """Return the list of available preset modes."""
        return ["On", "Off"]

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed of the fan."""
        await self.coordinator.config_entry.runtime_data.api.set_fan_speed(
            percentage, ElegooFan.from_key(self.entity_description.key)
        )
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the fan."""
        if preset_mode == "On":
            await self.async_turn_on(percentage=100)
        elif preset_mode == "Off":
            await self.async_turn_off()
