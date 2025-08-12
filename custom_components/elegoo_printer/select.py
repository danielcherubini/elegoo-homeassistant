from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.elegoo_printer.sdcp.models.enums import PrinterType

from .coordinator import ElegooDataUpdateCoordinator
from .definitions import PRINTER_SELECT_TYPES, ElegooPrinterSelectEntityDescription
from .entity import ElegooPrinterEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Asynchronously sets up Elegoo printer select entities in Home Assistant.
    """
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator
    printer_type = coordinator.config_entry.runtime_data.api.printer.printer_type

    if printer_type == PrinterType.FDM:
        for description in PRINTER_SELECT_TYPES:
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
        """
        Initialize an Elegoo printer select entity.
        """
        super().__init__(coordinator)
        self.entity_description: ElegooPrinterSelectEntityDescription = description
        self._api = None  # Initialize _api to None

        self._attr_unique_id = coordinator.generate_unique_id(description.key)
        self._attr_name = description.name
        self._attr_options = description.options

    async def async_added_to_hass(self) -> None:
        """
        Run when entity about to be added to hass.
        """
        await super().async_added_to_hass()
        self._api = self.coordinator.config_entry.runtime_data.api

    @property
    def current_option(self):
        """
        Returns the current selected option.
        """
        if self._api and self._api.printer_data:
            return self.entity_description.current_option_fn(self._api.printer_data)
        return None

    async def async_select_option(self, option: str):
        """
        Asynchronously selects an option.
        """
        value = self.entity_description.options_map.get(option)
        if self._api:
            await self.entity_description.select_option_fn(self._api, value)
            if self._api.printer_data:
                self.coordinator.async_set_updated_data(self._api.printer_data)
            self.async_write_ha_state()
