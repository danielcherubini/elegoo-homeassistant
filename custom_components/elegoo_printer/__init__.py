"""
Custom integration to integrate elegoo_printer with Home Assistant.

For more details about this integration, please refer to
https://github.com/danielcherubini/elegoo-homeassistant
"""

from __future__ import annotations

from datetime import timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_IP_ADDRESS, Platform
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
    Platform.FAN,
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
                await hass.async_add_executor_job(client.elegoo_stop_proxy)

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
        except Exception as e:
            LOGGER.error(f"Error migrating config entry: {e}")
            return False

    if config_entry.version == 2:
        await async_migrate_unique_ids(hass, config_entry)
        hass.config_entries.async_update_entry(config_entry, version=3)
        LOGGER.debug("Migration to version 3 successful")
        return True

    return True


async def async_migrate_unique_ids(
    hass: HomeAssistant, entry: ElegooPrinterConfigEntry
) -> None:
    """Migrate entity unique IDs and re-parent entities to the correct device."""
    LOGGER.debug("Checking if unique ID migration is needed")
    machine_id = entry.data.get("id")
    machine_name = entry.data.get("name")
    LOGGER.debug("Migration - machine_id: %s, machine_name: %s", machine_id, machine_name)

    if not machine_name:
        LOGGER.debug("No machine name, skipping migration")
        return

    sanitized_machine_name = machine_name.replace(" ", "_").lower()

    # Get the device associated with the current config entry (which should now have the new machine_id)
    dev_reg = dr.async_get(hass)
    # Find the device associated with this config entry.
    # The config entry's unique_id should now be the machine_id after migration to version 2.
    # We need to find the device that has this config_entry_id.
    # There might be multiple devices if the migration created a new one.
    # We want the one that has the new machine_id as an identifier.
    new_device_entry = None
    for device_entry in dev_reg.devices.values():
        if entry.entry_id in device_entry.config_entries:
            # Check if the device has the new machine_id as an identifier
            if (DOMAIN, machine_id) in device_entry.identifiers:
                new_device_entry = device_entry
                break

    new_device_id = new_device_entry.id if new_device_entry else None

    @callback
    def async_migrate_callback(
        entity_entry: er.RegistryEntry,
    ) -> dict[str, Any] | None:
        """Migrate a single entity entry."""
        # old: {machine_name}_{key}
        # new: {machine_id}_{key}
        if (
            entity_entry.unique_id.startswith(sanitized_machine_name)
            and machine_id not in entity_entry.unique_id
        ):
            new_unique_id = entity_entry.unique_id.replace(
                sanitized_machine_name, machine_id
            )
            # Replace any remaining occurrences of the old sanitized machine name
            # in case it appeared multiple times in the unique ID (e.g., for proxy entities)
            new_unique_id = new_unique_id.replace(sanitized_machine_name, machine_id)

            LOGGER.debug(
                "Migrating unique_id from %s to %s",
                entity_entry.unique_id,
                new_unique_id,
            )

            # Prepare update data for entity
            update_data = {"new_unique_id": new_unique_id}

            # If a new device ID is available, and the entity is not already linked to it,
            # update the device_id
            if new_device_id and entity_entry.device_id != new_device_id:
                LOGGER.debug(
                    "Re-parenting entity %s from device %s to %s",
                    entity_entry.entity_id,
                    entity_entry.device_id,
                    new_device_id,
                )
                update_data["new_device_id"] = new_device_id
            return update_data
        return None

    # Perform the unique ID and device_id migration
    await er.async_migrate_entries(hass, entry.entry_id, async_migrate_callback)

    # After migration, check for and remove any remaining old entities
    entity_registry = er.async_get(hass)
    entities_to_remove = []
    for entity_entry in list(entity_registry.entities.values()):
        if (
            entity_entry.config_entry_id == entry.entry_id
            and entity_entry.unique_id.startswith(sanitized_machine_name)
            and machine_id not in entity_entry.unique_id
        ):
            LOGGER.debug(
                "Found old entity to remove: %s (unique_id: %s)",
                entity_entry.entity_id,
                entity_entry.unique_id,
            )
            entities_to_remove.append(entity_entry.entity_id)

    for entity_id in entities_to_remove:
        LOGGER.debug("Removing old entity: %s", entity_id)
        entity_registry.async_remove(entity_id)

    # Finally, remove any old devices that are no longer associated with any entities
    # This is a more aggressive cleanup, but necessary if the migration leaves orphaned devices.
    # We need to find devices that are linked to this config entry but do not have the new machine_id as an identifier.
    devices_to_remove = []
    for device_entry in dev_reg.devices.values():
        if entry.entry_id in device_entry.config_entries and (DOMAIN, machine_id) not in device_entry.identifiers:
            # Check if this device has any entities still linked to it.
            # If not, it's an orphaned device from the old config.
            if not any(e.device_id == device_entry.id for e in entity_registry.entities.values()):
                LOGGER.debug(
                    "Found old device to remove: %s (identifiers: %s)",
                    device_entry.id,
                    device_entry.identifiers,
                )
                devices_to_remove.append(device_entry.id)

    for device_id in devices_to_remove:
        LOGGER.debug("Removing old device: %s", device_id)
        dev_reg.async_remove_device(device_id)
