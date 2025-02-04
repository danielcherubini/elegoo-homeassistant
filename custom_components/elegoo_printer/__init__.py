"""
Custom integration to integrate elegoo_printer with Home Assistant.

For more details about this integration, please refer to
https://github.com/ludeeus/elegoo_printer
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import CONF_IP_ADDRESS, Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_loaded_integration

from .api import ElegooPrinterApiClient
from .const import DOMAIN, LOGGER
from .coordinator import ElegooDataUpdateCoordinator
from .data import ElegooPrinterData
from .elegoo.elegoo_printer import ElegooPrinterClient

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import ElegooPrinterConfigEntry

PLATFORMS: list[Platform] = [Platform.SENSOR]


# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    coordinator = ElegooDataUpdateCoordinator(
        hass=hass,
        logger=LOGGER,
        name=DOMAIN,
        update_interval=timedelta(seconds=2),
    )

    elegoo_printer = ElegooPrinterClient(entry.data[CONF_IP_ADDRESS])
    printer = elegoo_printer.discover_printer()
    if printer is None:
        LOGGER.info("No printers discovered.")
        return False

    connected = elegoo_printer.connect_printer()
    if connected:
        LOGGER.info("Polling Started")
        entry.runtime_data = ElegooPrinterData(
            client=ElegooPrinterApiClient(
                ip_address=entry.data[CONF_IP_ADDRESS],
                elegoo_printer=elegoo_printer,
                session=async_get_clientsession(hass),
            ),
            integration=async_get_loaded_integration(hass, entry.domain),
            coordinator=coordinator,
        )

    # https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
