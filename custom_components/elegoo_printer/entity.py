"""ElegooEntity class."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTRIBUTION,
    CONF_BRAND,
    CONF_FIRMWARE,
    CONF_ID,
    CONF_IP,
    CONF_MODEL,
    CONF_NAME,
    CONF_PROXY_ENABLED,
    DOMAIN,
    WEBSOCKET_PORT,
)
from .coordinator import ElegooDataUpdateCoordinator
from .sdcp.models.printer import PrinterData


class ElegooPrinterEntity(CoordinatorEntity[ElegooDataUpdateCoordinator]):
    """ElegooEntity class."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: ElegooDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info with dynamically updated configuration URL."""
        proxy_enabled: bool = self.coordinator.config_entry.data.get(
            CONF_PROXY_ENABLED, False
        )
        ip_address = self.coordinator.config_entry.data[CONF_IP]

        if proxy_enabled:
            # Use centralized proxy with MainboardID query parameter
            proxy_ip = PrinterData.get_local_ip(ip_address)
            mainboard_id = self.coordinator.config_entry.data[CONF_ID]
            configuration_url = f"http://{proxy_ip}:{WEBSOCKET_PORT}?id={mainboard_id}"
        else:
            configuration_url = f"http://{ip_address}:{WEBSOCKET_PORT}"

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.config_entry.data[CONF_ID])},
            name=self.coordinator.config_entry.data[CONF_NAME],
            model=self.coordinator.config_entry.data[CONF_MODEL],
            manufacturer=self.coordinator.config_entry.data[CONF_BRAND],
            sw_version=self.coordinator.config_entry.data[CONF_FIRMWARE],
            serial_number=self.coordinator.config_entry.data[CONF_ID],
            configuration_url=configuration_url,
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available
