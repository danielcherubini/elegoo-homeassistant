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
    CONF_PROXY_WEBSOCKET_PORT,
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
        proxy_enabled: bool = coordinator.config_entry.data.get(
            CONF_PROXY_ENABLED, False
        )
        ip_address = coordinator.config_entry.data[CONF_IP]

        if proxy_enabled:
            # Use stored proxy port if available
            proxy_port = coordinator.config_entry.data.get(CONF_PROXY_WEBSOCKET_PORT)
            if proxy_port:
                proxy_ip = PrinterData.get_local_ip(ip_address)
                configuration_url = f"http://{proxy_ip}:{proxy_port}"
            else:
                # Fallback for legacy configs without stored ports
                proxy_ip = PrinterData.get_local_ip(ip_address)
                configuration_url = f"http://{proxy_ip}:{WEBSOCKET_PORT}"
        else:
            configuration_url = f"http://{ip_address}:{WEBSOCKET_PORT}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.data[CONF_ID])},
            name=coordinator.config_entry.data[CONF_NAME],
            model=coordinator.config_entry.data[CONF_MODEL],
            manufacturer=coordinator.config_entry.data[CONF_BRAND],
            sw_version=coordinator.config_entry.data[CONF_FIRMWARE],
            serial_number=coordinator.config_entry.data[CONF_ID],
            configuration_url=configuration_url,
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.coordinator.online
