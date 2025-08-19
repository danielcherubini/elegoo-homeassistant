"""DataUpdateCoordinator for elegoo_printer."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.elegoo_printer.const import LOGGER
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
    ElegooPrinterNotConnectedError,
)

if TYPE_CHECKING:
    from .data import ElegooPrinterConfigEntry


# https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
class ElegooDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    config_entry: ElegooPrinterConfigEntry

    def __init__(self, hass, *, entry: ElegooPrinterConfigEntry) -> None:
        """Initialize."""
        self.online = False
        self.config_entry = entry
        super().__init__(
            hass,
            LOGGER,
            name=f"{entry.title}",
            update_interval=timedelta(seconds=1),
        )

    async def _async_update_data(self) -> Any:
        """
        Asynchronously fetches and updates the latest attributes and status from the Elegoo printer.

        Dynamically adjusts the polling interval based on connection status. If the printer is disconnected, attempts to reconnect and modifies the update interval accordingly.

        Returns:
            The most recent printer data retrieved from the client.

        Raises:
            UpdateFailed: If a connection or operating system error prevents data retrieval.
        """
        try:
            if not self.config_entry.runtime_data.api.client.is_connected:
                await self.config_entry.runtime_data.api.reconnect()
            self.data = (
                await self.config_entry.runtime_data.api.async_get_printer_data()
            )
            self.online = True
            if self.update_interval != timedelta(seconds=1):
                self.update_interval = timedelta(seconds=1)
            return self.data
        except (ElegooPrinterConnectionError, ElegooPrinterNotConnectedError) as e:
            self.online = False
            if self.update_interval != timedelta(seconds=30):
                self.update_interval = timedelta(seconds=30)
            LOGGER.info("Elegoo printer is not connected: %s", e)
            return self.data  # Return last known data
        except OSError as e:
            self.online = False
            LOGGER.warning(
                "OSError while communicating with Elegoo printer: [Errno %s] %s",
                e.errno,
                e.strerror,
            )
            raise UpdateFailed(f"Unexpected Error: {e.strerror}") from e

    def generate_unique_id(self, key: str) -> str:
        """
        Create a unique identifier for an entity by combining the sanitized printer name or machine ID with a specified key.

        If the printer name is unavailable or empty, the machine ID is used as the prefix. Otherwise, the printer name is converted to lowercase and spaces are replaced with underscores before appending the key.

        Parameters:
            key (str): Suffix to ensure uniqueness for the entity.

        Returns:
            str: The generated unique identifier.
        """
        machine_id = self.config_entry.data["id"]

        return machine_id + "_" + key
