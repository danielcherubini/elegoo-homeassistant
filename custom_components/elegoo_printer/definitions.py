"""Definitions for the Elegoo Printer Integration."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.button import ButtonEntityDescription
from homeassistant.components.fan import FanEntityDescription, FanEntityFeature
from homeassistant.components.light import LightEntityDescription
from homeassistant.components.number import NumberEntityDescription, NumberMode
from homeassistant.components.select import SelectEntityDescription
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.components.sensor.const import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    UnitOfInformation,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.helpers.typing import StateType

from custom_components.elegoo_printer.elegoo_sdcp.models.enums import (
    ElegooErrorStatusReason,
    ElegooMachineStatus,
    ElegooPrintError,
    ElegooPrintStatus,
    ElegooVideoStatus,
)


def _has_valid_current_coords(printer_data) -> bool:
    """Check if current_coord is valid."""
    if printer_data.status.current_coord is None:
        return False
    coords = printer_data.status.current_coord.split(",")
    return len(coords) == 3


def _get_current_coord_value(printer_data, index: int) -> float | None:
    """Get a coordinate value from current_coord."""
    if not _has_valid_current_coords(printer_data):
        return None
    try:
        return float(printer_data.status.current_coord.split(",")[index])
    except (ValueError, IndexError):
        return None


async def _async_noop() -> None:
    """Async no-op function"""
    pass


@dataclass
class ElegooPrinterSensorEntityDescriptionMixin:
    """Mixin for required keys."""

    value_fn: Callable[..., datetime | StateType]


@dataclass
class ElegooPrinterSensorEntityDescription(
    ElegooPrinterSensorEntityDescriptionMixin, SensorEntityDescription
):
    """Sensor entity description for Elegoo Printers."""

    available_fn: Callable[..., bool] = lambda printer_data: printer_data
    exists_fn: Callable[..., bool] = lambda _: True
    extra_attributes: Callable[..., dict] = lambda _: {}
    icon_fn: Callable[..., str] = lambda _: "mdi:eye"


@dataclass
class ElegooPrinterBinarySensorEntityDescription(
    ElegooPrinterSensorEntityDescriptionMixin, BinarySensorEntityDescription
):
    """Binary sensor entity description for Elegoo Printers."""

    available_fn: Callable[..., bool] = lambda printer_data: printer_data
    exists_fn: Callable[..., bool] = lambda _: True
    extra_attributes: Callable[..., dict] = lambda _: {}
    icon_fn: Callable[..., str] = lambda _: "mdi:eye"


@dataclass
class ElegooPrinterLightEntityDescription(
    ElegooPrinterSensorEntityDescriptionMixin, LightEntityDescription
):
    """Light entity description for Elegoo Printers."""

    available_fn: Callable[..., bool] = lambda printer_data: printer_data
    exists_fn: Callable[..., bool] = lambda _: True
    extra_attributes: Callable[..., dict] = lambda _: {}
    icon_fn: Callable[..., str] = lambda _: "mdi:lightbulb"


@dataclass
class ElegooPrinterButtonEntityDescription(ButtonEntityDescription):
    """Button entity description for Elegoo Printers."""

    action_fn: Callable[..., Coroutine[Any, Any, None]] = lambda _: _async_noop()
    available_fn: Callable[..., bool] = lambda printer_data: printer_data


@dataclass
class ElegooPrinterFanEntityDescription(
    ElegooPrinterSensorEntityDescriptionMixin,
    FanEntityDescription,
):
    """Fan entity description for Elegoo Printers."""

    available_fn: Callable[..., bool] = lambda printer_data: printer_data
    exists_fn: Callable[..., bool] = lambda _: True
    extra_attributes: Callable[..., dict] = lambda _: {}
    icon_fn: Callable[..., str] = lambda _: "mdi:fan"
    percentage_fn: Callable[..., int | None] = lambda _: None
    supported_features: FanEntityFeature = FanEntityFeature(0)


@dataclass(kw_only=True)
class ElegooPrinterSelectEntityDescription(SelectEntityDescription):
    """Select entity description for Elegoo Printers."""

    options_map: dict[str, Any]
    current_option_fn: Callable[..., str | None]
    select_option_fn: Callable[..., Coroutine[Any, Any, None]]


@dataclass(kw_only=True)
class ElegooPrinterNumberEntityDescription(NumberEntityDescription):
    """Number entity description for Elegoo Printers."""

    value_fn: Callable[..., float | None]
    set_value_fn: Callable[..., Coroutine[Any, Any, None]]


PRINT_SPEED_PRESETS = {"Silent": 50, "Balanced": 100, "Sport": 130, "Ludicrous": 160}

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
    ElegooPrinterSensorEntityDescription(
        key="remaining_memory",
        name="Remaining Memory",
        icon="mdi:memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfInformation.BITS,
        suggested_unit_of_measurement=UnitOfInformation.MEGABYTES,
        suggested_display_precision=2,
        value_fn=lambda printer_data: printer_data.attributes.remaining_memory,
    ),
    ElegooPrinterSensorEntityDescription(
        key="mainboard_mac",
        name="MAC Address",
        icon="mdi:network",
        value_fn=lambda printer_data: printer_data.attributes.mainboard_mac,
    ),
    ElegooPrinterSensorEntityDescription(
        key="mainboard_ip",
        name="IP Address",
        icon="mdi:ip-network",
        value_fn=lambda printer_data: printer_data.attributes.mainboard_ip,
    ),
    ElegooPrinterSensorEntityDescription(
        key="num_cloud_sdcp_services_connected",
        name="Cloud Services Connected",
        icon="mdi:cloud-check",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda printer_data: printer_data.attributes.num_cloud_sdcp_services_connected,
    ),
    ElegooPrinterSensorEntityDescription(
        key="max_cloud_sdcp_services_allowed",
        name="Max Cloud Services",
        icon="mdi:cloud-lock",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda printer_data: printer_data.attributes.max_cloud_sdcp_services_allowed,
    ),
)

PRINTER_ATTRIBUTES_BINARY_COMMON: tuple[
    ElegooPrinterBinarySensorEntityDescription, ...
] = (
    ElegooPrinterBinarySensorEntityDescription(
        key="usb_disk_status",
        name="USB Disk Status",
        icon="mdi:usb",
        value_fn=lambda printer_data: printer_data.attributes.usb_disk_status,
    ),
    ElegooPrinterBinarySensorEntityDescription(
        key="sdcp_status",
        name="SDCP Status",
        icon="mdi:lan-connect",
        value_fn=lambda printer_data: printer_data.attributes.sdcp_status,
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
        key="end_time",
        name="End Time",
        icon="mdi:clock",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda printer_data: printer_data.status.print_info.end_time,
        available_fn=lambda printer_data: printer_data.status.print_info.end_time
        is not None,
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
        key="task_id",
        name="Task ID",
        icon="mdi:identifier",
        entity_registry_enabled_default=False,
        value_fn=lambda printer_data: printer_data.status.print_info.task_id,
        available_fn=lambda printer_data: printer_data.status
        and printer_data.status.print_info.task_id is not None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_status",
        translation_key="current_status",
        name="Current Status",
        icon="mdi:file",
        device_class=SensorDeviceClass.ENUM,
        options=[status.name.lower() for status in ElegooMachineStatus],
        value_fn=lambda printer_data: printer_data.status.current_status.name.lower(),
        available_fn=lambda printer_data: printer_data.status.current_status
        is not None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="print_status",
        translation_key="print_status",
        name="Print Status",
        icon="mdi:file",
        device_class=SensorDeviceClass.ENUM,
        options=[status.name.lower() for status in ElegooPrintStatus],
        value_fn=lambda printer_data: printer_data.status.print_info.status.name.lower(),
        available_fn=lambda printer_data: printer_data.status.print_info.status
        is not None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="print_error",
        translation_key="print_error",
        name="Print Error",
        icon="mdi:file",
        device_class=SensorDeviceClass.ENUM,
        options=[error.name.lower() for error in ElegooPrintError],
        value_fn=lambda printer_data: printer_data.status.print_info.error_number.name.lower(),
        available_fn=lambda printer_data: printer_data.status.print_info.error_number
        is not None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_print_error_status_reason",
        translation_key="error_status_reason",
        name="Print Error Reason",
        icon="mdi:file",
        device_class=SensorDeviceClass.ENUM,
        options=[reason.name.lower() for reason in ElegooErrorStatusReason],
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
        suggested_display_precision=4,
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
    # --- Current X Coordinate Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="current_x",
        name="Current X",
        icon="mdi:axis-x-arrow",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=2,
        available_fn=lambda printer_data: printer_data.status.current_coord is not None,
        value_fn=lambda printer_data: float(
            printer_data.status.current_coord.split(",")[0]
        ),
    ),
    # --- Current Y Coordinate Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="current_y",
        name="Current Y",
        icon="mdi:axis-y-arrow",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=2,
        available_fn=lambda printer_data: printer_data.status.current_coord is not None,
        value_fn=lambda printer_data: float(
            printer_data.status.current_coord.split(",")[1]
        ),
    ),
    # --- Current Z Coordinate Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="current_z",
        name="Current Z",
        icon="mdi:axis-z-arrow",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=2,
        available_fn=lambda printer_data: printer_data.status.current_coord is not None,
        value_fn=lambda printer_data: float(
            printer_data.status.current_coord.split(",")[2]
        ),
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
        key="chamber_camera",
        name="Chamber Camera",
        value_fn=lambda camera_url: camera_url,
        available_fn=lambda video: video is not None
        and video.status == ElegooVideoStatus.SUCCESS,
    ),
)

PRINTER_FFMPEG_CAMERAS = PRINTER_MJPEG_CAMERAS

PRINTER_FDM_LIGHTS: tuple[ElegooPrinterLightEntityDescription, ...] = (
    ElegooPrinterLightEntityDescription(
        key="second_light",
        name="Chamber Light",
        value_fn=lambda light_status: light_status.second_light,
        available_fn=lambda light_status: light_status.second_light is not None,
    ),
)

PRINTER_SELECT_TYPES: tuple[ElegooPrinterSelectEntityDescription, ...] = (
    ElegooPrinterSelectEntityDescription(
        key="print_speed",
        name="Print Speed",
        icon="mdi:speedometer",
        options=list(PRINT_SPEED_PRESETS.keys()),
        options_map=PRINT_SPEED_PRESETS,
        current_option_fn=lambda printer_data: (
            next(
                (
                    name
                    for name, value in PRINT_SPEED_PRESETS.items()
                    if printer_data.status.print_info
                    and value == printer_data.status.print_info.print_speed_pct
                ),
                None,
            )
            if printer_data.status and printer_data.status.print_info
            else None
        ),
        select_option_fn=lambda api, value: api.async_set_print_speed(value),
    ),
)

PRINTER_NUMBER_TYPES: tuple[ElegooPrinterNumberEntityDescription, ...] = (
    ElegooPrinterNumberEntityDescription(
        key="target_nozzle_temp",
        name="Target Nozzle Temp",
        icon="mdi:thermometer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=0,
        native_max_value=320,
        native_step=1,
        mode=NumberMode.BOX,
        value_fn=lambda printer_data: printer_data.status.temp_target_nozzle,
        set_value_fn=lambda api, value: api.async_set_target_nozzle_temp(int(value)),
    ),
    ElegooPrinterNumberEntityDescription(
        key="target_bed_temp",
        name="Target Bed Temp",
        icon="mdi:thermometer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=0,
        native_max_value=110,
        native_step=1,
        mode=NumberMode.BOX,
        value_fn=lambda printer_data: printer_data.status.temp_target_hotbed,
        set_value_fn=lambda api, value: api.async_set_target_bed_temp(int(value)),
    ),
)


async def _pause_print_action(client):
    """Pause print action."""
    return await client.print_pause()


async def _resume_print_action(client):
    """Resume print action."""
    return await client.print_resume()


async def _stop_print_action(client):
    """Stop print action."""
    return await client.print_stop()


PRINTER_FDM_BUTTONS: tuple[ElegooPrinterButtonEntityDescription, ...] = (
    ElegooPrinterButtonEntityDescription(
        key="pause_print",
        name="Pause Print",
        action_fn=_pause_print_action,
        icon="mdi:pause",
        available_fn=lambda client: client.printer_data.status.current_status
        == ElegooMachineStatus.PRINTING,
    ),
    ElegooPrinterButtonEntityDescription(
        key="resume_print",
        name="Resume Print",
        action_fn=_resume_print_action,
        icon="mdi:play",
        available_fn=lambda client: client.printer_data.status.print_info.status
        == ElegooPrintStatus.PAUSED,
    ),
    ElegooPrinterButtonEntityDescription(
        key="stop_print",
        name="Stop Print",
        action_fn=_stop_print_action,
        icon="mdi:stop",
        available_fn=lambda client: client.printer_data.status.current_status
        in [ElegooMachineStatus.PRINTING]
        or client.printer_data.status.print_info.status == ElegooPrintStatus.PAUSED,
    ),
)

FANS: tuple[ElegooPrinterFanEntityDescription, ...] = (
    ElegooPrinterFanEntityDescription(
        key="model_fan",
        name="Model Fan",
        icon="mdi:fan",
        supported_features=FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF,
        value_fn=lambda printer_data: printer_data.status.current_fan_speed.model_fan
        > 0,
        percentage_fn=lambda printer_data: printer_data.status.current_fan_speed.model_fan,
    ),
    ElegooPrinterFanEntityDescription(
        key="auxiliary_fan",
        name="Auxiliary Fan",
        icon="mdi:fan",
        supported_features=FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF,
        value_fn=lambda printer_data: printer_data.status.current_fan_speed.auxiliary_fan
        > 0,
        percentage_fn=lambda printer_data: printer_data.status.current_fan_speed.auxiliary_fan,
    ),
    ElegooPrinterFanEntityDescription(
        key="box_fan",
        name="Enclosure Fan",
        icon="mdi:fan",
        supported_features=FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF,
        value_fn=lambda printer_data: printer_data.status.current_fan_speed.box_fan > 0,
        percentage_fn=lambda printer_data: printer_data.status.current_fan_speed.box_fan,
    ),
)
