"""DataUpdateCoordinator for elegoo_printer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.elegoo_printer.api import ElegooPrinterConnectionError
from custom_components.elegoo_printer.const import LOGGER

if TYPE_CHECKING:
    from .data import ElegooPrinterConfigEntry


# https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
class ElegooDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    config_entry: ElegooPrinterConfigEntry

    async def _async_update_data(self) -> Any:
        """
        Asynchronously fetches the latest attributes and status from the Elegoo printer.

        Attempts to reconnect and retry data retrieval if the printer is temporarily unavailable. Raises `ConfigEntryNotReady` if the printer cannot be reached or data cannot be fetched after retry attempts.

        Returns:
            The latest printer status data.
        """
        try:
            await self.config_entry.runtime_data.client.async_get_attributes()
            return await self.config_entry.runtime_data.client.async_get_status()
        except ElegooPrinterConnectionError as e:
            LOGGER.warning("Could not connect to Elegoo printer: %s", e)
            raise UpdateFailed(f"Error communicating with Elegoo printer: {e}") from e
        except OSError as e:
            LOGGER.warning(f"OSError while communicating with Elegoo printer: {e}")
            raise UpdateFailed("Unexpected Error") from e

    def generate_unique_id(self, key: str) -> str:
        """
        Generate a unique entity identifier by combining the printer's name or machine ID with the given key.

        If the printer name is unavailable or empty, the machine ID is used. Otherwise, the printer name is sanitized (spaces replaced with underscores and converted to lowercase) before concatenation with the key.

        Parameters:
            key (str): Suffix to append for uniqueness.

        Returns:
            str: The generated unique identifier.
        """
        machine_name = self.config_entry.data["name"]
        machine_id = self.config_entry.data["id"]
        if not machine_name or machine_name == "":
            return machine_id + "_" + key

        return machine_name.replace(" ", "_").lower() + "_" + key
