"""Definitions for the Elegoo Printer Integration."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.components.button import ButtonEntityDescription
from homeassistant.components.fan import FanEntityDescription, FanEntityFeature
from homeassistant.components.light import LightEntityDescription
from homeassistant.components.number import NumberEntityDescription, NumberMode
from homeassistant.components.select import SelectEntityDescription
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.sensor.const import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfInformation,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.helpers.typing import StateType

from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient

from .sdcp.models.enums import (
    ElegooErrorStatusReason,
    ElegooMachineStatus,
    ElegooPrintError,
    ElegooPrintStatus,
)
from .sdcp.models.printer import PrinterData


def _has_valid_current_coords(printer_data: PrinterData) -> bool:
    """Check if current_coord is valid."""
    if (
        not printer_data
        or not printer_data.status
        or printer_data.status.current_coord is None
    ):
        return False
    coords = printer_data.status.current_coord.split(",")
    return len(coords) == 3  # noqa: PLR2004


def _get_current_coord_value(printer_data: PrinterData, index: int) -> float | None:
    """Get a coordinate value from current_coord."""
    if not _has_valid_current_coords(printer_data):
        return None
    try:
        return float(printer_data.status.current_coord.split(",")[index])
    except (ValueError, IndexError):
        return None


async def _async_noop(*_: Any, **__: Any) -> None:
    """Async no-op function."""


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

    action_fn: Callable[..., Coroutine[Any, Any, None]] = _async_noop
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
    supported_features: FanEntityFeature = FanEntityFeature(0)  # noqa: RUF009


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
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda printer_data: printer_data.attributes.num_video_stream_connected,  # noqa: E501
    ),
    ElegooPrinterSensorEntityDescription(
        key="video_stream_max",
        name="Video Stream Max",
        icon="mdi:camera",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
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
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda printer_data: printer_data.attributes.remaining_memory,
    ),
    ElegooPrinterSensorEntityDescription(
        key="mainboard_mac",
        name="MAC Address",
        icon="mdi:network",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda printer_data: printer_data.attributes.mainboard_mac,
    ),
    ElegooPrinterSensorEntityDescription(
        key="mainboard_ip",
        name="IP Address",
        icon="mdi:ip-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda printer_data: printer_data.attributes.mainboard_ip,
    ),
    ElegooPrinterSensorEntityDescription(
        key="num_cloud_sdcp_services_connected",
        name="Cloud Services Connected",
        icon="mdi:cloud-check",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda printer_data: printer_data.attributes.num_cloud_sdcp_services_connected,  # noqa: E501
    ),
    ElegooPrinterSensorEntityDescription(
        key="max_cloud_sdcp_services_allowed",
        name="Max Cloud Services",
        icon="mdi:cloud-lock",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda printer_data: printer_data.attributes.max_cloud_sdcp_services_allowed,  # noqa: E501
    ),
)

PRINTER_ATTRIBUTES_BINARY_COMMON: tuple[
    ElegooPrinterBinarySensorEntityDescription, ...
] = (
    ElegooPrinterBinarySensorEntityDescription(
        key="usb_disk_status",
        name="USB Disk Status",
        icon="mdi:usb",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda printer_data: bool(printer_data.attributes.usb_disk_status)
        if printer_data is not None
        else False,
    ),
    ElegooPrinterBinarySensorEntityDescription(
        key="sdcp_status",
        name="SDCP Status",
        icon="mdi:lan-connect",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda printer_data: bool(printer_data.attributes.sdcp_status)
        if printer_data is not None
        else False,
    ),
)

PRINTER_ATTRIBUTES_RESIN: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    ElegooPrinterSensorEntityDescription(
        key="release_film_max",
        name="Release Film Max",
        icon="mdi:film",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda printer_data: printer_data.attributes.release_film_max,
    ),
    ElegooPrinterSensorEntityDescription(
        key="temp_of_uvled_max",
        name="UV LED Temp Max",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda printer_data: printer_data.attributes.temp_of_uvled_max
        if printer_data and printer_data.attributes
        else None,
        exists_fn=lambda printer_data: printer_data
        and printer_data.attributes
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
        value_fn=lambda printer_data: printer_data.current_job.end_time
        if printer_data.current_job
        else None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="begin_time",
        name="Begin Time",
        icon="mdi:clock-start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda printer_data: printer_data.current_job.begin_time
        if printer_data.current_job
        else None,
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
        value_fn=lambda printer_data: (
            (printer_data.status.print_info.filename or "").strip() or None
        ),
    ),
    ElegooPrinterSensorEntityDescription(
        key="task_id",
        name="Task ID",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda printer_data: (
            (printer_data.status.print_info.task_id or "").strip() or None
        ),
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_status",
        translation_key="current_status",
        name="Current Status",
        icon="mdi:file",
        device_class=SensorDeviceClass.ENUM,
        options=[status.name.lower() for status in ElegooMachineStatus],
        value_fn=lambda printer_data: printer_data.status.current_status.name.lower()
        if printer_data and printer_data.status and printer_data.status.current_status
        else None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="print_status",
        translation_key="print_status",
        name="Print Status",
        icon="mdi:file",
        device_class=SensorDeviceClass.ENUM,
        options=[status.name.lower() for status in ElegooPrintStatus],
        value_fn=lambda printer_data: printer_data.status.print_info.status.name.lower()
        if printer_data
        and printer_data.status
        and printer_data.status.print_info
        and printer_data.status.print_info.status
        else None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="print_error",
        translation_key="print_error",
        name="Print Error",
        icon="mdi:file",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=[error.name.lower() for error in ElegooPrintError],
        value_fn=lambda printer_data: printer_data.status.print_info.error_number.name.lower()  # noqa: E501
        if printer_data
        and printer_data.status
        and printer_data.status.print_info
        and printer_data.status.print_info.error_number
        else None,
    ),
    ElegooPrinterSensorEntityDescription(
        key="current_print_error_status_reason",
        translation_key="error_status_reason",
        name="Print Error Reason",
        icon="mdi:file",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=[reason.name.lower() for reason in ElegooErrorStatusReason],
        value_fn=lambda printer_data: (
            printer_data.print_history[
                printer_data.status.print_info.task_id
            ].error_status_reason.name.lower()
            if printer_data
            and printer_data.status
            and printer_data.status.print_info
            and printer_data.status.print_info.task_id
            and printer_data.print_history
            and printer_data.status.print_info.task_id in printer_data.print_history
            and printer_data.print_history[printer_data.status.print_info.task_id]
            and printer_data.print_history[
                printer_data.status.print_info.task_id
            ].error_status_reason
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
        value_fn=lambda printer_data: printer_data.status.temp_of_box
        if printer_data and printer_data.status
        else None,
    ),
    # --- Target Enclosure/Box Temperature Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="temp_target_box",
        name="Box Target Temp",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda printer_data: printer_data.status.temp_target_box
        if printer_data and printer_data.status
        else None,
    ),
    # --- Nozzle Temperature Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="nozzle_temp",
        name="Nozzle Temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda printer_data: printer_data.status.temp_of_nozzle
        if printer_data and printer_data.status
        else None,
    ),
    # --- Bed Temperature Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="bed_temp",
        name="Bed Temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda printer_data: printer_data.status.temp_of_hotbed
        if printer_data and printer_data.status
        else None,
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
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda printer_data: printer_data.status.z_offset
        if printer_data and printer_data.status
        else None,
    ),
    # --- Model Fan Speed Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="model_fan_speed",
        name="Model Fan Speed",
        icon="mdi:fan",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda printer_data: printer_data.status.current_fan_speed.model_fan
        if printer_data
        and printer_data.status
        and printer_data.status.current_fan_speed
        else None,
    ),
    # --- Auxiliary Fan Speed Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="aux_fan_speed",
        name="Auxiliary Fan Speed",
        icon="mdi:fan",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda printer_data: printer_data.status.current_fan_speed.auxiliary_fan  # noqa: E501
        if printer_data
        and printer_data.status
        and printer_data.status.current_fan_speed
        else None,
    ),
    # --- Box/Enclosure Fan Speed Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="box_fan_speed",
        name="Enclosure Fan Speed",
        icon="mdi:fan",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda printer_data: printer_data.status.current_fan_speed.box_fan
        if printer_data
        and printer_data.status
        and printer_data.status.current_fan_speed
        else None,
    ),
    # --- Print Speed Percentage Sensor ---
    ElegooPrinterSensorEntityDescription(
        key="print_speed_pct",
        name="Print Speed",
        icon="mdi:speedometer",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda printer_data: printer_data.status.print_info.print_speed_pct
        if printer_data and printer_data.status and printer_data.status.print_info
        else None,
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
        value_fn=lambda printer_data: _get_current_coord_value(printer_data, 0),
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
        value_fn=lambda printer_data: _get_current_coord_value(printer_data, 1),
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
        value_fn=lambda printer_data: _get_current_coord_value(printer_data, 2),
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
    ),
)

PRINTER_FFMPEG_CAMERAS = PRINTER_MJPEG_CAMERAS

PRINTER_FDM_LIGHTS: tuple[ElegooPrinterLightEntityDescription, ...] = (
    ElegooPrinterLightEntityDescription(
        key="second_light",
        name="Chamber Light",
        value_fn=lambda light_status: light_status.second_light
        if light_status
        else None,
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


async def _pause_print_action(client: ElegooPrinterClient) -> None:
    """Pause print action."""
    return await client.print_pause()


async def _resume_print_action(client: ElegooPrinterClient) -> None:
    """Resume print action."""
    return await client.print_resume()


async def _stop_print_action(client: ElegooPrinterClient) -> None:
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
        percentage_fn=lambda printer_data: printer_data.status.current_fan_speed.model_fan,  # noqa: E501
    ),
    ElegooPrinterFanEntityDescription(
        key="auxiliary_fan",
        name="Auxiliary Fan",
        icon="mdi:fan",
        supported_features=FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF,
        value_fn=lambda printer_data: printer_data.status.current_fan_speed.auxiliary_fan  # noqa: E501
        > 0,
        percentage_fn=lambda printer_data: printer_data.status.current_fan_speed.auxiliary_fan,  # noqa: E501
    ),
    ElegooPrinterFanEntityDescription(
        key="box_fan",
        name="Enclosure Fan",
        icon="mdi:fan",
        supported_features=FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF,
        value_fn=lambda printer_data: printer_data.status.current_fan_speed.box_fan > 0,
        percentage_fn=lambda printer_data: printer_data.status.current_fan_speed.box_fan,  # noqa: E501
    ),
)
