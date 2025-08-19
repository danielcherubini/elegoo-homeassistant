from __future__ import annotations

from io import BytesIO
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.httpx_client import get_async_client
from PIL import Image as PILImage

from custom_components.elegoo_printer.sdcp.models.elegoo_image import ElegooImage
from custom_components.elegoo_printer.sdcp.models.enums import ElegooFan
from custom_components.elegoo_printer.sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer, PrinterData
from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient
from custom_components.elegoo_printer.websocket.server import ElegooPrinterServer

from .const import CONF_PROXY_ENABLED, LOGGER

if TYPE_CHECKING:
    from logging import Logger


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
        """
        self._ip_address = printer.ip_address
        self._proxy_server_enabled: bool = config.get(CONF_PROXY_ENABLED, False)
        self._logger = logger
        self.printer = printer
        self._hass_client = get_async_client(hass)
        self.client = ElegooPrinterClient(
            printer.ip_address,
            config=config,
            logger=logger,
            session=async_get_clientsession(hass),
        )
        self.server: ElegooPrinterServer | None = None
        self.hass: HomeAssistant = hass

    @classmethod
    async def async_create(
        cls,
        config: MappingProxyType[str, Any],
        logger: Logger,
        hass: HomeAssistant,
    ) -> ElegooPrinterApiClient:
        """
        Asynchronously creates and initializes an ElegooPrinterApiClient instance.

        This method parses the configuration to construct a Printer object, optionally
        sets up a proxy server, and attempts to connect to the printer. It returns an
        initialized client instance; the connection status should be checked separately.
        """
        printer = Printer.from_dict(dict(config))
        proxy_server_enabled: bool = config.get(CONF_PROXY_ENABLED, False)
        logger.debug("CONFIGURATION %s", config)
        self = ElegooPrinterApiClient(printer, config=config, logger=logger, hass=hass)

        if printer.proxy_enabled:
            logger.debug("Proxy server is enabled, attempting to create proxy server.")
            try:
                self.server = ElegooPrinterServer(printer, logger=logger)
                printer = self.server.get_printer()
                printer.proxy_enabled = proxy_server_enabled
            except Exception as e:
                logger.error("Failed to create proxy server: %s", e)
                # Continue with direct printer connection

        logger.debug(
            "Connecting to printer: %s at %s with proxy enabled %s and printer.proxy: %s",
            printer.name,
            printer.ip_address,
            proxy_server_enabled,
            printer.proxy_enabled,
        )

        try:
            connected = await self.client.connect_printer(printer, proxy_server_enabled)
            if connected:
                logger.info("Polling Started")
            else:
                logger.warning(
                    f"Could not connect to printer at {printer.ip_address}, proxy_enabled: {proxy_server_enabled}"
                )
        except Exception as e:
            logger.warning(f"Could not connect to printer: {e}")

        return self

    async def elegoo_disconnect(self) -> None:
        """Disconnect from the printer by closing the WebSocket connection."""
        await self.client.disconnect()

    def elegoo_stop_proxy(self) -> None:
        """Stops the proxy server if it is running."""
        if self.server:
            self.server.stop()

    async def async_get_status(self) -> PrinterData:
        """
        Asynchronously retrieves and updates the current status information from the connected printer.

        Returns:
            PrinterData: The latest status data of the printer.
        """

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
        Checks if the current print job's thumbnail image exists and returns a bool.

        Returns:
            bool: True if thumbnail image is available, or False otherwise.
        """

        thumbnail = await self.client.async_get_current_print_thumbnail()
        return thumbnail is not None

    async def async_get_thumbnail_url(
        self, include_history: bool = False
    ) -> str | None:
        """
        Asynchronously retrieves the current print job's thumbnail image as a string.

        Returns:
            str | None: The thumbnail image if available, or None if there is no active print job or thumbnail.
        """
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
        """
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

                with PILImage.open(BytesIO(response.content)) as img:
                    with BytesIO() as output:
                        rgb_img = img.convert("RGB")
                        rgb_img.save(output, format="PNG")
                        png_bytes = output.getvalue()
                        LOGGER.debug("get_thumbnail converted image to png")
                        thumbnail_image = ElegooImage(
                            image_url=task.thumbnail,
                            image_bytes=png_bytes,
                            last_updated_timestamp=task.begin_time.timestamp(),
                            content_type="image/png",
                        )
                        return thumbnail_image
            except Exception as e:
                LOGGER.error("Error fetching thumbnail: %s", e)
                return None

        LOGGER.debug("No task found")
        return None

    async def async_get_thumbnail_bytes(self) -> bytes | None:
        """
        Asynchronously retrieves the current print job's thumbnail image as bytes.

        Returns:
            bytes | None: The thumbnail image if available, or None if there is no active print job or thumbnail.
        """
        if thumbnail_image := await self.async_get_thumbnail_image():
            return thumbnail_image.get_bytes()

        return None

    async def async_get_task(
        self, include_last_task: bool
    ) -> PrintHistoryDetail | None:
        if current_task := await self.client.async_get_printer_current_task():
            return current_task
        elif include_last_task:
            if last_task := await self.client.async_get_printer_last_task():
                return last_task

        return None

    async def async_get_current_task(self) -> PrintHistoryDetail | None:
        """
        Asynchronously retrieves details of the current print task from the printer.

        Returns:
            A list of PrintHistoryDetail objects representing the current print task, or None if no task is active.
        """
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
        """
        return await self.client.async_get_printer_historical_tasks()

    async def reconnect(self) -> bool:
        """
        Asynchronously attempts to reconnect to the printer, using a proxy server if enabled.

        Returns:
            bool: True if reconnection is successful, False otherwise.
        """
        printer = self.client.printer
        if self._proxy_server_enabled:
            if self.server:
                self.server.stop()
            try:
                self.server = ElegooPrinterServer(printer, logger=self._logger)
                printer = self.server.get_printer()
            except Exception as e:
                self._logger.error("Failed to (re)create proxy server: %s", e)

        self._logger.debug(
            "Reconnecting to printer: %s proxy_enabled %s",
            printer.ip_address,
            self._proxy_server_enabled,
        )
        return await self.client.connect_printer(printer, self._proxy_server_enabled)

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
