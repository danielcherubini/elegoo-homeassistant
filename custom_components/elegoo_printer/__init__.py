"""
Custom integration to integrate elegoo_printer with Home Assistant.

For more details about this integration, please refer to
https://github.com/danielcherubini/elegoo-homeassistant
"""

from __future__ import annotations

from datetime import timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING

from homeassistant.const import CONF_IP_ADDRESS, Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.loader import async_get_loaded_integration

from custom_components.elegoo_printer.elegoo_sdcp.client import ElegooPrinterClient

from .api import ElegooPrinterApiClient
from .const import CONF_PROXY_ENABLED, DOMAIN, LOGGER
from .coordinator import ElegooDataUpdateCoordinator
from .data import ElegooPrinterData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import ElegooPrinterConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.IMAGE,
    Platform.CAMERA,
    Platform.LIGHT,
    Platform.BUTTON,
]


# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
) -> bool:
    """
    Asynchronously sets up the Elegoo printer integration from a configuration entry.

    Initializes the data update coordinator and printer API client, performs the first data refresh, forwards setup to supported platforms, and registers a listener for entry updates. Raises ConfigEntryNotReady if the printer cannot be reached.

    Returns:
        bool: True if the integration is set up successfully.
    """
    coordinator = ElegooDataUpdateCoordinator(
        hass=hass,
        logger=LOGGER,
        name=DOMAIN,
        update_interval=timedelta(seconds=2),
        config_entry=entry,
    )

    config = {
        **(entry.data or {}),
        **(entry.options or {}),
    }

    client = await ElegooPrinterApiClient.async_create(
        config=MappingProxyType(config),
        logger=LOGGER,
    )

    if client is None:
        raise ConfigEntryNotReady("Failed to connect to the printer")

    entry.runtime_data = ElegooPrinterData(
        client=client,
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
    if client := entry.runtime_data.client:
        client.stop_proxy()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(
    hass: HomeAssistant, config_entry: ElegooPrinterConfigEntry
) -> bool:
    """Migrate old entry."""

    if config_entry.version == 1:
        # Migrating data by removing printer and re-adding it
        config = {
            **(config_entry.data or {}),
            **(config_entry.options or {}),
        }
        ip_address = config[CONF_IP_ADDRESS]
        proxy_enabled = config[CONF_PROXY_ENABLED]
        if ip_address is None:
            LOGGER.error("config migration failed, ip address is null")

        LOGGER.debug(
            "Migrating from version %s with ip_address: %s and proxy: %s",
            config_entry.version,
            ip_address,
            proxy_enabled,
        )
        client = ElegooPrinterClient(ip_address=ip_address, logger=LOGGER)
        printer = client.discover_printer(broadcast_address=ip_address)
        if printer:
            new_data = {}
            printer[0].proxy_enabled = proxy_enabled
            new_data = printer[0].to_dict()

            hass.config_entries.async_update_entry(
                config_entry, data=new_data, version=2
            )
            LOGGER.debug("Migration to version 2 successful")
        else:
            LOGGER.error("Config migration failed, no printer found")

    return True
