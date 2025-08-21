"""Number platform for Elegoo printer."""

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.elegoo_printer.sdcp.models.enums import PrinterType

from .coordinator import ElegooDataUpdateCoordinator
from .definitions import PRINTER_NUMBER_TYPES, ElegooPrinterNumberEntityDescription
from .entity import ElegooPrinterEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Asynchronously sets up Elegoo printer number entities in Home Assistant."""
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator
    entities: list[ElegooNumber] = []

    printer_type = coordinator.config_entry.runtime_data.api.printer.printer_type

    if printer_type == PrinterType.FDM:
        for description in PRINTER_NUMBER_TYPES:
            entities.extend(ElegooNumber(coordinator, description))

    if entities:
        async_add_entities(entities, update_before_add=True)


class ElegooNumber(ElegooPrinterEntity, NumberEntity):
    """Representation of an Elegoo printer number entity."""

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterNumberEntityDescription,
    ) -> None:
        """Initialize an Elegoo printer number entity."""
        super().__init__(coordinator)
        self.entity_description: ElegooPrinterNumberEntityDescription = description

        self._api = None  # Initialize _api to None

        self._attr_unique_id = coordinator.generate_unique_id(description.key)
        self._attr_name = description.name
        self._attr_native_min_value = description.native_min_value
        self._attr_native_max_value = description.native_max_value
        self._attr_native_step = description.native_step
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_mode = description.mode

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        self._api = self.coordinator.config_entry.runtime_data.api

    @property
    def native_value(self):
        """Returns the current value."""
        if self.coordinator.data:
            return self.entity_description.value_fn(self.coordinator.data)
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Asynchronously sets the value."""
        if self._api:
            await self.entity_description.set_value_fn(self._api, int(value))
            await self.coordinator.async_request_refresh()
