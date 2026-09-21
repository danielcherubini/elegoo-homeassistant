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
    PRINT_TRAY_OPTIONS,
    PRINTER_FILE_SELECT_CC2,
    PRINTER_SELECT_TYPES_CC2,
    PRINTER_SELECT_TYPES_V1V3,
    PRINTER_TRAY_SELECT_CC2,
    ElegooPrinterDynamicSelectEntityDescription,
    ElegooPrinterSelectEntityDescription,
    _tray_labels,
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
        if api.printer.has_canvas:
            for tray_description in PRINTER_TRAY_SELECT_CC2:
                async_add_entities(
                    [ElegooPrintTraySelect(coordinator, tray_description)],
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
    coordinator and by the Refresh File List button); the choice is kept in
    ``printer_data.selected_file`` and restored across restarts. Selecting
    never starts a print: that is the Print Selected File button, or the
    ``start_print`` service with this entity's state as ``filename``.
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

    async def async_added_to_hass(self) -> None:
        """Restore the last choice; it is validated against the options when read."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in (None, "unknown", "unavailable"):
            self.coordinator.data.selected_file = last.state

    @property
    def options(self) -> list[str]:
        """Return the files on the printer, sorted by name."""
        return self.entity_description.options_fn(self.coordinator.data)

    @property
    def current_option(self) -> str | None:
        """Return the chosen file, or None once it is no longer on the printer."""
        chosen = self.coordinator.data.selected_file if self.coordinator.data else None
        if chosen in self.options:
            return chosen
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
        self.coordinator.data.selected_file = option
        self.async_write_ha_state()


class ElegooPrintTraySelect(ElegooPrinterEntity, RestoreEntity, SelectEntity):
    """
    The Canvas tray the Print Selected File button prints from.

    ``Automatic`` leaves the choice to the printer, ``A1``-``A4`` map to
    tray 0-3 for G-code tool 0, as ``start_print``'s ``tray`` does. The
    choice is kept in ``printer_data.selected_tray`` and restored across
    restarts; the trays' current filaments are exposed as attributes.
    """

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterDynamicSelectEntityDescription,
    ) -> None:
        """Initialize the tray select."""
        super().__init__(coordinator)
        self.entity_description: ElegooPrinterDynamicSelectEntityDescription = (
            description
        )
        self._attr_unique_id = coordinator.generate_unique_id(description.key)
        self._attr_name = description.name
        self._attr_options = description.options_fn(None)

    async def async_added_to_hass(self) -> None:
        """Restore the last choice."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in PRINT_TRAY_OPTIONS:
            self.coordinator.data.selected_tray = PRINT_TRAY_OPTIONS[last.state]

    @property
    def current_option(self) -> str:
        """Return the label of the chosen tray; Automatic when none is chosen."""
        chosen = self.coordinator.data.selected_tray if self.coordinator.data else None
        for label, tray_id in PRINT_TRAY_OPTIONS.items():
            if tray_id == chosen:
                return label
        return "Automatic"

    @property
    def available(self) -> bool:
        """Available once the printer has reported its Canvas."""
        if not super().available:
            return False
        return self.entity_description.available_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """What each tray holds right now."""
        return _tray_labels(self.coordinator.data)

    async def async_select_option(self, option: str) -> None:
        """Remember the tray. Nothing is sent to the printer."""
        if option not in PRINT_TRAY_OPTIONS:
            msg = f"{option!r} is not a tray"
            raise ServiceValidationError(msg)
        self.coordinator.data.selected_tray = PRINT_TRAY_OPTIONS[option]
        self.async_write_ha_state()
