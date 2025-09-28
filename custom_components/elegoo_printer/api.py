"""API client for Elegoo printer."""

from __future__ import annotations

import asyncio
import re
from io import BytesIO
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.httpx_client import get_async_client
from httpx import HTTPStatusError, RequestError
from PIL import Image as PILImage
from PIL import UnidentifiedImageError

from .const import (
    CONF_PROXY_ENABLED,
    FIRMWARE_SERVICE_BASE_URL,
    FIRMWARE_UPDATE_ENDPOINT,
    LOGGER,
)
from .sdcp.models.elegoo_image import ElegooImage
from .sdcp.models.printer import Printer, PrinterData
from .websocket.client import ElegooPrinterClient
from .websocket.server import ElegooPrinterServer

if TYPE_CHECKING:
    from logging import Logger
    from types import MappingProxyType

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .sdcp.models.enums import ElegooFan
    from .sdcp.models.print_history_detail import (
        PrintHistoryDetail,
    )


class ElegooPrinterApiClient:
    """Sample API Client."""

    # Thumbnail fetch retry configuration
    _THUMBNAIL_MAX_RETRIES = 2
    _THUMBNAIL_RETRY_BASE_DELAY = 0.5  # seconds
    _THUMBNAIL_TIMEOUT = 10  # seconds

    _ip_address: str | None
    client: ElegooPrinterClient
    _logger: Logger
    printer: Printer
    printer_data: PrinterData
    hass: HomeAssistant

    def __init__(
        self,
        printer: Printer,
        config: MappingProxyType[str, Any],
        logger: Logger,
        hass: HomeAssistant,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """
        Initialize the ElegooPrinterApiClient with a Printer object, configuration, and logger.

        Creates an internal ElegooPrinterClient for communication with the specified printer and sets up proxy server usage based on the configuration.
        """  # noqa: E501
        self._ip_address = printer.ip_address
        self._proxy_server_enabled: bool = config.get(CONF_PROXY_ENABLED, False)
        self._logger = logger
        self.printer = printer
        self._hass_client = get_async_client(hass)
        self.server: ElegooPrinterServer | None = None
        self.hass: HomeAssistant = hass
        self._config_entry = config_entry

    @classmethod
    async def async_create(
        cls,
        config: MappingProxyType[str, Any],
        logger: Logger,
        hass: HomeAssistant,
        config_entry: ConfigEntry | None = None,
    ) -> ElegooPrinterApiClient | None:
        """
        Asynchronously creates and initializes an ElegooPrinterApiClient instance.

        This method parses the configuration to construct a Printer object, optionally
        sets up a proxy server, and attempts to connect to the printer. It returns an
        initialized client instance on success, otherwise None.
        """
        printer = Printer.from_dict(dict(config))
        proxy_server_enabled: bool = config.get(CONF_PROXY_ENABLED, False)
        logger.debug("CONFIGURATION %s", config)
        self = ElegooPrinterApiClient(
            printer, config=config, logger=logger, hass=hass, config_entry=config_entry
        )
        session = async_get_clientsession(hass)

        # First, test if printer is reachable before starting proxy server
        logger.debug(
            "Testing connectivity to printer: %s at %s",
            printer.name,
            printer.ip_address,
        )
        self.client = ElegooPrinterClient(
            printer.ip_address,
            config=config,
            logger=logger,
            session=session,
        )

        # Ping the printer to check if it's available
        printer_reachable = await self.client.ping_printer(ping_timeout=5.0)

        if not printer_reachable:
            logger.warning(
                "Printer %s at %s is not reachable. Not starting proxy server.",
                printer.name,
                printer.ip_address,
            )
            # This is probably unnecessary, but let's disconnect for completeness
            await self.client.disconnect()
            # No proxy server was started by this client; nothing to release here
            return None

        # Printer is reachable, now set up proxy if enabled
        if proxy_server_enabled:
            printer = await self._setup_proxy_if_enabled(printer)
            if printer is None:
                # Proxy was required but failed to start
                await self.client.disconnect()
                # Release proxy reference only if there's an active class-level ref
                if ElegooPrinterServer.has_reference():
                    await ElegooPrinterServer.release_reference()
                return None

        # Now connect to the printer (either direct or through proxy)
        target_ip = self.get_local_ip() if self.server else printer.ip_address
        logger.debug(
            "Connecting to printer: %s at %s with proxy enabled %s",
            printer.name,
            target_ip,
            proxy_server_enabled,
        )
        try:
            connected = await self.client.connect_printer(
                printer, proxy_enabled=proxy_server_enabled
            )
            if not connected:
                # Release only our proxy reference if any
                if self.server:
                    await ElegooPrinterServer.release_reference()
                self.server = None
                await self.client.disconnect()
                self._proxy_server_enabled = False
                return None
            logger.info("Polling Started")

            # Update config entry with fresh printer data from successful connection
            await self._update_config_entry_if_needed(printer)

            return self  # noqa: TRY300
        except (ConnectionError, TimeoutError):
            # Release only our proxy reference if any
            if self.server:
                await ElegooPrinterServer.release_reference()
            self.server = None
            await self.client.disconnect()
            self._proxy_server_enabled = False
            return None

    @property
    def is_connected(self) -> bool:
        """Return true if the client and server are connected to the printer."""
        if self._proxy_server_enabled:
            return (
                self.client.is_connected
                and self.server is not None
                and self.server.is_connected
            )
        return self.client.is_connected

    async def elegoo_disconnect(self) -> None:
        """Disconnect from the printer by closing the WebSocket connection."""
        await self.client.disconnect()

    async def elegoo_stop_proxy(self) -> None:
        """Release the proxy server reference if it is running."""
        # Release reference instead of forcing shutdown
        await ElegooPrinterServer.release_reference()
        self.server = None

    def get_local_ip(self) -> str:
        """Get the local IP for the proxy server, falling back to the printer's IP."""
        if self.server:
            return self.server.get_local_ip()
        return self.printer.ip_address

    async def reconnect(self) -> bool:
        """
        Asynchronously attempts to reconnect to the printer, using a proxy server if enabled.

        Returns:
            bool: True if reconnection is successful, False otherwise.

        """  # noqa: E501
        printer = self.printer

        # First, test if printer is reachable
        self._logger.debug(
            "Testing connectivity before reconnect to printer: %s at %s",
            printer.name,
            printer.ip_address,
        )

        # Ping the printer to check if it's available
        printer_reachable = await self.client.ping_printer(ping_timeout=5.0)

        if not printer_reachable:
            self._logger.debug(
                "Printer %s at %s is not reachable during reconnect. Stopping proxies.",
                printer.name,
                printer.ip_address,
            )
            # Release only our proxy reference if any
            if self.server:
                await ElegooPrinterServer.release_reference()
            self.server = None
            return False

        # Printer is reachable, handle proxy server if enabled
        if self._proxy_server_enabled:
            # Release existing reference before creating new one
            if self.server is not None:
                await ElegooPrinterServer.release_reference()
            self.server = None

            printer = await self._setup_proxy_if_enabled(printer)
            if printer is None:
                # Proxy was required but failed to start during reconnect
                return False

        self._logger.debug(
            "Reconnecting to printer: %s proxy_enabled %s",
            printer.ip_address,
            self._proxy_server_enabled and self.server is not None,
        )
        connected = await self.client.connect_printer(
            printer,
            proxy_enabled=self._proxy_server_enabled and self.server is not None,
        )

        # Update config entry with fresh printer data if reconnection was successful
        if connected:
            await self._update_config_entry_if_needed(printer)

        return connected

    async def async_get_status(self) -> PrinterData:
        """
        Asynchronously retrieves and updates the current status information from the connected printer.

        Returns:
            PrinterData: The latest status data of the printer.

        """  # noqa: E501
        self.printer_data = await self.client.get_printer_status()
        return self.printer_data

    async def async_get_attributes(self) -> PrinterData:
        """
        Asynchronously retrieves and updates the printer's attribute data.

        Returns:
            PrinterData: The latest attribute information for the printer.

        """
        self.printer_data = await self.client.get_printer_attributes()
        return self.printer_data

    async def async_is_thumbnail_available(self) -> bool:
        """
        Check if the current print job's thumbnail image exists and returns a bool.

        Returns:
            bool: True if thumbnail image is available, or False otherwise.

        """
        thumbnail = await self.client.async_get_current_print_thumbnail()
        return thumbnail is not None

    async def async_get_thumbnail_url(
        self, *, include_history: bool = False
    ) -> str | None:
        """
        Asynchronously retrieves the current print job's thumbnail image as a string.

        Returns:
            str | None: The thumbnail image if available, or None if there is no active print job or thumbnail.

        """  # noqa: E501
        if task := await self.async_get_task(include_last_task=include_history):
            return task.thumbnail
        return None

    async def _fetch_thumbnail_with_retry(self, thumbnail_url: str) -> Any:
        """Fetch thumbnail with exponential backoff retry logic."""
        for attempt in range(self._THUMBNAIL_MAX_RETRIES + 1):
            try:
                response = await self._hass_client.get(
                    thumbnail_url,
                    timeout=self._THUMBNAIL_TIMEOUT,
                    follow_redirects=True,
                )
                response.raise_for_status()
            except (RequestError, HTTPStatusError) as e:
                if attempt < self._THUMBNAIL_MAX_RETRIES:
                    LOGGER.debug(
                        "Thumbnail fetch attempt %d failed, retrying: %s",
                        attempt + 1,
                        e,
                    )
                    await asyncio.sleep(self._THUMBNAIL_RETRY_BASE_DELAY * (2**attempt))
                    continue
                raise
            else:
                return response

        # This should never be reached due to raise above, but satisfy linter
        msg = "Failed to fetch thumbnail after all retries"
        raise RequestError(msg)

    async def async_get_thumbnail_image(
        self, task: PrintHistoryDetail | None = None
    ) -> ElegooImage | None:
        """
        Asynchronously retrieves the current print job's thumbnail image as Image.

        Returns:
            Image | None: The thumbnail image if available, or None if there is no active print job or thumbnail.

        """  # noqa: E501
        if task is None:
            LOGGER.debug("get_thumbnail no task, getting task")
            task = await self.async_get_task(include_last_task=False)

        if not task:
            LOGGER.debug("No task found")
            return None

        LOGGER.debug(
            "get_thumbnail got begin_time: %s url: %s",
            task.begin_time,
            task.thumbnail,
        )
        if task.thumbnail and task.begin_time is not None:
            LOGGER.debug("get_thumbnail getting thumbnail from url")

            # Rewrite thumbnail URL to use proxy if proxy is enabled
            thumbnail_url = task.thumbnail
            if self.printer.proxy_enabled:
                # Replace printer host with centralized proxy and set id=query
                try:
                    proxy_ip = PrinterData.get_local_ip(self.printer.ip_address)
                    parts = urlsplit(thumbnail_url)
                    scheme = parts.scheme or "http"
                    netloc = parts.netloc or self.printer.ip_address

                    # Only rewrite if the URL points to our printer
                    if self.printer.ip_address in netloc:
                        # Force proxy host:port
                        new_netloc = f"{proxy_ip}:3030"
                        # Merge/replace query id
                        q = dict(parse_qsl(parts.query, keep_blank_values=True))
                        q["id"] = self.printer.id
                        thumbnail_url = urlunsplit(
                            (
                                scheme,
                                new_netloc,
                                parts.path,
                                urlencode(q, doseq=True),
                                parts.fragment,
                            )
                        )
                        LOGGER.debug(
                            "Rewritten thumbnail URL from %s to %s",
                            task.thumbnail,
                            thumbnail_url,
                        )
                except (OSError, ValueError) as e:
                    LOGGER.debug("Failed to rewrite thumbnail URL: %s", e)
                    thumbnail_url = task.thumbnail

            try:
                response = await self._fetch_thumbnail_with_retry(thumbnail_url)
                LOGGER.debug("get_thumbnail response status: %s", response.status_code)
                raw_ct = response.headers.get("content-type", "")
                content_type = raw_ct.split(";", 1)[0].strip().lower() or "image/png"
                LOGGER.debug("get_thumbnail content-type: %s", content_type)

                if content_type == "image/png":
                    # Normalize common header forms like "image/png; charset=binary"
                    content_type = content_type.split(";", 1)[0].strip().lower()
                    LOGGER.debug("get_thumbnail (FDM) content-type: %s", content_type)
                    return ElegooImage(
                        image_url=task.thumbnail,
                        image_bytes=response.content,
                        last_updated_timestamp=task.begin_time.timestamp(),
                        content_type=content_type or "image/png",
                    )

                with (
                    PILImage.open(BytesIO(response.content)) as img,
                    BytesIO() as output,
                ):
                    rgb_img = img.convert("RGB")
                    rgb_img.save(output, format="PNG")
                    png_bytes = output.getvalue()
                    LOGGER.debug("get_thumbnail converted image to png")
                    return ElegooImage(
                        image_url=task.thumbnail,
                        image_bytes=png_bytes,
                        last_updated_timestamp=task.begin_time.timestamp(),
                        content_type="image/png",
                    )
            except (
                ConnectionError,
                TimeoutError,
                UnidentifiedImageError,
                HTTPStatusError,
                RequestError,
            ) as e:
                LOGGER.error("Error fetching thumbnail: %s", e)
                return None

        LOGGER.debug("No task found")
        return None

    async def async_get_thumbnail_bytes(self) -> bytes | None:
        """
        Asynchronously retrieves the current print job's thumbnail image as bytes.

        Returns:
            bytes | None: The thumbnail image if available, or None if there is no active print job or thumbnail.

        """  # noqa: E501
        if thumbnail_image := await self.async_get_thumbnail_image():
            return thumbnail_image.get_bytes()

        return None

    async def async_get_task(
        self, *, include_last_task: bool
    ) -> PrintHistoryDetail | None:
        """
        Asynchronously retrieves the current or last print task from the printer.

        Arguments:
            include_last_task (bool): Whether to include the last print task if no current task is active.

        Returns:
            PrintHistoryDetail | None: The current or last print task, or None if no task is available.

        """  # noqa: E501
        if current_task := await self.client.async_get_printer_current_task():
            return current_task
        if include_last_task and (
            last_task := await self.client.async_get_printer_last_task()
        ):
            return last_task

        return None

    async def async_get_current_task(self) -> PrintHistoryDetail | None:
        """
        Asynchronously retrieves details of the current print task from the printer.

        Returns:
            A list of PrintHistoryDetail objects representing the current print task, or None if no task is active.

        """  # noqa: E501
        current_task = await self.client.async_get_printer_current_task()
        if current_task:
            self.printer_data.current_job = current_task
            if current_task.task_id:
                self.printer_data.print_history[current_task.task_id] = current_task
        return current_task

    async def async_get_print_history(
        self,
    ) -> dict[str, PrintHistoryDetail | None] | None:
        """
        Asynchronously retrieves the print history from the printer.

        Returns:
            A list of PrintHistoryDetail objects representing the print history, or None if no history is available.

        """  # noqa: E501
        return await self.client.async_get_printer_historical_tasks()

    async def set_fan_speed(self, percentage: int, fan: ElegooFan) -> None:
        """Set the speed of a fan."""
        await self.client.set_fan_speed(percentage, fan)

    async def async_set_print_speed(self, percentage: int) -> None:
        """Set the print speed."""
        await self.client.set_print_speed(percentage)

    async def async_set_target_nozzle_temp(self, temperature: int) -> None:
        """Set the target nozzle temperature."""
        await self.client.set_target_nozzle_temp(temperature)

    async def async_set_target_bed_temp(self, temperature: int) -> None:
        """Set the target bed temperature."""
        await self.client.set_target_bed_temp(temperature)

    async def async_get_printer_data(self) -> PrinterData:
        """
        Asynchronously retrieves and updates the printer's attribute data.

        Returns:
            PrinterData: The latest attribute information for the printer.

        """
        await self.async_get_attributes()
        await self.async_get_status()
        await self.async_get_print_history()
        await self.async_get_current_task()
        self.printer_data.calculate_current_job_end_time()
        return self.printer_data

    async def _setup_proxy_if_enabled(self, printer: Printer) -> Printer | None:
        """
        Set up proxy server if enabled and printer is reachable.

        Returns:
            Updated printer object with proxy IP, or None if proxy failed to start

        """
        if not self._proxy_server_enabled:
            return printer

        self._logger.debug("Printer is reachable. Starting proxy server.")
        try:
            self.server = await ElegooPrinterServer.async_create(
                self._logger, self.hass, printer=printer
            )
        except (OSError, ConfigEntryNotReady):
            # When proxy is explicitly enabled, server startup failures are fatal
            self._logger.exception(
                "Failed to start required proxy server; proxy ports may be in use."
            )
            # Clean up any partial state (only our reference)
            if self.server:
                await ElegooPrinterServer.release_reference()
            self.server = None
            return None
        else:
            self._logger.debug(
                "Calling get_printer with specific_printer: %s (MainboardID: %s)",
                printer.name,
                printer.id,
            )
            proxy_printer = self.server.get_printer(specific_printer=printer)
            proxy_printer.proxy_enabled = True
            self._logger.debug(
                "Got proxy printer: %s (MainboardID: %s)",
                proxy_printer.name,
                proxy_printer.id,
            )
            self.printer = proxy_printer
            return proxy_printer

    def _normalize_firmware_version(self, version: str) -> str:
        """
        Normalize firmware version to the expected format.

        The API expects format x.x.x where each x can be up to 5 digits.
        """
        if not version:
            return "1.1.0"

        # Remove any non-numeric characters except dots
        cleaned = re.sub(r"[^0-9.]", "", version)

        # Split by dots and ensure we have at least 3 parts
        parts = cleaned.split(".")

        # Pad or truncate to exactly 3 parts
        version_parts_count = 3
        while len(parts) < version_parts_count:
            parts.append("0")
        parts = parts[:version_parts_count]

        # Ensure each part is a valid number and not too long (max 5 digits)
        normalized_parts = []
        for part in parts:
            if not part or not part.isdigit():
                normalized_parts.append("0")
            else:
                # Limit to 5 digits as per API requirement
                normalized_parts.append(str(int(part))[:5])

        return ".".join(normalized_parts)

    async def async_check_firmware_update(self) -> dict[str, Any] | None:
        """
        Check for firmware updates from Elegoo servers.

        Returns:
            dict | None: Update information if available, None if check fails.

        """
        if not self.printer.model or not self.printer.firmware:
            LOGGER.warning(
                "Missing printer model or firmware version, cannot check for updates"
            )
            return None

        try:
            # Normalize the firmware version format
            firmware_version = self._normalize_firmware_version(self.printer.firmware)
            LOGGER.debug("Original firmware version: %s", self.printer.firmware)
            LOGGER.debug("Normalized firmware version: %s", firmware_version)

            # Construct the request parameters based on the API documentation
            machine_id = self.printer.id or 0
            params = {
                "machineType": f"ELEGOO {self.printer.model}",
                "machineId": machine_id,
                "version": firmware_version,
                "lan": "en",
                "firmwareType": 1,
            }

            url = f"{FIRMWARE_SERVICE_BASE_URL}{FIRMWARE_UPDATE_ENDPOINT}"
            LOGGER.debug("Checking for firmware updates")
            LOGGER.debug("URL: %s", url)
            LOGGER.debug("Params: %s", params)

            response = await self._hass_client.get(
                url,
                params=params,
                timeout=30,
                follow_redirects=True,
            )

            LOGGER.debug("Response status: %s", response.status_code)
            LOGGER.debug("Response headers: %s", dict(response.headers))

            response.raise_for_status()

            data = response.json()
            LOGGER.debug("Firmware update response: %s", data)

            # The API can return a string for certain errors
            if not isinstance(data, dict):
                if isinstance(data, str) and "格式" in data:
                    LOGGER.warning(
                        "Firmware update API returned format error: %s", data
                    )
                else:
                    LOGGER.warning(
                        "Firmware update response is not a dictionary: %s", data
                    )
                return None

            # Check if the dictionary response contains an error message
            if "error" in data:
                error_msg = data.get("error")
                LOGGER.warning("Firmware update API returned error: %s", error_msg)
                return None

        except (ConnectionError, TimeoutError, HTTPStatusError, RequestError) as err:
            LOGGER.error("Network error checking for firmware updates: %s", err)
            return None
        except (ValueError, KeyError) as err:
            LOGGER.error("Error parsing firmware update response: %s", err)
            return None
        else:
            return data

    async def async_is_firmware_update_available(self) -> bool:
        """
        Check if a firmware update is available.

        Returns:
            bool: True if update is available, False otherwise.

        """
        info = await self.async_get_firmware_update_info()
        return bool(info.get("update_available")) if info else False

    async def async_get_firmware_update_info(self) -> dict[str, Any]:
        """
        Get detailed firmware update information.

        Returns:
            dict: Firmware update details including versions and changelog.

        """
        update_data = await self.async_check_firmware_update()
        if not update_data:
            return {
                "update_available": False,
                "current_version": self.printer.firmware,
                "latest_version": None,
                "package_url": None,
                "changelog": None,
            }

        return {
            "update_available": update_data.get("update", False),
            "current_version": self.printer.firmware,
            "latest_version": update_data.get("version"),
            "package_url": update_data.get("packageUrl"),
            "changelog": update_data.get("log"),
        }

    async def _update_config_entry_if_needed(self, printer: Printer) -> None:
        """
        Update the config entry with fresh printer data if anything has changed.

        This ensures the stored config stays in sync with the actual printer state.
        """
        try:
            # Use stored config entry reference if available
            config_entry = self._config_entry
            if not config_entry:
                LOGGER.debug(
                    "No config entry reference available for printer %s", printer.id
                )
                return

            # Convert current and new printer data for comparison
            current_data = dict(config_entry.data)
            new_data = printer.to_dict()

            # Compare key fields that might have changed
            fields_to_check = [
                "name",
                "model",
                "brand",
                "firmware",
                "protocol",
                "ip_address",
            ]
            has_changes = False

            for field in fields_to_check:
                current_value = current_data.get(field)
                new_value = new_data.get(field)
                if current_value != new_value and new_value is not None:
                    LOGGER.debug(
                        "Printer %s field '%s' changed: '%s' -> '%s'",
                        printer.name,
                        field,
                        current_value,
                        new_value,
                    )
                    has_changes = True

            if has_changes:
                LOGGER.info(
                    "Updating config entry for printer %s with fresh data",
                    printer.name,
                )
                # Update the config entry with the new printer data
                self.hass.config_entries.async_update_entry(
                    config_entry,
                    data=new_data,
                )
            else:
                LOGGER.debug(
                    "No config changes needed for printer %s",
                    printer.name,
                )

        except AttributeError as e:
            # Handle missing attributes in printer or config
            LOGGER.debug(
                "Missing attributes when updating config for printer %s: %s",
                printer.name,
                e,
            )
        except RuntimeError as e:
            # Handle Home Assistant state errors
            LOGGER.warning(
                "Home Assistant error updating config for printer %s: %s",
                printer.name,
                e,
            )
        except Exception as e:  # noqa: BLE001
            # Log unexpected errors but don't crash
            LOGGER.error(
                "Unexpected error updating config for printer %s: %s",
                printer.name,
                e,
                exc_info=True,
            )
