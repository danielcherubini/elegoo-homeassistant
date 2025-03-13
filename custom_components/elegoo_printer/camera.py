"""Elegoo Camera."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.helpers.reload import async_setup_reload_service

from .definitions import ElegooPrinterSensorEntityDescription
from .entity import ElegooPrinterEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import ElegooDataUpdateCoordinator
    from .data import ElegooPrinterConfigEntry


_LOGGER = logging.getLogger(__name__)

CAMERA_SENSOR: list[ElegooPrinterSensorEntityDescription] = [
    ElegooPrinterSensorEntityDescription(
        key="camera_image",
        name="Camera Image",
        value_fn=lambda self: self.coordinator.data.camera_image,
        available_fn=lambda self: (
            self.coordinator.data.attributes.num_video_stream_connected
            < self.coordinator.data.attributes.max_video_stream_allowed
        ),
        icon="mdi:camera",
    )
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Camera platform."""
    # Reload the integration if any changes are detected in the config.
    await async_setup_reload_service(hass, "elegoo_printer", ["camera"])

    for entity_description in CAMERA_SENSOR:
        async_add_entities(
            [
                ElegooCamera(
                    hass=hass,
                    coordinator=entry.runtime_data.coordinator,
                    entity_description=entity_description,
                )
            ],
            update_before_add=False,
        )


class ElegooCamera(ElegooPrinterEntity, Camera):
    """elegoo_printer Camera class."""

    _attr_supported_features: int = CameraEntityFeature.STREAM

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ElegooDataUpdateCoordinator,
        entity_description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """Initialize the sensor class."""
        super().__init__(coordinator)
        Camera.__init__(self)
        self.entity_description = entity_description
        self._attr_unique_id: str = self.entity_description.key
        self.hass: HomeAssistant = hass
        self._last_image: bytes | None = None
        self._ffmpeg_manager = get_ffmpeg_manager(hass)
        self._ffmpeg_process: asyncio.subprocess.Process | None = None
        self._jpeg_header: bytes | None = None  # Header for parsing

    async def async_added_to_hass(self) -> None:
        """Handle when the entity is added to Home Assistant."""
        await super().async_added_to_hass()
        await self.start_stream()  # Start the stream when added

    async def async_will_remove_from_hass(self) -> None:
        """Handle when the entity is about to be removed from Home Assistant."""
        await super().async_will_remove_from_hass()
        await self.stop_stream()  # Stop the stream when removed

    @property
    def is_streaming(self) -> bool:
        """Return if stream is available."""
        return (
            self._ffmpeg_process is not None and self._ffmpeg_process.returncode is None
        )

    @property
    def is_recording(self) -> bool:
        """Return if recording."""
        return False

    @property
    def use_stream_for_stills(self) -> bool:
        """Return stream for stills."""
        return False

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.entity_description.available_fn(self) and self.is_streaming

    async def _find_jpeg_header(self) -> None:
        """Find the JPEG header (SOI marker)."""
        if self._ffmpeg_process is None or self._ffmpeg_process.stdout is None:
            return
        header = b""
        while True:
            try:
                byte = await asyncio.wait_for(
                    self._ffmpeg_process.stdout.readexactly(1), timeout=20
                )  # Read one byte at a time, increased timeout
                header += byte

                if header.endswith(b"\xff\xd8"):  # JPEG SOI marker
                    self._jpeg_header = header[:-2]  # Store header without SOI
                    break
            except TimeoutError:
                _LOGGER.error("Timeout searching for jpeg header.")
                return
            except Exception as e:
                _LOGGER.error("Error reading stream %s", e)
                return

    async def stream_source(self) -> str | None:
        """Return the stream source."""
        return None  # We're providing MJPEG directly, so no source needed.

    async def start_stream(self) -> None:
        """Start the FFmpeg stream."""
        if self._ffmpeg_process is not None:
            _LOGGER.warning("FFmpeg stream already running")
            return

        mainboard_ip: str = self.coordinator.data.attributes.mainboard_ip
        mainboard_ip = "10.0.0.212"
        rtsp_url: str = f"rtsp://{mainboard_ip}:554/video"
        ffmpeg_command: list[str] = [
            self._ffmpeg_manager.binary,
            "-hide_banner",
            "-v",
            "error",
            "-allowed_media_types",
            "video",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-timeout",
            "5000000",
            "-user_agent",
            "homeassistant/elegoo_printer",
            "-rtsp_transport",
            "udp",
            "-i",
            rtsp_url,
            "-c:v",
            "copy",
            "-an",
            "-user_agent",
            "ffmpeg/go2rtc",
            "scale=960:540",
            "-",
        ]

        _LOGGER.debug("Starting FFmpeg with command: %s", " ".join(ffmpeg_command))

        try:
            self._ffmpeg_process = await asyncio.create_subprocess_exec(
                *ffmpeg_command,
                stdin=None,
                stdout=asyncio.subprocess.PIPE,  # Capture stdout
                stderr=asyncio.subprocess.PIPE,  # Capture stderr
            )
            # Find the JPEG header
            await self._find_jpeg_header()

            # Start a task to monitor the process
            asyncio.create_task(self._monitor_ffmpeg())

        except Exception as e:
            _LOGGER.error("Error starting FFmpeg: %s", e)
            self._ffmpeg_process = None

    async def stop_stream(self) -> None:
        """Stop the FFmpeg stream."""
        if self._ffmpeg_process is not None:
            _LOGGER.debug("Stopping FFmpeg stream")
            self._ffmpeg_process.terminate()
            try:
                await asyncio.wait_for(self._ffmpeg_process.wait(), timeout=5)
            except TimeoutError:
                _LOGGER.warning("FFmpeg process did not terminate, killing it")
                self._ffmpeg_process.kill()
            finally:
                await self._ffmpeg_process.wait()  # Ensure process cleanup
                self._ffmpeg_process = None

    async def _monitor_ffmpeg(self) -> None:
        """Monitor the FFmpeg process for errors."""
        if self._ffmpeg_process is None:
            return
        stderr_data = await self._ffmpeg_process.stderr.read()  # Read all data
        if stderr_data:
            _LOGGER.warning("FFmpeg stderr: %s", stderr_data.decode())
        return_code = await self._ffmpeg_process.wait()

        if return_code != 0:
            _LOGGER.error("FFmpeg exited with error code: %s", return_code)
            # Optionally, restart the stream here.
            await self.start_stream()  # Simple restart logic
        else:
            _LOGGER.info("FFmpeg process exited normally. Return code %s", return_code)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera (from the stream)."""
        if self._ffmpeg_process is None or self._ffmpeg_process.stdout is None:
            _LOGGER.warning("FFmpeg process not running or stdout not available.")
            return None

        try:
            # Read until the JPEG end marker (EOI - 0xFF 0xD9)
            jpeg_bytes: bytes = await asyncio.wait_for(
                self._ffmpeg_process.stdout.readuntil(b"\xff\xd9"), timeout=5
            )
            if self._jpeg_header:
                jpeg_bytes = self._jpeg_header + jpeg_bytes
            self._last_image = jpeg_bytes
            return self._last_image

        except asyncio.IncompleteReadError as err:
            # This usually means the stream ended.
            _LOGGER.warning("IncompleteReadError: %s", err)
            await self.stop_stream()
            await self.start_stream()
            return None
        except TimeoutError:
            _LOGGER.warning("Timeout reading image from camera")
            #  Do *not* restart here. A timeout might just mean a slow frame.
            return None
        except Exception as e:
            _LOGGER.error("Error getting image: %s", e)
            await self.stop_stream()
            await self.start_stream()
            return None
