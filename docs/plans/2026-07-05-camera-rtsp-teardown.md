# Camera RTSP Teardown Plan

**Goal:** Prevent RTSP session leaks on resin printers by ensuring graceful ffmpeg shutdown and reference-counted video stream lifecycle.

**Architecture:** Add a custom `ElegooCameraMjpeg` subclass with SIGTERM-before-SIGKILL shutdown, ref-counted enable/disable of the printer's video endpoint, and an idle watchdog for native streams. All changes are in `camera.py`.

**Tech Stack:** Python, Home Assistant Camera API, haffmpeg, asyncio

---

### Known Limitations

- **`async_camera_image()` (still-image grabs):** Uses HA's `async_get_image()` which spawns its own `ImageFrame` with `close(timeout=0)` — this SIGKILLs ffmpeg and may leak sessions. The `_transient_viewers` counter prevents the image path from disabling an active MJPEG stream, but the image path itself does not get graceful shutdown. A future follow-up can add a custom `ElegooImageFrame` subclass if this proves problematic in practice.
- **FDM printers (`ElegooMjpegCamera`):** Unaffected — they use HTTP MJPEG with no RTSP session counter or TEARDOWN semantics.

---

### Task 1: Add `ElegooCameraMjpeg` class with graceful shutdown

**Context:**
The printer only decrements its RTSP session counter when the client sends RTSP `TEARDOWN`. When haffmpeg's `CameraMjpeg.close()` times out and sends `SIGKILL`, ffmpeg cannot send TEARDOWN, so the session leaks. This task adds a custom subclass that inserts a `SIGTERM` step between the graceful quit and `SIGKILL` — ffmpeg's RTSP demuxer sends TEARDOWN on SIGTERM.

**Files:**
- Modify: `custom_components/elegoo_printer/camera.py`

**What to implement:**

Add the following constants at the top of `camera.py`, after the existing imports and before any class/function definitions:

```python
FFMPEG_QUIT_TIMEOUT = 10        # seconds to wait after sending 'q' to ffmpeg
FFMPEG_TERMINATE_TIMEOUT = 5    # seconds to wait after SIGTERM before SIGKILL
NATIVE_STREAM_IDLE_TIMEOUT = 600  # 10 minutes — clear native stream flag after idle
IDLE_WATCHDOG_INTERVAL = 60     # seconds between idle checks
```

Add `import asyncio` to the existing imports (it is not currently imported in `camera.py`).

Add the following class in `camera.py`, before the `ElegooStreamCamera` class definition:

```python
class ElegooCameraMjpeg(CameraMjpeg):
    """CameraMjpeg with graceful shutdown: quit -> SIGTERM -> SIGKILL.

    ffmpeg's RTSP demuxer sends RTSP TEARDOWN on SIGTERM, which tells the
    printer to decrement its session counter. SIGKILL bypasses this entirely.
    """

    async def close(self, timeout: int = FFMPEG_QUIT_TIMEOUT) -> None:
        """Stop ffmpeg with graceful shutdown sequence."""
        if not self.is_running:
            return

        # Step 1: Send 'q' to ffmpeg stdin (ffmpeg's interactive quit)
        quit_timed_out = False
        try:
            self._proc.stdin.write(b"q")
            async with asyncio.timeout(timeout):
                await self._proc.wait()
            LOGGER.debug("Closed FFmpeg process gracefully (quit)")
            self._clear()
            return
        except (BrokenPipeError, RuntimeError, OSError):
            # stdin is closed or process already died — skip to SIGTERM
            LOGGER.debug("FFmpeg stdin unavailable, skipping to SIGTERM")
        except asyncio.TimeoutError:
            quit_timed_out = True

        if not quit_timed_out:
            # Process may have already exited after stdin error
            if not self.is_running:
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
```

**Steps:**
- [ ] Add `import asyncio` to camera.py imports
- [ ] Add the four constants after imports
- [ ] Add the `ElegooCameraMjpeg` class before `ElegooStreamCamera`
- [ ] Run `make format`
  - Did it succeed? If not, fix and re-run.
- [ ] Run `make lint`
  - Did it succeed? If not, fix and re-run.
- [ ] Run `make test`
  - Did all existing tests pass? If not, investigate.
- [ ] Commit with message: "feat(camera): add ElegooCameraMjpeg with graceful SIGTERM shutdown"

**Acceptance criteria:**
- [ ] `ElegooCameraMjpeg` exists and subclasses `CameraMjpeg`
- [ ] `close()` sends `b"q"` first, then `SIGTERM` on timeout/stdin-error, then `SIGKILL` as last resort
- [ ] `BrokenPipeError` / `RuntimeError` / `OSError` on `stdin.write` falls through to SIGTERM (not unhandled)
- [ ] `ProcessLookupError` on `terminate()` is treated as success (process already exited)
- [ ] `_clear()` is called in all exit paths
- [ ] Uses `asyncio.timeout` (not `haffmpeg.timeout.asyncio_timeout`)
- [ ] Code passes `make format` and `make lint`

---

### Task 2: Add state tracking and lifecycle helpers to `ElegooStreamCamera`

**Context:**
The printer video must be enabled only when at least one viewer is active, and disabled when all disconnect. This task adds state variables and helper methods to `ElegooStreamCamera` for tracking viewer count and managing the enable/disable lifecycle. It also refactors `_get_stream_url()` to read the cached URL without making any API call.

**Files:**
- Modify: `custom_components/elegoo_printer/camera.py`

**What to implement:**

#### 2a. Add state variables to `ElegooStreamCamera.__init__()`

Add these instance variables at the end of the existing `ElegooStreamCamera.__init__()` method, after the existing `self._extra_ffmpeg_arguments = ...` line:

```python
        # Stream lifecycle tracking
        self._active_mjpeg_streams: int = 0
        self._active_mjpeg_processes: set[ElegooCameraMjpeg] = set()
        self._transient_viewers: int = 0  # async_camera_image grabs
        self._native_stream_active: bool = False
        self._stream_enabled: bool = False
        self._last_activity: float = 0.0  # monotonic time of last stream activity
        self._idle_watchdog_task: asyncio.Task | None = None
```

#### 2b. Add `_has_active_viewers` helper property

Add this method to `ElegooStreamCamera`:

```python
    def _has_active_viewers(self) -> bool:
        """Check if any viewer type is currently active."""
        return (
            self._active_mjpeg_streams > 0
            or self._transient_viewers > 0
            or self._native_stream_active
        )
```

#### 2c. Add lifecycle helper methods

Add these methods to `ElegooStreamCamera`:

```python
    async def _ensure_stream_enabled(self) -> None:
        """Enable printer video if not already enabled.

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
        """Disable printer video.

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
```

#### 2d. Refactor `_get_stream_url()` to read cached URL

Replace the existing `_get_stream_url()` method in `ElegooStreamCamera`:

```python
    async def _get_stream_url(self) -> str | None:
        """Get the stream URL from cached printer data.

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
```

**Steps:**
- [ ] Add state variables to `ElegooStreamCamera.__init__()`
- [ ] Add `_has_active_viewers()` helper
- [ ] Add `_ensure_stream_enabled()` and `_disable_stream()` methods
- [ ] Replace `_get_stream_url()` to read cached URL (no API call)
- [ ] Run `make format`
  - Did it succeed? If not, fix and re-run.
- [ ] Run `make lint`
  - Did it succeed? If not, fix and re-run.
- [ ] Run `make test`
  - Did all existing tests pass? If not, investigate.
- [ ] Commit with message: "feat(camera): add stream lifecycle state and helpers to ElegooStreamCamera"

**Acceptance criteria:**
- [ ] State variables initialized in `__init__` (including `_transient_viewers`)
- [ ] `_has_active_viewers()` returns True if any viewer type is active
- [ ] `_ensure_stream_enabled()` is idempotent, sets flag only on success
- [ ] `_disable_stream()` clears flag only on success, logs warning on failure
- [ ] `_get_stream_url()` reads `printer_data.video.video_url` directly (no API call, no toggle)
- [ ] Code passes `make format` and `make lint`

---

### Task 3: Update stream methods with ref-counting and idle watchdog

**Context:**
This task wires up the lifecycle helpers into all three stream entry points (`handle_async_mjpeg_stream`, `stream_source`, `async_camera_image`), adds the idle watchdog for native streams, and implements cleanup on entity removal. This is the core of the fix — ensuring the printer video is enabled only when viewers are active and disabled when they disconnect.

**Files:**
- Modify: `custom_components/elegoo_printer/camera.py`

**What to implement:**

#### 3a. Replace `handle_async_mjpeg_stream()` with ref-counted version

Replace the existing `handle_async_mjpeg_stream()` in `ElegooStreamCamera`:

```python
    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse:
        """Generate an HTTP MJPEG stream from the camera.

        Ref-counted: enables video on first viewer, disables on last.
        Uses ElegooCameraMjpeg for graceful SIGTERM shutdown.
        """
        # Enable stream if first viewer
        if not self._has_active_viewers():
            await self._ensure_stream_enabled()

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

        try:
            stream_reader = await mjpeg_stream.get_reader()
            return await async_aiohttp_proxy_stream(
                self.hass,
                request,
                stream_reader,
                ffmpeg_manager.ffmpeg_stream_content_type,
            )
        finally:
            self._active_mjpeg_streams -= 1
            self._active_mjpeg_processes.discard(mjpeg_stream)
            await mjpeg_stream.close(timeout=FFMPEG_QUIT_TIMEOUT)
            # Disable stream if last viewer
            if not self._has_active_viewers():
                await self._disable_stream()
```

#### 3b. Replace `stream_source()` with native tracking version

Replace the existing `stream_source()` in `ElegooStreamCamera`:

```python
    async def stream_source(self) -> str | None:
        """Return the source of the stream.

        Enables video for native HA streaming. Uses idle watchdog to
        disable after NATIVE_STREAM_IDLE_TIMEOUT of no activity.
        """
        stream_url = await self._get_stream_url()
        if not stream_url:
            return None

        if not self._native_stream_active:
            await self._ensure_stream_enabled()
            self._native_stream_active = True
        self._last_activity = asyncio.get_running_loop().time()
        return stream_url
```

Note: `_native_stream_active` is set only after confirming the URL is valid (non-None). This prevents the flag from being stuck True when the stream is unavailable.

#### 3c. Replace `async_camera_image()` with lifecycle-covered version

Replace the existing `async_camera_image()` in `ElegooStreamCamera`:

```python
    async def async_camera_image(
        self,
        width: int | None = None,  # noqa: ARG002
        height: int | None = None,  # noqa: ARG002
    ) -> bytes | None:
        """Return a still image from the camera.

        Treats the image grab as a transient viewer — enables video if needed,
        but only disables if no other viewers are active.

        Note: This path uses HA's async_get_image() which spawns its own
        ffmpeg process. That process does NOT get graceful SIGTERM shutdown,
        so individual image grabs may leak RTSP sessions. The _transient_viewers
        counter prevents this path from disabling an active MJPEG stream.
        """
        self._transient_viewers += 1
        if not self._has_active_viewers():
            await self._ensure_stream_enabled()

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
            self._transient_viewers -= 1
            # Only disable if no other viewers are active
            if not self._has_active_viewers():
                await self._disable_stream()
```

Note: `_transient_viewers` is incremented BEFORE any await to prevent race conditions with other viewers' disable logic.

#### 3d. Add idle watchdog with `async_added_to_hass`

Add these methods to `ElegooStreamCamera`:

```python
    async def async_added_to_hass(self) -> None:
        """Start the idle watchdog when the entity is added."""
        await super().async_added_to_hass()
        self._idle_watchdog_task = asyncio.create_task(self._idle_watchdog())

    async def _idle_watchdog(self) -> None:
        """Periodically check for idle conditions and clean up.

        Runs every IDLE_WATCHDOG_INTERVAL seconds. Two responsibilities:
        1. If no viewers are active and video is enabled, attempt to disable
           (handles failed disables from normal disconnect path).
        2. If native stream has been idle for NATIVE_STREAM_IDLE_TIMEOUT,
           clear the native stream flag (allows future disable attempts).
        """
        loop = asyncio.get_running_loop()
        while True:
            try:
                await asyncio.sleep(IDLE_WATCHDOG_INTERVAL)
                # Always attempt disable if video is on but no viewers
                if self._stream_enabled and not self._has_active_viewers():
                    await self._disable_stream()
                # Clear native stream flag if idle
                if (
                    self._native_stream_active
                    and self._last_activity > 0
                    and loop.time() - self._last_activity > NATIVE_STREAM_IDLE_TIMEOUT
                ):
                    LOGGER.debug(
                        "Native stream idle for %.0fs, clearing flag for %s",
                        NATIVE_STREAM_IDLE_TIMEOUT,
                        self.entity_id,
                    )
                    self._native_stream_active = False
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                LOGGER.exception("Idle watchdog error for %s", self.entity_id)
```

#### 3e. Add `async_will_remove_from_hass()` for cleanup

Add this method to `ElegooStreamCamera`:

```python
    async def async_will_remove_from_hass(self) -> None:
        """Clean up when the entity is removed from Home Assistant.

        Cancels the idle watchdog, closes any in-flight MJPEG processes,
        and disables the printer video.
        """
        # Cancel idle watchdog
        if self._idle_watchdog_task:
            self._idle_watchdog_task.cancel()
            try:
                await self._idle_watchdog_task
            except asyncio.CancelledError:
                pass
            self._idle_watchdog_task = None

        # Close any in-flight MJPEG processes
        for proc in self._active_mjpeg_processes.copy():
            await proc.close(timeout=FFMPEG_QUIT_TIMEOUT)
        self._active_mjpeg_processes.clear()
        self._active_mjpeg_streams = 0
        self._transient_viewers = 0

        # Disable native stream tracking
        self._native_stream_active = False

        # Disable printer video
        await self._disable_stream()
```

**What NOT to change:**
- Do NOT modify `ElegooMjpegCamera` (FDM printers) — they use HTTP MJPEG with no RTSP session counter
- Do NOT modify `async_setup_entry()` — camera entity creation is unchanged
- Do NOT modify `_is_over_capacity()` — keep existing implementation

**Steps:**
- [ ] Replace `handle_async_mjpeg_stream()` with ref-counted version using `_has_active_viewers()`
- [ ] Replace `stream_source()` with native tracking version (set flag only after valid URL)
- [ ] Replace `async_camera_image()` with lifecycle-covered version (increment `_transient_viewers` before any await)
- [ ] Add `async_added_to_hass()` and `_idle_watchdog()`
- [ ] Add `async_will_remove_from_hass()`
- [ ] Run `make format`
  - Did it succeed? If not, fix and re-run.
- [ ] Run `make lint`
  - Did it succeed? If not, fix and re-run.
- [ ] Run `make test`
  - Did all existing tests pass? If not, investigate.
- [ ] Commit with message: "feat(camera): wire up ref-counted lifecycle, idle watchdog, and cleanup hooks"

**Acceptance criteria:**
- [ ] `handle_async_mjpeg_stream()` uses `ElegooCameraMjpeg` and ref-counts via `_has_active_viewers()`
- [ ] `stream_source()` sets `_native_stream_active = True` only after confirming valid URL
- [ ] `async_camera_image()` increments `_transient_viewers` before any await, decrements in finally
- [ ] `_idle_watchdog()` runs every 60s, attempts disable whenever zero viewers, clears native flag after 10 min idle
- [ ] `_idle_watchdog()` handles `asyncio.CancelledError` (breaks loop) and general exceptions (logs, continues)
- [ ] `async_will_remove_from_hass()` cancels watchdog, closes in-flight processes, resets all counters, disables video
- [ ] `async_added_to_hass()` starts the watchdog task (correct HA hook name)
- [ ] Code passes `make format` and `make lint`

---

### Task 4: Unit tests for `ElegooCameraMjpeg.close()`

**Context:**
Verify the graceful shutdown sequence works correctly: `b"q"` → SIGTERM → SIGKILL escalation, and that edge cases (closed stdin, already-dead process) are handled without exceptions.

**Files:**
- Create: `custom_components/elegoo_printer/tests/test_camera.py`

**What to implement:**

Create a new test file with the following test cases using `pytest-asyncio`:

```python
"""Tests for the camera module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.elegoo_printer.camera import (
    FFMPEG_QUIT_TIMEOUT,
    FFMPEG_TERMINATE_TIMEOUT,
    ElegooCameraMjpeg,
)


@pytest.fixture
def mock_ffmpeg_bin():
    return "/usr/bin/ffmpeg"


@pytest.fixture
def mock_proc():
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.wait = AsyncMock()
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.returncode = None
    return proc


class TestElegooCameraMjpegClose:
    """Test cases for ElegooCameraMjpeg.close() shutdown sequence."""

    @pytest.mark.asyncio
    async def test_close_graceful_quit(self, mock_ffmpeg_bin, mock_proc):
        """ffmpeg exits after receiving 'q' — no SIGTERM needed."""
        camera = ElegooCameraMjpeg(mock_ffmpeg_bin)
        camera._proc = mock_proc
        camera._proc.wait.return_value = 0

        await camera.close(timeout=1)

        mock_proc.stdin.write.assert_called_once_with(b"q")
        mock_proc.wait.assert_called_once()
        mock_proc.terminate.assert_not_called()
        mock_proc.kill.assert_not_called()
        assert camera._proc is None  # _clear() was called

    @pytest.mark.asyncio
    async def test_close_quit_timeout_escalates_to_sigterm(
        self, mock_ffmpeg_bin, mock_proc
    ):
        """ffmpeg doesn't exit after 'q' — SIGTERM is sent."""
        camera = ElegooCameraMjpeg(mock_ffmpeg_bin)
        camera._proc = mock_proc
        # First wait times out, second (after SIGTERM) succeeds
        mock_proc.wait.side_effect = [asyncio.TimeoutError(), None]

        await camera.close(timeout=0)

        mock_proc.stdin.write.assert_called_once_with(b"q")
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_not_called()
        assert camera._proc is None

    @pytest.mark.asyncio
    async def test_close_sigterm_timeout_escalates_to_sigkill(
        self, mock_ffmpeg_bin, mock_proc
    ):
        """ffmpeg doesn't exit after SIGTERM — SIGKILL is sent."""
        camera = ElegooCameraMjpeg(mock_ffmpeg_bin)
        camera._proc = mock_proc
        # Both waits time out
        mock_proc.wait.side_effect = [asyncio.TimeoutError(), asyncio.TimeoutError()]

        await camera.close(timeout=0)

        mock_proc.stdin.write.assert_called_once_with(b"q")
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        assert camera._proc is None

    @pytest.mark.asyncio
    async def test_close_stdin_broken_pipe_skips_to_sigterm(
        self, mock_ffmpeg_bin, mock_proc
    ):
        """stdin is closed — falls through to SIGTERM."""
        camera = ElegooCameraMjpeg(mock_ffmpeg_bin)
        camera._proc = mock_proc
        mock_proc.stdin.write.side_effect = BrokenPipeError()
        mock_proc.wait.return_value = 0

        await camera.close(timeout=1)

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_not_called()
        assert camera._proc is None

    @pytest.mark.asyncio
    async def test_close_already_not_running(self, mock_ffmpeg_bin):
        """close() is a no-op when ffmpeg isn't running."""
        camera = ElegooCameraMjpeg(mock_ffmpeg_bin)
        camera._proc = None

        await camera.close(timeout=1)

        # No calls should be made

    @pytest.mark.asyncio
    async def test_close_process_lookup_error_during_sigterm(
        self, mock_ffmpeg_bin, mock_proc
    ):
        """Process already died before SIGTERM — treated as success."""
        camera = ElegooCameraMjpeg(mock_ffmpeg_bin)
        camera._proc = mock_proc
        mock_proc.stdin.write.side_effect = BrokenPipeError()
        mock_proc.terminate.side_effect = ProcessLookupError()

        await camera.close(timeout=1)

        mock_proc.kill.assert_not_called()
        assert camera._proc is None
```

**Steps:**
- [ ] Create `custom_components/elegoo_printer/tests/` directory if it doesn't exist
- [ ] Create `__init__.py` in the tests directory
- [ ] Create `test_camera.py` with the test cases above
- [ ] Run `make test`
  - Did all tests pass? If not, fix failures.
- [ ] Run `make format`
  - Did it succeed? If not, fix and re-run.
- [ ] Run `make lint`
  - Did it succeed? If not, fix and re-run.
- [ ] Commit with message: "test(camera): add unit tests for ElegooCameraMjpeg shutdown sequence"

**Acceptance criteria:**
- [ ] All 6 test cases pass
- [ ] Tests cover: graceful quit, quit→SIGTERM, quit→SIGTERM→SIGKILL, broken stdin, not running, ProcessLookupError
- [ ] Code passes `make format` and `make lint`

---

### Task 5: Verification and final lint

**Context:**
Final verification pass to ensure all changes work together, code quality checks pass, and no regressions were introduced.

**Files:**
- Modify: `custom_components/elegoo_printer/camera.py` (if any fixes needed)

**Steps:**
- [ ] Run `make format`
  - Did it succeed? If not, fix and re-run.
- [ ] Run `make lint`
  - Did it succeed? If not, fix and re-run.
- [ ] Run `make test`
  - Did all tests pass (including new camera tests)? If not, fix failures.
- [ ] Manually verify `camera.py` has no references to `CameraMjpeg` outside of the `ElegooCameraMjpeg` subclass (all usage should go through `ElegooCameraMjpeg`)
- [ ] Manually verify `_get_stream_url()` reads `printer_data.video.video_url` directly (no API call)
- [ ] Manually verify `async_camera_image()` increments `_transient_viewers` before any await
- [ ] Manually verify `async_added_to_hass` is used (not `async_entity_added_to_hass`)
- [ ] Commit with message: "chore(camera): final verification pass for RTSP teardown fix"

**Acceptance criteria:**
- [ ] `make format` passes cleanly
- [ ] `make lint` passes cleanly
- [ ] `make test` passes all tests (existing + new camera tests)
- [ ] No `CameraMjpeg` usage outside of `ElegooCameraMjpeg` subclass
- [ ] `_get_stream_url()` reads cached URL (no API call, no toggle)
- [ ] `async_camera_image()` has `_transient_viewers` guard
- [ ] `async_added_to_hass` is the correct hook name
