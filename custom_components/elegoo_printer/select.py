"""Platform for selecting Elegoo printer options."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.elegoo_printer.sdcp.models.enums import (
    PrinterType,
    ProtocolVersion,
)

if TYPE_CHECKING:
    from .api import ApiType
    from .coordinator import ElegooDataUpdateCoordinator

from .const import LOGGER
from .definitions import (
    PRINTER_SELECT_TYPES_CC2,
    PRINTER_SELECT_TYPES_V1V3,
    ElegooPrinterSelectEntityDescription,
)
from .entity import ElegooPrinterEntity


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Asynchronously sets up Elegoo printer select entities in Home Assistant.

    Supports FDM printers only. Different protocol versions use different speed presets:
    - SDCP (WebSocket/MQTT): max 160%, presets at 50/100/130/160
    - CC2: discrete modes, presets at 50/100/150/200
    """
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator
    api: ApiType = coordinator.config_entry.runtime_data.api
    protocol_version = api.printer.protocol_version

    if api.printer.printer_type.value != PrinterType.FDM.value:
        LOGGER.debug(
            "Print speed select only available for FDM printers, skipping setup"
        )
        return

    descriptions: tuple[
        ElegooPrinterSelectEntityDescription,
        ...,
    ] = (
        PRINTER_SELECT_TYPES_CC2
        if protocol_version == ProtocolVersion.CC2
        else PRINTER_SELECT_TYPES_V1V3
    )
    for description in descriptions:
        async_add_entities(
            [ElegooPrintSpeedSelect(coordinator, description)],
            update_before_add=True,
        )


class ElegooPrintSpeedSelect(ElegooPrinterEntity, SelectEntity):
    """Representation of an Elegoo printer select entity."""

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterSelectEntityDescription,
    ) -> None:
        """Initialize an Elegoo printer select entity."""
        super().__init__(coordinator)
        self.entity_description: ElegooPrinterSelectEntityDescription = description
        self._api = None  # Initialize _api to None

        self._attr_unique_id = coordinator.generate_unique_id(description.key)
        self._attr_name = description.name
        self._attr_options = description.options

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        self._api = self.coordinator.config_entry.runtime_data.api

    @property
    def current_option(self) -> str | None:
        """Return the current select option."""
        if self.coordinator.data:
            return self.entity_description.current_option_fn(self.coordinator.data)
        return None

    async def async_select_option(self, option: str) -> None:
        """Asynchronously selects an option."""
        value = self.entity_description.options_map.get(option)
        if self._api:
            await self.entity_description.select_option_fn(self._api, value)
            if self.coordinator.data:
                self.coordinator.async_set_updated_data(self.coordinator.data)
            self.async_write_ha_state()
