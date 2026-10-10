"""Camera platform for Elegoo printer."""

import asyncio
import contextlib
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
    CC2_VIDEO_PATH,
    CC2_VIDEO_PORT,
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
# The printer reports its video stream connections periodically (about every
# two seconds on both transports). Before releasing the shared video stream,
# wait one reporting cycle and re-check, so a client that connected during our
# own grab is reflected in the count and does not get cut off.
STREAM_DISABLE_SETTLE_SECONDS = 3.0
# When the entity is torn down while another client still holds the shared
# video stream, a bounded background task keeps retrying the release so the
# stream is not left enabled forever once that client leaves.
CLEANUP_RETRY_INTERVAL_SECONDS = 60.0
CLEANUP_RETRY_ATTEMPTS = 10  # roughly 10 minutes

# Config entries whose shared printer video still owes a release, mapped to the
# entity id that left it enabled.
#
# Unloading the integration tears the camera entity down *before* the printer
# client is disconnected, so the only release that ever reaches the teardown
# retry is one a foreign viewer is blocking. Once that retry loses its live
# connection there is nothing left for the remaining attempts to act through, so
# instead of spending them it parks the unpaid release here; the next camera
# entity built for the same config entry adopts it and finishes the job through
# its own connected client.
#
# It is module level because nothing else spans the gap between the unload that
# records the debt and the setup that adopts it: the recording entity is gone and
# the entry keeps no runtime data in between. It deliberately does NOT use
# ``entry.async_on_unload()`` — core drains that callback list right after
# ``async_unload_entry()`` returns, which is the very unload that records the
# debt, so it would wipe the handover before any reload could use it.
#
# Nothing else has to reset it: the retry clears the entry once the stream is off
# or once it gives up on a removed entry (removal is terminal and parks nothing),
# the adopter clears it when the release lands, and an entry id is never reused,
# so at most one short string is held per printer that is between connections.
_pending_video_releases: dict[str, str] = {}


def _record_pending_video_release(entry_id: str, entity_id: str) -> None:
    """
    Park an unpaid video release for the next entity of this entry.

    Arguments:
        entry_id: Config entry whose printer video is still enabled.
        entity_id: Entity that left the stream enabled, for the log line.

    """
    _pending_video_releases[entry_id] = entity_id


def _pending_video_release_entity(entry_id: str) -> str | None:
    """Return the entity that owes a release for this entry, if any."""
    return _pending_video_releases.get(entry_id)


def _clear_pending_video_release(entry_id: str) -> None:
    """Forget a pending release once it has been paid or given up on."""
    _pending_video_releases.pop(entry_id, None)


class ElegooCameraMjpeg(CameraMjpeg):
    """
    CameraMjpeg with graceful shutdown: quit -> SIGTERM -> SIGKILL.

    ffmpeg's RTSP demuxer sends RTSP TEARDOWN on SIGTERM, which tells the
    printer to decrement its session counter. SIGKILL bypasses this entirely.
    """

    async def close(self, shutdown_timeout: int = FFMPEG_QUIT_TIMEOUT) -> None:
        """
        Stop ffmpeg with graceful shutdown sequence.

        Arguments:
            shutdown_timeout: Seconds to wait after sending 'q' before SIGTERM.

        """
        if not self.is_running:
            self._clear()
            return

        # Step 1: Send 'q' to ffmpeg stdin (ffmpeg's interactive quit)
        quit_timed_out = False
        try:
            self._proc.stdin.write(b"q")
            async with asyncio.timeout(shutdown_timeout):
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


class ElegooVideoStreamLifecycle(ElegooPrinterEntity):
    """
    Ref-counted lifecycle for the printer's video stream.

    Printers advertise a fixed number of concurrent video stream
    connections (num_video_stream_connected vs. max_video_stream_allowed)
    and once the stream is enabled it stays active on the printer side
    until explicitly disabled. Leaving it enabled with no viewers occupies
    a slot (which can block other consumers and requires a printer reboot
    to release), so this mixin:

    - Enables the video when the first viewer (MJPEG stream, transient
      image grab, or native stream) appears
    - Disables it when the last viewer disconnects, but only once the
      printer reports no other (untracked) stream connection — a foreign
      viewer such as the Elegoo Slicer must not be cut off
    - An idle watchdog re-attempts failed disables and clears stale
      native stream flags
    - Disables on entity removal to clean up residual state
    - Hands a release that a foreign viewer still blocks to the next camera
      entity of the same config entry (see _pending_video_releases)

    The mixin does not own entity state. Camera classes call
    ``_init_video_lifecycle(client)`` inside their own ``__init__`` once
    ``self._printer_client`` is available.
    """

    # Opt-in: while set, the shared printer video is not disabled while the
    # printer reports connections this entity does not own (see
    # _has_external_video_viewers). Left off for the resin stream camera, whose
    # still-image path can leave one of its own RTSP sessions behind — that
    # session would otherwise be mistaken for an external viewer and never
    # released.
    _keep_stream_for_external_viewers = False

    def _init_video_lifecycle(self, client: "ElegooPrinterClient") -> None:
        """Initialize stream lifecycle state on this camera entity."""
        self._printer_client = client
        self._active_mjpeg_streams = 0
        self._transient_viewers = 0
        self._native_stream_active = False
        self._local_stream_state: dict[str, bool] = {"enabled": False}
        # Per-entity: the stream URL/video info has been fetched by this
        # entity. The shared enabled flag can be inherited from a previous
        # entity (reload), but a fresh entity must still refresh its own
        # URL once instead of trusting its constructor fallback.
        self._stream_ready = False
        self._last_activity = 0.0
        self._idle_watchdog_task = None
        self._cleanup_retry_task = None

    def _is_over_capacity(self) -> bool:
        """Check if the printer is over capacity."""
        attrs = self._printer_client.printer_data.attributes
        num_connected = getattr(attrs, "num_video_stream_connected", 0) or 0
        max_allowed = getattr(attrs, "max_video_stream_allowed", 0) or 0
        return num_connected >= max_allowed

    def _has_active_viewers(self) -> bool:
        """Check if any viewer type is currently active."""
        return (
            self._active_mjpeg_streams > 0
            or self._transient_viewers > 0
            or self._native_stream_active
        )

    def _own_video_viewer_count(self) -> int:
        """Count the video viewers this entity is currently tracking."""
        return (
            self._active_mjpeg_streams
            + self._transient_viewers
            + (1 if self._native_stream_active else 0)
        )

    def _has_external_video_viewers(self) -> bool:
        """
        Return True when the printer reports a stream we do not own.

        The printer's video stream is a single on/off resource shared by
        every client, but this entity only tracks the connections it opens
        itself. Other consumers hold their own connection that the
        integration never sees (for example the Elegoo Slicer viewing the
        chamber camera directly), so disabling the stream while they are
        connected would kill their feed. Only disable when every connection
        the printer reports belongs to us.
        """
        attrs = self._printer_client.printer_data.attributes
        try:
            connected = int(getattr(attrs, "num_video_stream_connected", 0) or 0)
        except (TypeError, ValueError):
            return False
        return connected > self._own_video_viewer_count()

    def _config_entry(self) -> "ElegooPrinterConfigEntry | None":
        """
        Return this entity's config entry, when it still has one.

        Read through the coordinator rather than cached, so an entity whose
        entry has been removed reports the entry it belonged to instead of a
        stale runtime-data reference. Returns None for an entity that was
        never attached to an entry (bare lifecycle subjects).
        """
        return getattr(getattr(self, "coordinator", None), "config_entry", None)

    def _stream_state(self) -> dict[str, bool]:
        """
        Return the shared printer-video state for this config entry.

        The printer video is a single resource shared with every client, so
        the enabled flag is shared too: a teardown retry can outlive the
        entity it was created for and act through the replacement camera's
        client, and both must agree on whether the stream is on. The state
        lives on the config entry, which survives a reload.
        """
        entry = self._config_entry()
        if entry is None:
            return self._local_stream_state
        state = getattr(entry, "_elegoo_video_state", None)
        if state is None:
            state = {"enabled": False}
            setattr(entry, "_elegoo_video_state", state)  # noqa: B010
        return state

    @property
    def _stream_enabled(self) -> bool:
        """Whether the shared printer video is currently enabled."""
        return self._stream_state()["enabled"]

    @_stream_enabled.setter
    def _stream_enabled(self, value: bool) -> None:
        self._stream_state()["enabled"] = value

    async def _ensure_stream_enabled(self) -> None:
        """
        Enable printer video if not already enabled.

        Idempotent — safe to call when already enabled.
        On failure, _stream_enabled is NOT set (may retry later).
        """
        if self._stream_enabled and self._stream_ready:
            return
        if not self._printer_client.is_connected:
            LOGGER.debug(
                "Printer client not connected, deferring video enable for %s",
                self.entity_id,
            )
            return
        try:
            video = await self._printer_client.get_printer_video(enable=True)
        except Exception as e:  # noqa: BLE001
            LOGGER.warning(
                "Exception enabling printer video for %s: %s",
                self.entity_id,
                e,
            )
            return
        if video.status == ElegooVideoStatus.SUCCESS:
            self._stream_enabled = True
            self._stream_ready = True
            LOGGER.debug("Enabled printer video for %s", self.entity_id)
        else:
            LOGGER.warning(
                "Failed to enable printer video for %s: %s",
                self.entity_id,
                video.status,
            )

    async def _disable_stream(self) -> None:
        """
        Disable printer video.

        Skipped while the printer reports a connection we do not own
        (another client is watching) or while a viewer of our own is
        active. The connection count is only reported periodically and
        briefly keeps counting our own just-finished grab, so always wait
        one reporting cycle before trusting it — after that a lingering
        count is a real external viewer, and a viewer of ours that started
        during the wait is caught by the active-viewer re-check. On
        failure, _stream_enabled stays True (video may still be on printer)
        and the idle watchdog re-attempts.
        """
        if not self._stream_enabled:
            return
        if self._keep_stream_for_external_viewers:
            if STREAM_DISABLE_SETTLE_SECONDS > 0:
                await asyncio.sleep(STREAM_DISABLE_SETTLE_SECONDS)
            if self._has_active_viewers():
                # One of our own viewers started during the wait.
                return
            if self._has_external_video_viewers():
                self._log_external_viewer_skip()
                return
        try:
            await self._printer_client.set_printer_video_stream(enable=False)
        except Exception as e:  # noqa: BLE001
            LOGGER.warning(
                "Failed to disable printer video for %s (may be over capacity): %s",
                self.entity_id,
                e,
            )
            # Don't clear flag — video may still be enabled on printer
            return
        self._stream_enabled = False
        LOGGER.debug("Disabled printer video for %s", self.entity_id)

    def _log_external_viewer_skip(self) -> None:
        """Log that the disable was skipped for another client's benefit."""
        LOGGER.debug(
            "Not disabling printer video for %s: other viewer(s) connected",
            self.entity_id,
        )

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
                "stream_source: Using cached stream URL: %s",
                video_url,
            )
            return video_url
        return None

    async def _idle_watchdog_tick(self) -> None:
        """
        Run a single watchdog pass.

        1. If no viewers are active and video is enabled, attempt to
           disable it (handles failed disables from the normal
           disconnect path).
        2. If the native stream has been idle for
           NATIVE_STREAM_IDLE_TIMEOUT, clear the native-stream flag
           (allows a future disable attempt).
        """
        if self._stream_enabled and not self._has_active_viewers():
            await self._disable_stream()
        if (
            self._native_stream_active
            and self._last_activity > 0
            and asyncio.get_running_loop().time() - self._last_activity
            > NATIVE_STREAM_IDLE_TIMEOUT
        ):
            LOGGER.debug(
                "Native stream idle for %.0fs, clearing flag for %s",
                NATIVE_STREAM_IDLE_TIMEOUT,
                self.entity_id,
            )
            self._native_stream_active = False

    async def _idle_watchdog(self) -> None:
        """
        Periodically check for idle conditions and clean up.

        Runs every IDLE_WATCHDOG_INTERVAL seconds. See the tick for the
        two responsibilities (disabled-when-idle, stale native flag).
        """
        while True:
            try:
                await asyncio.sleep(IDLE_WATCHDOG_INTERVAL)
                await self._idle_watchdog_tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                LOGGER.exception("Idle watchdog error for %s", self.entity_id)

    async def _cleanup_video_lifecycle(self) -> None:
        """Cancel the idle watchdog and release the video stream state."""
        if self._idle_watchdog_task is not None:
            self._idle_watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_watchdog_task
            self._idle_watchdog_task = None
        # A retry started before this teardown would keep acting through an
        # entity whose viewer counters and client are about to be reset, so it
        # is dropped here. The retry scheduled below (if the stream is still
        # held) is the only one allowed to outlive the entity.
        self._cancel_pending_cleanup_retry()
        self._active_mjpeg_streams = 0
        self._transient_viewers = 0
        self._native_stream_active = False
        await self._disable_stream()
        if self._stream_enabled:
            # Another client is still watching: do not cut their feed, but
            # keep retrying from a task that outlives this entity so the
            # stream is not left enabled forever once they leave.
            self._schedule_cleanup_retry()

    def _schedule_cleanup_retry(self) -> None:
        """Start the shared-stream release retry if one is not running."""
        if self._cleanup_retry_task is not None:
            return
        # async_create_task() only keeps a weak reference to the task, so this
        # deliberately long-lived (~10 minute) fire-and-forget retry can be
        # garbage collected mid-flight and warn at shutdown. Background tasks
        # are held by hass for their whole lifetime.
        self._cleanup_retry_task = self.hass.async_create_background_task(
            self._async_cleanup_retry(),
            f"elegoo_video_cleanup_{self.entity_id}",
        )

    def _cancel_pending_cleanup_retry(self) -> None:
        """Drop a retry task this entity started earlier."""
        task = self._cleanup_retry_task
        self._cleanup_retry_task = None
        if task is not None and not task.done():
            task.cancel()

    def _config_entry_id(self) -> str | None:
        """Return this entity's config entry id, or None when detached."""
        entry_id = getattr(self._config_entry(), "entry_id", None)
        return entry_id if isinstance(entry_id, str) else None

    def _config_entry_removed(self) -> bool:
        """
        Report whether nothing can hand this release a live client again.

        Core drops the entry from ``hass.config_entries`` on removal (before
        calling the removal hook), while a plain reload keeps it registered and
        only clears its runtime data — so a missing entry is terminal, and a
        present one means a reload may still bring a fresh connected client.
        An entity with no resolvable entry has nothing to wait for either.
        """
        entry_id = self._config_entry_id()
        if entry_id is None:
            return True
        entries = getattr(getattr(self, "hass", None), "config_entries", None)
        get_entry = getattr(entries, "async_get_entry", None)
        if not callable(get_entry):
            return True
        return get_entry(entry_id) is None

    def _park_pending_video_release(self) -> None:
        """Hand this unpaid release to the next entity of the config entry."""
        entry_id = self._config_entry_id()
        if entry_id is not None:
            _record_pending_video_release(entry_id, self.entity_id)

    def _clear_pending_video_release(self) -> None:
        """Forget the entry's pending release once it no longer owes one."""
        entry_id = self._config_entry_id()
        if entry_id is not None:
            _clear_pending_video_release(entry_id)

    def _abandon_video_release(self) -> None:
        """Stop chasing a release no live connection can ever pay."""
        self._clear_pending_video_release()
        LOGGER.warning(
            "Giving up releasing the printer video stream for %s: the config "
            "entry has been removed and there is no live printer connection "
            "left to disable it with. The printer video may stay enabled until "
            "the printer is rebooted or the integration is reloaded.",
            self.entity_id,
        )

    def _adopt_pending_video_release(self) -> None:
        """
        Take over a release a torn-down entity could not pay.

        A previous camera of this config entry left the shared video enabled
        because a foreign viewer held it, and its retry ran out of live
        connections when the integration unloaded the client. This entity was
        just built with a freshly connected client, so it finishes the job —
        through the same retry loop and therefore the exact same external /
        active-viewer guard: while another client is still watching the stream
        stays on and nothing is force-disabled.
        """
        entry_id = self._config_entry_id()
        if entry_id is None or _pending_video_release_entity(entry_id) is None:
            return
        self._schedule_cleanup_retry()

    def _current_printer_client(self) -> "ElegooPrinterClient | None":
        """
        Return a live printer client, re-resolved from the config entry.

        A teardown retry can outlive the client the entity was built with
        (unload disconnects it), so look up the current one each time
        instead of caching it; fall back to the original client when the
        entry is unavailable. Returns None when nothing is connected.
        """
        entry = self._config_entry()
        runtime = getattr(entry, "runtime_data", None)
        client = getattr(getattr(runtime, "api", None), "client", None)
        if client is None:
            client = self._printer_client
        if not getattr(client, "is_connected", False):
            return None
        return client

    async def _async_cleanup_retry(self) -> None:
        """
        Release the shared stream once other viewers leave.

        The release can outlive both this entity and the client it was built
        with, so a tick with no live connection decides instead of spinning:
        while the config entry is still registered the release is parked for
        the next camera entity of that entry (a reload hands it a connected
        client) and the bounded wait continues; once the entry has been removed
        there is nothing left to act through, so the retry gives up at once
        with a single warning instead of burning every remaining attempt.
        """
        try:
            for _ in range(CLEANUP_RETRY_ATTEMPTS):
                await asyncio.sleep(CLEANUP_RETRY_INTERVAL_SECONDS)
                if not self._stream_enabled:
                    self._clear_pending_video_release()
                    return
                client = self._current_printer_client()
                if client is None:
                    if self._config_entry_removed():
                        self._abandon_video_release()
                        return
                    # Unload disconnected the client; a reload will not hand it
                    # back, so leave the release for the next entity instead of
                    # retrying against a dead connection.
                    self._park_pending_video_release()
                    continue
                self._printer_client = client
                if self._has_external_video_viewers():
                    continue
                await self._disable_stream()
                if not self._stream_enabled:
                    self._clear_pending_video_release()
                    return
        finally:
            task = asyncio.current_task()
            # Only clear the slot when this task still owns it: teardown can
            # cancel us and immediately schedule the next retry, and a retiring
            # task must not drop the task its replacement just stored.
            if self._cleanup_retry_task is task:
                self._cleanup_retry_task = None

    async def async_added_to_hass(self) -> None:
        """
        Start the idle watchdog and inherit any unpaid video release.

        The watchdog is entity-local; the inherited release is the config
        entry's business, so it runs through the shared retry loop.
        """
        await super().async_added_to_hass()
        self._idle_watchdog_task = asyncio.create_task(self._idle_watchdog())
        self._adopt_pending_video_release()

    async def async_will_remove_from_hass(self) -> None:
        """
        Clean up when the entity is removed from Home Assistant.

        Cancels the idle watchdog, resets stream state and disables the
        printer video.
        """
        await super().async_will_remove_from_hass()
        await self._cleanup_video_lifecycle()


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


class ElegooStreamCamera(ElegooVideoStreamLifecycle, Camera):
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
        self._active_mjpeg_processes: set[ElegooCameraMjpeg] = set()
        self._init_video_lifecycle(self._printer_client)

    @cached_property
    def supported_features(self) -> CameraEntityFeature:
        """Return supported features."""
        return self._attr_supported_features

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse:
        """
        Generate an HTTP MJPEG stream from the camera.

        Ref-counted: enables video on first viewer, disables on last.
        Uses ElegooCameraMjpeg for graceful SIGTERM shutdown.
        """
        mjpeg_stream: ElegooCameraMjpeg | None = None

        # Enable stream if first viewer
        if not self._has_active_viewers():
            await self._ensure_stream_enabled()

        try:
            stream_url = await self._get_stream_url()
            if not stream_url:
                return web.Response(
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                    reason="Stream URL not available",
                )

            ffmpeg_manager = self.hass.data[DOMAIN]
            mjpeg_stream = ElegooCameraMjpeg(ffmpeg_manager.binary)
            await mjpeg_stream.open_camera(
                stream_url, extra_cmd=self._extra_ffmpeg_arguments
            )

            self._active_mjpeg_streams += 1
            self._active_mjpeg_processes.add(mjpeg_stream)
            self._last_activity = asyncio.get_running_loop().time()

            stream_reader = await mjpeg_stream.get_reader()
            return await async_aiohttp_proxy_stream(
                self.hass,
                request,
                stream_reader,
                ffmpeg_manager.ffmpeg_stream_content_type,
            )
        finally:
            if mjpeg_stream is not None:
                self._active_mjpeg_streams = max(0, self._active_mjpeg_streams - 1)
                self._active_mjpeg_processes.discard(mjpeg_stream)
                await mjpeg_stream.close(shutdown_timeout=FFMPEG_QUIT_TIMEOUT)
            # Disable stream if last viewer
            if not self._has_active_viewers():
                await self._disable_stream()

    async def stream_source(self) -> str | None:
        """
        Return the source of the stream.

        Enables video for native HA streaming. Uses idle watchdog to
        disable after NATIVE_STREAM_IDLE_TIMEOUT of no activity.
        """
        if not self._native_stream_active:
            await self._ensure_stream_enabled()
            # Only set flag if video was actually enabled
            if not self._stream_enabled:
                return None
            self._native_stream_active = True

        stream_url = await self._get_stream_url()
        if not stream_url:
            return None

        self._last_activity = asyncio.get_running_loop().time()
        return stream_url

    async def async_camera_image(
        self,
        width: int | None = None,  # noqa: ARG002
        height: int | None = None,  # noqa: ARG002
    ) -> bytes | None:
        """
        Return a still image from the camera.

        Treats the image grab as a transient viewer — enables video if
        needed, but only disables if no other viewers are active.

        Note: This path uses HA's async_get_image() which spawns its own
        ffmpeg process. That process does NOT get graceful SIGTERM shutdown,
        so individual image grabs may leak RTSP sessions. The _transient_viewers
        counter prevents this path from disabling an active MJPEG stream.
        """
        # Enable stream if no other viewers are active (check before increment)
        if not self._has_active_viewers():
            await self._ensure_stream_enabled()
        self._transient_viewers += 1

        try:
            stream_url = await self._get_stream_url()
            if not stream_url:
                return None
            return await async_get_image(
                self.hass,
                input_source=stream_url,
            )
        except Exception as e:  # noqa: BLE001
            LOGGER.error(
                "Failed to get camera image via ffmpeg (ffmpeg may be missing): %s", e
            )
            return None
        finally:
            self._transient_viewers = max(0, self._transient_viewers - 1)
            # Only disable if no other viewers are active
            if not self._has_active_viewers():
                await self._disable_stream()

    async def async_will_remove_from_hass(self) -> None:
        """
        Clean up when the entity is removed from Home Assistant.

        Closes any in-flight MJPEG processes (camera-specific state),
        then delegates to the lifecycle for watchdog cancellation and
        stream disabling.
        """
        for proc in self._active_mjpeg_processes.copy():
            await proc.close(shutdown_timeout=FFMPEG_QUIT_TIMEOUT)
        self._active_mjpeg_processes.clear()
        await super().async_will_remove_from_hass()


class ElegooMjpegCamera(ElegooVideoStreamLifecycle, MjpegCamera):
    """Representation of an MjpegCamera."""

    # The FDM chamber camera shares its MJPEG stream with the printer's own
    # clients (e.g. the Elegoo Slicer), so keep it enabled while they watch.
    _keep_stream_for_external_viewers = True

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
        elif printer.proxy_host:
            # Through a forward proxy the CC2 stream is only reachable on the
            # proxy's 8080 pass-through, so the CC1 proxy's 3031/`video` URL
            # could never be served. This mirrors the URL the CC2 client builds
            # once connected; it is a fallback either way, since
            # _update_stream_url refreshes it before the URL is ever read.
            mjpeg_url = f"http://{printer.proxy_host}:{CC2_VIDEO_PORT}{CC2_VIDEO_PATH}"
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
        self._init_video_lifecycle(self._printer_client)

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
        """
        Update the MJPEG stream URL and manage video state.

        Ref-counted like the rest of the lifecycle: the update is
        re-requested only when the video is not enabled, or when the
        video is enabled but the URL is mismatched (retries are safe
        because an already-enabled stream tolerates a subsequent enable).
        Over-capacity/disconnected printers are left untouched.
        """
        if self._stream_enabled and self._mjpeg_url and self._stream_ready:
            # URL already refreshed for this entity
            return
        if (not self._printer_client.is_connected) or self._is_over_capacity():
            self._mjpeg_url = None
            return
        video = await self._printer_client.get_printer_video(enable=True)
        if video.status == ElegooVideoStatus.SUCCESS:
            self._stream_enabled = True
            self._stream_ready = True
            video_url = self._normalize_video_url(video.video_url)
            self._mjpeg_url = video_url
            if not video_url:
                LOGGER.debug("stream_source: Empty or invalid video URL from printer")
            else:
                LOGGER.debug("stream_source: Using video url: %s", video_url)
        else:
            LOGGER.debug("stream_source: Failed to get video stream: %s", video.status)
            self._stream_enabled = False
            self._stream_ready = False
            self._mjpeg_url = None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """
        Return a still image from the printer camera.

        Treats the image grab as a transient viewer — ref-counts the
        video stream per ElegooVideoStreamLifecycle: enables the stream
        on the first viewer and disables it when the last viewer
        disconnects. The base MjpegCamera image path reads a single
        frame from a short-lived HTTP connection, which closes when the
        grab completes, so no stream connection is left open afterwards.
        """
        # Enable stream if no other viewers are active (check before increment)
        if not self._has_active_viewers():
            await self._update_stream_url()
        self._transient_viewers += 1
        try:
            if (not self._mjpeg_url) or self._is_over_capacity():
                return None
            return await super().async_camera_image(width=width, height=height)
        finally:
            self._transient_viewers = max(0, self._transient_viewers - 1)
            # Only disable if no other viewers are active
            if not self._has_active_viewers():
                await self._disable_stream()

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse:
        """
        Generate an HTTP MJPEG stream from the camera.

        Ref-counted: enables video on first viewer, disables on last.
        """
        # Enable stream if first viewer
        if not self._has_active_viewers():
            await self._update_stream_url()
        self._active_mjpeg_streams += 1
        self._last_activity = asyncio.get_running_loop().time()
        try:
            if not self._mjpeg_url or self._is_over_capacity():
                return web.Response(
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                    reason="Stream URL not available",
                )
            return await super().handle_async_mjpeg_stream(request)
        finally:
            # Disable stream if last viewer
            self._last_activity = asyncio.get_running_loop().time()
            self._active_mjpeg_streams = max(0, self._active_mjpeg_streams - 1)
            if not self._has_active_viewers():
                await self._disable_stream()

    async def stream_source(self) -> str | None:
        """
        Return the MJPEG stream source.

        Enables video for native streams (which uses the MJPEG source
        with FFmpeg), tracks the stream, and disables it after
        NATIVE_STREAM_IDLE_TIMEOUT of idle via the idle watchdog.
        """
        if not self._native_stream_active:
            await self._update_stream_url()
            if not self._mjpeg_url:
                return None
            self._native_stream_active = True
            self._last_activity = asyncio.get_running_loop().time()
        return self._mjpeg_url
