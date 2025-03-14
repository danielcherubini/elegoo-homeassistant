"""Elegoo Camera."""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
import urllib.parse
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.helpers.reload import async_setup_reload_service

from custom_components.elegoo_printer.coordinator import ElegooDataUpdateCoordinator

from .definitions import ElegooPrinterSensorEntityDescription
from .entity import ElegooPrinterEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

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

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ElegooDataUpdateCoordinator,
        entity_description: ElegooPrinterSensorEntityDescription,
    ) -> None:
        """Initialize the camera."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._data = coordinator.data
        self._name = "camera"
        self._unique_id: str = self.entity_description.key
        self.hass: HomeAssistant = hass
        self._rtsp_url = f"rtsp://{self._data.printer.ip_address}:554/video"
        self._process = None
        self._cseq = 1  # RTSP sequence counter

    @property
    def name(self):
        """Return the name of the camera."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique ID of the camera."""
        return self._unique_id

    @property
    def supported_features(self):
        """Return supported features."""
        return CameraEntityFeature.STREAM

    async def async_stream_source(self):
        """Return the source of the stream."""
        return self._rtsp_url

    async def async_added_to_hass(self):
        """Start the ffmpeg process when entity is added."""
        await self.start_stream()

    async def async_will_remove_from_hass(self):
        """Clean up resources when entity is removed."""
        await self.close_stream()

    async def start_stream(self):
        """Start the RTSP stream via ffmpeg."""
        if self._process and self._process.poll() is None:
            _LOGGER.debug("Stream already running")
            return

        command = [
            "ffmpeg",
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
            "-i",
            self._rtsp_url,
            "-c:v",
            "copy",
            "-an",
            "-user_agent",
            "hacs/elegoo_printer",
            "-rtsp_transport",
            "udp",
        ]
        _LOGGER.debug(f"Starting ffmpeg with command: {' '.join(command)}")
        try:
            self._process = await self.hass.async_add_executor_job(
                subprocess.Popen, command, stderr=subprocess.PIPE
            )
        except Exception as ex:
            _LOGGER.error(f"Error starting ffmpeg: {ex}")

    async def close_stream(self):
        """Properly close the RTSP stream and terminate ffmpeg process."""
        if self._process is None:
            return

        _LOGGER.debug("Closing RTSP stream to printer")

        # Send RTSP TEARDOWN request to the printer
        await self.send_rtsp_teardown()

        # Terminate the ffmpeg process
        if self._process and self._process.poll() is None:
            _LOGGER.debug("Terminating ffmpeg process")
            try:
                self._process.terminate()
                # Give it a moment to terminate gracefully
                await asyncio.sleep(1)
                if self._process.poll() is None:
                    # If still running, force kill
                    self._process.kill()
            except Exception as ex:
                _LOGGER.error(f"Error terminating ffmpeg process: {ex}")

        self._process = None
        _LOGGER.debug("Stream closed successfully")

    async def send_rtsp_teardown(self):
        """Send an RTSP TEARDOWN request using a simple socket implementation."""
        try:
            # Parse the RTSP URL
            parsed_url = urllib.parse.urlparse(self._rtsp_url)
            host = parsed_url.hostname
            port = parsed_url.port or 554  # Default RTSP port is 554

            _LOGGER.debug(
                f"Sending RTSP TEARDOWN to {host}:{port} for URL {self._rtsp_url}"
            )

            # Create TEARDOWN request
            teardown_request = (
                f"TEARDOWN {self._rtsp_url} RTSP/1.0\r\n"
                f"CSeq: {self._cseq}\r\n"
                f"User-Agent: HomeAssistant/Elegoo-Printer\r\n"
                "\r\n"
            )

            # Send the TEARDOWN request
            def send_teardown():
                # Create socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)  # 5 second timeout

                try:
                    # Connect to the server
                    sock.connect((host, port))

                    # Send TEARDOWN request
                    sock.sendall(teardown_request.encode())

                    # Wait for response (optional, we don't really need to process it)
                    response = sock.recv(1024).decode()
                    return response
                except Exception as ex:
                    _LOGGER.warning(f"Socket error during TEARDOWN: {ex}")
                    return None
                finally:
                    sock.close()

            # Execute the socket operations in the executor to avoid blocking
            response = await self.hass.async_add_executor_job(send_teardown)

            self._cseq += 1  # Increment sequence number for future requests

            # Log response (optional)
            if response:
                _LOGGER.debug(f"RTSP TEARDOWN response: {response}")

        except Exception as ex:
            _LOGGER.error(f"Error sending RTSP TEARDOWN: {ex}")
