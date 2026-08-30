"""
Tests for the video stream lifecycle management (documented in issue #399).

Covers the shared ElegooVideoStreamLifecycle mixin, the FDM
ElegooMjpegCamera (the class reported to leak video stream sessions), and
the resin ElegooStreamCamera regression check.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.mjpeg.camera import MjpegCamera

import custom_components.elegoo_printer.camera as camera_module
from custom_components.elegoo_printer.camera import (
    ElegooMjpegCamera,
    ElegooStreamCamera,
    ElegooVideoStreamLifecycle,
)
from custom_components.elegoo_printer.sdcp.models.enums import ElegooVideoStatus

REQUEST = MagicMock()  # web.Request stand-in


def _run(coro):
    """Run an async coroutine to completion (fresh loop)."""
    asyncio.run(coro)


class _VideoLifecycleSubject(ElegooVideoStreamLifecycle):
    """Bare lifecycle subject, bypassing entity/camera machinery."""

    def __init__(
        self,
        client: MagicMock,
        *,
        entity_id: str = "camera.test",
    ) -> None:
        self.entity_id = entity_id
        self._init_video_lifecycle(client)


def _make_client(
    *,
    connected: bool = True,
    over_capacity: bool = False,
) -> tuple[MagicMock, MagicMock]:
    """
    Build a mock printer client with a video object.

    Returns:
        (client, video) — video.parameters can be set per test to make
        status checks pass/fail.

    """
    client = MagicMock()
    client.is_connected = connected
    client.printer_data = MagicMock()
    attrs = client.printer_data.attributes
    attrs.num_video_stream_connected = 2 if over_capacity else 0
    attrs.max_video_stream_allowed = 1
    video = MagicMock()
    video.status = ElegooVideoStatus.SUCCESS
    video.video_url = "127.0.0.1:8080/mjpeg"
    client.printer_data.video = video
    client.get_printer_video = AsyncMock(return_value=video)
    client.set_printer_video_stream = AsyncMock()
    return client, video


def _fdm_camera(client: MagicMock) -> ElegooMjpegCamera:
    """Build an ElegooMjpegCamera object without full entity init."""
    cam = object.__new__(ElegooMjpegCamera)
    cam.hass = MagicMock()
    cam.entity_id = "camera.test"
    cam._mjpeg_url = None
    cam._init_video_lifecycle(client)
    return cam


def _resin_camera(client: MagicMock) -> ElegooStreamCamera:
    """Build an ElegooStreamCamera object without full entity init."""
    cam = object.__new__(ElegooStreamCamera)
    cam.hass = MagicMock()
    cam.entity_id = "camera.test"
    cam.entity_description = MagicMock(key="chamber_camera")
    cam._extra_ffmpeg_arguments = "-rtsp_transport udp"
    cam._active_mjpeg_processes: set = set()
    cam._init_video_lifecycle(client)
    return cam


class TestVideoLifecycleMixin:
    """Shared ref-counted lifecycle behaviour."""

    def test_initial_state_disabled(self) -> None:
        """Stream starts disabled with zero viewers."""
        client, _ = _make_client()
        subject = _VideoLifecycleSubject(client)
        assert subject._stream_enabled is False
        assert subject._active_mjpeg_streams == 0
        assert subject._transient_viewers == 0
        assert subject._native_stream_active is False

    def test_ensure_enabled_sends_enable_and_sets_flag(self) -> None:
        """First enable sends get_printer_video(enable=True)."""

        async def run() -> None:
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            await subject._ensure_stream_enabled()
            client.get_printer_video.assert_called_once_with(enable=True)
            assert subject._stream_enabled is True

        _run(run())

    def test_ensure_enabled_is_idempotent(self) -> None:
        """Second call while enabled issues no further command."""

        async def run() -> None:
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            await subject._ensure_stream_enabled()
            client.get_printer_video.assert_not_called()

        _run(run())

    def test_ensure_enabled_skips_when_not_connected(self) -> None:
        """No command is sent when the client is disconnected."""

        async def run() -> None:
            client, _ = _make_client(connected=False)
            subject = _VideoLifecycleSubject(client)
            await subject._ensure_stream_enabled()
            client.get_printer_video.assert_not_called()
            assert subject._stream_enabled is False

        _run(run())

    def test_ensure_enabled_failure_keeps_disabled(self) -> None:
        """A non-success status means no flag and no disable later."""

        async def run() -> None:
            client, video = _make_client()
            video.status = ElegooVideoStatus.UNKNOWN_ERROR
            subject = _VideoLifecycleSubject(client)
            await subject._ensure_stream_enabled()
            assert subject._stream_enabled is False

        _run(run())

    def test_ensure_enabled_swallows_exception(self) -> None:
        """A raised enable failure is logged, flag stays unset."""

        async def run() -> None:
            client, _ = _make_client()
            client.get_printer_video.side_effect = RuntimeError("boom")
            subject = _VideoLifecycleSubject(client)
            await subject._ensure_stream_enabled()  # must not raise
            assert subject._stream_enabled is False

        _run(run())

    def test_disable_stream_sends_disable_and_clears_flag(self) -> None:
        """Disable sends set_printer_video_stream(enable=False)."""

        async def run() -> None:
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            await subject._disable_stream()
            client.set_printer_video_stream.assert_called_once_with(enable=False)
            assert subject._stream_enabled is False

        _run(run())

    def test_disable_is_noop_when_not_enabled(self) -> None:
        """No command if the stream was never enabled."""

        async def run() -> None:
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            await subject._disable_stream()
            client.set_printer_video_stream.assert_not_called()

        _run(run())

    def test_disable_failure_keeps_flag_for_watchdog(self) -> None:
        """A failed disable keeps the flag set (watchdog retries)."""

        async def run() -> None:
            client, _ = _make_client()
            client.set_printer_video_stream.side_effect = RuntimeError("busy")
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            await subject._disable_stream()
            assert subject._stream_enabled is True

        _run(run())

    def test_watchdog_tick_disables_idle_stream(self) -> None:
        """Enabled with no active viewer gets disabled on the next tick."""

        async def run() -> None:
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            await subject._idle_watchdog_tick()
            client.set_printer_video_stream.assert_called_once_with(enable=False)
            assert subject._stream_enabled is False

        _run(run())

    def test_watchdog_tick_keeps_stream_while_viewers_active(self) -> None:
        """Active viewers prevent a tabletop disable."""

        async def run() -> None:
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            subject._transient_viewers = 1
            await subject._idle_watchdog_tick()
            client.set_printer_video_stream.assert_not_called()
            assert subject._stream_enabled is True

        _run(run())

    def test_watchdog_tick_clears_idle_native_stream(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A stale native stream flag is cleared for the next disable."""

        async def run() -> None:
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            # Use a zero timeout and a tiny positive last_activity so the
            # idle check (loop_time - last > 0) holds regardless of the
            # event loop's clock basis. A real past value is unrepresentable
            # when the loop's clock is a fresh counter, so we make the
            # timeout itself zero instead.
            monkeypatch.setattr(camera_module, "NATIVE_STREAM_IDLE_TIMEOUT", 0)
            subject._native_stream_active = True
            subject._last_activity = 1e-9
            await subject._idle_watchdog_tick()
            assert subject._native_stream_active is False

        _run(run())

    def test_cleanup_video_lifecycle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Removal cancels the watchdog, resets counters, disables the stream."""

        async def run() -> None:
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            subject._active_mjpeg_streams = 1
            subject._transient_viewers = 1
            subject._native_stream_active = True
            task = asyncio.create_task(subject._idle_watchdog())
            monkeypatch.setattr(
                camera_module, "IDLE_WATCHDOG_INTERVAL", 100
            )  # avoid extra ticks interfering
            await subject._cleanup_video_lifecycle()
            assert subject._idle_watchdog_task is None
            assert subject._active_mjpeg_streams == 0
            assert subject._transient_viewers == 0
            assert subject._native_stream_active is False
            assert subject._stream_enabled is False
            client.set_printer_video_stream.assert_called_once_with(enable=False)
            task.cancel()

        _run(run())

    def test_idle_watchdog_loop_runs_tick(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The long-lived watchdog loop performs at least one tick."""

        async def run() -> None:
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            monkeypatch.setattr(camera_module, "IDLE_WATCHDOG_INTERVAL", 0.001)
            task = asyncio.create_task(subject._idle_watchdog())
            # Wait for the first tick to land
            deadline = asyncio.get_event_loop().time() + 2.0
            while client.set_printer_video_stream.call_count == 0:
                if asyncio.get_event_loop().time() > deadline:
                    break
                await asyncio.sleep(0.01)
            assert client.set_printer_video_stream.call_count >= 1
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        _run(run())


class TestFdmMjpegCameraVideoLifecycle:
    """
    Cover the #399 leak in the FDM cameras.

    FDM cameras enabled the video stream and never disabled it again,
    so stream sessions leaked until the printer was power-cycled.
    """

    def test_camera_image_refcounts_video_on_off(self) -> None:
        """A single still capture enables once and disables on release."""

        async def run() -> None:
            client, _ = _make_client()
            cam = _fdm_camera(client)
            with patch.object(
                MjpegCamera,
                "async_camera_image",
                new=AsyncMock(return_value=b"img"),
            ):
                await cam.async_camera_image()
            client.get_printer_video.assert_called_once_with(enable=True)
            assert cam._mjpeg_url is not None
            client.set_printer_video_stream.assert_called_once_with(enable=False)
            assert cam._active_mjpeg_streams == 0
            assert cam._transient_viewers == 0

        _run(run())

    def test_camera_image_reuses_enabled_stream(self) -> None:
        """A capture during an active MJPEG stream must not re-enable the stream."""

        async def run() -> None:
            client, _ = _make_client()
            cam = _fdm_camera(client)
            cam._active_mjpeg_streams = 1
            cam._stream_enabled = True
            with patch.object(
                MjpegCamera,
                "async_camera_image",
                new=AsyncMock(return_value=b"img"),
            ):
                await cam.async_camera_image()
            client.get_printer_video.assert_not_called()
            client.set_printer_video_stream.assert_not_called()
            assert cam._active_mjpeg_streams == 1

        _run(run())

    def test_camera_image_disabled_when_managing(self) -> None:
        """A failed still capture still disables the stream afterwards."""

        async def run() -> None:
            client, _ = _make_client()
            cam = _fdm_camera(client)
            with (
                patch.object(
                    MjpegCamera,
                    "async_camera_image",
                    new=AsyncMock(side_effect=TimeoutError("frame lost")),
                ),
                pytest.raises(TimeoutError),
            ):
                await cam.async_camera_image()
            client.set_printer_video_stream.assert_called_once_with(enable=False)
            assert cam._stream_enabled is False

        _run(run())

    def test_camera_image_not_connected_no_enable(self) -> None:
        """No command is issued when the client is disconnected."""

        async def run() -> None:
            client, _ = _make_client(connected=False)
            cam = _fdm_camera(client)
            with patch.object(
                MjpegCamera,
                "async_camera_image",
                new=AsyncMock(return_value=b"img"),
            ):
                result = await cam.async_camera_image()
            assert result is None
            client.get_printer_video.assert_not_called()
            client.set_printer_video_stream.assert_not_called()

        _run(run())

    def test_camera_image_over_capacity_no_enable(self) -> None:
        """An over-capacity printer is left untouched."""

        async def run() -> None:
            client, _ = _make_client(over_capacity=True)
            cam = _fdm_camera(client)
            with patch.object(
                MjpegCamera,
                "async_camera_image",
                new=AsyncMock(return_value=b"img"),
            ):
                result = await cam.async_camera_image()
            assert result is None
            client.get_printer_video.assert_not_called()
            client.set_printer_video_stream.assert_not_called()

        _run(run())

    def test_camera_image_failed_keeps_state_clean(self) -> None:
        """A failed enable leaves no residual enabled state on the stream."""

        async def run() -> None:
            client, video = _make_client()
            video.status = ElegooVideoStatus.UNKNOWN_ERROR
            cam = _fdm_camera(client)
            with patch.object(
                MjpegCamera,
                "async_camera_image",
                new=AsyncMock(return_value=b"img"),
            ):
                result = await cam.async_camera_image()
            assert result is None
            # No enable command is sent, no disable needed
            client.get_printer_video.assert_called_once_with(enable=True)
            client.set_printer_video_stream.assert_not_called()
            assert cam._stream_enabled is False

        _run(run())

    def test_handle_mjpeg_stream_enables_and_disables(self) -> None:
        """A live stream viewer refcounts the video stream."""

        async def run() -> None:
            client, _ = _make_client()
            cam = _fdm_camera(client)
            response = MagicMock()
            with patch.object(
                MjpegCamera,
                "handle_async_mjpeg_stream",
                new=AsyncMock(return_value=response),
            ) as mjpeg_handler:
                result = await cam.handle_async_mjpeg_stream(REQUEST)
            mjpeg_handler.assert_awaited_once()
            assert result is response
            client.get_printer_video.assert_called_once_with(enable=True)
            client.set_printer_video_stream.assert_called_once_with(enable=False)
            assert cam._active_mjpeg_streams == 0

        _run(run())

    def test_handle_mjpeg_stream_over_capacity_returns_503(self) -> None:
        """An over-capacity stream request is rejected with 503."""

        async def run() -> None:
            client, _ = _make_client(over_capacity=True)
            cam = _fdm_camera(client)
            with patch.object(
                MjpegCamera,
                "handle_async_mjpeg_stream",
                new=AsyncMock(return_value=MagicMock()),
            ) as mjpeg_handler:
                result = await cam.handle_async_mjpeg_stream(REQUEST)
            mjpeg_handler.assert_not_awaited()
            assert result.status == 503
            client.get_printer_video.assert_not_called()
            client.set_printer_video_stream.assert_not_called()

        _run(run())

    def test_handle_mjpeg_stream_keeps_stream_while_viewers(self) -> None:
        """A second viewer does not re-sync or re-disable the stream."""

        async def run() -> None:
            client, _ = _make_client()
            cam = _fdm_camera(client)
            cam._active_mjpeg_streams = 1
            cam._stream_enabled = True
            with patch.object(
                MjpegCamera,
                "handle_async_mjpeg_stream",
                new=AsyncMock(return_value=MagicMock()),
            ):
                await cam.handle_async_mjpeg_stream(REQUEST)
            client.get_printer_video.assert_not_called()
            client.set_printer_video_stream.assert_not_called()
            assert cam._active_mjpeg_streams == 1

        _run(run())

    def test_stream_source_refcounts_video(self) -> None:
        """The native stream path enables, tracks, and releases cleanly."""

        async def run() -> None:
            client, _ = _make_client()
            camera = _fdm_camera(client)
            result = await camera.stream_source()
            assert result is not None
            client.get_printer_video.assert_called_once_with(enable=True)
            assert camera._native_stream_active is True
            # An active native viewer blocks the disable; repeated calls
            # never re-ask the printer for the URL
            result = await camera.stream_source()
            client.get_printer_video.assert_called_once_with(enable=True)
            # After the idle clear drops the flag, the next tick
            # releases the stream on the printer
            camera._native_stream_active = False
            await camera._idle_watchdog_tick()
            assert camera._stream_enabled is False
            client.set_printer_video_stream.assert_called_once_with(enable=False)

        _run(run())

    def test_will_remove_from_hass_disables_stream(self) -> None:
        """Entity removal releases the video stream on the printer."""

        async def run() -> None:
            client, _ = _make_client()
            cam = _fdm_camera(client)
            # The full async_added_to_hass path needs a real coordinator;
            # the mixin starts its watchdog on add, which the cleanup path
            # cancels — removal can be verified without the added hook.
            cam._stream_enabled = True
            await cam.async_will_remove_from_hass()
            client.set_printer_video_stream.assert_called_once_with(enable=False)
            assert cam._idle_watchdog_task is None

        _run(run())


class TestResinStreamCameraLifecycle:
    """Regression: the resin camera's stream lifecycle continues to apply."""

    def test_mjpeg_stream_refcounts(self) -> None:
        """Stream viewers enable and disable the printer video stream."""

        async def run() -> None:
            client, _ = _make_client()
            cam = _resin_camera(client)
            with (
                patch.object(camera_module, "ElegooCameraMjpeg") as cam_mjpeg,
                patch.object(
                    camera_module,
                    "async_aiohttp_proxy_stream",
                    new=AsyncMock(return_value=MagicMock()),
                ),
            ):
                cam_mjpeg.return_value.open_camera = AsyncMock()
                cam_mjpeg.return_value.get_reader = AsyncMock(return_value=MagicMock())
                cam_mjpeg.return_value.close = AsyncMock()
                await cam.handle_async_mjpeg_stream(REQUEST)
            cam_mjpeg.return_value.get_reader.assert_awaited_once()
            client.get_printer_video.assert_called_once_with(enable=True)
            client.set_printer_video_stream.assert_called_once_with(enable=False)
            assert cam._active_mjpeg_streams == 0
            assert len(cam._active_mjpeg_processes) == 0

        _run(run())

    def test_camera_image_refcounts(self) -> None:
        """A still capture through the camera class reference-management."""

        async def run() -> None:
            client, video = _make_client()
            video.video_url = "rtsp://127.0.0.1:8080/stream"
            cam = _resin_camera(client)
            with patch.object(
                camera_module,
                "async_get_image",
                new=AsyncMock(return_value=b"img"),
            ) as image_mock:
                await cam.async_camera_image()
            image_mock.assert_awaited_once()
            client.get_printer_video.assert_called_once_with(enable=True)
            client.set_printer_video_stream.assert_called_once_with(enable=False)

        _run(run())

    def test_will_remove_closes_processes_and_disables(self) -> None:
        """Removal closes in-flight MJPEG processes and releases the stream."""

        async def run() -> None:
            client, _ = _make_client()
            cam = _resin_camera(client)
            cam._stream_enabled = True
            in_flight = MagicMock()
            in_flight.close = AsyncMock()
            with patch.object(camera_module, "ElegooCameraMjpeg") as cam_mjpeg:
                cam_mjpeg.return_value = in_flight
                cam._active_mjpeg_processes = {in_flight}
            await cam.async_will_remove_from_hass()
            in_flight.close.assert_awaited_once()
            client.set_printer_video_stream.assert_called_once_with(enable=False)

        _run(run())
