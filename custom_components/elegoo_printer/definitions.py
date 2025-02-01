"""Definitions for the Elegoo Printer Integration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.sensor.const import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.helpers.typing import StateType


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


PRINTER_SENSORS: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    ElegooPrinterSensorEntityDescription(
        key="temp_of_uvled",
        name="UV LED Temp",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda self: self.coordinator.data.temp_of_uvled,
    ),
    ElegooPrinterSensorEntityDescription(
        key="total_ticks",
        name="Total Print Time",
        icon="mdi:timer-sand-complete",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        value_fn=lambda self: self.coordinator.data.print_info.total_ticks,
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_ticks",
        name="Current Print Time",
        icon="mdi:progress-clock",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        value_fn=lambda self: self.coordinator.data.print_info.current_ticks,
    ),
    ElegooPrinterSensorEntityDescription(
        key="ticks_remaining",
        name="Remaining Print Time",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        value_fn=lambda self: self.coordinator.data.print_info.remaining_ticks,
    ),
    ElegooPrinterSensorEntityDescription(
        key="total_layers",
        name="Total Layers",
        icon="mdi:eye",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda self: self.coordinator.data.print_info.total_layers,
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_layer",
        name="Current Layer",
        icon="mdi:eye",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda self: self.coordinator.data.print_info.current_layer,
    ),
    ElegooPrinterSensorEntityDescription(
        key="remaining_layers",
        name="Remaining Layers",
        icon="mdi:eye",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda self: self.coordinator.data.print_info.remaining_layers,
    ),
    ElegooPrinterSensorEntityDescription(
        key="percent_complete",
        name="Percent Complete",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda self: self.coordinator.data.print_info.percent_complete,
    ),
    ElegooPrinterSensorEntityDescription(
        key="filename",
        name="File Name",
        icon="mdi:file",
        value_fn=lambda self: self.coordinator.data.print_info.filename,
        available_fn=lambda self: self.coordinator.data.print_info.filename != "",
    ),
    ElegooPrinterSensorEntityDescription(
        key="print_status",
        name="Print Status",
        icon="mdi:file",
        value_fn=lambda self: self.coordinator.data.current_status.name,
        available_fn=lambda self: self.coordinator.data.current_status is not None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="print_error",
        name="Print Error",
        icon="mdi:file",
        value_fn=lambda self: self.coordinator.data.print_info.error_number.name,
        available_fn=lambda self: self.coordinator.data.print_info.error_number
        is not None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="release_film",
        name="Release Film",
        icon="mdi:film",
        value_fn=lambda self: self.coordinator.data.print_info.release_film,
    ),
)
