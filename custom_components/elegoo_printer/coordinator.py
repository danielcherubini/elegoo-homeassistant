"""DataUpdateCoordinator for elegoo_printer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.elegoo_printer.const import LOGGER
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
    ElegooPrinterNotConnectedError,
    ElegooPrinterTimeoutError,
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
        self._last_firmware_check: datetime | None = None
        self._firmware_check_interval = timedelta(hours=12)  # Check every 12 hours
        self._last_canvas_check: datetime | None = None
        self._canvas_check_interval = timedelta(seconds=30)  # Check every 30 seconds
        super().__init__(
            hass,
            LOGGER,
            name=f"{entry.title}",
            update_interval=timedelta(seconds=2),
        )

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
            self.data = (
                await self.config_entry.runtime_data.api.async_get_printer_data()
            )

            # Check if we need to update firmware info
            now = datetime.now(UTC)
            if (
                self._last_firmware_check is None
                or now - self._last_firmware_check >= self._firmware_check_interval
            ):
                LOGGER.debug("Checking for firmware updates")
                api = self.config_entry.runtime_data.api
                try:
                    firmware_info = await api.async_get_firmware_update_info()
                    if firmware_info:
                        self.data.firmware_update_info = firmware_info
                except httpx.HTTPError as fw_err:
                    LOGGER.debug("Firmware update check failed: %s", fw_err)
                finally:
                    # Rate-limit even on failure to avoid hammering the endpoint
                    self._last_firmware_check = now

            # Check Canvas status periodically (CC2 only)
            if api.printer.transport_type == TransportType.CC2_MQTT and (
                self._last_canvas_check is None
                or now - self._last_canvas_check >= self._canvas_check_interval
            ):
                LOGGER.debug("Checking Canvas/AMS status")
                try:
                    await api.async_get_canvas_status()
                except (
                    ElegooPrinterConnectionError,
                    ElegooPrinterTimeoutError,
                ):
                    LOGGER.debug("Canvas status check failed")
                finally:
                    # Rate-limit even on failure to avoid hammering the endpoint
                    self._last_canvas_check = now

            self.online = True
            if self.update_interval != timedelta(seconds=2):
                self.update_interval = timedelta(seconds=2)
            return self.data  # noqa: TRY300
        except (
            ElegooPrinterConnectionError,
            ElegooPrinterNotConnectedError,
            ElegooPrinterTimeoutError,
        ) as e:
            self.online = False
            LOGGER.info(
                "Connection to Elegoo printer lost: %s. Attempting to reconnect.", e
            )
            if self.update_interval != timedelta(seconds=30):
                self.update_interval = timedelta(seconds=30)

            try:
                await self.config_entry.runtime_data.api.reconnect()
            except (ConnectionError, TimeoutError) as recon_e:
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
