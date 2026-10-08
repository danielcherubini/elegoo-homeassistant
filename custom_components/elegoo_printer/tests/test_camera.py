"""Tests for the camera module."""

import asyncio
import inspect
from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.elegoo_printer.camera import (
    FFMPEG_QUIT_TIMEOUT,
    ElegooCameraMjpeg,
    ElegooMjpegCamera,
)
from custom_components.elegoo_printer.sdcp.models.enums import (
    PrinterType,
    ProtocolVersion,
    TransportType,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer


def _run(coro: Coroutine[Any, Any, None]) -> None:
    """Run an async coroutine in a fresh event loop."""
    asyncio.run(coro)


@pytest.fixture
def mock_ffmpeg_bin() -> str:
    return "/usr/bin/ffmpeg"


@pytest.fixture
def mock_proc() -> MagicMock:
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.wait = AsyncMock()
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.communicate = AsyncMock()
    proc.returncode = None
    return proc


async def _create_camera(ffmpeg_bin):
    """Create ElegooCameraMjpeg inside a running event loop."""
    return ElegooCameraMjpeg(ffmpeg_bin)


class TestElegooCameraMjpegClose:
    """Test cases for ElegooCameraMjpeg.close() shutdown sequence."""

    def test_close_graceful_quit(self, mock_ffmpeg_bin, mock_proc):
        """ffmpeg exits after receiving 'q' — no SIGTERM needed."""

        async def run_test():
            camera = await _create_camera(mock_ffmpeg_bin)
            camera._proc = mock_proc
            await camera.close(shutdown_timeout=1)

            mock_proc.stdin.write.assert_called_once_with(b"q")
            mock_proc.wait.assert_called_once()
            mock_proc.terminate.assert_not_called()
            mock_proc.kill.assert_not_called()
            assert camera._proc is None  # _clear() was called

        _run(run_test())

    def test_close_quit_shutdown_timeout_escalates_to_sigterm(
        self, mock_ffmpeg_bin, mock_proc
    ):
        """ffmpeg doesn't exit after 'q' — SIGTERM is sent."""

        async def run_test():
            camera = await _create_camera(mock_ffmpeg_bin)
            camera._proc = mock_proc
            # First wait times out, second (after SIGTERM) succeeds
            mock_proc.wait.side_effect = [asyncio.TimeoutError(), None]

            await camera.close(shutdown_timeout=0)

            mock_proc.stdin.write.assert_called_once_with(b"q")
            mock_proc.terminate.assert_called_once()
            mock_proc.kill.assert_not_called()
            assert camera._proc is None

        _run(run_test())

    def test_close_sigterm_shutdown_timeout_escalates_to_sigkill(
        self, mock_ffmpeg_bin, mock_proc
    ):
        """ffmpeg doesn't exit after SIGTERM — SIGKILL is sent."""

        async def run_test():
            camera = await _create_camera(mock_ffmpeg_bin)
            camera._proc = mock_proc
            # Both waits time out
            mock_proc.wait.side_effect = [
                asyncio.TimeoutError(),
                asyncio.TimeoutError(),
            ]

            await camera.close(shutdown_timeout=0)

            mock_proc.stdin.write.assert_called_once_with(b"q")
            mock_proc.terminate.assert_called_once()
            mock_proc.kill.assert_called_once()
            assert camera._proc is None

        _run(run_test())

    def test_close_stdin_broken_pipe_skips_to_sigterm(self, mock_ffmpeg_bin, mock_proc):
        """stdin is closed — falls through to SIGTERM."""

        async def run_test():
            camera = await _create_camera(mock_ffmpeg_bin)
            camera._proc = mock_proc
            mock_proc.stdin.write.side_effect = BrokenPipeError()
            mock_proc.wait.return_value = None

            await camera.close(shutdown_timeout=1)

            mock_proc.terminate.assert_called_once()
            mock_proc.kill.assert_not_called()
            assert camera._proc is None

        _run(run_test())

    def test_close_already_not_running(self, mock_ffmpeg_bin):
        """close() is a no-op when ffmpeg isn't running."""

        async def run_test():
            camera = await _create_camera(mock_ffmpeg_bin)
            camera._proc = None

            await camera.close(shutdown_timeout=1)

            # No assertions — no calls should be made

        _run(run_test())

    def test_close_process_lookup_error_during_sigterm(
        self, mock_ffmpeg_bin, mock_proc
    ):
        """Process already died before SIGTERM — treated as success."""

        async def run_test():
            camera = await _create_camera(mock_ffmpeg_bin)
            camera._proc = mock_proc
            mock_proc.stdin.write.side_effect = BrokenPipeError()
            mock_proc.terminate.side_effect = ProcessLookupError()

            await camera.close(shutdown_timeout=1)

            mock_proc.kill.assert_not_called()
            assert camera._proc is None

        _run(run_test())

    def test_close_stdin_error_process_already_exited(self, mock_ffmpeg_bin, mock_proc):
        """
        stdin error but process already exited — no SIGTERM sent.

        Note: close() returns early when is_running is False, so _clear()
        is not called. The caller should handle cleanup if needed.
        """

        async def run_test():
            camera = await _create_camera(mock_ffmpeg_bin)
            camera._proc = mock_proc
            mock_proc.returncode = 0  # process already exited

            await camera.close(shutdown_timeout=1)

            # close() returns early when is_running is False
            mock_proc.terminate.assert_not_called()
            mock_proc.kill.assert_not_called()

        _run(run_test())

    def test_close_default_shutdown_timeout_value(self, mock_ffmpeg_bin):
        """close() uses FFMPEG_QUIT_TIMEOUT as default."""

        async def run_test():
            camera = await _create_camera(mock_ffmpeg_bin)
            sig = inspect.signature(camera.close)
            default_shutdown_timeout = sig.parameters["shutdown_timeout"].default
            assert default_shutdown_timeout == FFMPEG_QUIT_TIMEOUT

        _run(run_test())


class TestElegooMjpegCameraInitialUrl:
    """The FDM initial MJPEG URL must honor proxy_host (#414)."""

    @staticmethod
    def _cc2_printer(proxy_host: str | None) -> Printer:
        """Build a real CC2 printer for the camera to read (never a MagicMock)."""
        printer = Printer()
        printer.name = "Centauri Carbon 2"
        printer.id = "SERIAL123"
        printer.model = "Centauri Carbon 2"
        printer.ip_address = "10.0.0.9"
        printer.protocol_version = ProtocolVersion.CC2
        printer.transport_type = TransportType.CC2_MQTT
        printer.printer_type = PrinterType.from_model("Centauri Carbon 2")
        printer.proxy_enabled = False
        printer.proxy_host = proxy_host
        return printer

    @classmethod
    def _camera_for(cls, proxy_host: str | None) -> ElegooMjpegCamera:
        """Build an ElegooMjpegCamera whose coordinator carries that printer."""
        coordinator = MagicMock()
        coordinator.config_entry.runtime_data.api.printer = cls._cc2_printer(proxy_host)
        coordinator.generate_unique_id.return_value = "unique-id"
        description = MagicMock()
        description.name = "Camera"
        description.key = "camera"
        return ElegooMjpegCamera(MagicMock(), coordinator, description)

    def test_proxy_host_builds_a_cc2_stream_url(self) -> None:
        """
        With proxy_host set, the URL targets the proxy's CC2 stream port.

        Not the CC1 proxy's 3031/`video`: a forward proxy forwards the CC2
        stream on 8080 only, so a 3031 URL could never be served through it.
        """
        camera = self._camera_for("10.0.0.5")

        assert camera._mjpeg_url == "http://10.0.0.5:8080/?action=stream"

    def test_printer_ip_used_without_proxy_host(self) -> None:
        """Without proxy_host the stream URL points at the printer itself."""
        camera = self._camera_for(None)

        assert camera._mjpeg_url == "http://10.0.0.9:3031/video"

    def test_cc1_proxy_branch_is_untouched_by_proxy_host(self) -> None:
        """
        The CC1 local-proxy URL keeps its own host, port and query string.

        proxy_host is a CC2-only field, so it must not perturb the
        proxy_enabled branch that CC1 printers use.
        """
        printer = self._cc2_printer("10.0.0.5")
        printer.proxy_enabled = True
        coordinator = MagicMock()
        coordinator.config_entry.runtime_data.api.printer = printer
        coordinator.generate_unique_id.return_value = "unique-id"
        description = MagicMock()
        description.name = "Camera"
        description.key = "camera"

        camera = ElegooMjpegCamera(MagicMock(), coordinator, description)

        assert camera._mjpeg_url.startswith("http://")
        assert ":3031/video?id=" in camera._mjpeg_url
