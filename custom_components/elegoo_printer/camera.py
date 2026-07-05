"""Camera platform for Elegoo printer."""

import asyncio
from http import HTTPStatus
from typing import TYPE_CHECKING

from aiohttp import web
from haffmpeg.camera import CameraMjpeg
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import (
    DOMAIN,
    async_get_image,
)
from homeassistant.components.mjpeg.camera import MjpegCamera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_aiohttp_proxy_stream
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from propcache.api import cached_property

from custom_components.elegoo_printer.const import (
    CONF_CAMERA_ENABLED,
    LOGGER,
    VIDEO_ENDPOINT,
    VIDEO_PORT,
)
from custom_components.elegoo_printer.data import ElegooPrinterConfigEntry
from custom_components.elegoo_printer.definitions import (
    PRINTER_FFMPEG_CAMERAS,
    PRINTER_MJPEG_CAMERAS,
    ElegooPrinterSensorEntityDescription,
)
from custom_components.elegoo_printer.entity import ElegooPrinterEntity
from custom_components.elegoo_printer.sdcp.models.enums import (
    ElegooVideoStatus,
    PrinterType,
)
from custom_components.elegoo_printer.sdcp.models.printer import PrinterData

from .coordinator import ElegooDataUpdateCoordinator

if TYPE_CHECKING:
    from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient


# Graceful ffmpeg shutdown timeouts
FFMPEG_QUIT_TIMEOUT = 10  # seconds to wait after sending 'q' to ffmpeg
FFMPEG_TERMINATE_TIMEOUT = 5  # seconds to wait after SIGTERM before SIGKILL
NATIVE_STREAM_IDLE_TIMEOUT = 600  # 10 minutes — clear native stream flag after idle
IDLE_WATCHDOG_INTERVAL = 60  # seconds between idle checks


class ElegooCameraMjpeg(CameraMjpeg):
    """
    CameraMjpeg with graceful shutdown: quit -> SIGTERM -> SIGKILL.

    ffmpeg's RTSP demuxer sends RTSP TEARDOWN on SIGTERM, which tells the
    printer to decrement its session counter. SIGKILL bypasses this entirely.
    """

    async def close(self, close_timeout: int = FFMPEG_QUIT_TIMEOUT) -> None:
        """
        Stop ffmpeg with graceful shutdown sequence.

        Arguments:
            close_timeout: Seconds to wait after sending 'q' before SIGTERM.

        """
        if not self.is_running:
            return

        # Step 1: Send 'q' to ffmpeg stdin (ffmpeg's interactive quit)
        quit_timed_out = False
        try:
            self._proc.stdin.write(b"q")
            async with asyncio.timeout(close_timeout):
                await self._proc.wait()
        except (BrokenPipeError, RuntimeError, OSError):
            # stdin is closed or process already died — skip to SIGTERM
            LOGGER.debug("FFmpeg stdin unavailable, skipping to SIGTERM")
        except asyncio.TimeoutError:
            quit_timed_out = True
        else:
            LOGGER.debug("Closed FFmpeg process gracefully (quit)")
            self._clear()
            return

        if not quit_timed_out and not self.is_running:
            # Process may have already exited after stdin error
            self._clear()
            return

        # Step 2: SIGTERM — ffmpeg sends RTSP TEARDOWN on SIGTERM
        try:
            self._proc.terminate()  # SIGTERM
            async with asyncio.timeout(FFMPEG_TERMINATE_TIMEOUT):
                await self._proc.wait()
            LOGGER.debug("Closed FFmpeg process (SIGTERM)")
        except ProcessLookupError:
            # Process already exited — treat as success
            LOGGER.debug("FFmpeg process already exited during SIGTERM")
        except asyncio.TimeoutError:
            # Step 3: SIGKILL as absolute last resort
            LOGGER.warning("SIGTERM timed out, escalating to SIGKILL")
            self.kill()  # reuse base class SIGKILL + background communicate task

        self._clear()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Asynchronously sets up Elegoo camera entities."""
    coordinator: ElegooDataUpdateCoordinator = config_entry.runtime_data.coordinator
    printer_type = coordinator.config_entry.runtime_data.api.printer.printer_type

    if printer_type == PrinterType.FDM:
        LOGGER.debug(f"Adding {len(PRINTER_MJPEG_CAMERAS)} Camera entities")
        for camera in PRINTER_MJPEG_CAMERAS:
            async_add_entities(
                [ElegooMjpegCamera(hass, coordinator, camera)], update_before_add=True
            )
    elif printer_type == PrinterType.RESIN:
        LOGGER.debug(f"Adding {len(PRINTER_FFMPEG_CAMERAS)} Camera entities")
        for camera in PRINTER_FFMPEG_CAMERAS:
            async_add_entities(
                [ElegooStreamCamera(hass, coordinator, camera)],
                update_before_add=True,
            )


class ElegooStreamCamera(ElegooPrinterEntity, Camera):
    """Representation of a camera that streams from an Elegoo printer."""

    def __init__(
        self,
        hass: HomeAssistant,  # noqa: ARG002
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """Initialize an Elegoo stream camera entity."""
        Camera.__init__(self)
        ElegooPrinterEntity.__init__(self, coordinator)

        self.entity_description = description
        self._printer_client: ElegooPrinterClient = (
            coordinator.config_entry.runtime_data.api.client
        )
        self._attr_name = description.name
        self._attr_unique_id = coordinator.generate_unique_id(description.key)
        self._attr_entity_registry_enabled_default = coordinator.config_entry.data.get(
            CONF_CAMERA_ENABLED, False
        )

        # For MJPEG stream
        self._extra_ffmpeg_arguments = (
            "-rtsp_transport udp -fflags nobuffer -err_detect ignore_err"
        )

        # Stream lifecycle tracking
        self._active_mjpeg_streams: int = 0
        self._active_mjpeg_processes: set[ElegooCameraMjpeg] = set()
        self._transient_viewers: int = 0  # async_camera_image grabs
        self._native_stream_active: bool = False
        self._stream_enabled: bool = False
        self._last_activity: float = 0.0  # monotonic time of last stream activity
        self._idle_watchdog_task: asyncio.Task | None = None

    def _is_over_capacity(self) -> bool:
        """Check if the printer is over capacity."""
        attrs = self._printer_client.printer_data.attributes
        num_connected = getattr(attrs, "num_video_stream_connected", 0) or 0
        max_allowed = getattr(attrs, "max_video_stream_allowed", 0) or 0
        return num_connected >= max_allowed

    @cached_property
    def supported_features(self) -> CameraEntityFeature:
        """Return supported features."""
        return self._attr_supported_features

    def _has_active_viewers(self) -> bool:
        """Check if any viewer type is currently active."""
        return (
            self._active_mjpeg_streams > 0
            or self._transient_viewers > 0
            or self._native_stream_active
        )

    async def _ensure_stream_enabled(self) -> None:
        """
        Enable printer video if not already enabled.

        Idempotent — safe to call when already enabled.
        On failure, _stream_enabled is NOT set (may retry later).
        """
        if self._stream_enabled:
            return
        try:
            video = await self._printer_client.get_printer_video(enable=True)
            if video.status == ElegooVideoStatus.SUCCESS:
                self._stream_enabled = True
                LOGGER.debug("Enabled printer video for %s", self.entity_id)
            else:
                LOGGER.warning(
                    "Failed to enable printer video for %s: %s",
                    self.entity_id,
                    video.status,
                )
        except Exception as e:  # noqa: BLE001
            LOGGER.warning(
                "Exception enabling printer video for %s: %s",
                self.entity_id,
                e,
            )

    async def _disable_stream(self) -> None:
        """
        Disable printer video.

        On failure, _stream_enabled stays True (video may still be on printer).
        The idle watchdog will re-attempt on subsequent intervals.
        """
        if not self._stream_enabled:
            return
        try:
            await self._printer_client.set_printer_video_stream(enable=False)
            self._stream_enabled = False
            LOGGER.debug("Disabled printer video for %s", self.entity_id)
        except Exception as e:  # noqa: BLE001
            LOGGER.warning(
                "Failed to disable printer video for %s (may be over capacity): %s",
                self.entity_id,
                e,
            )
            # Don't clear flag — video may still be enabled on printer

    async def _get_stream_url(self) -> str | None:
        """
        Get the stream URL from cached printer data.

        Does NOT toggle the printer video — reads the URL cached by the
        last call to get_printer_video(). Callers must ensure the video
        is enabled via _ensure_stream_enabled() before calling this method.
        """
        if (not self._printer_client.is_connected) or self._is_over_capacity():
            return None
        video_url = self._printer_client.printer_data.video.video_url
        if video_url:
            LOGGER.debug(
                "stream_source: Resin printer video (RTSP), using direct URL: %s",
                video_url,
            )
            return video_url
        return None

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse:
        """Generate an HTTP MJPEG stream from the camera."""
        stream_url = await self._get_stream_url()
        if not stream_url:
            return web.Response(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="Stream URL not available",
            )

        ffmpeg_manager = self.hass.data[DOMAIN]
        mjpeg_stream = CameraMjpeg(ffmpeg_manager.binary)
        await mjpeg_stream.open_camera(
            stream_url, extra_cmd=self._extra_ffmpeg_arguments
        )

        try:
            stream_reader = await mjpeg_stream.get_reader()
            return await async_aiohttp_proxy_stream(
                self.hass,
                request,
                stream_reader,
                ffmpeg_manager.ffmpeg_stream_content_type,
            )
        finally:
            await mjpeg_stream.close()

    async def stream_source(self) -> str | None:
        """Return the source of the stream."""
        return await self._get_stream_url()

    async def async_camera_image(
        self,
        width: int | None = None,  # noqa: ARG002
        height: int | None = None,  # noqa: ARG002
    ) -> bytes | None:
        """Return a still image response from the camera."""
        stream_url = await self._get_stream_url()
        if not stream_url:
            return None

        try:
            return await async_get_image(
                self.hass,
                input_source=stream_url,
            )
        except Exception as e:  # noqa: BLE001
            LOGGER.error(
                "Failed to get camera image via ffmpeg (ffmpeg may be missing): %s", e
            )
            return None


class ElegooMjpegCamera(ElegooPrinterEntity, MjpegCamera):
    """Representation of an MjpegCamera."""

    def __init__(
        self,
        hass: HomeAssistant,  # noqa: ARG002
        coordinator: ElegooDataUpdateCoordinator,
        description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """
        Initialize an Elegoo MJPEG camera entity.

        Arguments:
            hass: The Home Assistant instance.
            coordinator: The data update coordinator.
            description: The entity description.

        """
        # Use centralized proxy with MainboardID routing
        printer = coordinator.config_entry.runtime_data.api.printer
        if printer.proxy_enabled:
            external_ip = getattr(printer, "external_ip", None)
            proxy_ip = PrinterData.get_local_ip(printer.ip_address, external_ip)
            # Use centralized proxy on port 3031 with MainboardID as query parameter
            mjpeg_url = f"http://{proxy_ip}:{VIDEO_PORT}/video?id={printer.id}"
        else:
            # Direct HTTP MJPEG stream from the printer
            mjpeg_url = f"http://{printer.ip_address}:{VIDEO_PORT}/{VIDEO_ENDPOINT}"

        MjpegCamera.__init__(
            self,
            name=f"{description.name}",
            mjpeg_url=mjpeg_url,
            still_image_url=None,  # This camera does not have a separate still URL
            unique_id=coordinator.generate_unique_id(description.key),
        )

        ElegooPrinterEntity.__init__(self, coordinator)
        self.entity_description = description
        self._printer_client: ElegooPrinterClient = (
            coordinator.config_entry.runtime_data.api.client
        )

    def _is_over_capacity(self) -> bool:
        """Check if the printer is over capacity."""
        attrs = self._printer_client.printer_data.attributes
        num_connected = getattr(attrs, "num_video_stream_connected", 0) or 0
        max_allowed = getattr(attrs, "max_video_stream_allowed", 0) or 0
        return num_connected >= max_allowed

    @staticmethod
    def _normalize_video_url(video_url: str | None) -> str | None:
        """
        Check if video_url starts with 'http://' and adds it if missing.

        Arguments:
            video_url: The video URL to normalize.

        Returns:
            Normalized video URL string, or None if invalid/empty.

        """
        if not video_url:
            return None

        video_url = video_url.strip()
        if not video_url:
            return None

        if not video_url.startswith("http://"):
            video_url = "http://" + video_url

        return video_url

    async def _update_stream_url(self) -> None:
        """Update the MJPEG stream URL."""
        if (not self._printer_client.is_connected) or self._is_over_capacity():
            return
        video = await self._printer_client.get_printer_video(enable=True)
        if video.status and video.status == ElegooVideoStatus.SUCCESS:
            LOGGER.debug("stream_source: Video is OK, getting stream source")
            video_url = self._normalize_video_url(video.video_url)
            if not video_url:
                LOGGER.debug("stream_source: Empty or invalid video URL from printer")
                self._mjpeg_url = None
                return

            LOGGER.debug("stream_source: Using video url: %s", video_url)
            self._mjpeg_url = video_url
        else:
            LOGGER.debug("stream_source: Failed to get video stream: %s", video.status)
            self._mjpeg_url = None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Asynchronously gets the current MJPEG stream URL for the printer camera."""
        await self._update_stream_url()
        if (not self._mjpeg_url) or self._is_over_capacity():
            return None
        return await super().async_camera_image(width=width, height=height)

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse:
        """Generate an HTTP MJPEG stream from the camera."""
        await self._update_stream_url()
        if not self._mjpeg_url:
            return web.Response(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="Stream URL not available",
            )
        return await super().handle_async_mjpeg_stream(request)
