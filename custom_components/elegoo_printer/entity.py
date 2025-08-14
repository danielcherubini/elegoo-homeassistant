"""ElegooEntity class."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import ElegooDataUpdateCoordinator


class ElegooPrinterEntity(CoordinatorEntity[ElegooDataUpdateCoordinator]):
    """ElegooEntity class."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: ElegooDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.data["id"])},
            name=coordinator.config_entry.data["name"],
            model=coordinator.config_entry.data["model"],
            manufacturer=coordinator.config_entry.data["brand"],
            sw_version=coordinator.config_entry.data["firmware"],
        )
