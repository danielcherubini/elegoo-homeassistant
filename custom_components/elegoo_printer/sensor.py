"""Sensor platform for elegoo_printer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTime

from custom_components.elegoo_printer.const import USE_SECONDS

from .definitions import (
    PRINTER_ATTRIBUTES,
    PRINTER_STATUS,
    ElegooPrinterSensorEntityDescription,
)
from .entity import ElegooPrinterEntity

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.typing import StateType

    from .coordinator import ElegooDataUpdateCoordinator
    from .data import ElegooPrinterConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 Unused function argument: `hass`
    entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    for entity_description in PRINTER_STATUS:
        async_add_entities(
            [
                ElegooPrinterSensor(
                    coordinator=entry.runtime_data.coordinator,
                    entity_description=entity_description,
                )
            ],
            update_before_add=True,
        )
    for entity_description in PRINTER_ATTRIBUTES:
        async_add_entities(
            [
                ElegooPrinterSensor(
                    coordinator=entry.runtime_data.coordinator,
                    entity_description=entity_description,
                )
            ],
            update_before_add=True,
        )


class ElegooPrinterSensor(ElegooPrinterEntity, SensorEntity):
    """elegoo_printer Sensor class."""

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        entity_description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """Initialize the sensor class."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = self.entity_description.key

        """This block fixes the issues with the Centurai Carbon"""
        if (
            self.entity_description.device_class == SensorDeviceClass.DURATION
            and coordinator.config_entry.data.get(USE_SECONDS, False)
        ):
            self._attr_native_unit_of_measurement = UnitOfTime.SECONDS

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state attributes."""
        return self.entity_description.extra_attributes(self)

    @property
    def native_value(self) -> datetime | StateType:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.entity_description.available_fn(self)
