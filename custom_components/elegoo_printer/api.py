"""API client for Elegoo printer."""

from __future__ import annotations

import socket
from io import BytesIO
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.httpx_client import get_async_client
from PIL import Image as PILImage
from PIL import UnidentifiedImageError

from .const import CONF_PROXY_ENABLED, DEFAULT_FALLBACK_IP, LOGGER
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

    def _get_local_ip(self, target_ip: str) -> str:
        """
        Determine the local IP address used for outbound communication.

        Args:
            target_ip: The target IP to determine the route to.

        Returns:
            The local IP address, or "127.0.0.1" if detection fails.

        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                # Doesn't have to be reachable
                s.connect((target_ip or DEFAULT_FALLBACK_IP, 1))
                return s.getsockname()[0]
        except (socket.gaierror, OSError):
            return "127.0.0.1"

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
                    logger=logger, hass=hass, session=session, printer=printer
                )
                # For multi-printer server, we'll use the original printer config
                # but note that proxy is enabled
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
                    removed = await ElegooPrinterServer.remove_printer_from_server(
                        self.printer, logger
                    )
                    if removed:
                        # Server stopped because no printers remained
                        self.server = None
                if self.client:
                    await self.client.disconnect()
                return None
            logger.info("Polling Started")
            return self  # noqa: TRY300
        except (ConnectionError, TimeoutError):
            if self.server:
                removed = await ElegooPrinterServer.remove_printer_from_server(
                    self.printer, logger
                )
                if removed:
                    self.server = None
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
        """Remove this printer from the proxy server or stop if no printers remain."""
        if self.server and self.printer:
            removed = await ElegooPrinterServer.remove_printer_from_server(
                self.printer, self._logger
            )
            if removed:
                self.server = None

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

            # Rewrite thumbnail URL to use proxy if proxy is enabled
            thumbnail_url = task.thumbnail
            if self.printer.proxy_enabled and thumbnail_url.startswith(
                f"http://{self.printer.ip_address}"
            ):
                # Replace printer IP with proxy IP and add mainboard_id to path
                try:
                    proxy_ip = self._get_local_ip(self.printer.ip_address)
                    thumbnail_url = thumbnail_url.replace(
                        f"http://{self.printer.ip_address}:3030",
                        f"http://{proxy_ip}:3030/{self.printer.id}",
                        1,
                    )
                    # Also handle cases without explicit port
                    if thumbnail_url == task.thumbnail:  # No replacement happened
                        thumbnail_url = thumbnail_url.replace(
                            f"http://{self.printer.ip_address}",
                            f"http://{proxy_ip}:3030/{self.printer.id}",
                            1,
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
                response = await self._hass_client.get(
                    thumbnail_url, timeout=10, follow_redirects=True
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
            try:
                self.server = await ElegooPrinterServer.async_create(
                    logger=self._logger,
                    hass=self.hass,
                    session=session,
                    printer=printer,
                )
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
