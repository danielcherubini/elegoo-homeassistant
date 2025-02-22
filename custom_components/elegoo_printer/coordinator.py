"""DataUpdateCoordinator for elegoo_printer."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.elegoo_printer.elegoo_sdcp.elegoo_printer import (
    ElegooPrinterClientWebsocketConnectionError,
    ElegooPrinterClientWebsocketError,
)

if TYPE_CHECKING:
    from .data import ElegooPrinterConfigEntry


# https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
class ElegooDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    config_entry: ElegooPrinterConfigEntry

    async def _async_update_data(self) -> Any:
        """Update data via library."""
        try:
            await self.config_entry.runtime_data.client.async_get_attributes()
            return await self.config_entry.runtime_data.client.async_get_status()
        except ElegooPrinterClientWebsocketConnectionError:
            try:
                connected = await self.config_entry.runtime_data.client.retry()
                if connected:
                    self.update_interval = timedelta(seconds=2)
                    await self.config_entry.runtime_data.client.async_get_attributes()
                    return (
                        await self.config_entry.runtime_data.client.async_get_status()
                    )
            except ElegooPrinterClientWebsocketError as e:
                self.update_interval = timedelta(minutes=5)
                raise UpdateFailed from e
        except (
            ElegooPrinterClientWebsocketError,
            OSError,
        ) as exception:
            self.update_interval = timedelta(minutes=5)
            raise UpdateFailed from exception
