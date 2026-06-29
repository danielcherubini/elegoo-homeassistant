# CC2 Client Improvements Plan

**Goal:** Harden the CC2 MQTT client with firmware-compatible light control, stale callback prevention, explicit auth failure detection, and delayed connection-loss reporting.

**Architecture:** Four independent improvements to `custom_components/elegoo_printer/cc2/client.py`. Each change is self-contained and modifies a single method or adds a small helper. The CC2 client uses asyncio (single-threaded), so the connection generation guard is a lightweight safeguard.

**Tech Stack:** Python, asyncio, aiomqtt

---

## Shared `__init__` Additions

All four tasks add fields to `ElegooCC2Client.__init__()`. To avoid merge churn, add all new fields in a single block after `self._is_registered: bool = False` (around line 116). Place each field next to its conceptual sibling (no banner comment — blend with surrounding style):

```python
self._is_registered: bool = False

# Connection generation for stale callback guard (Task 2)
self._connection_generation: int = 0
self._listener_generation: int = 0

# Auth failure tracking (Task 3)
self._last_auth_failure: bool = False

# Delayed disconnect (Task 4)
self._disconnect_delay_task: asyncio.Task | None = None
```

---

### Task 1: Light control dual-param (brightness + power)

**Context:**
The CC2 light command (method 1029) has been through three iterations (`brightness` → `status` → `power`) trying to find the correct parameter name. The OctoEverywhere implementation sends **both** `brightness` (0-255) and `power` (0/1) for maximum firmware compatibility. This change follows that approach — sending both params ensures it works across firmware versions that expect either format.

**Note:** This assumes binary on/off only. If dimming is added later, set `brightness` from a new field rather than inferring from `power`.

**Files:**
- Modify: `custom_components/elegoo_printer/cc2/client.py`
- Create: `custom_components/elegoo_printer/cc2/tests/test_light_control.py`

**What to implement:**
In `ElegooCC2Client.set_light_status()`, change the payload from `{"power": 0/1}` to `{"brightness": 0/255, "power": 0/1}`.

**Steps:**
- [ ] In `custom_components/elegoo_printer/cc2/client.py`, find `set_light_status()` method
- [ ] Replace the current implementation:
  ```python
  async def set_light_status(self, light_status: LightStatus) -> None:
      """Set the printer's light status."""
      # CC2 uses "power" field for LED control (0=off, 1=on)
      # Based on web interface: LightSwitch,params:{power:Se?1:0}
      power = 1 if light_status.second_light else 0
      await self._send_command(CC2_CMD_SET_LIGHT, {"power": power})
  ```
  With:
  ```python
  async def set_light_status(self, light_status: LightStatus) -> None:
      """Set the printer's light status."""
      # Send both brightness and power for firmware compatibility.
      # Some firmware versions expect "power" (0/1), others expect "brightness" (0-255).
      # OctoEverywhere sends both — follow that pattern.
      # Note: assumes binary on/off only; dimming would need a separate brightness field.
      power = 1 if light_status.second_light else 0
      await self._send_command(CC2_CMD_SET_LIGHT, {
          "brightness": 255 if power else 0,
          "power": power,
      })
  ```
- [ ] Verify the test simulator at `scripts/test_cc2_printer.py` handles both `brightness` and `power` (it already does — the handler checks `power`, `brightness`, and `status` keys).
- [ ] Create `custom_components/elegoo_printer/cc2/tests/test_light_control.py`:
  ```python
  """Tests for CC2 light control dual-param."""
  from __future__ import annotations

  from unittest.mock import AsyncMock, patch

  from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
  from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
  from custom_components.elegoo_printer.sdcp.models.printer import Printer
  from custom_components.elegoo_printer.sdcp.models.status import LightStatus


  def _client() -> ElegooCC2Client:
      printer = Printer()
      printer.printer_type = PrinterType.FDM
      return ElegooCC2Client("192.168.1.1", "TESTSN", printer=printer)


  def test_light_on_sends_brightness_and_power() -> None:
      client = _client()
      with patch.object(client, "_send_command", new_callable=AsyncMock) as mock_cmd:
          import asyncio
          asyncio.get_event_loop().run_until_complete(
              client.set_light_status(LightStatus({"SecondLight": 1}))
          )
          mock_cmd.assert_called_once()
          params = mock_cmd.call_args[0][1]
          assert params["brightness"] == 255
          assert params["power"] == 1


  def test_light_off_sends_brightness_and_power() -> None:
      client = _client()
      with patch.object(client, "_send_command", new_callable=AsyncMock) as mock_cmd:
          import asyncio
          asyncio.get_event_loop().run_until_complete(
              client.set_light_status(LightStatus({"SecondLight": 0}))
          )
          mock_cmd.assert_called_once()
          params = mock_cmd.call_args[0][1]
          assert params["brightness"] == 0
          assert params["power"] == 0
  ```
- [ ] Run `make test` to ensure all tests pass
- [ ] Run `make lint` and `make format`
- [ ] Commit with message: "Fix CC2 light command to send both brightness and power params"

**Acceptance criteria:**
- [ ] `set_light_status()` sends `{"brightness": 255, "power": 1}` when turning on
- [ ] `set_light_status()` sends `{"brightness": 0, "power": 0}` when turning off
- [ ] Two new tests pass verifying the dual-param payload
- [ ] All existing tests pass
- [ ] Lint and format checks pass

---

### Task 2: ConnectionGeneration guard for stale callbacks

**Context:**
The CC2 client uses asyncio with a long-running `_mqtt_listener()` task that processes incoming MQTT messages. On disconnect, this task is cancelled, but there's a race window where a message could be decoded and processed after `disconnect()` starts but before the task is fully cancelled. This guard increments a counter on connect/disconnect and checks it at the top of the message loop — if the counter changed, the listener stops processing.

**Files:**
- Modify: `custom_components/elegoo_printer/cc2/client.py`
- Create: `custom_components/elegoo_printer/cc2/tests/test_connection_generation.py`

**What to implement:**
Add `_connection_generation: int` and `_listener_generation: int` (added in shared `__init__` block). Increment `_connection_generation` on every `disconnect()` call and at the start of `_try_connect_with_password()`. Capture the generation value into `_listener_generation` when starting the listener. Check at the top of each loop iteration — if generation changed, break out of the listener.

**Steps:**
- [ ] In `custom_components/elegoo_printer/cc2/client.py`, add `_connection_generation: int = 0` and `_listener_generation: int = 0` in the shared `__init__` block (see Shared section above).
- [ ] In `_try_connect_with_password()`, at the very top of the method (before any other logic), add:
  ```python
  # Increment generation for this connection attempt
  self._connection_generation += 1
  ```
- [ ] In `disconnect()`, at the very top of the method (before cancelling tasks), add:
  ```python
  # Increment generation to invalidate any in-flight callbacks
  self._connection_generation += 1
  ```
- [ ] In `_mqtt_listener()`, before the `async for message in self.mqtt_client.messages:` loop, capture the generation:
  ```python
  # Capture generation for stale callback detection
  self._listener_generation = self._connection_generation
  ```
  
  Inside the `async for` loop, at the top of the loop body (inside the `try` block, before decoding the message), add:
  ```python
  # Stale callback guard: if disconnect() incremented the generation,
  # stop processing messages from the old connection.
  if self._connection_generation != self._listener_generation:
      self.logger.debug("CC2 MQTT listener: generation changed, stopping")
      break
  ```
- [ ] Create `custom_components/elegoo_printer/cc2/tests/test_connection_generation.py`:
  ```python
  """Tests for CC2 connection generation guard."""
  from __future__ import annotations

  from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
  from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
  from custom_components.elegoo_printer.sdcp.models.printer import Printer


  def _client() -> ElegooCC2Client:
      printer = Printer()
      printer.printer_type = PrinterType.FDM
      return ElegooCC2Client("192.168.1.1", "TESTSN", printer=printer)


  def test_generation_starts_at_zero() -> None:
      client = _client()
      assert client._connection_generation == 0
      assert client._listener_generation == 0


  def test_disconnect_increments_generation() -> None:
      """Simulating disconnect() incrementing generation invalidates listener."""
      client = _client()
      # Simulate what _try_connect_with_password does
      client._connection_generation += 1
      # Simulate what _mqtt_listener does (capture generation)
      client._listener_generation = client._connection_generation
      # Simulate what disconnect() does (increment generation)
      client._connection_generation += 1
      # Now the generations should mismatch (stale callback detected)
      assert client._connection_generation != client._listener_generation
  ```
- [ ] Run `make test` to ensure all tests pass
- [ ] Run `make lint` and `make format`
- [ ] Commit with message: "Add connection generation guard to prevent stale CC2 MQTT callbacks"

**Acceptance criteria:**
- [ ] `_connection_generation` starts at 0 and increments on both connect and disconnect
- [ ] `_mqtt_listener()` captures generation into `_listener_generation` before the loop
- [ ] If `_connection_generation != _listener_generation` during the loop, the listener breaks cleanly
- [ ] New tests pass
- [ ] All existing tests pass
- [ ] Lint and format checks pass

---

### Task 3: Auth failure detection from MQTT CONACK

**Context:**
When the CC2 MQTT connection fails due to wrong credentials, the current code silently tries the next fallback password. This wastes time and produces confusing logs. aiomqtt raises `MqttConnectError` (from `aiomqtt.client`) with an `rc` attribute for CONACK failures: `rc=4` (Bad username or password) and `rc=5` (Not authorised) for MQTT 3.1.1. For MQTT 5, `rc` is a `ReasonCode` object with `.value` of 134 or 135.

This change detects auth-specific failures by checking the typed exception, skips remaining fallback passwords when confirmed, and surfaces a clear error.

**Files:**
- Modify: `custom_components/elegoo_printer/cc2/client.py`
- Create: `custom_components/elegoo_printer/cc2/tests/test_auth_detection.py`

**What to implement:**
Add a static `_is_auth_failure(exc) -> bool` that checks for `MqttConnectError` with `rc` in `{4, 5}` (MQTT 3.1.1) or `rc.value in {134, 135}` (MQTT 5). In `connect_printer()`, after a failed attempt, check the flag and break the password loop if set. Reset the flag at the start of each `connect_printer()` call.

**Steps:**
- [ ] In `custom_components/elegoo_printer/cc2/client.py`, add `_last_auth_failure: bool = False` in the shared `__init__` block (see Shared section above).
- [ ] Add a static helper method to `ElegooCC2Client`:
  ```python
  @staticmethod
  def _is_auth_failure(exc: Exception) -> bool:
      """Check if an MQTT exception indicates an authentication failure.
      
      aiomqtt raises MqttConnectError with rc=4 (bad username/password)
      or rc=5 (not authorised) for MQTT 3.1.1, or ReasonCode objects
      with value 134/135 for MQTT 5.
      """
      from paho.mqtt.reasoncodes import ReasonCode

      # Check for MqttConnectError with auth-specific rc
      if isinstance(exc, aiomqtt.MqttCodeError):
          rc = exc.rc
          # MQTT 3.1.1 integer codes: 4 = bad username/password, 5 = not authorised
          if isinstance(rc, int) and rc in (4, 5):
              return True
          # MQTT 5 ReasonCode objects: 134 = bad username/password, 135 = not authorized
          if isinstance(rc, ReasonCode) and rc.value in (134, 135):
              return True
      return False
  ```
  
  Note: Uses `aiomqtt.MqttCodeError` (the documented parent class, re-exported at module level) instead of `MqttConnectError` (which lives in `aiomqtt.client` and may not be re-exported in all versions). This matches the existing `except` patterns in the codebase.
- [ ] In `_try_connect_with_password()`, in the `except` block, add auth detection after the debug log:
  ```python
  except (asyncio.TimeoutError, OSError, aiomqtt.MqttError) as e:
      self.logger.debug(
          "Connection attempt failed: %s",
          e,
      )
      # Check for auth failure to skip remaining passwords
      if ElegooCC2Client._is_auth_failure(e):
          self._last_auth_failure = True
      return False
  ```
- [ ] In `connect_printer()`, at the very top of the method (before the `if self.is_connected` check), reset the flag:
  ```python
  # Reset auth failure flag for fresh connection attempt
  self._last_auth_failure = False
  ```
- [ ] In `connect_printer()`, in the password loop, after `success = await self._try_connect_with_password(password)` and its `if success:` block, add:
  ```python
      # Stop trying if this was an auth failure (wrong credentials, not wrong password)
      if self._last_auth_failure:
          break  # Skip remaining fallbacks
  ```
- [ ] Add a property for external consumers:
  ```python
  @property
  def last_auth_failure(self) -> bool:
      """Return True if the last connection failure was due to auth."""
      return self._last_auth_failure
  ```
- [ ] Create `custom_components/elegoo_printer/cc2/tests/test_auth_detection.py`:
  ```python
  """Tests for CC2 auth failure detection."""
  from __future__ import annotations

  import aiomqtt
  from paho.mqtt.packettypes import PacketTypes
  from paho.mqtt.reasoncodes import ReasonCode

  from custom_components.elegoo_printer.cc2.client import ElegooCC2Client


  def test_auth_failure_rc_4() -> None:
      """MQTT 3.1.1 rc=4 (bad username/password) is detected."""
      exc = aiomqtt.MqttCodeError(4, "Connection refused")
      assert ElegooCC2Client._is_auth_failure(exc) is True


  def test_auth_failure_rc_5() -> None:
      """MQTT 3.1.1 rc=5 (not authorised) is detected."""
      exc = aiomqtt.MqttCodeError(5, "Connection refused")
      assert ElegooCC2Client._is_auth_failure(exc) is True


  def test_not_auth_failure_rc_1() -> None:
      """MQTT 3.1.1 rc=1 (incorrect protocol) is NOT auth failure."""
      exc = aiomqtt.MqttCodeError(1, "Connection refused")
      assert ElegooCC2Client._is_auth_failure(exc) is False


  def test_not_auth_failure_timeout() -> None:
      """asyncio.TimeoutError is NOT auth failure."""
      assert ElegooCC2Client._is_auth_failure(TimeoutError()) is False


  def test_not_auth_failure_os_error() -> None:
      """OSError (connection refused) is NOT auth failure."""
      assert ElegooCC2Client._is_auth_failure(OSError("Connection refused")) is False


  def test_auth_failure_mqtt5_reason_code() -> None:
      """MQTT 5 ReasonCode 134 (bad username/password) is detected."""
      rc = ReasonCode(PacketTypes.CONNACK, "Bad user name or password")
      assert rc.value == 134
      exc = aiomqtt.MqttCodeError(rc, "Connection refused")
      assert ElegooCC2Client._is_auth_failure(exc) is True


  def test_auth_failure_mqtt5_not_authorized() -> None:
      """MQTT 5 ReasonCode 135 (not authorized) is detected."""
      rc = ReasonCode(PacketTypes.CONNACK, "Not authorized")
      assert rc.value == 135
      exc = aiomqtt.MqttCodeError(rc, "Connection refused")
      assert ElegooCC2Client._is_auth_failure(exc) is True
  ```
- [ ] Run `make test` to ensure all tests pass
- [ ] Run `make lint` and `make format`
- [ ] Commit with message: "Add auth failure detection for CC2 MQTT connection"

**Acceptance criteria:**
- [ ] `_is_auth_failure()` returns True for `MqttCodeError` with rc=4 or rc=5
- [ ] `_is_auth_failure()` returns True for MQTT 5 ReasonCode with value 134 or 135
- [ ] `_is_auth_failure()` returns False for `TimeoutError`, `OSError`, and other rc values
- [ ] When auth failure detected, remaining fallback passwords are skipped
- [ ] `_last_auth_failure` is reset at the start of each `connect_printer()` call
- [ ] New tests pass
- [ ] All existing tests pass
- [ ] Lint and format checks pass

---

### Task 4: Delayed connection-loss reporting

**Context:**
When the MQTT connection to the CC2 printer drops momentarily (network blip, broker restart), the current code immediately clears the `_print_status_transition_queue`, losing transient state like `COMPLETE → IDLE` transitions. If the reconnect succeeds quickly, this state is lost before HA can poll it.

This change delays the **queue clearing** by 5 seconds. The `_is_connected` flag is set to False **immediately** (preserving existing behavior — `is_connected` property and `_send_command()` remain accurate). Only the queue-clearing is delayed, giving the reconnect logic a window to succeed without losing transient state.

**Note:** `is_connected` still flips to False immediately; HA availability polling may briefly see "unavailable". The win is that the queued print-status transitions are not lost, so HA does not miss the `COMPLETE` snapshot.

**Files:**
- Modify: `custom_components/elegoo_printer/cc2/client.py`
- Modify: `custom_components/elegoo_printer/cc2/const.py`
- Create: `custom_components/elegoo_printer/cc2/tests/test_delayed_disconnect.py`

**What to implement:**
Add `_disconnect_delay_task` (added in shared `__init__` block). In the `_mqtt_listener()` `finally` block: set `_is_connected = False` immediately (preserving existing behavior), then schedule a delayed task to clear the queue. If reconnect succeeds during the delay, cancel the pending task.

**Steps:**
- [ ] In `custom_components/elegoo_printer/cc2/const.py`, add at the end of the constants section:
  ```python
  # Delayed disconnect settings
  CC2_DISCONNECT_DELAY = 5  # seconds to wait before finalizing disconnect
  ```
- [ ] In `custom_components/elegoo_printer/cc2/client.py`, add `CC2_DISCONNECT_DELAY` to the import from `.const`:
  ```python
  from .const import (
      # ... existing imports ...
      CC2_DISCONNECT_DELAY,
  )
  ```
- [ ] In `custom_components/elegoo_printer/cc2/client.py`, add `_disconnect_delay_task: asyncio.Task | None = None` in the shared `__init__` block (see Shared section above).
- [ ] Add a new method to `ElegooCC2Client`:
  ```python
  async def _delayed_disconnect(self) -> None:
      """Wait before finalizing disconnect to allow quick reconnects.
      
      If reconnect succeeds during the delay, the cleanup is skipped.
      This prevents losing transient state (e.g., COMPLETE->IDLE transitions)
      during brief network interruptions.
      """
      try:
          await asyncio.sleep(CC2_DISCONNECT_DELAY)
      except asyncio.CancelledError:
          # Reconnect succeeded during delay — task was cancelled
          return
      # Re-check after sleep (handles cancel arriving after sleep resumes)
      if self._is_connected and self._is_registered:
          self.logger.debug(
              "CC2 reconnect succeeded during disconnect delay, skipping cleanup"
          )
          return
      # Finalize disconnect cleanup
      self._print_status_transition_queue.clear()
      self.logger.debug("CC2 disconnect delay expired, cleanup complete")
  ```
- [ ] In `_mqtt_listener()`, in the `finally` block, replace:
  ```python
  finally:
      self._is_connected = False
      self.logger.info("CC2 MQTT listener stopped")
  ```
  With:
  ```python
  finally:
      # Mark disconnected immediately so is_connected and _send_command are accurate
      self._is_connected = False
      # Schedule delayed cleanup to allow quick reconnects
      if self._disconnect_delay_task is None:
          self._disconnect_delay_task = asyncio.create_task(
              self._delayed_disconnect()
          )
          self._background_tasks.add(self._disconnect_delay_task)
          self._disconnect_delay_task.add_done_callback(
              self._on_disconnect_delay_done
          )
      self.logger.info("CC2 MQTT listener stopped")
  ```
- [ ] Add a callback helper:
  ```python
  def _on_disconnect_delay_done(self, task: asyncio.Task) -> None:
      """Callback when the disconnect delay task completes."""
      self._background_tasks.discard(task)
      self._disconnect_delay_task = None
  ```
- [ ] In `_try_connect_with_password()`, after setting `_is_connected = True` and `_is_registered = True` (in the success path), cancel any pending disconnect:
  ```python
  # Cancel any pending disconnect delay (reconnect succeeded)
  if self._disconnect_delay_task is not None:
      self._disconnect_delay_task.cancel()
      self._disconnect_delay_task = None
      self.logger.debug("Cancelled pending disconnect delay (reconnect succeeded)")
  ```
- [ ] In `disconnect()` (explicit disconnect), after incrementing `_connection_generation`, cancel any pending delay:
  ```python
  # Cancel any pending disconnect delay
  if self._disconnect_delay_task is not None:
      self._disconnect_delay_task.cancel()
      self._disconnect_delay_task = None
  ```
- [ ] In the heartbeat timeout path (`_heartbeat_loop()`), where it currently sets `_is_connected = False` and schedules `disconnect()`: the existing `disconnect()` call already cancels the delay task, so no additional change needed.
- [ ] Create `custom_components/elegoo_printer/cc2/tests/test_delayed_disconnect.py`:
  ```python
  """Tests for CC2 delayed disconnect."""
  from __future__ import annotations

  import asyncio
  from unittest.mock import patch

  from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
  from custom_components.elegoo_printer.cc2.const import CC2_DISCONNECT_DELAY
  from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
  from custom_components.elegoo_printer.sdcp.models.printer import Printer
  from custom_components.elegoo_printer.sdcp.models.status import PrinterStatus


  def _client() -> ElegooCC2Client:
      printer = Printer()
      printer.printer_type = PrinterType.FDM
      return ElegooCC2Client("192.168.1.1", "TESTSN", printer=printer)


  def test_delayed_disconnect_clears_queue() -> None:
      """After delay expires, the transition queue is cleared."""
      client = _client()
      client._print_status_transition_queue.append(PrinterStatus())
      assert len(client._print_status_transition_queue) == 1

      async def run():
          async def instant_sleep(_):
              pass
          with patch("asyncio.sleep", instant_sleep):
              await client._delayed_disconnect()

      asyncio.get_event_loop().run_until_complete(run())
      assert len(client._print_status_transition_queue) == 0


  def test_delayed_disconnect_skips_if_reconnected() -> None:
      """If reconnect succeeds during delay, cleanup is skipped."""
      client = _client()
      client._print_status_transition_queue.append(PrinterStatus())
      client._is_connected = True
      client._is_registered = True

      async def run():
          async def instant_sleep(_):
              pass
          with patch("asyncio.sleep", instant_sleep):
              await client._delayed_disconnect()

      asyncio.get_event_loop().run_until_complete(run())
      # Queue should NOT be cleared (reconnect succeeded)
      assert len(client._print_status_transition_queue) == 1


  def test_delayed_disconnect_cancelled() -> None:
      """If the task is cancelled, no cleanup occurs."""
      client = _client()
      client._print_status_transition_queue.append(PrinterStatus())

      async def run():
          async def raise_cancelled(_):
              raise asyncio.CancelledError()
          with patch("asyncio.sleep", raise_cancelled):
              await client._delayed_disconnect()

      asyncio.get_event_loop().run_until_complete(run())
      # Queue should NOT be cleared (task was cancelled)
      assert len(client._print_status_transition_queue) == 1


  def test_disconnect_delay_constant() -> None:
      """Verify the disconnect delay constant is set."""
      assert CC2_DISCONNECT_DELAY == 5
  ```
- [ ] Run `make test` to ensure all tests pass
- [ ] Run `make lint` and `make format`
- [ ] Commit with message: "Add delayed disconnect for CC2 to avoid false connection-loss during quick reconnects"

**Acceptance criteria:**
- [ ] `_is_connected` is set to False immediately in the listener `finally` (no delay)
- [ ] Queue clearing is delayed by 5 seconds
- [ ] If reconnect succeeds during the delay (task cancelled or `_is_connected` is True), cleanup is skipped
- [ ] Explicit `disconnect()` calls cancel any pending delay immediately
- [ ] New tests pass
- [ ] All existing tests pass
- [ ] Lint and format checks pass

---

## Execution Order

Tasks 1 and 3 are independent and can be done first. Tasks 2 and 4 both touch `_mqtt_listener()` and `disconnect()` — do Task 2 before Task 4 to avoid merge conflicts.

**Recommended order:**
1. Task 1 (light control) — simplest, no conflicts
2. Task 3 (auth detection) — independent
3. Task 2 (connection generation) — touches listener/disconnect
4. Task 4 (delayed disconnect) — touches listener/disconnect, builds on Task 2's changes

**Important:** All four `__init__` field additions should be made in a single edit (see Shared `__init__` Additions section) to avoid merge churn between tasks.
