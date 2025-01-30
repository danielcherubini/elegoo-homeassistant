"""Sensor platform for elegoo_printer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.components.sensor.const import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature, UnitOfTime

from .entity import ElegooPrinterEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import ElegooDataUpdateCoordinator
    from .data import ElegooPrinterConfigEntry


@dataclass
class ElegooPrinterSensorEntityDescriptionMixin:
    """Mixin for required keys."""

    value_fn: Callable[..., datetime | StateType]


@dataclass
class ElegooPrinterSensorEntityDescription(
    SensorEntityDescription, ElegooPrinterSensorEntityDescriptionMixin
):
    """Sensor entity description for Bambu Lab."""

    available_fn: Callable[..., bool] = lambda _: True
    exists_fn: Callable[..., bool] = lambda _: True
    extra_attributes: Callable[..., dict] = lambda _: {}
    icon_fn: Callable[..., str] = lambda _: None


ENTITY_DESCRIPTIONS: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    ElegooPrinterSensorEntityDescription(
        key="temp_of_uvled",
        name="Elegoo UV Temp",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda self: self.coordinator.data.temp_of_uvled,
    ),
    ElegooPrinterSensorEntityDescription(
        key="total_ticks",
        name="Elegoo Total Ticks",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        value_fn=lambda self: self.coordinator.data.print_info.total_ticks,
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_ticks",
        name="Elegoo Current Ticks",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        value_fn=lambda self: self.coordinator.data.print_info.current_ticks,
    ),
    # ElegooPrinterSensorEntityDescription(
    #     key="ticks_remaining",
    #     name="Elegoo Remaining Ticks",
    #     icon="mdi:thermometer",
    #     device_class=SensorDeviceClass.DURATION,
    #     state_class=SensorStateClass.TOTAL,
    #     native_unit_of_measurement=UnitOfTime.MILLISECONDS,
    #     value_fn=lambda self: self.coordinator.data.calculate_time_remaining(),
    # ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 Unused function argument: `hass`
    entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    for entity_description in ENTITY_DESCRIPTIONS:
        async_add_entities(
            [
                ElegooPrinterSensor(
                    coordinator=entry.runtime_data.coordinator,
                    entity_description=entity_description,
                )
            ]
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

    @property
    def native_value(self) -> str | None:
        """Return the native value of the sensor."""
        return self.entity_description.value_fn(self)
