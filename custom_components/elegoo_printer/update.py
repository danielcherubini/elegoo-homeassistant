"""Update platform for elegoo_printer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from awesomeversion import AwesomeVersion, AwesomeVersionCompareException
from homeassistant.components.update import (
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)

from .const import LOGGER
from .definitions import PRINTER_FIRMWARE_UPDATE
from .entity import ElegooPrinterEntity
from .sdcp.models.enums import ProtocolVersion

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import ElegooDataUpdateCoordinator
    from .data import ElegooPrinterConfigEntry
    from .sdcp.models.printer import FirmwareUpdateInfo


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the update platform."""
    coordinator: ElegooDataUpdateCoordinator = entry.runtime_data.coordinator
    printer = coordinator.config_entry.runtime_data.api.printer

    # Same gate as the Firmware Update Available binary sensor: the
    # firmware check only has data for V3 (WebSocket/SDCP) printers.
    if printer.protocol_version != ProtocolVersion.V3:
        return

    LOGGER.debug("Adding firmware update entity")
    async_add_entities(
        [ElegooPrinterFirmwareUpdate(coordinator, PRINTER_FIRMWARE_UPDATE)],
        update_before_add=True,
    )


class ElegooPrinterFirmwareUpdate(ElegooPrinterEntity, UpdateEntity):
    """
    Firmware update entity, so the update shows up in Settings -> Updates.

    Read-only: the firmware is installed from the printer itself, so this
    entity reports versions and release notes but offers no install action.
    """

    _attr_supported_features = UpdateEntityFeature.RELEASE_NOTES

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        entity_description: UpdateEntityDescription,
    ) -> None:
        """Initialize the firmware update entity."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = coordinator.generate_unique_id(
            self.entity_description.key
        )

    @property
    def _info(self) -> FirmwareUpdateInfo:
        """Return the firmware info the coordinator fetches every 12 hours."""
        if self.coordinator.data is None:
            return {}
        return self.coordinator.data.firmware_update_info

    @property
    def installed_version(self) -> str | None:
        """
        Return the firmware version running on the printer.

        The printer's own version is kept live from its attributes, while
        ``current_version`` is a snapshot from the 12-hourly check, so the
        live one wins: right after an update it already shows the new version.
        """
        return (
            self.coordinator.config_entry.runtime_data.api.printer.firmware
            or self._info.get("current_version")
        )

    @property
    def latest_version(self) -> str | None:
        """
        Return the newest firmware version.

        Unknown until a firmware check has succeeded (a successful check
        always records ``current_version``). Elegoo's server only returns a
        version when an update exists, so with no update the installed
        version is the latest one.
        """
        if not self._info.get("current_version"):
            return None
        if self._info.get("update_available"):
            return self._info.get("latest_version")
        return self.installed_version

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """
        Compare the versions directly, prefix aside.

        The printer reports ``V1.1.40`` while the server answers ``1.4.46``
        (seen on a Centauri Carbon); ``AwesomeVersion`` already ignores a
        leading ``V``/``v``, so the two compare without normalising them
        first. Comparing the live installed version keeps the entity right
        after an update, when Elegoo's flag is stale until the next 12-hourly
        check; the flag is only the fallback for versions that can't be
        compared.
        """
        try:
            return AwesomeVersion(latest_version) > AwesomeVersion(installed_version)
        except AwesomeVersionCompareException:
            return bool(self._info.get("update_available"))

    @property
    def release_summary(self) -> str | None:
        """
        Return the changelog as the dashboard's inline summary.

        Home Assistant truncates this to 255 characters itself, so the full
        changelog is passed through: the summary shows on the Updates card and
        ``async_release_notes`` offers the whole thing in the dialog.
        """
        return self._info.get("changelog")

    async def async_release_notes(self) -> str | None:
        """Return Elegoo's changelog for the latest firmware."""
        return self._info.get("changelog")
