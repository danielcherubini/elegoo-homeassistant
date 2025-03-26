"""Elegoo Camera platform for Home Assistant."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

# Home Assistant Core Components
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.const import Platform
from homeassistant.helpers.reload import async_setup_reload_service

# Local Imports
from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator
from custom_components.elegoo_printer.definitions import (
    ElegooPrinterSensorEntityDescription,
)
from custom_components.elegoo_printer.entity import ElegooPrinterEntity

# Type Checking
if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.elegoo_printer.data import ElegooPrinterConfigEntry


_LOGGER = logging.getLogger(__name__)

# Define the camera entity description
CAMERA_ENTITY_DESCRIPTIONS: tuple[ElegooPrinterSensorEntityDescription, ...] = (
    ElegooPrinterSensorEntityDescription(
        key="camera_image",  # Unique key for this entity
        name="Camera",  # Default name, can be overridden by user
        value_fn=lambda data: data.camera_image,  # Potential future use for snapshots?
        # Function to determine if the camera entity should be available
        available_fn=lambda data: (
            data.attributes.num_video_stream_connected
            < data.attributes.max_video_stream_allowed
        ),
        icon="mdi:camera",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElegooPrinterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Elegoo Printer camera platform."""
    # Set up reload handling for the camera platform
    await async_setup_reload_service(hass, "elegoo_printer", [Platform.CAMERA])

    coordinator = entry.runtime_data.coordinator

    # Create and add the camera entity
    entities = [
        ElegooCamera(coordinator=coordinator, entity_description=description)
        for description in CAMERA_ENTITY_DESCRIPTIONS
    ]
    async_add_entities(entities, update_before_add=False)


class ElegooCamera(ElegooPrinterEntity, Camera):
    """Represents an Elegoo Printer Camera entity."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator: ElegooDataUpdateCoordinator,
        entity_description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """Initialize the Elegoo Camera."""
        super().__init__(coordinator)
        Camera.__init__(self)  # Initialize Camera base class

        self.entity_description = entity_description
        self._printer_client = coordinator.config_entry.runtime_data.client

        # Set entity attributes
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.key}"
        )
        self._attr_name = entity_description.name  # Use name from description

        # Construct RTSP URL (assuming printer data is available at init)
        self._rtsp_url = (
            f"rtsp://{coordinator.data.printer.ip_address}:554/video"
            if coordinator.data and coordinator.data.printer
            else None
        )
        self._process: subprocess.Popen | None = None

        if not self._rtsp_url:
            _LOGGER.warning(
                "RTSP URL could not be determined at init for %s", self.entity_id
            )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Check coordinator availability, description availability, and if RTSP URL is set
        return True
        # return (
        #     super().available
        #     and self.entity_description.available_fn(self.coordinator.data)
        #     and self._rtsp_url is not None
        # )

    async def async_stream_source(self) -> str | None:
        """Return the source of the stream (RTSP URL)."""
        # Update RTSP URL if it wasn't available at init or might have changed
        if (
            not self._rtsp_url
            and self.coordinator.data
            and self.coordinator.data.printer
        ):
            self._rtsp_url = (
                f"rtsp://{self.coordinator.data.printer.ip_address}:554/video"
            )
            _LOGGER.info("RTSP URL updated for %s", self.entity_id)

        return self._rtsp_url

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to Home Assistant."""
        await super().async_added_to_hass()
        # Note: Starting ffmpeg manually here might conflict with HA's stream
        # component, which usually handles ffmpeg based on async_stream_source.
        # This manual start is kept assuming it's needed for specific reasons
        # (e.g., ensuring the TEARDOWN command in close_stream is paired correctly).
        await self.start_ffmpeg_process()

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from Home Assistant."""
        await self.close_stream()
        await super().async_will_remove_from_hass()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image representation of the camera."""
        # Implement if snapshot functionality is desired, potentially using ffmpeg
        # or another method to grab a frame from the stream or a separate endpoint.
        _LOGGER.debug("Snapshot requested for %s", self.entity_id)
        # return await ffmpeg.async_get_image(self.hass, self._rtsp_url)
        return None  # Placeholder

    async def start_ffmpeg_process(self) -> None:
        """Start the ffmpeg process to connect to the RTSP stream."""
        if not self._rtsp_url:
            _LOGGER.error(
                "Cannot start stream, RTSP URL is not set for %s", self.entity_id
            )
            return

        if self._process and self._process.poll() is None:
            _LOGGER.debug("ffmpeg process already running for %s", self.entity_id)
            return

        command = [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",  # Only log errors
            "-allowed_media_types",
            "video",
            "-fflags",
            "nobuffer",  # Reduce latency
            "-flags",
            "low_delay",  # Reduce latency
            "-timeout",
            "5000000",  # 5 seconds RTSP connection timeout (microseconds)
            "-rtsp_transport",
            "udp",  # Use UDP (often lower latency, but less reliable)
            "-user_agent",
            "hacs/elegoo_printer",
            "-i",
            self._rtsp_url,  # Input RTSP URL
            "-c:v",
            "copy",  # Copy video stream without re-encoding
            "-an",  # No audio
            # Outputting somewhere? Usually HA stream component handles this.
            # If manual ffmpeg is truly needed, an output target (like pipe:)
            # might be required depending on how async_camera_image is implemented.
            # For now, assume it's just to keep the connection active / test.
        ]

        _LOGGER.debug("Starting ffmpeg for %s: %s", self.entity_id, " ".join(command))
        try:
            # Run Popen in executor thread
            self._process = await self.hass.async_add_executor_job(
                lambda: subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,  # Pipe stdout if needed later
                    stderr=subprocess.PIPE,  # Pipe stderr to capture errors
                )
            )
            _LOGGER.info("ffmpeg process started for %s", self.entity_id)
        except FileNotFoundError:
            _LOGGER.error(
                "ffmpeg command not found. Ensure ffmpeg is installed and in PATH."
            )
            self._process = None
        except Exception as ex:
            _LOGGER.error("Error starting ffmpeg for %s: %s", self.entity_id, ex)
            self._process = None

    async def close_stream(self) -> None:
        """Close the RTSP stream and terminate the ffmpeg process."""
        if self._process is None:
            _LOGGER.debug("No active ffmpeg process to close for %s", self.entity_id)
            return

        _LOGGER.debug("Closing stream and ffmpeg process for %s", self.entity_id)

        # 1. Send RTSP TEARDOWN request (important for the printer)
        try:
            await self._printer_client.teardown_rtsp_stream()
            _LOGGER.debug(
                "RTSP TEARDOWN command sent to printer for %s", self.entity_id
            )
        except Exception as ex:
            _LOGGER.error("Error sending RTSP TEARDOWN for %s: %s", self.entity_id, ex)

        # 2. Terminate the ffmpeg process
        if self._process.poll() is None:  # Check if process is still running
            _LOGGER.debug("Terminating ffmpeg process for %s", self.entity_id)
            try:
                self._process.terminate()
                # Wait briefly for graceful termination
                _, stderr_data = await self.hass.async_add_executor_job(
                    lambda: self._process.communicate(timeout=1)
                )
                if stderr_data:
                    _LOGGER.debug(
                        "ffmpeg stderr on terminate: %s",
                        stderr_data.decode(errors="ignore"),
                    )

            except subprocess.TimeoutExpired:
                _LOGGER.warning(
                    "ffmpeg process did not terminate gracefully for %s, killing.",
                    self.entity_id,
                )
                self._process.kill()
                # Optionally wait for kill
                await self.hass.async_add_executor_job(self._process.wait)
            except Exception as ex:
                _LOGGER.error(
                    "Error terminating/killing ffmpeg process for %s: %s",
                    self.entity_id,
                    ex,
                )

        _LOGGER.info("Stream closed successfully for %s", self.entity_id)
        self._process = None
