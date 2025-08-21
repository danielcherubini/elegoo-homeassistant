"""Binary Sensor platform for elegoo_printer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)

from .const import LOGGER
from .definitions import (
    PRINTER_ATTRIBUTES_BINARY_COMMON,
    ElegooPrinterBinarySensorEntityDescription,
)
from .entity import ElegooPrinterEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import ElegooDataUpdateCoordinator
    from .data import ElegooPrinterConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator: ElegooDataUpdateCoordinator = entry.runtime_data.coordinator

    sensors: list[ElegooPrinterBinarySensorEntityDescription] = []
    sensors.extend(PRINTER_ATTRIBUTES_BINARY_COMMON)

    LOGGER.debug(f"Adding {len(sensors)} binary sensor entities")
    async_add_entities(
        [
            ElegooPrinterBinarySensor(
                coordinator=coordinator,
                entity_description=entity_description,
            )
            for entity_description in sensors
        ],
        update_before_add=True,
    )


class ElegooPrinterBinarySensor(ElegooPrinterEntity, BinarySensorEntity):
    """elegoo_printer binary_sensor class."""

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        entity_description: ElegooPrinterBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary_sensor class."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = coordinator.generate_unique_id(
            self.entity_description.key
        )

    @property
    def is_on(self) -> bool:
        """Return true if the binary_sensor is on."""
        if self.coordinator.data:
            return self.entity_description.value_fn(self.coordinator.data)
        return False

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            super().available
            and self.coordinator.data
            and self.entity_description.available_fn(self.coordinator.data)
        )
