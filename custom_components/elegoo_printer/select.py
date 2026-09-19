"""Platform for selecting Elegoo printer options."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.restore_state import RestoreEntity

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
    PRINTER_FILE_SELECT_CC2,
    PRINTER_SELECT_TYPES_CC2,
    PRINTER_SELECT_TYPES_V1V3,
    ElegooPrinterDynamicSelectEntityDescription,
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

    if protocol_version == ProtocolVersion.CC2:
        for file_description in PRINTER_FILE_SELECT_CC2:
            async_add_entities(
                [ElegooPrintFileSelect(coordinator, file_description)],
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


class ElegooPrintFileSelect(ElegooPrinterEntity, RestoreEntity, SelectEntity):
    """
    A file in the printer's local storage, chosen but not started.

    The options are the printer's file list (method 1044, refreshed by the
    coordinator and by the Refresh File List button); the choice is kept on
    the entity and restored across restarts. Selecting never starts a print -
    that stays with the ``start_print`` service, which takes this entity's
    state as its ``filename``.
    """

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterDynamicSelectEntityDescription,
    ) -> None:
        """Initialize the file select."""
        super().__init__(coordinator)
        self.entity_description: ElegooPrinterDynamicSelectEntityDescription = (
            description
        )
        self._attr_unique_id = coordinator.generate_unique_id(description.key)
        self._attr_name = description.name
        self._chosen: str | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last choice; it is validated against the options when read."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in (None, "unknown", "unavailable"):
            self._chosen = last.state

    @property
    def options(self) -> list[str]:
        """Return the files on the printer, sorted by name."""
        return self.entity_description.options_fn(self.coordinator.data)

    @property
    def current_option(self) -> str | None:
        """Return the chosen file, or None once it is no longer on the printer."""
        if self._chosen in self.options:
            return self._chosen
        return None

    @property
    def available(self) -> bool:
        """Available once the printer has reported a file list."""
        if not super().available:
            return False
        return self.entity_description.available_fn(self.coordinator.data)

    async def async_select_option(self, option: str) -> None:
        """Remember the choice. Nothing is sent to the printer."""
        if option not in self.options:
            msg = f"{option!r} is not on the printer"
            raise ServiceValidationError(msg)
        self._chosen = option
        self.async_write_ha_state()
