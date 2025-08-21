"""
Custom integration to integrate elegoo_printer with Home Assistant.

For more details about this integration, please refer to
https://github.com/danielcherubini/elegoo-homeassistant
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CONF_IP_ADDRESS, Platform, UnitOfTime
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_registry import (
    EntityRegistry,
    async_get,
)
from homeassistant.loader import async_get_loaded_integration

from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient

from .api import ElegooPrinterApiClient
from .const import CONF_PROXY_ENABLED, DOMAIN, LOGGER
from .coordinator import ElegooDataUpdateCoordinator
from .data import ElegooPrinterData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import ElegooPrinterConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.IMAGE,
    Platform.CAMERA,
    Platform.LIGHT,
    Platform.BUTTON,
    Platform.FAN,
    Platform.SELECT,
    Platform.NUMBER,
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
    coordinator = ElegooDataUpdateCoordinator(hass=hass, entry=entry)

    config = {
        **(entry.data or {}),
        **(entry.options or {}),
    }

    client = await ElegooPrinterApiClient.async_create(
        config=MappingProxyType(config),
        logger=LOGGER,
        hass=hass,
    )

    if client is None:
        raise ConfigEntryNotReady("Failed to connect to the printer")

    entry.runtime_data = ElegooPrinterData(
        api=client,
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
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        if client := entry.runtime_data.api:
            await client.elegoo_disconnect()
            if client.printer and client.printer.proxy_enabled:
                await client.elegoo_stop_proxy()

    return unload_ok


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
        try:
            # Migrating data by removing printer and re-adding it
            config = {
                **(config_entry.data or {}),
                **(config_entry.options or {}),
            }
            ip_address = config.get(CONF_IP_ADDRESS)
            proxy_enabled = config.get(CONF_PROXY_ENABLED, False)
            if ip_address is None:
                LOGGER.error("Config migration failed, IP address is null")
                return False

            LOGGER.debug(
                "Migrating from version %s with ip_address: %s and proxy: %s",
                config_entry.version,
                ip_address,
                proxy_enabled,
            )
            client = ElegooPrinterClient(
                ip_address=ip_address,
                logger=LOGGER,
                session=async_get_clientsession(hass),
            )
            printer = await hass.async_add_executor_job(
                client.discover_printer, ip_address
            )
            if printer and len(printer) > 0:
                printer[0].proxy_enabled = proxy_enabled
                new_data = printer[0].to_dict()

                hass.config_entries.async_update_entry(
                    config_entry, data=new_data, version=2
                )
                LOGGER.debug("Migration to version 2 successful")
            else:
                LOGGER.error("Config migration failed, no printer found")
                return False
        except (ConnectionError, IndexError, KeyError) as e:
            LOGGER.error(f"Error migrating config entry: {e}")
            return False

    if config_entry.version == 2:
        # Migrate to version 3: Update native_unit_of_measurement for remaining_print_time
        entity_registry: EntityRegistry = async_get(hass)
        entries = entity_registry.entities.get_entries_for_config_entry_id(
            config_entry.entry_id
        )
        for entry in entries:
            if entry.device_class == SensorDeviceClass.DURATION:
                if entry.native_unit_of_measurement == UnitOfTime.SECONDS:
                    entity_registry.async_update_entity(
                        entry.entity_id,
                        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
                    )
        hass.config_entries.async_update_entry(config_entry, version=3)
        LOGGER.debug("Migration to version 3 successful")

    if config_entry.version == 3:
        LOGGER.debug("Migrating to version 4: updating unique IDs from 'name' to 'id'.")
        try:
            # --- 1. Get Old and New Identifiers ---
            config = {
                **(config_entry.data or {}),
                **(config_entry.options or {}),
            }
            # Old ID was based on the 'name' field.
            old_identifier_name = config.get("name")
            # New stable ID is the 'id' field.
            new_identifier = config.get("id")

            if not old_identifier_name or not new_identifier:
                LOGGER.error(
                    "Migration v3->v4 failed: 'name' or 'id' field is missing from config."
                )
                return False

            old_identifier_slug = old_identifier_name.lower().replace(" ", "_")
            LOGGER.debug(
                "MIGRATION CHECK: Using old identifier prefix: '%s'",
                old_identifier_slug,
            )

            if not old_identifier_slug or not new_identifier:
                LOGGER.error(
                    "Migration v3->v4 failed: 'name' or 'id' field is missing from config."
                )
                return False

            if old_identifier_slug == new_identifier:
                LOGGER.debug("Identifier is already up-to-date. Finalizing migration.")
                hass.config_entries.async_update_entry(config_entry, version=4)
                return True

            # --- 2. Get Device and Entity Registries ---
            device_registry = dr.async_get(hass)
            entity_registry = er.async_get(hass)

            # --- 3. Migrate the Device Registry ---
            device_entries = dr.async_entries_for_config_entry(
                device_registry, config_entry.entry_id
            )
            device_entry = device_entries[0] if device_entries else None
            if device_entry:
                new_identifiers = {(DOMAIN, new_identifier)}
                LOGGER.debug("Updating device identifiers to %s", new_identifiers)
                device_registry.async_update_device(
                    device_entry.id, new_identifiers=new_identifiers
                )

            # --- 4. Migrate the Entity Registry ---
            entity_entries = er.async_get(
                hass
            ).entities.get_entries_for_config_entry_id(config_entry.entry_id)
            for entry in entity_entries:
                LOGGER.debug(
                    "MIGRATION CHECK: Comparing with entity unique_id: '%s'",
                    entry.unique_id,
                )
                # If it's the camera entity, remove it so it can be recreated cleanly.
                if entry.domain == "camera":
                    LOGGER.debug(
                        "Removing old camera entity '%s' to allow for clean re-creation.",
                        entry.entity_id,
                    )
                    entity_registry.async_remove(entry.entity_id)
                    continue  # Skip to the next entity

                # Check if the entity's unique_id uses the old identifier
                if entry.unique_id.lower().startswith(old_identifier_slug.lower()):
                    LOGGER.debug("MIGRATION MATCH FOUND!")
                    # Replace the old identifier prefix with the new one
                    new_unique_id = entry.unique_id.replace(
                        old_identifier_slug, new_identifier, 1
                    )
                    LOGGER.debug(
                        "Migrating entity '%s' to new unique_id: %s",
                        entry.entity_id,
                        new_unique_id,
                    )
                    entity_registry.async_update_entity(
                        entry.entity_id, new_unique_id=new_unique_id
                    )

            # --- 5. Update Config Entry Version ---
            hass.config_entries.async_update_entry(config_entry, version=4)
            LOGGER.info(
                "Migration to version 4 successful for printer ID %s", new_identifier
            )

        except (KeyError, ValueError) as e:
            LOGGER.error(
                "Error migrating config entry to version 4: %s", e, exc_info=True
            )
            return False
    return True
