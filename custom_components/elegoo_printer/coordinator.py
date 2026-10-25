"""DataUpdateCoordinator for elegoo_printer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from homeassistant.core import HomeAssistantError, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.const import (
    CONF_HAS_CANVAS,
    CONF_ID,
    DOMAIN,
    LOGGER,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    PRINT_TRANSPORT_ERRORS,
    ElegooPrinterNotConnectedError,
)
from custom_components.elegoo_printer.sdcp.models.enums import TransportType

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import ElegooPrinterConfigEntry


# https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
class ElegooDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    config_entry: ElegooPrinterConfigEntry

    def __init__(self, hass: HomeAssistant, *, entry: ElegooPrinterConfigEntry) -> None:
        """Initialize."""
        self.online = False
        self.config_entry = entry
        # Options-flow updates land in entry.options, not entry.data — merge
        # so a Canvas toggle set after initial setup is honored.
        merged_config = {**entry.data, **(entry.options or {})}
        self._has_canvas = merged_config.get(CONF_HAS_CANVAS, False)
        self._last_firmware_check: datetime | None = None
        self._firmware_check_interval = timedelta(hours=12)  # Check every 12 hours
        self._last_canvas_check: datetime | None = None
        self._canvas_check_interval = timedelta(seconds=30)  # Check every 30 seconds
        self._last_file_list_check: datetime | None = None
        self._file_list_check_interval = timedelta(minutes=10)
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{entry.title}",
            update_interval=timedelta(seconds=2),
        )

    @callback
    def async_start_offline(self) -> None:
        """
        Start the coordinator against a printer that is switched off (#171).

        Used instead of ``async_config_entry_first_refresh`` on the offline path
        of ``async_setup_entry``: that helper raises ``ConfigEntryNotReady`` when
        its refresh fails, which would put the entry straight back into the
        "Failed setup, will retry" state the option exists to avoid. Nothing can
        be refreshed here anyway — there is no connection — so this only marks
        the state a lost connection already produces, and entities render as
        ``unavailable`` instead of as live data.

        It performs **no I/O at all**: no refresh, no reconnect attempt, no
        scheduling. Arming the retry timer is ``async_add_listener``'s job (it
        schedules a refresh only for its very first listener, which the first
        entity added after platform forwarding is), and reconnecting is the
        existing poll's job — a failed refresh already drops to the 30 s cadence
        and calls ``api.reconnect()``, so a powered-on printer recovers by
        itself. A second reconnect mechanism would only race that one.

        The interval is set *before* any listener attaches, because
        ``_schedule_refresh`` no-ops while ``update_interval`` is unset and this
        runs before setup forwards the platforms.

        ``last_update_success`` is assigned rather than routed through
        ``async_set_update_error``: that helper logs at ERROR on the
        success-to-failure transition, and the attribute is still ``True`` at
        setup, so it would print "Error requesting <printer> data" for a printer
        the user has simply switched off — the exact noise this feature removes.
        Nothing is listening yet, so there is no notification to send either.
        """
        self.online = False
        self.update_interval = timedelta(seconds=30)
        self.last_update_success = False

    def _ensure_connected(self, api: Any) -> None:
        """
        Refuse to poll a client that is not connected (#171).

        MQTT and CC2 answer ``get_printer_status``/``get_printer_attributes``
        from a cache *without raising*, so a poll of a powered-off printer would
        "succeed" and every entity would render fabricated defaults (0 C,
        "idle", fans off) as live truth. Raising here instead of asking the
        transports to be honest covers all three of them the same way, and sends
        the poll down the existing failure branch below: entities unavailable,
        30 s cadence, reconnect attempted. The side-checks never see a
        disconnected client, so they need no gate of their own.

        The gate sees what a transport can see. For WebSocket that is the link to
        the printer; a CC2 hosts its own broker, so powering it off drops our
        link too. A plain MQTT printer is the exception: ``is_connected`` tracks
        our link to the *broker*, so a printer that goes silent while its broker
        outlives it — including the broker this integration hosts itself — keeps
        answering from cache. That case behaves as it always has and is still
        open: closing it needs a printer-side heartbeat, which the integration
        has no way to ask the printer for.

        Raises:
            ElegooPrinterNotConnectedError: if the client is not connected.

        """
        if not api.is_connected:
            msg = "Printer not connected, cannot fetch data."
            raise ElegooPrinterNotConnectedError(msg)

    async def _async_update_data(self) -> Any:
        """
        Asynchronously fetches and updates the latest attributes and status from the Elegoo printer.

        If the printer is disconnected, it attempts to reconnect and adjusts the update interval.

        Returns:
            The most recent printer data retrieved from the client.

        Raises:
            UpdateFailed: If communication with the printer fails.

        """  # noqa: E501
        try:
            api = self.config_entry.runtime_data.api
            self._ensure_connected(api)
            self.data = await api.async_get_printer_data()

            # Get API reference for periodic checks
            now = datetime.now(UTC)

            # Check if we need to update firmware info
            if (
                self._last_firmware_check is None
                or now - self._last_firmware_check >= self._firmware_check_interval
            ):
                LOGGER.debug("Checking for firmware updates")
                try:
                    firmware_info = await api.async_get_firmware_update_info()
                    if firmware_info:
                        self.data.firmware_update_info = firmware_info
                except httpx.HTTPError as fw_err:
                    LOGGER.debug("Firmware update check failed: %s", fw_err)
                finally:
                    # Rate-limit even on failure to avoid hammering the endpoint
                    self._last_firmware_check = now

            if self._has_canvas and (
                self._last_canvas_check is None
                or now - self._last_canvas_check >= self._canvas_check_interval
            ):
                LOGGER.debug("Checking Canvas/AMS status")
                try:
                    await api.async_get_canvas_status()
                except PRINT_TRANSPORT_ERRORS:
                    LOGGER.debug("Canvas status check failed")
                finally:
                    # Rate-limit even on failure to avoid hammering the endpoint
                    self._last_canvas_check = now

            await self._refresh_file_list_if_due(api, now)

            self._replay_cc2_print_status_transitions()
            self._sync_device_firmware()

            self.online = True
            if self.update_interval != timedelta(seconds=2):
                self.update_interval = timedelta(seconds=2)
            return self.data  # noqa: TRY300
        except PRINT_TRANSPORT_ERRORS as e:
            # One INFO line per live-to-offline transition, DEBUG for every
            # consecutive failure after it (see ``_log_failed_poll``).
            was_online = self.online
            self.online = False
            self._log_failed_poll(e, was_online=was_online)
            if self.update_interval != timedelta(seconds=30):
                self.update_interval = timedelta(seconds=30)

            try:
                await self.config_entry.runtime_data.api.reconnect()
            except (ConnectionError, TimeoutError, *PRINT_TRANSPORT_ERRORS) as recon_e:
                # A transport must never reach Home Assistant's catch-all from
                # here: it reports anything that escapes the poll as
                # "Unexpected error fetching <printer> data" with a full
                # traceback, for a printer that is simply switched off. A
                # ``reconnect()`` that behaves returns False instead of raising
                # (see ``ElegooPrinterApiClient.reconnect``); this is the net that
                # catches one that does not.
                LOGGER.warning("Error during reconnect attempt: %s", recon_e)

            msg = f"Failed to communicate with printer: {e}"
            raise UpdateFailed(msg) from e
        except OSError as e:
            self.online = False
            LOGGER.warning(
                "OSError while communicating with Elegoo printer: [Errno %s] %s",
                e.errno,
                e.strerror,
            )
            msg = f"Unexpected Error: {e.strerror}"
            raise UpdateFailed(msg) from e

    def _sync_device_firmware(self) -> None:
        """
        Keep the device record's firmware version in step with the printer.

        Home Assistant reads ``entity.device_info`` once, when the entity is
        added, so a fresher value there never reaches the device record. The
        printer's live version (kept from its attributes frames) is written
        straight to the registry instead, and only when it differs: writing
        ``entry.data`` would fire the entry's update listener, which is wired
        to a full reload (#436).
        """
        entry = self.config_entry
        live = entry.runtime_data.api.printer.firmware
        identifier = entry.data.get(CONF_ID)
        if not live or not identifier:
            return

        registry = dr.async_get(self.hass)
        device_entry = registry.async_get_device(identifiers={(DOMAIN, identifier)})
        if device_entry is None or device_entry.sw_version == live:
            return

        # The registry is shared with everything else in Home Assistant, and a
        # device can be removed between the lookup above and this write (a user
        # deleting the device, an entry unload, another integration pruning it).
        # ``async_update_device`` then raises -- ``KeyError`` out of its own
        # ``self.devices[device_id]`` lookup, ``HomeAssistantError`` from its
        # collision checks -- and this runs inside ``_async_update_data``, where
        # core's catch-all would report "Unexpected error fetching <printer>
        # data" with a full traceback on EVERY poll (2 s while online). A stale
        # version on a device record is not worth that noise, which is the same
        # reason ``_log_failed_poll`` and the ``reconnect()`` net exist; the next
        # poll simply looks the device up again.
        try:
            registry.async_update_device(device_entry.id, sw_version=live)
        except (HomeAssistantError, KeyError) as err:
            LOGGER.debug(
                "Could not write firmware version '%s' to device %s: %r",
                live,
                device_entry.id,
                err,
            )
            return

        # Logged once the write landed: the line reads as "the device record now
        # says X", which is only true if the registry took it, and announcing a
        # version the record never gained is the kind of statement this file
        # avoids making before it is owed.
        LOGGER.info(
            "Updating firmware version for printer %s: '%s' -> '%s'",
            entry.title,
            device_entry.sw_version,
            live,
        )

    def _log_failed_poll(self, error: Exception, *, was_online: bool) -> None:
        """
        Report a failed poll once, at the volume the outage deserves.

        ``INFO`` only on the live-to-offline transition — the moment a user
        wants to hear about — and ``DEBUG`` for every consecutive failure after
        it. A printer that is simply switched off is polled every 30 s, so
        repeating the line would print ~2,880 lines a day, which is the exact
        complaint #171 exists to answer.

        The wording follows the two cases: a printer that was up has *lost* a
        connection, while one that came up offline (``async_start_offline``
        leaves ``online`` false) never had one to lose and is only "not
        reachable yet".

        Arguments:
            error: The transport error that failed the poll.
            was_online: Whether the previous poll had reached the printer.

        """
        if was_online:
            LOGGER.info(
                "Connection to printer %s lost, attempting to reconnect: %s",
                self.name,
                error,
            )
        else:
            LOGGER.debug(
                "Printer %s is not reachable yet, attempting to reconnect: %s",
                self.name,
                error,
            )

    async def _refresh_file_list_if_due(self, api: Any, now: datetime) -> None:
        """
        Fetch the CC2 file list every ten minutes.

        The list only changes when a file is uploaded or deleted, and a request
        answers with every file's metadata, so this is deliberately slow; the
        Refresh File List button covers the moment right after a slicer upload.
        """
        # isinstance, not hasattr: the file list is a CC2 method, and a mock
        # client answers hasattr with True for everything.
        if not isinstance(api.client, ElegooCC2Client):
            return
        if (
            self._last_file_list_check is not None
            and now - self._last_file_list_check < self._file_list_check_interval
        ):
            return
        LOGGER.debug("Refreshing the printer's file list")
        try:
            await api.async_get_file_list()
        except PRINT_TRANSPORT_ERRORS:
            LOGGER.debug("File list refresh failed")
        finally:
            self._last_file_list_check = now

    def _replay_cc2_print_status_transitions(self) -> None:
        """
        Replay queued CC2 print status snapshots so Home Assistant sees each transition.

        CC2 MQTT can deliver several status deltas within seconds; the client keeps only the
        latest merged state. After a normal data fetch, this drains the client's transition
        queue, calls async_set_updated_data for each snapshot, then restores the live status
        from the client so printer_data stays authoritative.

        Returns:
            None

        """  # noqa: E501
        api = self.config_entry.runtime_data.api
        if api.printer.transport_type != TransportType.CC2_MQTT:
            return
        pending = api.client.consume_print_status_transition_queue()
        if not pending:
            return
        original_status = api.printer_data.status
        for status_snapshot in pending:
            api.printer_data.status = status_snapshot
            self.async_set_updated_data(api.printer_data)
        api.printer_data.status = original_status

    def generate_unique_id(self, key: str) -> str:
        """
        Create a unique identifier for an entity by combining the sanitized printer name or machine ID with a specified key.

        If the printer name is unavailable or empty, the machine ID is used as the prefix.
        Otherwise, the printer name is converted to lowercase and spaces are replaced with underscores before appending the key.

        Arguments:
            key (str): Suffix to ensure uniqueness for the entity.

        Returns:
            str: The generated unique identifier.

        """  # noqa: E501
        machine_id = self.config_entry.data["id"]

        return machine_id + "_" + key
