"""
Custom integration to integrate elegoo_printer with Home Assistant.

For more details about this integration, please refer to
https://github.com/danielcherubini/elegoo-homeassistant
"""

from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import TYPE_CHECKING

import voluptuous as vol
from aiohttp import ClientError
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_IP_ADDRESS, Platform, UnitOfTime
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
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
from .const import (
    CONF_PROXY_ENABLED,
    CONFIG_VERSION_1,
    CONFIG_VERSION_2,
    CONFIG_VERSION_3,
    CONFIG_VERSION_4,
    CONFIG_VERSION_5,
    DOMAIN,
    LOGGER,
)
from .coordinator import ElegooDataUpdateCoordinator
from .data import ElegooPrinterData
from .websocket.server import ElegooPrinterServer

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

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

SERVICE_UPDATE_IP = "update_ip"

# min-length: a watcher script that forwards an empty address must fail loudly,
# not silently. Plain str (no IP regex): hostnames are allowed, matching the
# config flow's plain TextSelector.
SERVICE_UPDATE_IP_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): str,
        vol.Required(CONF_IP_ADDRESS): vol.Length(min=1),
    }
)


async def _async_update_ip(hass: HomeAssistant, call: ServiceCall) -> dict:
    """
    Update a config entry's printer IP address and reload the entry.

    Accepts ``entry_id`` and ``ip_address``; writes the new address into the
    entry data, reloads the entry so the integration reconnects at the new
    address, and returns a definitive ``{success, error|message}`` result
    (supports_response=optional).
    """
    entry_id = call.data["entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)
    # The services.yaml selector constrains the entry picker in the UI, but a
    # programmatic/automation call can pass any entry_id — this domain check
    # is the real guard, not dead code.
    if entry is None or entry.domain != DOMAIN:
        return {
            "success": False,
            "error": f"Config entry {entry_id} not found for {DOMAIN}",
        }

    new_ip = call.data[CONF_IP_ADDRESS]
    LOGGER.info("Updating printer IP to %s for entry %s", new_ip, entry_id)

    # Update the entry data only: the integration has no options flow; the
    # config flow and all migrations write the IP into entry.data.
    # Pin note: async_update_entry is a sync @callback on the HA 2025.4.0 pin
    # — it must NOT be awaited. If the HA pin is ever advanced, re-verify
    # this call form.
    new_data = {**entry.data, CONF_IP_ADDRESS: new_ip}
    hass.config_entries.async_update_entry(entry, data=new_data)

    try:
        # On a still-LOADED entry, the data write above also fires the
        # entry's add_update_listener(async_reload_entry) reload. HA
        # serializes both on the entry's setup lock (the final state is
        # deterministic) but the second full unload→setup cycle would still
        # run (extra printer API connect/disconnect, MQTT broker
        # stop/start, entity re-registration churn, ~2x latency). In the
        # headline use case (the printer is unreachable at the old IP, the
        # entry is in SETUP_RETRY/SETUP_ERROR, and there is no live update
        # listener) this explicit reload is the only cycle. No awaitable
        # public API exists to wait for the listener-driven reload (verified
        # HA 2025.4 and 2026.8), so this explicit one is what lets the
        # handler return a definitive result.
        await hass.config_entries.async_reload(entry.entry_id)
    except ConfigEntryError as err:
        # DEFENSIVE catch: on HA 2025.4 the entry setup flow catches
        # ConfigEntryError internally (marking the entry SETUP_ERROR) instead
        # of propagating it, so the state check below is the real failure
        # signal; this branch guards other HA versions/paths.
        return {
            "success": False,
            "error": f"Reload failed after IP update: {err}",
        }

    # The real failure signal: on HA 2025.4 a failed reload leaves the entry
    # not-LOADED rather than raising.
    if entry.state is not ConfigEntryState.LOADED:
        LOGGER.warning(
            "Reload after IP update failed for entry %s (state: %s)",
            entry_id,
            entry.state,
        )
        return {
            "success": False,
            "error": (
                f"Printer unreachable at new address {new_ip} "
                f"(entry state: {entry.state})"
            ),
        }

    LOGGER.info("IP updated to %s and entry %s reloaded", new_ip, entry_id)
    return {"success": True, "message": f"IP updated to {new_ip} and entry reloaded"}


# https://developers.home-assistant.io/docs/creating_integration_file_structure/#defining-services
async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: ARG001
    """Set up the Elegoo Printer component."""
    # `config` is part of the HA async_setup contract (the manifest-level
    # service registration is unconditional); ARG001 noqa is intentional.
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_IP,
        _async_update_ip,
        schema=SERVICE_UPDATE_IP_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
) -> bool:
    """
    Asynchronously sets up the Elegoo printer integration from a configuration entry.

    Initializes the data update coordinator and printer API client,
    performs the first data refresh, forwards setup to supported platforms,
    and registers a listener for entry updates.
    Raises ConfigEntryNotReady if the printer cannot be reached.

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
        config_entry=entry,
    )

    if client is None:
        msg = "Failed to connect to the printer"
        raise ConfigEntryNotReady(msg)

    entry.runtime_data = ElegooPrinterData(
        api=client,
        integration=async_get_loaded_integration(hass, entry.domain),
        coordinator=coordinator,
    )

    # https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # If first refresh fails, clean up the client resources
        try:
            await client.elegoo_disconnect()
            await client.elegoo_stop_mqtt_broker()
            if client.server:
                await ElegooPrinterServer.release_reference()
        except Exception as cleanup_error:  # noqa: BLE001
            LOGGER.warning("Error during cleanup after failed setup: %s", cleanup_error)
        raise

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and (client := entry.runtime_data.api):
        # Disconnect client first
        try:
            await asyncio.shield(client.elegoo_disconnect())
        except (asyncio.CancelledError, ClientError, OSError, RuntimeError) as e:
            LOGGER.warning("Error disconnecting client: %s", e, exc_info=True)

        # Remove this specific printer from proxy server registry
        try:
            should_stop = await asyncio.shield(
                ElegooPrinterServer.remove_printer_from_server(client.printer, LOGGER)
            )
            if not should_stop:
                # Server continues with other printers, just decrement reference count
                await asyncio.shield(ElegooPrinterServer.release_reference())
        except (asyncio.CancelledError, OSError, RuntimeError) as e:
            LOGGER.warning(
                "Error removing printer from proxy server: %s", e, exc_info=True
            )

        # Stop MQTT broker if it was started by this client
        # Don't use shield here - we want this to be cancellable
        try:
            await client.elegoo_stop_mqtt_broker()
        except (OSError, RuntimeError) as e:
            LOGGER.warning("Error stopping MQTT broker: %s", e, exc_info=True)

    return unload_ok


async def async_reload_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(  # noqa: PLR0911, PLR0912, PLR0915
    hass: HomeAssistant, config_entry: ElegooPrinterConfigEntry
) -> bool:
    """Migrate old entry."""
    if config_entry.version == CONFIG_VERSION_1:
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

    if config_entry.version == CONFIG_VERSION_2:
        # Migrate to version 3: Update native_unit_of_measurement for remaining_print_time  # noqa: E501
        entity_registry: EntityRegistry = async_get(hass)
        entries = entity_registry.entities.get_entries_for_config_entry_id(
            config_entry.entry_id
        )
        for entry in entries:
            if (
                entry.device_class == SensorDeviceClass.DURATION
                and entry.native_unit_of_measurement == UnitOfTime.SECONDS
            ):
                entity_registry.async_update_entity(
                    entry.entity_id,
                    native_unit_of_measurement=UnitOfTime.MILLISECONDS,
                )
        hass.config_entries.async_update_entry(config_entry, version=3)
        LOGGER.debug("Migration to version 3 successful")

    if config_entry.version == CONFIG_VERSION_3:
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
                    "Migration v3->v4 failed: 'name' or 'id' field is missing."
                )
                return False

            old_identifier_slug = old_identifier_name.lower().replace(" ", "_")
            LOGGER.debug(
                "MIGRATION CHECK: Using old identifier prefix: '%s'",
                old_identifier_slug,
            )

            if not old_identifier_slug or not new_identifier:
                LOGGER.error(
                    "Migration v3->v4 failed: 'name' or 'id' field is missing."
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
                        "Removing old camera entity '%s' to allow for clean re-creation.",  # noqa: E501
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

    if config_entry.version == CONFIG_VERSION_4:
        new_data = {**config_entry.data}
        transport = new_data.get("transport_type")
        new_data["has_canvas"] = transport == "cc2_mqtt"
        hass.config_entries.async_update_entry(
            config_entry, data=new_data, version=CONFIG_VERSION_5
        )
        LOGGER.debug(
            "Migration to version 5 successful: has_canvas=%s",
            new_data["has_canvas"],
        )

    return True
