"""
Tests for the video stream lifecycle management (documented in issue #399).

Covers the shared ElegooVideoStreamLifecycle mixin, the FDM
ElegooMjpegCamera (the class reported to leak video stream sessions), and
the resin ElegooStreamCamera regression check. Also covers what happens to a
release a foreign viewer is still blocking when the entity, its client and
eventually its config entry go away: the bounded teardown retry, the handover
to the next camera entity of the entry, the task that carries it, and the two
places that settle a handed-over release for good — a teardown that pays it and
the integration's config-entry removal hook.

Both halves of that handover are the config entry's business, so they are
checked as such: the viewers that gate a release are counted across every live
camera entity of the entry (a retry that outlived its entity must not act on the
counters its teardown reset), and every exit of the retry that leaves the release
unpaid hands it on instead of ending with the printer still enabled.
"""

import asyncio
import contextlib
import gc
import logging
import weakref
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.mjpeg.camera import MjpegCamera

import custom_components.elegoo_printer as integration_module
import custom_components.elegoo_printer.camera as camera_module
from custom_components.elegoo_printer.camera import (
    ElegooMjpegCamera,
    ElegooStreamCamera,
    ElegooVideoStreamLifecycle,
)
from custom_components.elegoo_printer.definitions import PRINTER_MJPEG_CAMERAS
from custom_components.elegoo_printer.sdcp.models.enums import ElegooVideoStatus

REQUEST = MagicMock()  # web.Request stand-in
POLL_TIMEOUT_SECONDS = 2.0  # ceiling for waiting on a scheduled task to react


@pytest.fixture(autouse=True)
def _no_disable_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the disable settle to zero so tests stay fast and deterministic."""
    monkeypatch.setattr(camera_module, "STREAM_DISABLE_SETTLE_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def _isolated_pending_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Give every test its own pending-release registry.

    The handover registry is module level on purpose (it has to survive the
    entity and the entry's runtime data), so tests must not inherit each
    other's entries. Replacing the mapping keeps production code untouched.
    """
    monkeypatch.setattr(camera_module, "_pending_video_releases", {})


@pytest.fixture(autouse=True)
def _isolated_viewer_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Give every test its own live-entity registry.

    Like the handover registry this one is module level because the entity it
    tracks can outlive nothing but itself: it must not carry entities (or the
    leftover groups of a previous test) across test boundaries.
    """
    monkeypatch.setattr(camera_module, "_video_viewers_by_entry", {})


def _run(coro):
    """Run an async coroutine to completion (fresh loop)."""
    asyncio.run(coro)


class _VideoLifecycleSubject(ElegooVideoStreamLifecycle):
    """Bare lifecycle subject, bypassing entity/camera machinery."""

    # Behave like the FDM camera so the external-viewer guard is exercised.
    _keep_stream_for_external_viewers = True

    def __init__(
        self,
        client: MagicMock,
        *,
        entity_id: str = "camera.test",
    ) -> None:
        self.hass = MagicMock()
        self.entity_id = entity_id
        self._init_video_lifecycle(client)


def _make_client(
    *,
    connected: bool = True,
    over_capacity: bool = False,
    connected_streams: int | None = None,
    max_streams: int = 1,
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
    if connected_streams is not None:
        attrs.num_video_stream_connected = connected_streams
    else:
        attrs.num_video_stream_connected = 2 if over_capacity else 0
    attrs.max_video_stream_allowed = max_streams
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


def _make_entry(entry_id: str = "entry-1") -> SimpleNamespace:
    """
    Build a config-entry double.

    ``runtime_data`` starts unset (None), which is the state core leaves the
    entry in once it has been unloaded — a reload has to set a brand-new one.
    """
    return SimpleNamespace(entry_id=entry_id, data={}, runtime_data=None)


def _leave_stream_enabled(entry: SimpleNamespace) -> None:
    """
    Mark the shared printer video of an entry as enabled.

    The enabled flag lives on the config entry so it survives an unload, while
    its runtime data does not — pairing the two models the state a reload starts
    from after a previous entity left the stream on.
    """
    setattr(entry, "_elegoo_video_state", {"enabled": True})  # noqa: B010


def _make_printer() -> MagicMock:
    """Build the printer description double the MJPEG camera reads in __init__."""
    printer = MagicMock()
    printer.proxy_enabled = False
    printer.proxy_host = None
    printer.ip_address = "192.168.1.50"
    printer.id = "mainboard-1"
    return printer


def _attach_client(entry: SimpleNamespace, client: MagicMock) -> MagicMock:
    """
    Give an entry live runtime data around ``client``, as setup/reload does.

    Returns:
        The coordinator the camera entities of that entry are built with.

    """
    coordinator = MagicMock()
    coordinator.config_entry = entry
    coordinator.generate_unique_id = MagicMock(side_effect=lambda key: f"uid-{key}")
    entry.runtime_data = SimpleNamespace(
        api=SimpleNamespace(client=client, printer=_make_printer()),
        coordinator=coordinator,
    )
    return coordinator


def _make_hass(entry: SimpleNamespace | None) -> MagicMock:
    """
    Build a hass double that resolves the config entry and schedules tasks.

    ``entry=None`` models a removed entry: core drops it from
    ``hass.config_entries`` before calling the removal hook, so
    ``async_get_entry`` stops finding it while a plain reload keeps it present.
    ``async_create_background_task`` creates a real task so tests can inspect
    and cancel what the lifecycle scheduled.
    """
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = entry

    def _background_task(coro, name, **_kwargs: object):
        # Mirrors hass, which starts background tasks immediately via
        # create_eager_task(), and keeps a strong reference to them.
        return asyncio.eager_task_factory(asyncio.get_running_loop(), coro, name=name)

    hass.async_create_background_task.side_effect = _background_task
    return hass


def _lifecycle_subject(
    client: MagicMock,
    *,
    entry: SimpleNamespace | None,
    hass: MagicMock | None = None,
    entity_id: str = "camera.test",
) -> _VideoLifecycleSubject:
    """Build a lifecycle subject attached to a config-entry double."""
    subject = _VideoLifecycleSubject(client, entity_id=entity_id)
    subject.hass = hass if hass is not None else _make_hass(entry)
    if entry is not None:
        coordinator = MagicMock()
        coordinator.config_entry = entry
        subject.coordinator = coordinator
    return subject


def _live_fdm_camera(entry: SimpleNamespace, hass: MagicMock) -> ElegooMjpegCamera:
    """
    Build a real FDM chamber camera entity for an entry with live runtime data.

    Unlike ``_fdm_camera`` this runs the class constructor, so the entity is
    attached to the coordinator and its client exactly as a reload would build
    it — which is what the pending-release handover needs.
    """
    coordinator = entry.runtime_data.coordinator
    cam = ElegooMjpegCamera(hass, coordinator, PRINTER_MJPEG_CAMERAS[0])
    # Entities normally get hass from the platform when they are added.
    cam.hass = hass
    cam.entity_id = "camera.chamber_camera"
    return cam


async def _wait_for(predicate) -> bool:
    """
    Poll ``predicate`` until it holds or the deadline passes.

    Returns:
        Whether the predicate ended up holding.

    """
    deadline = asyncio.get_event_loop().time() + POLL_TIMEOUT_SECONDS
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            break
        await asyncio.sleep(0.005)
    return bool(predicate())


async def _cancel(task) -> None:
    """Cancel a scheduled task and collect it."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


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
        """Second call while enabled and refreshed issues no further command."""

        async def run() -> None:
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            subject._stream_ready = True
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

    def test_disable_skipped_when_external_viewer_connected(self) -> None:
        """A foreign stream connection keeps the printer video enabled."""

        async def run() -> None:
            client, _ = _make_client(connected_streams=1, max_streams=2)
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            await subject._disable_stream()
            client.set_printer_video_stream.assert_not_called()
            assert subject._stream_enabled is True

        _run(run())

    def test_external_viewer_detection_boundary(self) -> None:
        """Only connections beyond our own tracked viewers are external."""
        client, _ = _make_client(connected_streams=1, max_streams=5)
        subject = _VideoLifecycleSubject(client)
        subject._active_mjpeg_streams = 1
        assert subject._has_external_video_viewers() is False

        client.printer_data.attributes.num_video_stream_connected = 2
        assert subject._has_external_video_viewers() is True

    def test_stream_enabled_state_is_shared_per_entry(self) -> None:
        """Entities of the same config entry agree on the enabled flag."""
        coordinator = MagicMock()
        coordinator.config_entry = SimpleNamespace()
        client_a, _ = _make_client()
        client_b, _ = _make_client()
        a = _VideoLifecycleSubject(client_a)
        b = _VideoLifecycleSubject(client_b)
        a.coordinator = coordinator
        b.coordinator = coordinator

        a._stream_enabled = True
        assert b._stream_enabled is True

        b._stream_enabled = False
        assert a._stream_enabled is False

    def test_new_camera_refreshes_url_despite_inherited_enabled(self) -> None:
        """A shared enabled flag must not make a fresh entity trust its fallback."""
        coordinator = MagicMock()
        coordinator.config_entry = SimpleNamespace()

        old_client, _ = _make_client()
        old = _VideoLifecycleSubject(old_client)
        old.coordinator = coordinator
        old._stream_enabled = True  # left enabled by a previous entity

        client, _ = _make_client()
        cam = _fdm_camera(client)
        cam.coordinator = coordinator
        cam._mjpeg_url = "http://printer:3031/video"  # __init__ fallback
        assert cam._stream_enabled is True  # inherited from the entry

        async def run() -> None:
            with patch.object(
                MjpegCamera,
                "async_camera_image",
                new=AsyncMock(return_value=b"img"),
            ):
                await cam.async_camera_image()

        _run(run())
        client.get_printer_video.assert_called_once_with(enable=True)
        assert cam._mjpeg_url == "http://127.0.0.1:8080/mjpeg"

    def test_watchdog_tick_keeps_stream_with_external_viewer(self) -> None:
        """The idle watchdog leaves the stream on while others use it."""

        async def run() -> None:
            client, _ = _make_client(connected_streams=1, max_streams=2)
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            await subject._idle_watchdog_tick()
            client.set_printer_video_stream.assert_not_called()
            assert subject._stream_enabled is True

        _run(run())

    def test_watchdog_tick_disables_after_external_viewer_leaves(self) -> None:
        """Once the external viewer disconnects the watchdog releases the stream."""

        async def run() -> None:
            client, _ = _make_client(connected_streams=1, max_streams=2)
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            await subject._idle_watchdog_tick()
            client.set_printer_video_stream.assert_not_called()
            # External viewer disconnects
            client.printer_data.attributes.num_video_stream_connected = 0
            await subject._idle_watchdog_tick()
            client.set_printer_video_stream.assert_called_once_with(enable=False)
            assert subject._stream_enabled is False

        _run(run())

    def test_disable_waits_for_refreshed_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A viewer appearing during the settle window blocks the disable."""
        monkeypatch.setattr(camera_module, "STREAM_DISABLE_SETTLE_SECONDS", 0.02)

        async def run() -> None:
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True

            async def viewer_connects() -> None:
                # Lands while _disable_stream is waiting its settle window.
                await asyncio.sleep(0)
                client.printer_data.attributes.num_video_stream_connected = 1

            task = asyncio.create_task(viewer_connects())
            await subject._disable_stream()
            await task

            client.set_printer_video_stream.assert_not_called()
            assert subject._stream_enabled is True

        _run(run())

    def test_disable_ignores_own_connection_after_settle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Our own just-finished grab is not mistaken for an external viewer."""
        monkeypatch.setattr(camera_module, "STREAM_DISABLE_SETTLE_SECONDS", 0.02)

        async def run() -> None:
            client, _ = _make_client(connected_streams=1, max_streams=2)
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True

            async def grab_connection_closes() -> None:
                # Drop the transient count while the settle window is running.
                await asyncio.sleep(0)
                client.printer_data.attributes.num_video_stream_connected = 0

            task = asyncio.create_task(grab_connection_closes())
            await subject._disable_stream()
            await task

            client.set_printer_video_stream.assert_called_once_with(enable=False)
            assert subject._stream_enabled is False

        _run(run())

    def test_disable_skips_when_own_viewer_arrives_during_settle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A viewer of ours starting during the settle blocks the disable."""
        monkeypatch.setattr(camera_module, "STREAM_DISABLE_SETTLE_SECONDS", 0.02)

        async def run() -> None:
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True

            async def viewer_starts() -> None:
                await asyncio.sleep(0)
                subject._transient_viewers = 1

            task = asyncio.create_task(viewer_starts())
            await subject._disable_stream()
            await task

            client.set_printer_video_stream.assert_not_called()
            assert subject._stream_enabled is True

        _run(run())

    def test_cleanup_keeps_stream_for_external_viewer(self) -> None:
        """Teardown must not cut off another client that is still watching."""

        async def run() -> None:
            client, _ = _make_client(connected_streams=1, max_streams=2)
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True

            await subject._cleanup_video_lifecycle()

            client.set_printer_video_stream.assert_not_called()
            assert subject._stream_enabled is True
            # A background retry is scheduled to release it once they leave.
            # It must be a *background* task: hass only keeps a weak reference
            # to ordinary tasks, so a ~10 minute retry could be collected.
            subject.hass.async_create_background_task.assert_called_once()
            subject.hass.async_create_task.assert_not_called()
            scheduled = subject.hass.async_create_background_task.call_args.args[0]
            scheduled.close()

        _run(run())

    def test_cleanup_paying_the_debt_clears_the_parked_release(self) -> None:
        """
        A teardown that switches the shared video off settles the entry's debt.

        The debt was parked for this entity by an earlier teardown, and this one
        reaches the printer, so the key must not survive for the next entity to
        chase a stream that is already off.
        """

        async def run() -> None:
            entry = _make_entry()
            client, _ = _make_client()
            subject = _lifecycle_subject(client, entry=entry)
            camera_module._record_pending_video_release(
                entry.entry_id, "camera.chamber_camera_old"
            )
            subject._stream_enabled = True

            await subject._cleanup_video_lifecycle()

            client.set_printer_video_stream.assert_called_once_with(enable=False)
            assert subject._stream_enabled is False
            assert camera_module._pending_video_releases == {}

        _run(run())

    def test_cleanup_without_a_debt_to_pay_clears_nothing(self) -> None:
        """
        A teardown that never reaches the printer keeps the parked release.

        This is the over-clearing guard: the stream is still held by a foreign
        viewer, so the entry still owes the release. Teardown hands it to the
        retry instead of forgetting it — an entity that pays a debt it does not
        own would otherwise be erased by an unrelated entity's removal.
        """

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            client, _ = _make_client(connected_streams=1, max_streams=2)
            subject = _lifecycle_subject(client, entry=entry, hass=hass)
            camera_module._record_pending_video_release(
                entry.entry_id, "camera.chamber_camera_old"
            )
            subject._stream_enabled = True

            await subject._cleanup_video_lifecycle()

            client.set_printer_video_stream.assert_not_called()
            assert subject._stream_enabled is True
            assert camera_module._pending_video_releases == {
                entry.entry_id: "camera.chamber_camera_old"
            }
            await _cancel(subject._cleanup_retry_task)

        _run(run())

    def test_cleanup_of_a_detached_entity_keeps_another_entrys_debt(self) -> None:
        """A stream that was never on leaves an unrelated entry's debt alone."""

        async def run() -> None:
            camera_module._record_pending_video_release("entry-1", "camera.other")
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)

            await subject._cleanup_video_lifecycle()

            # The detach guard is in _config_entry_id; the registry lookup never
            # happens for an entity with no entry at all.
            assert subject._config_entry_id() is None
            assert camera_module._pending_video_releases == {"entry-1": "camera.other"}

        _run(run())

    def test_cleanup_retry_disables_after_external_viewer_leaves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The teardown retry releases the stream once the viewer leaves."""
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_INTERVAL_SECONDS", 0.01)

        async def run() -> None:
            client, _ = _make_client(connected_streams=1, max_streams=2)
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True

            async def leaves() -> None:
                await asyncio.sleep(0.02)
                client.printer_data.attributes.num_video_stream_connected = 0

            task = asyncio.create_task(leaves())
            await subject._async_cleanup_retry()
            await task

            client.set_printer_video_stream.assert_called_once_with(enable=False)
            assert subject._stream_enabled is False

        _run(run())

    def test_cleanup_retry_continues_after_failed_disable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed disable is retried instead of ending the cleanup."""
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_INTERVAL_SECONDS", 0.01)

        async def run() -> None:
            client, _ = _make_client(connected_streams=0, max_streams=2)
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            client.set_printer_video_stream.side_effect = [RuntimeError("busy"), None]

            await subject._async_cleanup_retry()

            assert client.set_printer_video_stream.call_count == 2
            assert subject._stream_enabled is False

        _run(run())

    def test_current_printer_client_none_when_disconnected(self) -> None:
        """The retry client resolver reports no live connection when down."""
        client, _ = _make_client(connected=False)
        subject = _VideoLifecycleSubject(client)
        assert subject._current_printer_client() is None

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

    def test_camera_image_keeps_stream_when_external_viewer(self) -> None:
        """A snapshot must not disable the stream while the Slicer is viewing."""

        async def run() -> None:
            client, _ = _make_client(connected_streams=1, max_streams=2)
            cam = _fdm_camera(client)
            with patch.object(
                MjpegCamera,
                "async_camera_image",
                new=AsyncMock(return_value=b"img"),
            ):
                await cam.async_camera_image()
            # The stream is enabled for the grab ...
            client.get_printer_video.assert_called_once_with(enable=True)
            # ... but not disabled, because another client is still connected
            client.set_printer_video_stream.assert_not_called()
            assert cam._stream_enabled is True

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


class TestTeardownRetryWithoutLiveClient:
    """
    Cover a retry that outlives the connection it was built with.

    Unloading the integration removes the camera entity while the client is
    still connected, so the only release that reaches the retry is one a
    foreign viewer is blocking. After that the client is disconnected and the
    entry either comes back on a reload or is gone for good — the retry has to
    tell those apart instead of spending every attempt on a dead connection.
    """

    @staticmethod
    def _fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
        """Shorten the retry interval so the loop is testable in milliseconds."""
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_INTERVAL_SECONDS", 0.01)

    @staticmethod
    def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
        """
        Collect the warning messages logged for the test entity.

        ``caplog`` is attached to the root logger, which the component LOGGER
        (``custom_components.elegoo_printer``) propagates to.
        """
        return [
            record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
            and "camera.test" in record.getMessage()
        ]

    @staticmethod
    def _unloaded(old_client: MagicMock, entry: SimpleNamespace) -> None:
        """Model the integration having finished unloading its client."""
        old_client.is_connected = False
        entry.runtime_data = None

    def test_retry_gives_up_when_entry_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A removed entry ends the retry on the first tick, with one warning."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            # Teardown happens while a foreign viewer still holds the stream.
            old_client, _ = _make_client(connected_streams=1, max_streams=2)
            entry = _make_entry()
            _attach_client(entry, old_client)
            hass = _make_hass(None)  # core dropped the entry on removal
            ticks: list[str] = []

            def forget(entry_id: str) -> None:
                ticks.append(entry_id)

            hass.config_entries.async_get_entry.side_effect = forget
            subject = _lifecycle_subject(old_client, entry=entry, hass=hass)
            subject._stream_enabled = True

            await subject._cleanup_video_lifecycle()
            retry = subject._cleanup_retry_task
            assert retry is not None

            self._unloaded(old_client, entry)
            await retry

            # Gave up on the very first tick: the entry is consulted once per
            # tick without a live client, so one lookup out of the full
            # CLEANUP_RETRY_ATTEMPTS budget means it stopped instead of spinning.
            assert ticks == ["entry-1"]
            # Exactly one loud warning, naming the entity and the way out.
            warnings = self._warnings(caplog)
            assert len(warnings) == 1
            assert "camera.test" in warnings[0]
            assert "reboot" in warnings[0]
            assert "reload" in warnings[0]
            # Nothing was ever sent through the disconnected client.
            old_client.set_printer_video_stream.assert_not_called()
            assert subject._cleanup_retry_task is None
            assert camera_module._pending_video_releases == {}

        _run(run())

    def test_retry_treats_unresolvable_entry_as_terminal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An entity with no config entry at all has nothing to wait for."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            client, _ = _make_client(connected=False)
            subject = _VideoLifecycleSubject(client)
            subject._stream_enabled = True
            await subject._async_cleanup_retry()

            warnings = self._warnings(caplog)
            assert len(warnings) == 1
            client.set_printer_video_stream.assert_not_called()

        _run(run())

    def test_retry_disables_through_the_reloaded_client(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A reload hands the retry a fresh connected client and it releases."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            old_client, _ = _make_client(connected_streams=1, max_streams=2)
            entry = _make_entry()
            _attach_client(entry, old_client)
            subject = _lifecycle_subject(
                old_client, entry=entry, hass=_make_hass(entry)
            )
            subject._stream_enabled = True
            await subject._cleanup_video_lifecycle()
            await _cancel(subject._cleanup_retry_task)

            self._unloaded(old_client, entry)
            # The entry survived the unload; a reload gives it new runtime data.
            new_client, _ = _make_client(connected_streams=0, max_streams=2)
            _attach_client(entry, new_client)

            await subject._async_cleanup_retry()

            new_client.set_printer_video_stream.assert_called_once_with(enable=False)
            assert subject._printer_client is new_client
            assert subject._stream_enabled is False
            assert not self._warnings(caplog)

        _run(run())

    def test_retry_parks_the_release_for_the_next_entity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A down client on a still-registered entry hands the release over."""
        self._fast_retry(monkeypatch)
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_ATTEMPTS", 3)

        async def run() -> None:
            old_client, _ = _make_client(connected_streams=1, max_streams=2)
            entry = _make_entry()
            _attach_client(entry, old_client)
            subject = _lifecycle_subject(
                old_client, entry=entry, hass=_make_hass(entry)
            )
            subject._stream_enabled = True

            self._unloaded(old_client, entry)
            await subject._async_cleanup_retry()

            assert camera_module._pending_video_releases == {
                entry.entry_id: "camera.test"
            }
            old_client.set_printer_video_stream.assert_not_called()

        _run(run())


class TestPendingVideoReleaseHandover:
    """A release the old entity could not pay is finished by the new one."""

    @staticmethod
    def _fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
        """Shorten the retry interval so a handover is testable in milliseconds."""
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_INTERVAL_SECONDS", 0.01)

    @staticmethod
    async def _stop(cam) -> None:
        """Cancel what the entity scheduled so the test loop exits clean."""
        await _cancel(cam._cleanup_retry_task)
        await _cancel(cam._idle_watchdog_task)

    def test_new_entity_completes_the_pending_release(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh camera for the entry disables the stream it inherited."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            # A previous entity left the entry's shared stream enabled and its
            # retry parked the release when the unload killed its connection.
            camera_module._record_pending_video_release(
                entry.entry_id, "camera.chamber_camera_old"
            )
            _leave_stream_enabled(entry)
            assert entry.runtime_data is None  # unloaded: no live client

            new_client, _ = _make_client(connected_streams=0, max_streams=2)
            coordinator = _attach_client(entry, new_client)
            assert coordinator.config_entry is entry
            cam = _live_fdm_camera(entry, hass)
            assert cam._stream_enabled is True  # inherited shared flag

            await cam.async_added_to_hass()
            try:
                released = await _wait_for(
                    lambda: new_client.set_printer_video_stream.await_count == 1
                )
                assert released, "the adopted release never reached the printer"
                new_client.set_printer_video_stream.assert_awaited_once_with(
                    enable=False
                )
                assert cam._stream_enabled is False
                # The retry runs through hass' background-task API, not the
                # weakly-referenced plain one.
                hass.async_create_background_task.assert_called_once()
                hass.async_create_task.assert_not_called()
            finally:
                await self._stop(cam)

        _run(run())

    def test_new_entity_waits_while_external_viewer_holds_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The inherited release keeps the exact same guard: never force it."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            camera_module._record_pending_video_release(
                entry.entry_id, "camera.chamber_camera_old"
            )
            _leave_stream_enabled(entry)

            new_client, _ = _make_client(connected_streams=1, max_streams=2)
            _attach_client(entry, new_client)
            cam = _live_fdm_camera(entry, hass)

            await cam.async_added_to_hass()
            try:
                assert await _wait_for(lambda: cam._cleanup_retry_task is not None)
                # Several retry intervals pass with the slicer still watching.
                await asyncio.sleep(0.05)
                new_client.set_printer_video_stream.assert_not_called()
                assert cam._stream_enabled is True
                assert (
                    camera_module._pending_video_release_entity(entry.entry_id)
                    == "camera.chamber_camera_old"
                )

                # The foreign viewer leaves: the inherited release lands.
                new_client.printer_data.attributes.num_video_stream_connected = 0
                assert await _wait_for(
                    lambda: new_client.set_printer_video_stream.await_count == 1
                )
                assert cam._stream_enabled is False
            finally:
                await self._stop(cam)

        _run(run())

    def test_completed_release_is_not_reattempted_by_a_later_entity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pending flag is cleared, so the next entity leaves it alone."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            camera_module._record_pending_video_release(
                entry.entry_id, "camera.chamber_camera_old"
            )
            _leave_stream_enabled(entry)

            first_client, _ = _make_client(connected_streams=0, max_streams=2)
            _attach_client(entry, first_client)
            first = _live_fdm_camera(entry, hass)
            await first.async_added_to_hass()
            try:
                assert await _wait_for(
                    lambda: first_client.set_printer_video_stream.await_count == 1
                )
            finally:
                await self._stop(first)

            assert camera_module._pending_video_releases == {}
            assert hass.async_create_background_task.call_count == 1

            # A later entity for the same entry adopts nothing: it must not
            # re-disable a stream it never enabled.
            second_client, _ = _make_client(connected_streams=0, max_streams=2)
            _attach_client(entry, second_client)
            second = _live_fdm_camera(entry, hass)
            await second.async_added_to_hass()
            try:
                await asyncio.sleep(0.05)
                assert second._cleanup_retry_task is None
                assert hass.async_create_background_task.call_count == 1
                second_client.set_printer_video_stream.assert_not_called()
                assert second._stream_enabled is False
            finally:
                await self._stop(second)

        _run(run())

    def test_detached_entity_does_not_adopt_another_entrys_release(self) -> None:
        """An entity with no resolvable config entry adopts nothing."""

        async def run() -> None:
            camera_module._record_pending_video_release("entry-1", "camera.other")
            client, _ = _make_client()
            subject = _VideoLifecycleSubject(client)
            subject._adopt_pending_video_release()

            await asyncio.sleep(0)
            assert subject._cleanup_retry_task is None
            subject.hass.async_create_background_task.assert_not_called()

        _run(run())

    def test_adopter_paying_the_debt_in_its_own_teardown_clears_the_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The entity that finally releases the stream settles the entry's debt."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            camera_module._record_pending_video_release(
                entry.entry_id, "camera.chamber_camera_old"
            )
            _leave_stream_enabled(entry)

            # The foreign viewer is still watching while the new entity starts,
            # so its adopted retry can only wait.
            client, _ = _make_client(connected_streams=1, max_streams=2)
            _attach_client(entry, client)
            cam = _live_fdm_camera(entry, hass)
            await cam.async_added_to_hass()
            assert await _wait_for(lambda: cam._cleanup_retry_task is not None)
            await asyncio.sleep(0.05)
            client.set_printer_video_stream.assert_not_called()
            assert camera_module._pending_video_release_entity(entry.entry_id)

            # The viewer leaves and this entity is torn down at the same time:
            # its own teardown pays the debt instead of the retry's next tick.
            # The retry is collected first (teardown cancels it anyway) so the
            # release below can only have come from this entity's teardown.
            await _cancel(cam._cleanup_retry_task)
            client.printer_data.attributes.num_video_stream_connected = 0
            await cam.async_will_remove_from_hass()

            client.set_printer_video_stream.assert_awaited_once_with(enable=False)
            assert cam._stream_enabled is False
            assert camera_module._pending_video_releases == {}

            # A later entity for the same entry adopts nothing: the release it
            # would have inherited has already been paid.
            later_client, _ = _make_client(connected_streams=0, max_streams=2)
            _attach_client(entry, later_client)
            later = _live_fdm_camera(entry, hass)
            await later.async_added_to_hass()
            try:
                await asyncio.sleep(0.05)
                assert later._cleanup_retry_task is None
                later_client.set_printer_video_stream.assert_not_called()
            finally:
                await self._stop(later)

        _run(run())

    def test_reload_sequence_releases_the_held_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end: unload with a foreign viewer, reload, stream released."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            old_client, _ = _make_client(connected_streams=1, max_streams=2)
            _attach_client(entry, old_client)
            old = _live_fdm_camera(entry, hass)
            await old.async_added_to_hass()
            # The integration enabled the shared video for a viewer of ours.
            old._stream_enabled = True

            # --- unload: entity goes first, then the client is disconnected ---
            await old.async_will_remove_from_hass()
            await _wait_for(lambda: old._cleanup_retry_task is not None)
            old_client.is_connected = False
            entry.runtime_data = None

            # The held stream survives the unload; the retry parks the release
            # instead of spending its attempts on the dead client.
            assert await _wait_for(
                lambda: camera_module._pending_video_release_entity(entry.entry_id)
                is not None
            )
            old_client.set_printer_video_stream.assert_not_called()
            assert old._stream_enabled is True
            await self._stop(old)

            # --- reload: a brand-new entity with a brand-new client ---
            new_client, _ = _make_client(connected_streams=0, max_streams=2)
            _attach_client(entry, new_client)
            new = _live_fdm_camera(entry, hass)
            await new.async_added_to_hass()
            try:
                assert await _wait_for(
                    lambda: new_client.set_printer_video_stream.await_count == 1
                )
                assert new._stream_enabled is False
                assert camera_module._pending_video_releases == {}
                old_client.set_printer_video_stream.assert_not_called()

                # The adopted release must not leave the new entity believing it
                # enabled the video itself: neither its watchdog nor a later
                # teardown may disable a stream it never turned on.
                await new._idle_watchdog_tick()
                await new.async_will_remove_from_hass()
                new_client.set_printer_video_stream.assert_awaited_once_with(
                    enable=False
                )
            finally:
                await self._stop(new)

        _run(run())


class TestCleanupRetryTaskOwnership:
    """The long-lived retry must survive GC and not outlive its purpose."""

    def test_retry_is_a_named_background_task_and_is_not_duplicated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """hass keeps background tasks, and only one retry may run."""
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_INTERVAL_SECONDS", 0.05)

        async def run() -> None:
            client, _ = _make_client(connected_streams=1, max_streams=2)
            entry = _make_entry()
            _attach_client(entry, client)
            hass = _make_hass(entry)
            subject = _lifecycle_subject(client, entry=entry, hass=hass)
            subject._stream_enabled = True

            subject._schedule_cleanup_retry()
            first = subject._cleanup_retry_task
            assert first is not None
            subject._schedule_cleanup_retry()

            hass.async_create_background_task.assert_called_once()
            hass.async_create_task.assert_not_called()
            assert subject._cleanup_retry_task is first
            name = hass.async_create_background_task.call_args.args[1]
            assert subject.entity_id in name
            await _cancel(first)
            assert first.cancelled()

        _run(run())

    def test_teardown_cancels_a_retry_started_before_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Teardown drops a stale retry instead of leaving it acting blindly."""
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_INTERVAL_SECONDS", 0.05)

        async def run() -> None:
            client, _ = _make_client(connected_streams=1, max_streams=2)
            entry = _make_entry()
            _attach_client(entry, client)
            subject = _lifecycle_subject(client, entry=entry, hass=_make_hass(entry))
            subject._stream_enabled = True
            subject._schedule_cleanup_retry()
            stale = subject._cleanup_retry_task
            assert stale is not None

            await subject._cleanup_video_lifecycle()

            # Collect the cancelled task: cancellation only lands once it runs.
            with contextlib.suppress(asyncio.CancelledError):
                await stale
            assert stale.cancelled()
            # Teardown re-armed a retry for the stream it could not release and
            # the retiring task did not clear the slot its replacement owns.
            retry = subject._cleanup_retry_task
            assert retry is not None
            assert retry is not stale
            assert not retry.done()
            await _cancel(retry)

        _run(run())

    def test_cancelled_retry_keeps_the_retry_its_replacement_scheduled(self) -> None:
        """A retiring task must not clear the task slot owned by its successor."""

        async def run() -> None:
            client, _ = _make_client(connected_streams=1, max_streams=2)
            entry = _make_entry()
            _attach_client(entry, client)
            hass = _make_hass(entry)
            subject = _lifecycle_subject(client, entry=entry, hass=hass)
            subject._stream_enabled = True
            subject._schedule_cleanup_retry()
            stale = subject._cleanup_retry_task
            assert stale is not None
            stale.cancel()
            # A replacement takes the slot before the cancellation unwinds.
            subject._cleanup_retry_task = None
            subject._schedule_cleanup_retry()
            replacement = subject._cleanup_retry_task
            await asyncio.sleep(0)  # let the cancelled task run its finally

            assert subject._cleanup_retry_task is replacement
            assert not replacement.done()
            await _cancel(replacement)

        _run(run())


class TestEntryRemovalForgetsTheParkedRelease:
    """
    Cover the integration's removal hook for a parked release.

    A retry only ever notices a removal while it is still running. Once it has
    exhausted its attempts — or once teardown left the key without a retry at all
    — nothing is watching for the entry to disappear, so the integration's
    ``async_remove_entry`` hook is what keeps the registry from holding the entry
    id forever. Core calls it through ``hasattr(component, "async_remove_entry")``,
    so these tests resolve it off the integration module the same way.
    """

    @staticmethod
    def _hook() -> Callable[..., Awaitable[None]]:
        """Resolve the removal hook off the integration module, as core does."""
        assert hasattr(integration_module, "async_remove_entry"), (
            "core only calls a component's async_remove_entry when it defines one"
        )
        return integration_module.async_remove_entry

    def test_retry_exhaustion_then_removal_leaves_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Path A: an exhausted retry parks a key; removal forgets it."""
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_ATTEMPTS", 3)

        async def run() -> None:
            client, _ = _make_client(connected_streams=1, max_streams=2)
            entry = _make_entry()
            _attach_client(entry, client)
            hass = _make_hass(entry)
            subject = _lifecycle_subject(client, entry=entry, hass=hass)
            subject._stream_enabled = True
            # The unload killed the connection the retry was acting through.
            client.is_connected = False
            entry.runtime_data = None

            await subject._async_cleanup_retry()

            # The retry ran out of attempts and left the debt parked: the entry
            # is still registered, so a reload could still pay it.
            assert camera_module._pending_video_releases == {
                entry.entry_id: "camera.test"
            }

            # Removal is terminal and core has already dropped the entry by the
            # time the hook runs (ConfigEntries.async_remove deletes it before
            # calling the remove callback), which is what makes the parked key
            # forgettable. The hook never consults hass, so the double is only
            # here to stand in for that dropped-entry hass.
            hass.config_entries.async_get_entry.return_value = None
            await self._hook()(hass, entry)

            assert camera_module._pending_video_releases == {}

        _run(run())

    def test_removal_is_idempotent_and_never_raises(self) -> None:
        """Removing an entry that parked nothing is a silent no-op."""

        async def run() -> None:
            entry = _make_entry()
            await self._hook()(MagicMock(), entry)
            assert camera_module._pending_video_releases == {}
            # A second removal (or a reload followed by one) changes nothing.
            await self._hook()(MagicMock(), entry)
            assert camera_module._pending_video_releases == {}

        _run(run())

    def test_removal_only_forgets_its_own_entry(self) -> None:
        """Another printer's parked release survives this entry's removal."""

        async def run() -> None:
            removed = _make_entry("entry-removed")
            other = _make_entry("entry-other")
            camera_module._record_pending_video_release(
                removed.entry_id, "camera.chamber_camera"
            )
            camera_module._record_pending_video_release(
                other.entry_id, "camera.chamber_camera_other"
            )

            await self._hook()(MagicMock(), removed)

            assert camera_module._pending_video_releases == {
                other.entry_id: "camera.chamber_camera_other"
            }

        _run(run())

    def test_removal_does_not_disturb_the_shared_stream_flag(self) -> None:
        """The hook forgets the debt; it never touches the printer or its flags."""

        async def run() -> None:
            entry = _make_entry()
            _leave_stream_enabled(entry)
            camera_module._record_pending_video_release(
                entry.entry_id, "camera.chamber_camera"
            )
            hass = MagicMock()

            await self._hook()(hass, entry)

            assert camera_module._pending_video_releases == {}
            # The stream flag is the entry's own state and survives: only core's
            # unload path talks to the printer.
            assert entry._elegoo_video_state == {"enabled": True}
            hass.config_entries.async_get_entry.assert_not_called()
            hass.async_create_task.assert_not_called()
            hass.async_create_background_task.assert_not_called()

        _run(run())


class TestEntryWideViewerAccounting:
    """
    Cover whose viewers a release is allowed to see.

    The viewer counters are entity state, but the resource they gate is the
    config entry's: the printer reports a single connection count for the shared
    video stream, and the teardown retry that releases it deliberately outlives
    the entity that started it and re-binds to the replacement entity's client.
    Read through the entity it was built for, those counters have already been
    zeroed by teardown, so the retry would be comparing the printer's report
    against nothing — and a stream the replacement entity is actively serving
    looks like a stream nobody is watching.

    The lifecycle therefore counts the viewers of every live camera entity of the
    entry (see _video_viewers_by_entry), reading each entity's own counters so
    that no entity can reset another's, and forgets an entity the moment it is
    removed.
    """

    @staticmethod
    def _fast_retry(monkeypatch: pytest.MonkeyPatch, attempts: int = 500) -> None:
        """
        Make the retry fast, and keep it alive across a multi-phase test.

        The default budget is ~10 ticks; a phase test waits through several of
        them, so the attempts are raised to stop the loop from ending early.
        """
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_ATTEMPTS", attempts)

    @staticmethod
    async def _stop(cam) -> None:
        """Cancel what an entity scheduled so the test loop exits clean."""
        await _cancel(cam._cleanup_retry_task)
        await _cancel(cam._idle_watchdog_task)

    @staticmethod
    def _peers(entry_id: str) -> list:
        """Return the entities the entry still shares viewers with."""
        return list(camera_module._video_viewers_by_entry.get(entry_id, ()))

    def test_retry_renamed_to_the_new_entitys_viewer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retry must not cut off the replacement entity's own viewer."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            # Teardown happens while a foreign viewer holds the stream, which is
            # the only way a release reaches the retry at all.
            old_client, _ = _make_client(connected_streams=1, max_streams=2)
            _attach_client(entry, old_client)
            old = _live_fdm_camera(entry, hass)
            await old.async_added_to_hass()
            old._stream_enabled = True
            await old.async_will_remove_from_hass()
            assert await _wait_for(lambda: old._cleanup_retry_task is not None)

            # --- reload: a new entity, on a new client, that gets a viewer ---
            new_client, _ = _make_client(connected_streams=1, max_streams=2)
            _attach_client(entry, new_client)
            new = _live_fdm_camera(entry, hass)
            await new.async_added_to_hass()
            new._active_mjpeg_streams = 1
            try:
                # The retry has re-bound to the live client, so it is now acting
                # for the entry the new entity belongs to.
                assert await _wait_for(lambda: old._printer_client is new_client)
                await asyncio.sleep(0.05)
                new_client.set_printer_video_stream.assert_not_called()
                assert old._stream_enabled is True

                # The slicer leaves. The printer's periodic report has not
                # counted the new entity's own session yet, so it now reports
                # nothing while the new entity is streaming — the exact state a
                # retry may not read as "nobody is watching".
                new_client.printer_data.attributes.num_video_stream_connected = 0
                await asyncio.sleep(0.05)
                new_client.set_printer_video_stream.assert_not_called()
                assert old._stream_enabled is True
                assert new._stream_enabled is True

                # The new entity's viewer leaves too: the debt is finally paid.
                new._active_mjpeg_streams = 0
                assert await _wait_for(
                    lambda: new_client.set_printer_video_stream.await_count == 1
                )
                new_client.set_printer_video_stream.assert_awaited_once_with(
                    enable=False
                )
                assert old._stream_enabled is False
            finally:
                await self._stop(old)
                await self._stop(new)

        _run(run())

    def test_retry_releases_once_the_new_entity_has_no_viewers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The replacement's viewers block the release only while they exist."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            old_client, _ = _make_client(connected_streams=1, max_streams=2)
            _attach_client(entry, old_client)
            old = _live_fdm_camera(entry, hass)
            await old.async_added_to_hass()
            old._stream_enabled = True
            await old.async_will_remove_from_hass()

            new_client, _ = _make_client(connected_streams=0, max_streams=2)
            _attach_client(entry, new_client)
            new = _live_fdm_camera(entry, hass)
            await new.async_added_to_hass()
            try:
                # A native stream on the new entity is a viewer of ours: with the
                # printer reporting nothing, the guard would otherwise fire.
                new._native_stream_active = True
                assert new._has_active_viewers() is True
                assert old._own_video_viewer_count() == 1
                await asyncio.sleep(0.05)
                new_client.set_printer_video_stream.assert_not_called()

                # And so is a transient image grab.
                new._native_stream_active = False
                new._transient_viewers = 1
                await asyncio.sleep(0.05)
                new_client.set_printer_video_stream.assert_not_called()

                # Nothing is watching any more, so the release lands.
                new._transient_viewers = 0
                assert await _wait_for(
                    lambda: new_client.set_printer_video_stream.await_count == 1
                )
                assert old._stream_enabled is False
            finally:
                await self._stop(old)
                await self._stop(new)

        _run(run())

    def test_teardown_of_one_entity_keeps_another_entitys_viewers(self) -> None:
        """
        A sibling's teardown must not erase the viewers this entity serves.

        This is the stomping hazard: were the counters shared per entry and reset
        by any teardown, removing one camera would disarm the guard for the whole
        entry and the surviving camera's viewers would be cut off.
        """

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            watched_client, _ = _make_client(connected_streams=0, max_streams=2)
            _attach_client(entry, watched_client)
            watched = _live_fdm_camera(entry, hass)
            watched.entity_id = "camera.chamber_camera_watched"
            await watched.async_added_to_hass()
            watched._stream_enabled = True
            watched._active_mjpeg_streams = 1  # someone is watching this camera

            other_client, _ = _make_client(connected_streams=0, max_streams=2)
            _attach_client(entry, other_client)
            other = _live_fdm_camera(entry, hass)
            other.entity_id = "camera.chamber_camera_other"
            await other.async_added_to_hass()

            assert other._own_video_viewer_count() == 1
            assert other._has_active_viewers() is True

            await other.async_will_remove_from_hass()

            # The surviving entity still reports the viewer it is serving.
            assert watched._active_mjpeg_streams == 1
            assert watched._own_video_viewer_count() == 1
            assert watched._has_active_viewers() is True
            # The retired entity has dropped out of the entry's accounting.
            assert self._peers(entry.entry_id) == [watched]
            # And nothing it did on the way out disabled the watched stream.
            watched_client.set_printer_video_stream.assert_not_called()
            await watched._idle_watchdog_tick()
            watched_client.set_printer_video_stream.assert_not_called()

            # Once its own viewer is gone the entity releases the stream.
            watched._active_mjpeg_streams = 0
            await watched._idle_watchdog_tick()
            watched_client.set_printer_video_stream.assert_called_once_with(
                enable=False
            )
            await self._stop(watched)
            await self._stop(other)

        _run(run())

    def test_live_entity_still_sees_its_own_viewers(self) -> None:
        """Registering for the entry does not hide an entity from itself."""

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            client, _ = _make_client(connected_streams=0, max_streams=2)
            _attach_client(entry, client)
            cam = _live_fdm_camera(entry, hass)
            await cam.async_added_to_hass()
            try:
                assert self._peers(entry.entry_id) == [cam]
                cam._stream_enabled = True
                cam._active_mjpeg_streams = 2
                cam._transient_viewers = 1
                assert cam._has_active_viewers() is True
                assert cam._own_video_viewer_count() == 3
                await cam._idle_watchdog_tick()
                client.set_printer_video_stream.assert_not_called()
                assert cam._stream_enabled is True
            finally:
                await self._stop(cam)

        _run(run())

    def test_detached_entity_still_counts_its_own_viewers(self) -> None:
        """A subject with no entry has no peers, so it counts only itself."""
        client, _ = _make_client(connected_streams=1, max_streams=5)
        subject = _VideoLifecycleSubject(client)
        subject._transient_viewers = 1
        assert subject._has_active_viewers() is True
        assert subject._own_video_viewer_count() == 1
        # The boundary is unchanged: only connections beyond ours are external.
        assert subject._has_external_video_viewers() is False
        client.printer_data.attributes.num_video_stream_connected = 2
        assert subject._has_external_video_viewers() is True
        assert self._peers("entry-1") == []

    def test_teardown_unregisters_the_entity_and_prunes_the_entry(self) -> None:
        """The last entity out of an entry leaves no registry group behind."""

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            client, _ = _make_client()
            _attach_client(entry, client)
            cam = _live_fdm_camera(entry, hass)
            await cam.async_added_to_hass()
            assert self._peers(entry.entry_id) == [cam]

            await cam.async_will_remove_from_hass()

            assert camera_module._video_viewers_by_entry == {}
            await self._stop(cam)

        _run(run())

    def test_registry_holds_no_strong_reference_to_an_entity(self) -> None:
        """Being tracked must never keep a released entity alive."""

        async def run() -> None:
            entry = _make_entry()
            client, _ = _make_client()
            subject = _lifecycle_subject(client, entry=entry)
            subject._register_video_viewers()
            reference = weakref.ref(subject)
            assert self._peers(entry.entry_id) == [subject]

            del subject
            gc.collect()

            assert reference() is None
            # The group is empty, and the next entity of the entry that asks
            # drops the leftover key instead of carrying it: an entity can only
            # be untracked without reaching its teardown hook if core never
            # removed it properly, so the read path prunes for itself.
            client_b, _ = _make_client()
            peer = _lifecycle_subject(client_b, entry=entry)
            assert peer._entry_video_viewer_counts() == (0, 0, 0)
            assert camera_module._video_viewers_by_entry == {}

        _run(run())


class TestExhaustedRetryParksTheRelease:
    """
    Cover the retry running out of attempts while a client stayed connected.

    The disconnected-client path has always parked a release a retry could not
    pay. The connected one never did: the user disables the camera entity while a
    foreign viewer still holds the stream, so ``self._printer_client`` stays up,
    every tick takes the "someone else is watching, keep waiting" branch, and the
    attempts simply run out. The ``finally`` only cleared the task slot, so the
    stream was left enabled with no retry, no watchdog and no parked debt — the
    printer stayed enabled until it was rebooted. Every exit that leaves the
    release unpaid has to hand it on, because the alternative is a silent,
    permanent leak; only a removed entry is terminal, and it parks nothing.
    """

    @staticmethod
    def _fast_retry(monkeypatch: pytest.MonkeyPatch, attempts: int = 3) -> None:
        """Shorten the retry window so exhaustion is reachable in milliseconds."""
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(camera_module, "CLEANUP_RETRY_ATTEMPTS", attempts)

    @staticmethod
    async def _stop(cam) -> None:
        """Cancel what an entity scheduled so the test loop exits clean."""
        await _cancel(cam._cleanup_retry_task)
        await _cancel(cam._idle_watchdog_task)

    @staticmethod
    def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
        """Collect the warning messages logged for the test entity."""
        return [
            record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
            and "camera.test" in record.getMessage()
        ]

    def test_connected_viewer_holding_the_stream_parks_the_release(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Attempts exhausted through a live client: the debt is handed on."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            # The client never disconnects and the viewer never leaves, so the
            # release is blocked for the whole retry window — exactly the state
            # the existing exhaustion test, which drives a disconnected client,
            # could not catch.
            client, _ = _make_client(connected_streams=1, max_streams=2)
            _attach_client(entry, client)
            subject = _lifecycle_subject(client, entry=entry, hass=hass)
            subject._stream_enabled = True

            await subject._async_cleanup_retry()

            client.set_printer_video_stream.assert_not_called()
            assert subject._stream_enabled is True
            assert camera_module._pending_video_releases == {
                entry.entry_id: "camera.test"
            }
            assert subject._cleanup_retry_task is None
            # Giving up quietly is correct here: the debt was handed on, so only
            # the terminal removed-entry path is allowed to warn about it.
            assert not self._warnings(caplog)

        _run(run())

    def test_exhaustion_after_teardown_parks_through_the_real_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reported shape: entity removed, viewer holding on, retries spent."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            client, _ = _make_client(connected_streams=1, max_streams=2)
            _attach_client(entry, client)
            cam = _live_fdm_camera(entry, hass)
            await cam.async_added_to_hass()
            cam._stream_enabled = True

            # The user disables the camera entity while the slicer is watching.
            await cam.async_will_remove_from_hass()
            retry = cam._cleanup_retry_task
            assert retry is not None
            await retry

            client.set_printer_video_stream.assert_not_called()
            assert cam._stream_enabled is True
            assert camera_module._pending_video_releases == {
                entry.entry_id: "camera.chamber_camera"
            }
            await self._stop(cam)

        _run(run())

    def test_parked_release_from_exhaustion_is_adopted_and_paid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The next entity of the entry finishes what the exhausted retry left."""
        self._fast_retry(monkeypatch, attempts=500)

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            blocked_client, _ = _make_client(connected_streams=1, max_streams=2)
            _attach_client(entry, blocked_client)
            subject = _lifecycle_subject(blocked_client, entry=entry, hass=hass)
            subject._stream_enabled = True
            await subject._async_cleanup_retry()
            assert camera_module._pending_video_releases == {
                entry.entry_id: "camera.test"
            }
            # The parking retry is gone; only a new entity can pay now.
            assert subject._cleanup_retry_task is None

            # A fresh camera entity for the same entry adopts the debt. The
            # viewer is still watching, so it waits instead of cutting them off.
            new_client, _ = _make_client(connected_streams=1, max_streams=2)
            _attach_client(entry, new_client)
            cam = _live_fdm_camera(entry, hass)
            await cam.async_added_to_hass()
            try:
                assert await _wait_for(lambda: cam._cleanup_retry_task is not None)
                await asyncio.sleep(0.05)
                new_client.set_printer_video_stream.assert_not_called()
                assert cam._stream_enabled is True
                # The adopter keeps chasing the debt rather than re-parking it.
                assert camera_module._pending_video_release_entity(entry.entry_id)

                # The foreign viewer finally leaves: the release lands.
                new_client.printer_data.attributes.num_video_stream_connected = 0
                assert await _wait_for(
                    lambda: new_client.set_printer_video_stream.await_count == 1
                )
                new_client.set_printer_video_stream.assert_awaited_once_with(
                    enable=False
                )
                assert cam._stream_enabled is False
                assert camera_module._pending_video_releases == {}
            finally:
                await self._stop(cam)

        _run(run())

    def test_release_that_landed_parks_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retry that starts on an already-disabled stream owes nothing."""
        self._fast_retry(monkeypatch)

        async def run() -> None:
            entry = _make_entry()
            client, _ = _make_client()
            subject = _lifecycle_subject(client, entry=entry)
            subject._stream_enabled = False

            await subject._async_cleanup_retry()

            assert camera_module._pending_video_releases == {}
            client.set_printer_video_stream.assert_not_called()

        _run(run())

    def test_removed_entry_gives_up_and_parks_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """
        Removal stays terminal even when the client outlives the entry.

        The client here is still connected, so only the entry lookup can end the
        retry: parking a debt for an entry that is gone would hand it to an
        entity that can never exist.
        """
        self._fast_retry(monkeypatch)

        async def run() -> None:
            entry = _make_entry()
            client, _ = _make_client(connected_streams=1, max_streams=2)
            _attach_client(entry, client)
            hass = _make_hass(None)  # core has already dropped the entry
            subject = _lifecycle_subject(client, entry=entry, hass=hass)
            subject._stream_enabled = True

            await subject._async_cleanup_retry()

            assert camera_module._pending_video_releases == {}
            assert subject._stream_enabled is True
            client.set_printer_video_stream.assert_not_called()
            warnings = self._warnings(caplog)
            assert len(warnings) == 1
            assert "reboot" in warnings[0]
            assert "reload" in warnings[0]

        _run(run())

    def test_parking_leaves_the_entry_flag_the_adopter_reads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        The parked debt and the shared enabled flag travel together.

        Parking is only useful if the adopting entity can tell the stream is
        still on; the flag is per entry, so a retry parking through a dead
        entity still leaves it set for the entity that adopts.
        """
        self._fast_retry(monkeypatch)

        async def run() -> None:
            entry = _make_entry()
            hass = _make_hass(entry)
            client, _ = _make_client(connected_streams=1, max_streams=2)
            _attach_client(entry, client)
            subject = _lifecycle_subject(client, entry=entry, hass=hass)
            subject._stream_enabled = True
            # Driven to exhaustion in-process: every tick sees the foreign
            # viewer still watching, so the loop spends its shortened budget and
            # parks on the way out. Awaiting the retry directly (rather than
            # letting async_added_to_hass arm it as a background task) keeps the
            # test loop free of a task that would outlive it.
            await subject._async_cleanup_retry()

            assert camera_module._pending_video_release_entity(entry.entry_id)
            assert entry._elegoo_video_state == {"enabled": True}
            _attach_client(entry, _make_client(connected_streams=0, max_streams=2)[0])
            cam = _live_fdm_camera(entry, hass)
            assert cam._stream_enabled is True  # what makes adoption possible

        _run(run())
