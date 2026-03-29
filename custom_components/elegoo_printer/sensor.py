"""Sensor platform for elegoo_printer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity

from .const import CONF_GCODE_PROXY_URL, LOGGER
from .definitions import (
    PRINTER_ATTRIBUTES_COMMON,
    PRINTER_ATTRIBUTES_RESIN,
    PRINTER_ATTRIBUTES_V3_ONLY,
    PRINTER_STATUS_CANVAS,
    PRINTER_STATUS_CC2_GCODE_FILAMENT,
    PRINTER_STATUS_CC2_GCODE_PROXY_FILAMENT,
    PRINTER_STATUS_COMMON,
    PRINTER_STATUS_FDM,
    PRINTER_STATUS_FDM_CURRENT_EXTRUSION,
    PRINTER_STATUS_FDM_TOTAL_EXTRUSION,
    PRINTER_STATUS_RESIN,
    PRINTER_STATUS_RESIN_VAT_HEATER,
    ElegooPrinterSensorEntityDescription,
)
from .entity import ElegooPrinterEntity
from .sdcp.models.enums import PrinterType, ProtocolVersion

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.typing import StateType

    from .coordinator import ElegooDataUpdateCoordinator
    from .data import ElegooPrinterConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Asynchronously sets up Elegoo printer sensor entities for a configuration entry.

    Adds sensor entities based on printer type (FDM/RESIN) and protocol version
    (V1/V3). V1 (MQTT) printers get a subset of attributes compared to V3 printers.
    """
    coordinator: ElegooDataUpdateCoordinator = entry.runtime_data.coordinator
    printer = coordinator.config_entry.runtime_data.api.printer
    printer_type = printer.printer_type
    protocol_version = printer.protocol_version

    sensors: list[ElegooPrinterSensorEntityDescription] = []

    # Common status sensors (all printers, both V1 and V3)
    sensors.extend(PRINTER_STATUS_COMMON)

    # Common attributes (both V1 and V3)
    sensors.extend(PRINTER_ATTRIBUTES_COMMON)

    # V3-only attributes (WebSocket/SDCP printers)
    if protocol_version == ProtocolVersion.V3:
        sensors.extend(PRINTER_ATTRIBUTES_V3_ONLY)

    # Type-specific sensors (both V1 and V3)
    if printer_type == PrinterType.FDM:
        sensors.extend(PRINTER_STATUS_FDM)

        # Canvas/AMS sensors (CC2 with Canvas support)
        if protocol_version == ProtocolVersion.CC2:
            sensors.extend(PRINTER_STATUS_CANVAS)

        # Gcode filament data sensors (CC2 only, uses CC2_CMD_GET_FILE_DETAIL)
        if protocol_version == ProtocolVersion.CC2:
            sensors.extend(PRINTER_STATUS_CC2_GCODE_FILAMENT)
            config = {
                **(entry.data or {}),
                **(entry.options or {}),
            }
            if config.get(CONF_GCODE_PROXY_URL):
                sensors.extend(PRINTER_STATUS_CC2_GCODE_PROXY_FILAMENT)

        # Current extrusion
        if printer.open_centauri or protocol_version == ProtocolVersion.CC2:
            sensors.extend(PRINTER_STATUS_FDM_CURRENT_EXTRUSION)

        # Total extrusion
        if printer.open_centauri:
            sensors.extend(PRINTER_STATUS_FDM_TOTAL_EXTRUSION)
    elif printer_type == PrinterType.RESIN:
        sensors.extend(PRINTER_STATUS_RESIN)
        sensors.extend(PRINTER_ATTRIBUTES_RESIN)

        # Vat heater specific sensors
        if printer.has_vat_heater:
            sensors.extend(PRINTER_STATUS_RESIN_VAT_HEATER)

    LOGGER.debug(
        f"Adding {len(sensors)} sensor entities for {protocol_version.value} "
        f"{printer_type.value if printer_type else 'unknown'} printer"
    )
    entities = [
        ElegooPrinterSensor(
            coordinator=coordinator,
            entity_description=entity_description,
        )
        for entity_description in sensors
    ]

    async_add_entities(entities, update_before_add=True)


class ElegooPrinterSensor(ElegooPrinterEntity, SensorEntity):
    """elegoo_printer Sensor class."""

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        entity_description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """
        Initialize an Elegoo printer sensor entity with the given data coordinator and entity description.

        For duration sensors on FDM printers, sets the native unit of measurement to seconds.
        """  # noqa: E501
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = coordinator.generate_unique_id(
            self.entity_description.key
        )

    @property
    def available(self) -> bool:
        """Use exists_fn when set (e.g. UV LED); otherwise entity stays available."""
        if not super().available:
            return False
        return self.entity_description.exists_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict:
        """
        Returns additional state attributes for the sensor.

        The attributes are generated by the entity descriptions extra_attributes method.
        """
        return self.entity_description.extra_attributes(self)

    @property
    def native_value(self) -> datetime | StateType:
        """Return the state of the sensor."""
        if self.coordinator.data:
            return self.entity_description.value_fn(self.coordinator.data)
        return None
