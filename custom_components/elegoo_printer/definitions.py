"""Definitions for the Elegoo Printer Integration."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.button import ButtonEntityDescription
from homeassistant.components.light import LightEntityDescription
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.sensor.const import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfTemperature, UnitOfTime
from homeassistant.helpers.typing import StateType

from custom_components.elegoo_printer.elegoo_sdcp.models.enums import (
    ElegooMachineStatus,
    ElegooPrintStatus,
    ElegooVideoStatus,
)


@dataclass
class ElegooPrinterSensorEntityDescriptionMixin:
    """Mixin for required keys."""

    value_fn: Callable[..., datetime | StateType]


@dataclass
class ElegooPrinterSensorEntityDescription(
    SensorEntityDescription, ElegooPrinterSensorEntityDescriptionMixin
):
    """Sensor entity description for Elegoo Printers."""

    available_fn: Callable[..., bool] = lambda printer_data: printer_data
    exists_fn: Callable[..., bool] = lambda _: True
    extra_attributes: Callable[..., dict] = lambda _: {}
    icon_fn: Callable[..., str] = lambda _: "mdi:eye"


@dataclass
class ElegooPrinterLightEntityDescription(
    LightEntityDescription, ElegooPrinterSensorEntityDescriptionMixin
):
    """Light entity description for Elegoo Printers."""

    available_fn: Callable[..., bool] = lambda printer_data: printer_data
    exists_fn: Callable[..., bool] = lambda _: True
    extra_attributes: Callable[..., dict] = lambda _: {}
    icon_fn: Callable[..., str] = lambda _: "mdi:lightbulb"


@dataclass
class ElegooPrinterButtonEntityDescription(ButtonEntityDescription):
    """Button entity description for Elegoo Printers."""

    action_fn: Callable[..., Coroutine[Any, Any, None]] = lambda _: None
    available_fn: Callable[..., bool] = lambda printer_data: printer_data


PRINTER_ATTRIBUTES_COMMON: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    ElegooPrinterSensorEntityDescription(
        key="video_stream_connected",
        name="Video Stream Connected",
        icon="mdi:camera",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda printer_data: printer_data.attributes.num_video_stream_connected,  # noqa: E501
    ),
    ElegooPrinterSensorEntityDescription(
        key="video_stream_max",
        name="Video Stream Max",
        icon="mdi:camera",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda printer_data: printer_data.attributes.max_video_stream_allowed,
    ),
)


PRINTER_ATTRIBUTES_RESIN: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    ElegooPrinterSensorEntityDescription(
        key="release_film_max",
        name="Release Film Max",
        icon="mdi:film",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda printer_data: printer_data.attributes.release_film_max,
    ),
    ElegooPrinterSensorEntityDescription(
        key="temp_of_uvled_max",
        name="UV LED Temp Max",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda printer_data: printer_data.attributes.temp_of_uvled_max,
        exists_fn=lambda printer_data: printer_data.attributes.temp_of_uvled_max > 0,
        available_fn=lambda printer_data: printer_data
        and printer_data.attributes.temp_of_uvled_max > 0,
        entity_registry_enabled_default=False,
    ),
)

PRINTER_STATUS_COMMON: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    ElegooPrinterSensorEntityDescription(
        key="total_ticks",
        name="Total Print Time",
        icon="mdi:timer-sand-complete",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda printer_data: printer_data.status.print_info.total_ticks,
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_ticks",
        name="Current Print Time",
        icon="mdi:progress-clock",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda printer_data: printer_data.status.print_info.current_ticks,
    ),
    ElegooPrinterSensorEntityDescription(
        key="ticks_remaining",
        name="Remaining Print Time",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda printer_data: printer_data.status.print_info.remaining_ticks,
    ),
    ElegooPrinterSensorEntityDescription(
        key="total_layers",
        name="Total Layers",
        icon="mdi:eye",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda printer_data: printer_data.status.print_info.total_layers,
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_layer",
        name="Current Layer",
        icon="mdi:eye",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda printer_data: printer_data.status.print_info.current_layer,
    ),
    ElegooPrinterSensorEntityDescription(
        key="remaining_layers",
        name="Remaining Layers",
        icon="mdi:eye",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda printer_data: printer_data.status.print_info.remaining_layers,
    ),
    ElegooPrinterSensorEntityDescription(
        key="percent_complete",
        name="Percent Complete",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda printer_data: printer_data.status.print_info.percent_complete,
    ),
    ElegooPrinterSensorEntityDescription(
        key="filename",
        name="File Name",
        icon="mdi:file",
        value_fn=lambda printer_data: printer_data.status.print_info.filename,
        available_fn=lambda printer_data: printer_data.status
        and printer_data.status.print_info.filename != "",
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_status",
        translation_key="current_status",
        name="Current Status",
        icon="mdi:file",
        value_fn=lambda printer_data: printer_data.status.current_status.name.lower(),
        available_fn=lambda printer_data: printer_data.status.current_status
        is not None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="print_status",
        translation_key="print_status",
        name="Print Status",
        icon="mdi:file",
        value_fn=lambda printer_data: printer_data.status.print_info.status.name.lower(),
        available_fn=lambda printer_data: printer_data.status.print_info.status
        is not None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="print_error",
        translation_key="print_error",
        name="Print Error",
        icon="mdi:file",
        value_fn=lambda printer_data: printer_data.status.print_info.error_number.name.lower(),
        available_fn=lambda printer_data: printer_data.status.print_info.error_number
        is not None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_print_error_status_reason",
        translation_key="error_status_reason",
        name="Error Status Reason",
        icon="mdi:file",
        available_fn=lambda printer_data: printer_data
        and printer_data.print_history
        and printer_data.status.print_info.task_id in printer_data.print_history
        and printer_data.print_history[printer_data.status.print_info.task_id]
        is not None
        and printer_data.print_history[
            printer_data.status.print_info.task_id
        ].error_status_reason
        is not None,
        value_fn=lambda printer_data: (
            printer_data.print_history[
                printer_data.status.print_info.task_id
            ].error_status_reason.name.lower()
            if printer_data.status.print_info.task_id in printer_data.print_history
            and printer_data.print_history[printer_data.status.print_info.task_id]
            is not None
            else None
        ),
    ),
)

PRINTER_STATUS_RESIN: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    ElegooPrinterSensorEntityDescription(
        key="temp_of_uvled",
        name="UV LED Temp",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda printer_data: printer_data.status.temp_of_uvled,
    ),
    ElegooPrinterSensorEntityDescription(
        key="release_film",
        name="Release Film",
        icon="mdi:film",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda printer_data: printer_data.status.release_film,
    ),
)


PRINTER_STATUS_FDM: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    # --- Enclosure/Box Temperature Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="temp_of_box",
        name="Box Temp",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Check if the attribute exists and has a valid temperature
        available_fn=lambda printer_data: printer_data.status.temp_of_box > 0,
        value_fn=lambda printer_data: printer_data.status.temp_of_box,
    ),
    # --- Target Enclosure/Box Temperature Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="temp_target_box",
        name="Box Target Temp",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Check if the attribute exists and has a valid target temperature
        available_fn=lambda printer_data: printer_data.status.temp_target_box > 0,
        value_fn=lambda printer_data: printer_data.status.temp_target_box,
    ),
    # --- Nozzle Temperature Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="nozzle_temp",
        name="Nozzle Temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Correct path to nozzle temperature
        available_fn=lambda printer_data: printer_data.status.temp_of_nozzle > 0,
        value_fn=lambda printer_data: printer_data.status.temp_of_nozzle,
    ),
    # --- Bed Temperature Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="bed_temp",
        name="Bed Temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Correct path to bed/hotbed temperature
        available_fn=lambda printer_data: printer_data.status.temp_of_hotbed > 0,
        value_fn=lambda printer_data: printer_data.status.temp_of_hotbed,
    ),
    # --- Z Offset Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="z_offset",
        name="Z Offset",
        icon="mdi:arrow-expand-vertical",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        # Z-Offset is a direct attribute of PrinterStatus
        available_fn=lambda printer_data: printer_data.status is not None,
        value_fn=lambda printer_data: printer_data.status.z_offset,
    ),
    # --- Model Fan Speed Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="model_fan_speed",
        name="Model Fan Speed",
        icon="mdi:fan",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        # Check if the nested fan speed object is available
        available_fn=lambda printer_data: printer_data.status.current_fan_speed
        is not None,
        value_fn=lambda printer_data: printer_data.status.current_fan_speed.model_fan,
    ),
    # --- Auxiliary Fan Speed Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="aux_fan_speed",
        name="Auxiliary Fan Speed",
        icon="mdi:fan",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        # Correct path to auxiliary_fan inside the nested object
        available_fn=lambda printer_data: printer_data.status.current_fan_speed
        is not None,
        value_fn=lambda printer_data: printer_data.status.current_fan_speed.auxiliary_fan,
    ),
    # --- Box/Enclosure Fan Speed Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="box_fan_speed",
        name="Enclosure Fan Speed",
        icon="mdi:fan",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        # Correct path to box_fan inside the nested object
        available_fn=lambda printer_data: printer_data.status.current_fan_speed
        is not None,
        value_fn=lambda printer_data: printer_data.status.current_fan_speed.box_fan,
    ),
    # --- Print Speed Percentage Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="print_speed_pct",
        name="Print Speed",
        icon="mdi:speedometer",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        # Check if the nested print info object is available
        available_fn=lambda printer_data: printer_data.status.print_info is not None,
        value_fn=lambda printer_data: printer_data.status.print_info.print_speed_pct,
    ),
)

PRINTER_IMAGES: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    ElegooPrinterSensorEntityDescription(
        key="cover_image",
        name="Cover Image",
        value_fn=lambda thumbnail: thumbnail,
    ),
)

PRINTER_MJPEG_CAMERAS: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    ElegooPrinterSensorEntityDescription(
        key="centauri_carbon_camera",
        name="Centauri Carbon Camera",
        value_fn=lambda camera_url: camera_url,
        available_fn=lambda video: video.status is not None
        and video.status == ElegooVideoStatus.SUCCESS,
    ),
)

PRINTER_FDM_LIGHTS: tuple[ElegooPrinterLightEntityDescription, ...] = (
    ElegooPrinterLightEntityDescription(
        key="second_light",
        name="Chamber Light",
        value_fn=lambda light_status: light_status.second_light,
        available_fn=lambda light_status: light_status.second_light is not None,
    ),
)

PRINTER_FDM_BUTTONS: tuple[ElegooPrinterButtonEntityDescription, ...] = (
    ElegooPrinterButtonEntityDescription(
        key="pause_print",
        name="Pause Print",
        action_fn=lambda client: client.print_pause(),
        icon="mdi:pause",
        available_fn=lambda client: client.printer_data.status.current_status
        == ElegooMachineStatus.PRINTING,
    ),
    ElegooPrinterButtonEntityDescription(
        key="resume_print",
        name="Resume Print",
        action_fn=lambda client: client.print_resume(),
        icon="mdi:play",
        available_fn=lambda client: client.printer_data.status.print_info.status
        == ElegooPrintStatus.PAUSED,
    ),
    ElegooPrinterButtonEntityDescription(
        key="stop_print",
        name="Stop Print",
        action_fn=lambda client: client.print_stop(),
        icon="mdi:stop",
        available_fn=lambda client: client.printer_data.status.current_status
        in [ElegooMachineStatus.PRINTING]
        or client.printer_data.status.print_info.status == ElegooPrintStatus.PAUSED,
    ),
)
