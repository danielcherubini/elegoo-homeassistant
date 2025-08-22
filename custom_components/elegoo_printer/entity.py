"""ElegooEntity class."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import network as network_util

from .const import (
    ATTRIBUTION,
    CONF_BRAND,
    CONF_FIRMWARE,
    CONF_ID,
    CONF_IP,
    CONF_MODEL,
    CONF_NAME,
    CONF_PRINTER_TYPE,
    CONF_PROXY_ENABLED,
    DOMAIN,
    WEBSOCKET_PORT,
)
from .coordinator import ElegooDataUpdateCoordinator
from .sdcp.models.enums import PrinterType


class ElegooPrinterEntity(CoordinatorEntity[ElegooDataUpdateCoordinator]):
    """ElegooEntity class."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: ElegooDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        printer_type: PrinterType = coordinator.config_entry.data[CONF_PRINTER_TYPE]
        proxy_enabled: bool = coordinator.config_entry.data.get(
            CONF_PROXY_ENABLED, False
        )
        ip_address = coordinator.config_entry.data[CONF_IP]
        if printer_type == PrinterType.FDM and proxy_enabled:
            ip_address = network_util.get_local_ip()

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.data[CONF_ID])},
            name=coordinator.config_entry.data[CONF_NAME],
            model=coordinator.config_entry.data[CONF_MODEL],
            manufacturer=coordinator.config_entry.data[CONF_BRAND],
            sw_version=coordinator.config_entry.data[CONF_FIRMWARE],
            serial_number=coordinator.config_entry.data[CONF_ID],
            configuration_url=f"http://{ip_address}:{WEBSOCKET_PORT}",
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.coordinator.online
