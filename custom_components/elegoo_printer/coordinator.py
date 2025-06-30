"""DataUpdateCoordinator for elegoo_printer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryNotReady, PlatformNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.elegoo_printer.elegoo_sdcp.client import (
    ElegooPrinterClientWebsocketError,
)

if TYPE_CHECKING:
    from .data import ElegooPrinterConfigEntry


# https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
class ElegooDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    config_entry: ElegooPrinterConfigEntry

    async def _async_update_data(self) -> Any:
        """
        Fetches the latest printer attributes and status asynchronously from the Elegoo printer API.

        If a websocket connection error occurs, attempts to reconnect and retries the data fetch. Adjusts the polling interval based on connection success or failure. Raises UpdateFailed if data cannot be retrieved.
        """
        try:
            await self.config_entry.runtime_data.client.async_get_attributes()
            return await self.config_entry.runtime_data.client.async_get_status()
        except PlatformNotReady:
            try:
                connected = await self.config_entry.runtime_data.client.retry()
                if connected:
                    await self.config_entry.runtime_data.client.async_get_attributes()
                    await self.config_entry.runtime_data.client.async_get_current_task()
                    return (
                        await self.config_entry.runtime_data.client.async_get_status()
                    )
            except Exception as e:
                raise ConfigEntryNotReady from e
        except (
            ElegooPrinterClientWebsocketError,
            OSError,
        ) as exception:
            raise ConfigEntryNotReady from exception

    def generate_unique_id(self, key: str) -> str:
        """
        Generate a unique identifier string for an entity based on the printer's name or ID and the provided key.

        If the printer name is missing or empty, the unique ID is formed by combining the machine ID and the key. Otherwise, the unique ID uses the sanitized (lowercased and underscores for spaces) machine name and the key.
        """
        machine_name = self.config_entry.data["name"]
        machine_id = self.config_entry.data["id"]
        if not machine_name or machine_name == "":
            return machine_id + "_" + key

        return machine_name.replace(" ", "_").lower() + "_" + key
