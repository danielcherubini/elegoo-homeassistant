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
    """Sensor entity description for Elegoo Printers."""

    available_fn: Callable[..., bool] = lambda self: self.coordinator.data
    exists_fn: Callable[..., bool] = lambda _: True
    extra_attributes: Callable[..., dict] = lambda _: {}
    icon_fn: Callable[..., str] = lambda _: "mdi:eye"


PRINTER_ATTRIBUTES: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    ElegooPrinterSensorEntityDescription(
        key="release_film_max",
        name="Release Film Max",
        icon="mdi:film",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda self: self.coordinator.data.attributes.release_film_max,
    ),
    ElegooPrinterSensorEntityDescription(
        key="temp_of_uvled_max",
        name="UV LED Temp Max",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda self: self.coordinator.data.attributes.temp_of_uvled_max,
        exists_fn=lambda self: self.coordinator.data.attributes.temp_of_uvled_max > 0,
        available_fn=lambda self: self.coordinator.data
        and self.coordinator.data.attributes.temp_of_uvled_max > 0,
        entity_registry_enabled_default=False,
    ),
    ElegooPrinterSensorEntityDescription(
        key="video_stream_connected",
        name="Video Stream Connected",
        icon="mdi:camera",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda self: self.coordinator.data.attributes.num_video_stream_connected,  # noqa: E501
    ),
    ElegooPrinterSensorEntityDescription(
        key="video_stream_max",
        name="Video Stream Max",
        icon="mdi:camera",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda self: self.coordinator.data.attributes.max_video_stream_allowed,
    ),
)

PRINTER_STATUS: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    ElegooPrinterSensorEntityDescription(
        key="temp_of_uvled",
        name="UV LED Temp",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda self: self.coordinator.data.status.temp_of_uvled,
    ),
    ElegooPrinterSensorEntityDescription(
        key="total_ticks",
        name="Total Print Time",
        icon="mdi:timer-sand-complete",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda self: self.coordinator.data.status.print_info.total_ticks,
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_ticks",
        name="Current Print Time",
        icon="mdi:progress-clock",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda self: self.coordinator.data.status.print_info.current_ticks,
    ),
    ElegooPrinterSensorEntityDescription(
        key="ticks_remaining",
        name="Remaining Print Time",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda self: self.coordinator.data.status.print_info.remaining_ticks,
    ),
    ElegooPrinterSensorEntityDescription(
        key="total_layers",
        name="Total Layers",
        icon="mdi:eye",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda self: self.coordinator.data.status.print_info.total_layers,
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_layer",
        name="Current Layer",
        icon="mdi:eye",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda self: self.coordinator.data.status.print_info.current_layer,
    ),
    ElegooPrinterSensorEntityDescription(
        key="remaining_layers",
        name="Remaining Layers",
        icon="mdi:eye",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda self: self.coordinator.data.status.print_info.remaining_layers,
    ),
    ElegooPrinterSensorEntityDescription(
        key="percent_complete",
        name="Percent Complete",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda self: self.coordinator.data.status.print_info.percent_complete,
    ),
    ElegooPrinterSensorEntityDescription(
        key="filename",
        name="File Name",
        icon="mdi:file",
        value_fn=lambda self: self.coordinator.data.status.print_info.filename,
        available_fn=lambda self: self.coordinator.data.status
        and self.coordinator.data.status.print_info.filename != "",
    ),
    ElegooPrinterSensorEntityDescription(
        key="print_status",
        name="Print Status",
        icon="mdi:file",
        value_fn=lambda self: self.coordinator.data.status.current_status.name.title(),
        available_fn=lambda self: self.coordinator.data.status.current_status
        is not None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="print_error",
        name="Print Error",
        icon="mdi:file",
        value_fn=lambda self: self.coordinator.data.status.print_info.error_number.name.title(),
        available_fn=lambda self: self.coordinator.data.status.print_info.error_number
        is not None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="release_film",
        name="Release Film",
        icon="mdi:film",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda self: self.coordinator.data.status.release_film,
    ),
    ElegooPrinterSensorEntityDescription(
        key="temp_of_box",
        name="Box Temp",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        exists_fn=lambda self: self.coordinator.data.status.temp_of_box > 0,
        available_fn=lambda self: self.coordinator.data.status.temp_of_box > 0,
        value_fn=lambda self: self.coordinator.data.status.temp_of_box,
    ),
    ElegooPrinterSensorEntityDescription(
        key="temp_target_box",
        name="Box Target Temp",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        exists_fn=lambda self: self.coordinator.data.status.temp_target_box > 0,
        available_fn=lambda self: self.coordinator.data.status.temp_target_box > 0,
        value_fn=lambda self: self.coordinator.data.status.temp_target_box,
    ),
)
