"""API client for Elegoo printer."""

from __future__ import annotations

import re
from io import BytesIO
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.httpx_client import get_async_client
from PIL import Image as PILImage
from PIL import UnidentifiedImageError

from .const import CONF_PROXY_ENABLED, LOGGER
from .sdcp.models.elegoo_image import ElegooImage
from .sdcp.models.printer import Printer, PrinterData
from .websocket.client import ElegooPrinterClient
from .websocket.server import ElegooPrinterServer

if TYPE_CHECKING:
    from logging import Logger
    from types import MappingProxyType

    from homeassistant.core import HomeAssistant

    from .sdcp.models.enums import ElegooFan
    from .sdcp.models.print_history_detail import (
        PrintHistoryDetail,
    )


class ElegooPrinterApiClient:
    """Sample API Client."""

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

    @classmethod
    async def async_create(
        cls,
        config: MappingProxyType[str, Any],
        logger: Logger,
        hass: HomeAssistant,
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
        self = ElegooPrinterApiClient(printer, config=config, logger=logger, hass=hass)
        session = async_get_clientsession(hass)

        if proxy_server_enabled:
            logger.debug("Proxy server is enabled, attempting to create proxy server.")
            try:
                self.server = await ElegooPrinterServer.async_create(
                    printer, logger=logger, hass=hass, session=session
                )
                printer = self.server.get_printer()
                printer.proxy_enabled = proxy_server_enabled
            except (ConnectionError, TimeoutError) as e:
                logger.warning(
                    "Failed to start proxy server: %s. Falling back to direct conn.",
                    e,
                )
                self.server = None

        logger.debug(
            "Connecting to printer: %s at %s with proxy enabled %s",
            printer.name,
            printer.ip_address,
            proxy_server_enabled,
        )
        try:
            self.client = ElegooPrinterClient(
                printer.ip_address,
                config=config,
                logger=logger,
                session=session,
            )
            connected = await self.client.connect_printer(
                printer, proxy_enabled=proxy_server_enabled
            )
            if not connected:
                if self.server:
                    await self.server.stop()
                if self.client:
                    await self.client.disconnect()
                return None
            logger.info("Polling Started")
            return self  # noqa: TRY300
        except (ConnectionError, TimeoutError):
            if self.server:
                await self.server.stop()
            if self.client:
                await self.client.disconnect()
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
        """Stop the proxy server if it is running."""
        if self.server:
            await self.server.stop()

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
            try:
                response = await self._hass_client.get(
                    task.thumbnail, timeout=10, follow_redirects=True
                )
                response.raise_for_status()
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
            except (ConnectionError, TimeoutError, UnidentifiedImageError) as e:
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

    async def reconnect(self) -> bool:
        """
        Asynchronously attempts to reconnect to the printer, using a proxy server if enabled.

        Returns:
            bool: True if reconnection is successful, False otherwise.

        """  # noqa: E501
        printer = self.printer
        session = async_get_clientsession(self.hass)
        if self._proxy_server_enabled:
            if self.server:
                await self.server.stop()
            try:
                self.server = await ElegooPrinterServer.async_create(
                    printer, logger=self._logger, hass=self.hass, session=session
                )
                printer = self.server.get_printer()
            except (ConnectionError, TimeoutError):
                self._logger.exception("Failed to (re)create proxy server")
                self.server = None

        self._logger.debug(
            "Reconnecting to printer: %s proxy_enabled %s",
            printer.ip_address,
            self._proxy_server_enabled,
        )
        return await self.client.connect_printer(
            printer, proxy_enabled=self._proxy_server_enabled
        )

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

    def _normalize_firmware_version(self, version: str) -> str:
        """
        Normalize firmware version to the expected format.

        The API expects format x.x.x where each x can be up to 5 digits.
        """
        if not version:
            return "1.0.0"

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

            url = "https://mms.chituiot.com/mainboardVersionUpdate/getInfo.do7"
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

            # Check if the response contains an error message
            if isinstance(data, dict) and "error" in data:
                error_msg = data.get("error")
                LOGGER.warning("Firmware update API returned error: %s", error_msg)
            elif isinstance(data, str) and "格式" in data:
                LOGGER.warning("Firmware update API returned format error: %s", data)

        except (ConnectionError, TimeoutError) as err:
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
        update_data = await self.async_check_firmware_update()
        if update_data:
            return update_data.get("update", False)
        return False

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
