"""Sensor platform for elegoo_printer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberEntityDescription

from .entity import ElegooPrinterEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import ElegooDataUpdateCoordinator
    from .data import ElegooPrinterConfigEntry

ENTITY_DESCRIPTIONS = (
    NumberEntity(
        key="elegoo_printer",
        name="Elegoo UV Temp",
        icon="mdi:thermometer",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 Unused function argument: `hass`
    entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    async_add_entities(
        ElegooPrinterNumber(
            coordinator=entry.runtime_data.coordinator,
            entity_description=entity_description,
        )
        for entity_description in ENTITY_DESCRIPTIONS
    )


class ElegooPrinterNumber(ElegooPrinterEntity, NumberEntity):
    """elegoo_printer Sensor class."""

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        entity_description: NumberEntityDescription,
    ) -> None:
        """Initialize the sensor class."""
        super().__init__(coordinator)
        self.entity_description = entity_description

    @property
    def native_value(self) -> str | None:
        """Return the native value of the sensor."""
        return self.coordinator.data
