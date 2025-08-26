"""ElegooEntity class."""

from __future__ import annotations

import socket

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
    DEFAULT_FALLBACK_IP,
    DOMAIN,
    WEBSOCKET_PORT,
)
from .coordinator import ElegooDataUpdateCoordinator


class ElegooPrinterEntity(CoordinatorEntity[ElegooDataUpdateCoordinator]):
    """ElegooEntity class."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def _get_local_ip(self, target_ip: str) -> str:
        """
        Determine the local IP address used for outbound communication.

        Args:
            target_ip: The target IP to determine the route to.

        Returns:
            The local IP address, or "127.0.0.1" if detection fails.

        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                # Doesn't have to be reachable
                s.connect((target_ip or DEFAULT_FALLBACK_IP, 1))
                return s.getsockname()[0]
        except (socket.gaierror, OSError):
            return "127.0.0.1"

    def __init__(self, coordinator: ElegooDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        proxy_enabled: bool = coordinator.config_entry.data.get(
            CONF_PROXY_ENABLED, False
        )
        ip_address = coordinator.config_entry.data[CONF_IP]
        mainboard_id = coordinator.config_entry.data[CONF_ID]

        if proxy_enabled:
            ip_address = self._get_local_ip(ip_address)
            configuration_url = f"http://{ip_address}:{WEBSOCKET_PORT}/{mainboard_id}"
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
