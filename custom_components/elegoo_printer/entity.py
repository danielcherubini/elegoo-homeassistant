"""ElegooEntity class."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION
from .coordinator import ElegooDataUpdateCoordinator


class ElegooPrinterEntity(CoordinatorEntity[ElegooDataUpdateCoordinator]):
    """ElegooEntity class."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: ElegooDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.config_entry.entry_id
        self._attr_device_info = DeviceInfo(
            name=coordinator.config_entry.title,
            model=coordinator.config_entry.data["model"],
            manufacturer=coordinator.config_entry.data["brand"],
            sw_version=coordinator.config_entry.data["firmware"],
            serial_number=coordinator.config_entry.data["id"],
            identifiers={
                (
                    coordinator.config_entry.domain,
                    coordinator.config_entry.entry_id,
                ),
            },
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.online
