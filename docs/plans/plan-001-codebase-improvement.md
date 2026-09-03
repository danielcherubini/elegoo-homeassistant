# Codebase Improvement Plan

**Goal:** Implement all 11 findings from `docs/reviews/2026-08-31-codebase-improvement.md` in 7 ordered, commit-able tasks: harden the ws/cc2/mqtt transport clients and fix the ws last-task sort bug, add a test harness and transport coverage, extract the shared SDCP transport base, add typed wire contracts, split the oversized definition/config-flow modules, and apply a batch of low-risk cleanup.

**Architecture:** Behavior-preserving hardening fixes first (≈20 lines of production code + red→green tests), then a zero-production-line test foundation (pytest-asyncio + a shared conftest), then coverage for the previously-untested transports (mqtt, coordinator, broker, 8 entity platforms) with two minimal additive injector seams, then a mechanical extraction of the SDCP client plumbing into `sdcp/transport/` (concrete classes stay in their current modules so `api.py` / `config_flow.py` diff to zero), then a 0-logic-line TypedDict wire-contract tranche, then the definitions/config-flow split, and finally a lint-verified cleanup batch (import edges, dead code, naming, exception tuples).

**Tech Stack:** Python 3.13, pytest (newly +pytest-asyncio), ruff via `make format` / `make lint`, `make test` (pytest), uv (`uv add --dev`), aiohttp, aiomqtt, paho-mqtt, Home Assistant custom-component conventions. Verification source of truth: `make format && make lint && make test` plus the `ty` diagnostics baseline (Task 5).

**Ground rules for every task:**
- Read the named code sections before editing; the instructions reference code *content* (guards, method names) not line numbers — line numbers shift between tasks, so locate anchors by content.
- Never change the serialized wire shapes (MQTT/CC2/SDCP payloads) or the config-entry state shape. Behavior changes are allowed ONLY where a task explicitly says "fix".
- Do not reformat untouched files; `make format` diffs must only touch files this task modified.
- Run the exact commands listed in Steps; on any unexpected failure STOP and report.

---

### Task 1: Harden ws/cc2/mqtt clients + fix ws last-task sort bug (F2, F1, F5, F11-bonus)

**Context:**
This codebase has three printer transport clients: `websocket/client.py` (`ElegooPrinterClient`), `cc2/client.py` (`ElegooCC2Client`), `mqtt/client.py` (`ElegooMqttClient`). Two latent bugs and one live bug were verified during review: (a) the ws `_ws_listener()` message handler calls `_parse_response(msg.data)` with no guard, and `_parse_response` only catches `json.JSONDecodeError` — any other exception (e.g. a malformed frame reaching a model that raises) propagates out of the `async for` loop, is re-raised as `ElegooPrinterConnectionError`, and kills the listener; the listener task has no done-callback and ws `disconnect()` cancels it **without awaiting**, so every death leaks a "never retrieved" exception; (b) ws `disconnect()` therefore cannot contain a terminal listener exception, and the same containment is missing from the **mqtt** `disconnect()` listener await (cc2's already has the `contextlib.suppress` await, but that await can still re-raise a stored non-CancelledError exception); (c) the ws `get_printer_last_task` / `async_get_printer_last_task` select the latest task via `task.end_time or 0 if task else 0`, which puts `int 0` and `datetime` objects into the SAME `max()` comparison → `TypeError` whenever one finished task and one unfinished task coexist in `print_history` (the cc2/mqtt equivalents use `task.end_time.timestamp() if task and task.end_time else 0.0` — this task makes ws match them). Finally, this task names the per-transport `is_connected` clause (`_transport_open()`) on all three clients so the Stage-2 base contract (Task 4) has a documented seam. A latent `IndexError` in ws `_parse_response` (`topic.split("/")[1]` on a 1-segment topic) is guarded here too.

**Files:**
- Modify: `custom_components/elegoo_printer/websocket/client.py`
- Modify: `custom_components/elegoo_printer/cc2/client.py`
- Modify: `custom_components/elegoo_printer/mqtt/client.py`
- Create: `custom_components/elegoo_printer/tests/test_client_hardening.py`

**What to implement:**

1. `websocket/client.py`:
   - Add `import contextlib` to the stdlib import block (sort after `import asyncio`).
   - `_ws_listener()`: in the `async for msg in self.printer_websocket:` loop, wrap the TEXT branch:
     ```python
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        self._parse_response(msg.data)
                    except Exception:
                        # A malformed frame must never kill the listener.
                        self.logger.exception("Failed to parse WebSocket message")
     ```
   - Same method, final handler: keep the heartbeat/PONG classification block as-is, but change the `else: self.logger.debug("WebSocket listener exception: %s", e)` line to `self.logger.exception("WebSocket listener exception: %s", e)` (log with traceback).
   - `disconnect()`: currently
     ```python
        if self._listener_task:
            self._listener_task.cancel()
            self._listener_task = None
     ```
     change to (IDENTICAL containment to cc2 — a terminal listener exception must not escape):
     ```python
        if self._listener_task:
            self._listener_task.cancel()
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await self._listener_task
            except Exception:
                # A terminal listener exception must never escape — failing here
                # would skip the remaining cleanup and leak the exception.
                self.logger.exception(
                    "WebSocket listener ended with an exception"
                )
            self._listener_task = None
     ```
   - `_parse_response()`: before `match topic.split("/")[1]:`, add the guard:
     ```python
            if topic:
                parts = topic.split("/")
                if len(parts) < 2:
                    self.logger.warning(
                        "Ignoring message with malformed Topic: %s", topic
                    )
                    return
                match parts[1]:
     ```
     (i.e. `match` moves one indent level under the new guard; the rest of the match block is unchanged).
   - `get_printer_last_task()` and `async_get_printer_last_task()`: both contain
     ```python
            def sort_key(tid: str) -> int:
                task = self.printer_data.print_history.get(tid)
                return task.end_time or 0 if task else 0
     ```
     In BOTH methods (sync and async) replace with:
     ```python
            def sort_key(tid: str) -> float:
                task = self.printer_data.print_history.get(tid)
                return task.end_time.timestamp() if task and task.end_time else 0.0
     ```
   - `is_connected` property: currently
     ```python
        return (
            self._is_connected
            and self.printer_websocket is not None
            and not self.printer_websocket.closed
        )
     ```
     refactor to:
     ```python
        return self._is_connected and self._transport_open()
     ```
     and add:
     ```python
    def _transport_open(self) -> bool:
        """True while the ws transport itself is open (per-transport part of is_connected)."""
        return self.printer_websocket is not None and not self.printer_websocket.closed
     ```

2. `cc2/client.py`:
   - `disconnect()`: the "Cancel listener task" block currently reads:
     ```python
        if self._listener_task:
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
            self._listener_task = None
            # The listener's finally may have created a new delay task — cancel it
            if self._disconnect_delay_task is not None:
                self._disconnect_delay_task.cancel()
                self._disconnect_delay_task = None
     ```
     Add exception containment around the await (the `delay task` sub-block keeps its current position, inside the outer `if`, after the await):
     ```python
        if self._listener_task:
            self._listener_task.cancel()
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await self._listener_task
            except Exception:
                # A terminal listener exception must never escape — failing here
                # would skip the remaining cleanup and leak the exception.
                self.logger.exception(
                    "CC2 MQTT listener ended with an exception"
                )
            self._listener_task = None
            # The listener's finally may have created a new delay task — cancel it
            if self._disconnect_delay_task is not None:
                self._disconnect_delay_task.cancel()
                self._disconnect_delay_task = None
     ```
     (`contextlib` is already imported in this file.)
   - `is_connected` property: find it (it conjunction-checks `_is_connected`, `_is_registered`, `self.mqtt_client is not None`); refactor to `return self._is_connected and self._transport_open()` with:
     ```python
    def _transport_open(self) -> bool:
        """True while the cc2 transport itself is usable (registered + client object)."""
        return self._is_registered and self.mqtt_client is not None
     ```
     Verify the property body exactly matches the refactor before editing (read it first); preserve semantics 1:1.

3. `mqtt/client.py`:
   - `disconnect()`: the listener block currently reads:
     ```python
        if self._listener_task:
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
            self._listener_task = None
     ```
     Apply the same containment as cc2 (wrap the `with contextlib.suppress(...)` / `await` pair in `try/except Exception: self.logger.exception("MQTT listener ended with an exception")`).
   - In the same `disconnect()`, the disconnect-command send currently catches:
     ```python
            except (ElegooPrinterConnectionError, ElegooPrinterTimeoutError, OSError):
     ```
     change to:
     ```python
            except (
                ElegooPrinterConnectionError,
                ElegooPrinterNotConnectedError,
                ElegooPrinterTimeoutError,
                OSError,
            ):
     ```
     and add `ElegooPrinterNotConnectedError` to this file's `from custom_components.elegoo_printer.sdcp.exceptions import (...)` block if not already imported.
   - `is_connected` property: refactor to `return self._is_connected and self._transport_open()` with:
     ```python
    def _transport_open(self) -> bool:
        """True while the mqtt transport itself is usable (client object exists)."""
        return self.mqtt_client is not None
     ```
     (read the current body first; these two expressions must be behavior-identical — if the current body differs from what's described, stop and align the refactor to the actual body.)

4. New test file `custom_components/elegoo_printer/tests/test_client_hardening.py`. Use the existing test style of `tests/test_video_lifecycle.py` / `tests/test_canvas_detection.py` (sync test functions driving coroutines via `asyncio.run`, `unittest.mock.MagicMock/AsyncMock/Mock`). Define a small local `FakeWebSocket` (async-iterable with a pre-queued frame list, `close()` / `closed` attribute, `exception()` → None) and `FakeClientSession` (`.ws_connect` returns the fake). Construct clients directly via constructors (no real network). Tests:
   - `test_ws_disconnect_suppresses_terminal_listener_exception`: build `ElegooPrinterClient` with `FakeClientSession` (MagicMock); set `client._is_connected = True`; `client._listener_task = asyncio.create_task(_terminal())` where `_terminal` is an `async def` that raises `RuntimeError("terminal")`; `await asyncio.sleep(0)` so the task actually raises and stores the exception; `await client.disconnect()` must NOT raise; assert `client._is_connected is False` AND `client.logger.exception.called` (pre-fix ws had NO containment and never logged — the test is RED pre-fix on the assert and GREEN post-fix: step 2 gives ws the same `except Exception` + `logger.exception` containment as cc2/mqtt, pinning the unified contract).
   - `test_cc2_disconnect_suppresses_terminal_listener_exception`: same pattern with `ElegooCC2Client` (construct with `ElegooCC2Client(printer_ip="192.168.1.100", serial_number="serial1", access_code=None)`), `client._is_connected = True`, terminal listener task, `await client.disconnect()` must not raise, assert `client._is_connected is False`. (RED pre-fix: cc2's existing `suppress(CancelledError)`-wrapped await re-raises the stored `RuntimeError`.)
   - `test_mqtt_disconnect_suppresses_terminal_listener_exception`: same with `ElegooMqttClient()`. (RED pre-fix: same uncontained await shape at the mqtt listener block.)
   - `test_ws_listener_survives_malformed_status_payload`: build ws client with fake ws whose frame queue is: (1) ONE malformed TEXT frame, (2) ONE valid status frame (a 2-segment `Topic` like `"x/status"` whose payload makes `PrinterStatus.from_json` raise — `from_json` → `cls(data, printer_type)`; `PrinterStatus.__init__` does e.g. `round(status.get("TempOfUVLED", 0), 2)`, so `"abc"` in such a slot raises `TypeError` — pick the payload empirically in the pre-fix run until the listener provably dies), (3) then `StopAsyncIteration`. Drive the listener to completion and assert: PRE-FIX RED = listener task completes with an exception (`task.done()` and `task.exception() is not None`) and `printer_data.status` is NOT updated; POST-FIX GREEN = `client.logger.exception` called for the malformed frame AND the valid frame was still processed (`printer_data.status` updated/`_status_handler` ran). Do NOT assert on calling `client._parse_response` directly — it still raises for model-raising shapes by design (only the listener guard must contain it).
   - `test_ws_parse_response_ignores_missing_topic_segment`: `client._parse_response(json.dumps({"Id": 1, "Topic": "status"}))` must not raise (pre-fix `IndexError`; post-fix logs a warning and returns) — assert `client.logger.warning.called` (logger is a MagicMock).
   - Sort-key regression tests — three cases, each operating on `async_get_printer_last_task` (import `PrintHistoryDetail` from `sdcp.models.print_history_detail`; `from datetime import datetime, timedelta`; construct a ws client via the same `FakeClientSession` pattern and populate `client.printer_data.print_history`). IMPORTANT: `PrintHistoryDetail.__init__` reads the wire keys `BeginTime`/`EndTime` as UNIX timestamps (`datetime.fromtimestamp(ts, tz=UTC)`); a missing/None `EndTime` leaves `end_time` as `None`. Build tasks accordingly:
     - Case A (regression catcher): `print_history = {"a": PrintHistoryDetail({}), "b": PrintHistoryDetail({"EndTime": base.timestamp()})}` — pre-fix: `async_get_printer_last_task()` raises `TypeError` (int `0` vs `datetime` in `max()`); post-fix: it returns the task whose `end_time` is non-None (task `b`).
     - Case B: both end times `None` (both `PrintHistoryDetail({})`) — post-fix: no exception; the winner is deterministic — run it, record the observed winner, and assert it.
     - Case C: two tasks with identical `EndTime` floats (a tie) — run it, record the observed winner, and assert it (this pins the tie-break behavior; `max` returns the first maximal element in iteration order).
     Do NOT test the sync `get_printer_last_task` in Task 1 — it is deleted in Task 7 (F9); only `async_get_printer_last_task` is pinned here.

**Steps:**
- [ ] Read `websocket/client.py` (imports, `_ws_listener`, `disconnect`, `_parse_response`, both `*get_printer_last_task`, `is_connected`), `cc2/client.py` (`disconnect`, `is_connected`), `mqtt/client.py` (`disconnect`, `is_connected`) to confirm each anchor matches this task text.
- [ ] Create `tests/test_client_hardening.py` with all six tests; run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_client_hardening.py`
  - Did the tests fail in the expected states (the ws + cc2 + mqtt disconnect tests all fail pre-fix — RED; malformed-payload: listener dies pre-fix; missing-topic-segment raises `IndexError`; case A raises `TypeError`)? If any red expectation misses, stop and investigate why.
- [ ] Apply the ws, cc2, mqtt production changes above.
- [ ] `make test`
  - Did all tests pass? If not, fix the failures and re-run before continuing.
- [ ] `make format`
  - Did it succeed (no unexpected reformatting of untouched files)? If not, fix and re-run.
- [ ] `make lint`
  - Did it succeed? If not, fix and re-run.
- [ ] Commit with message: `fix(clients): harden ws/cc2/mqtt disconnect and listener, fix ws last-task sort, name is_connected contract`

**Acceptance criteria:**
- [ ] `make test` fully green, including the six new red→green tests.
- [ ] `websocket.client.ElegooPrinterClient.disconnect()` awaits its cancelled listener task.
- [ ] ws `_parse_response` survives one malformed frame (no listener death); the 1-segment Topic is ignored with a warning.
- [ ] ws `sort_key` in both last-task methods is `task.end_time.timestamp() if task and task.end_time else 0.0` (return type `float`).
- [ ] All three `is_connected` properties read `self._is_connected and self._transport_open()` with per-transport `_transport_open()` semantics exactly as before.
- [ ] `ElegooPrinterNotConnectedError` is in the mqtt disconnect-command catch tuple.

---

### Task 2: Test foundation — pytest-asyncio + shared conftest (F3 Phase 1)

**Context:**
The repo currently runs pytest WITHOUT pytest-asyncio (dev deps in `pyproject.toml`: pytest, ruff, ty, pre-commit — no plugin) and has NO `conftest.py` anywhere. Tests under `custom_components/elegoo_printer/tests/`, `cc2/tests/`, `websocket/tests/` are sync functions that drive coroutines via `asyncio.run(...)`. Three fixture definitions (`mock_logger`, `mock_printer_registry`, `sample_printer`) are copy-pasted in `websocket/tests/test_discovery.py` and `test_proxy.py`; `websocket/tests/test_registry.py` defines only **two** of the three (`mock_logger`, `sample_printer`) PLUS a separate real-instance `printer_registry` fixture that must STAY. Several files hand-roll a minimal `hass` double (bare `MagicMock()` — e.g. `tests/test_api_discovery.py`, `tests/test_canvas_detection.py`, `tests/test_config_flow_*options*.py`). `pyproject.toml` already has `[tool.pytest.ini_options]` with `pythonpath = ["."]`, `testpaths = ["custom_components"]`, `norecursedirs = ["scripts"]`. This task adds the plugin and ONE consolidated conftest so later tasks (3–4) can write async tests with shared fixtures. Zero production code changes in this task. `pytest-homeassistant` is intentionally NOT added this tranche (heavy, version-pinned; revisit when `config_flow.py` testing is needed). Test command convention for this plan: `VIRTUAL_ENV=.venv uv run pytest <paths>` (bare `python -m pytest` cannot run in this repo).

**Files:**
- Modify: `pyproject.toml` (dev dependency + pytest ini options)
- (auto-managed by `uv`): `uv.lock`
- Create: `custom_components/elegoo_printer/conftest.py`
- Modify: `custom_components/elegoo_printer/websocket/tests/test_discovery.py` (remove local fixture defs)
- Modify: `custom_components/elegoo_printer/websocket/tests/test_proxy.py` (remove local fixture defs)
- Modify: `custom_components/elegoo_printer/websocket/tests/test_registry.py` (remove ONLY `mock_logger` + `sample_printer`; keep `printer_registry` (real `PrinterRegistry` instance) and its `mock_printer_registry` if defined locally)

**What to implement:**
1. `uv add --dev pytest-asyncio` (uv writes `pyproject.toml` + `uv.lock`; pin whatever version uv resolves).
2. `pyproject.toml` `[tool.pytest.ini_options]`: add `asyncio_mode = "auto"`. The existing sync tests use `asyncio.run` inside plain `def test_...` functions — these keep working under auto mode (auto mode only converts `async def test_...`, of which there are currently zero).
3. `custom_components/elegoo_printer/conftest.py` with:
   - `mock_logger` → `unittest.mock.MagicMock()` (fixture).
   - `mock_printer_registry` → `unittest.mock.Mock(spec=PrinterRegistry)` (import `PrinterRegistry` from `custom_components.elegoo_printer.websocket.server.registry`).
   - `sample_printer` → verbatim copy of the `sample_printer` fixture body from `websocket/tests/test_discovery.py` (the `Printer(json.dumps({...}))` with `MainboardID test_mainboard_id_12345`).
   - `hass` → bare `MagicMock()` (the repo's established minimal shape; no coroutine attributes needed by Tasks 1–7) PLUS `hass.bus = MagicMock()` (pre-wired) for consumers that touch the bus (e.g. `DataUpdateCoordinator.__init__`'s `bus.async_listen_once`). Comment: "minimal HA double — replace with the pytest-homeassistant `hass` fixture when that plugin is adopted".
   - `entry` → a SEPARATE `types.SimpleNamespace` fixture: `.data` (dict — read `data.py`/`config_flow.py` for the exact minimal keys the coordinator reads, e.g. `printer_id`; `CONF_HAS_CANVAS` may also come from `.options`), `.options` (dict), `.title` (str, "Test Printer"), `.runtime_data` (→ the `runtime_data` fixture). Consumers read everything through `entry.runtime_data.*`.
   - `runtime_data` → `types.SimpleNamespace`: `.api` (a `MagicMock` with `AsyncMock` attributes for `async_get_printer_data`, `async_get_firmware_update_info`, `async_get_canvas_status`, `reconnect`, `is_thumbnail_available`), `.client` (a `MagicMock`), `.printer_data` (a real `PrinterData` from `sdcp.models.printer` with a real `Printer` so `printer.transport_type`/`printer_type` resolve — build from `sample_printer`), `.coordinator` (a `MagicMock` whose `.config_entry` is the same `entry` object), `.integration` (a `MagicMock`).
   - `ws_client` → a real `ElegooPrinterClient` with a fake session: `ElegooPrinterClient(ip_address="192.168.1.100", session=FakeClientSession(), logger=MagicMock(), config=MappingProxyType({...printer json...}))` where `FakeClientSession.ws_connect` returns a `FakeWebSocket` async-iterable (same pattern as `tests/test_canvas_detection.py` ws mocks). Put `FakeWebSocket` / `FakeClientSession` at module level of the conftest; tests that need a raw fake use the `ws_client` fixture (if a specific test needs to poke fakes directly, it constructs its own local copy — do NOT add an extra module in this task; the `ws_client` fixture is the shared seam).
4. Remove the duplicated fixtures: `mock_logger`, `mock_printer_registry`, `sample_printer` from `test_discovery.py` and `test_proxy.py`; from `test_registry.py` remove ONLY `mock_logger` + `sample_printer` (verify each removed fixture's parameter name matches; keep `printer_registry` untouched).

**Steps:**
- [ ] `uv add --dev pytest-asyncio`
  - Did it succeed and update `pyproject.toml` + `uv.lock`?
- [ ] Add `asyncio_mode = "auto"` to `[tool.pytest.ini_options]`.
- [ ] Create `custom_components/elegoo_printer/conftest.py` with the fixtures above.
- [ ] Remove the duplicated fixture definitions per item 4.
- [ ] `VIRTUAL_ENV=.venv uv run pytest custom_components`
  - Did the full suite stay green (no big-bang failure)? If any test now fails due to the fixture change, restore the removed fixture in that file and investigate.
- [ ] `make format`, `make lint` — both green.
- [ ] Commit with message: `test: add pytest-asyncio and shared conftest with transport fixtures`

**Acceptance criteria:**
- [ ] `uv add --dev pytest-asyncio` updated `pyproject.toml` and `uv.lock`.
- [ ] `asyncio_mode = "auto"` set; a one-off `async def test_x()` probe passes under `VIRTUAL_ENV=.venv uv run pytest -k probe` — then delete the probe (do not commit the probe).
- [ ] `custom_components/elegoo_printer/conftest.py` exists with `mock_logger`, `mock_printer_registry`, `sample_printer`, `hass`, `ws_client`, `runtime_data`; the three `websocket/tests/` files no longer define the three local fixtures.
- [ ] Full `make test` green (all pre-existing tests pass unchanged).
- [ ] Zero production (non-test) files modified in this task.

---

### Task 3: Transport coverage tranche (F3 Phase 2)

**Context:**
Tasks 1–2 fixed the hard bugs and installed the test foundation. This task adds the actual coverage for the transports that currently have none: the whole `mqtt/client.py` (951 lines, 0 tests) and `cc2/client.py` (1486 lines; its test files in `cc2/tests/` cover the CC2-only sides — auth fallback, connection generation, delayed disconnect, light control, print-status queue, gcode proxy — but not the shared SDCP plumbing), the coordinator (0 tests), 8 of 9 entity platforms (camera is already tested in `tests/test_camera.py`), and the singleton MQTT broker (`mqtt/server.py`, 0 tests). Two minimal additive seams are required to make the mqtt/cc2 connect path testable: a `client_factory` constructor parameter (defaults to `aiomqtt.Client`, behavior-identical by default) and a broker `_reset_for_tests()` seam. No behavior changes: every characterization test asserts the CURRENT behavior (a failure here = a previously-unknown bug → stop and report).

**Files:**
- Modify: `custom_components/elegoo_printer/cc2/client.py` (add `client_factory` param; ~6 lines)
- Modify: `custom_components/elegoo_printer/mqtt/client.py` (add `client_factory` param; ~6 lines)
- Modify: `custom_components/elegoo_printer/mqtt/server.py` (add `_reset_for_tests()` classmethod; ~4 lines)
- Create: `custom_components/elegoo_printer/tests/test_mqtt_client.py`
- Create: `custom_components/elegoo_printer/cc2/tests/test_client_factory.py`
- Create: `custom_components/elegoo_printer/tests/test_coordinator.py`
- Create: `custom_components/elegoo_printer/tests/test_entity_setup.py`
- Create: `custom_components/elegoo_printer/tests/test_broker.py`
- Create: `custom_components/elegoo_printer/websocket/tests/test_ws_client_lifecycle.py`

**What to implement:**

1. `client_factory` seam (both clients):
   - `cc2/client.py.__init__`: append `client_factory: Callable[[dict[str, Any]], Any] | None = None` as a keyword param (import `Callable` from `collections.abc`); store `self._client_factory = client_factory`. In `connect_printer()`, the line `self.mqtt_client = aiomqtt.Client(**client_kwargs)` becomes:
     ```python
            client_cls = self._client_factory or aiomqtt.Client
            self.mqtt_client = client_cls(**client_kwargs)
     ```
     (the following `await self.mqtt_client.__aenter__()` stays).
   - `mqtt/client.py.__init__`: append the same `client_factory` param; same connect change (currently `self.mqtt_client = aiomqtt.Client(**client_kwargs)` + `await self.mqtt_client.__aenter__()`).
   - Default behavior must be identical (factory None → `aiomqtt.Client`).
2. `FakeAiomqttClient` (a local class in `tests/test_mqtt_client.py`, imported by the cc2 test file so ONE implementation is shared): an async-context-manager-like object (NOT a real `aiomqtt.Client` subclass): `__init__(**kwargs)` records `self.kwargs`; `__aenter__` sets `self.connected = True` and returns self; `__aexit__(exc_type, exc, tb)` sets `self.connected = False`; `subscribe(topic)` records into `self.subscribed: list[str]`; `publish(topic, payload)` records; **`message()` items MUST carry `.topic` (str)** (both clients do `str(message.topic)`) — use `types.SimpleNamespace(topic=…, payload=bytes)`; `disconnect()` no-op. The factory passed in tests: `lambda **kw: FakeAiomqttClient(**kw)`. CRITICAL: the clients `await asyncio.wait_for(event.wait(), 10)` on `_send_printer_cmd` responses with UNPREDICTABLE `RequestID`s (`secrets.token_hex(8)`) — a silent fake blocks 10 s per command and then raises uncaught `ElegooPrinterTimeoutError` out of `connect_printer`. So the connect tests must: (a) capture the `publish()` payloads recorded by the fake, extract the `Data.RequestID` (and `Data.Cmd`) from each, and (b) inject matching response frames (for mqtt, topics route on `topic.split("/")[2] == "response"` — i.e. a leading-slash `response` topic like `/sdcp/response/<id>`; for cc2, the routing string is `api_response`; follow the code's routing with the fake's recorded `message().topic`, payload carrying that `RequestID`) into the message queue. Also pass `access_code="x"` (cc2) or otherwise avoid the password-fallback loop (read the code for the exact skip condition).
3. `test_mqtt_client.py` (characterization + failure paths):
   - connect-success: `ElegooMqttClient(mqtt_host="127.0.0.1", mqtt_port=1883, logger=MagicMock(), printer=printer_mock, client_factory=fake)` → `await connect_printer()` (verify the live signature before writing — ws SDCP `connect_printer` MAY take a `printer` argument while mqtt's does not; follow the code) → assert `is_connected True`, `fake.connected is True`, `len(fake.subscribed) == 5`, and the five subscribed strings CENTRAL-assert the leading-slash format (`f"/{TOPIC_PREFIX}/{TOPIC_*}/{id}"` — read `mqtt/client.py` for the exact topic constants; this test is the pin for the per-transport topic divergence). Pre-seed responses per the RequestID-extraction above for the 3 handshake `_send_printer_cmd`s (status refresh / attributes / status-update-period).
   - connect-failure: make the fake `__aenter__` raise `OSError` → pin the code's exact contract (read the `connect_printer` except-handling first; assert `is_connected is False` + whatever return/raise the code defines).
   - disconnect-unblock: connected fake; seed a `client._response_events` entry with an `asyncio.Event()`; `await client.disconnect()` → event is set, `is_connected False`, `mqtt_client is None`.
   - send_command future: pre-seed a matching `RequestID` response → `await client._send_printer_cmd(...)` completes and `fake` recorded the published frame; on no response → `ElegooPrinterTimeoutError` raised.
   - push handling: feed one of each status/attributes/print_history/print_video frame (JSON via `message()`) → assert `printer_data` updated fields (each a separate test function; NOTE: the ams path is CC2-specific — see Task 4's handler list).
4. `cc2/tests/test_client_factory.py`: same contract on `ElegooCC2Client` (connect → pin the current contract: `_is_connected True`, subscribe called for the cc2 topic set, listener task created). Pre-seed: a registration response frame (topic containing `register_response`, payload `{"error": "ok"}` — `CC2_REG_OK = "ok"`, `cc2/const.py`; `CC2_REGISTRATION_TIMEOUT = 3 s`) and `api_response` frames for `_request_counter` ids 1 and 2 (`GET_STATUS`/`GET_ATTRIBUTES` — ids are PREDICTABLE: `self._request_counter` increments from 0 on each send, `cc2/client.py` `_request_initial_data`) so `_request_initial_data()` doesn't wait 10 s per command; use `access_code="x"` to skip the password-fallback loop. Disconnect-unblock + send_command-future + push-handling mirrors of the mqtt tests.
5. `test_coordinator.py` (mock-only, conftest `hass`/`entry`/`runtime_data`):
   - Happy path: `entry.runtime_data.api.async_get_printer_data` → `PrinterData`; `await coordinator.async_refresh()` → `coordinator.data` not None, `coordinator.online is True`, `update_interval == timedelta(seconds=2)`. NOTE: on the first refresh the firmware path (`_last_firmware_check is None`) also calls `api.async_get_firmware_update_info()` — the mock must tolerate it.
   - Connection-failure: `async_get_printer_data` raises `ElegooPrinterConnectionError` → `online False`, interval 30s. IMPORTANT: the `reconnect()` mock must raise `ConnectionError` (or nothing) — `coordinator.py` only catches `(ConnectionError, TimeoutError)` around reconnect, so an `ElegooPrinterConnectionError` from the mock would escape uncaught.
   - has_canvas: set via `entry.data`/`entry.options` `CONF_HAS_CANVAS` (read at `__init__` from `{**entry.data, **(entry.options or {})}`) — the entry double must expose both accordingly; with canvas True + canvas service down → canvas check skipped, `_last_canvas_check` still updated on failure (rate-limited path).
   - CC2 early-return: `runtime_data.api.client` is a non-cc2 `MagicMock` → `_replay_cc2_print_status_transitions()` no-op (assert no attribute access side effects). Note: post-Task-7 the guard is `transport_type != TransportType.CC2_MQTT` — pin on the mock's `printer.transport_type`.
   - Interval flip on each success/failure.
6. `test_entity_setup.py` (parametrized × the 8 untested platforms — the component's 9 platforms are `sensor, binary_sensor, image, camera, light, button, fan, select, number`; `camera` is already tested in `tests/test_camera.py`; the other 8 are **`binary_sensor, button, fan, image, light, number, select, sensor`**). Platforms are TOP-LEVEL MODULES `custom_components/elegoo_printer/<platform>.py` (not packages). HA 2025.4 `async_setup_entry(hass, entry, async_add_entities)` — the callback is the **third argument** (an `AsyncMock` passed in, NOT `hass.async_add_entities`). Per platform read `custom_components/elegoo_printer/<platform>.py` for the exact signature and the `printer_type`-gated composition (e.g. FDM-only tuples), construct `printer` in the conftest `runtime_data.printer_data.printer` accordingly, call the setup, assert `async_add_entities` was called with `len(entities) >= 1`.
7. `test_broker.py`:
   - `_reset_for_tests()` seam in `mqtt/server.py` — the actual singleton state (verified in `mqtt/server.py`): class-level `_instance`, `_reference_count`, `_lock`; accessor methods `get_instance()` / `release_instance()`; `start()` takes NO arguments (binds `MQTT_BROKER_PORT = 18830`); `__init__(host=…, port=…)` allows a direct instance. The seam:
     ```python
    @classmethod
    def _reset_for_tests(cls) -> None:
        """Reset the singleton for tests only."""
        cls._instance = None
        cls._reference_count = 0
     ```
   - Refcount test: two `get_instance()` calls → same object; `release_instance()` ×2 → `get_instance()` re-creates fresh.
   - ONE ephemeral-port-ish integration test: construct the broker DIRECTLY (bypass the singleton port-18830 assumption if the class allows a per-instance port — read `__init__`/`start`; e.g. `ElegooMQTTBroker(port=29999)` or whatever the real constructor supports), `await start()`, publish one message through the broker API, read it back via a subscription (paho-mqtt is in dev deps — a real round-trip is feasible), then stop. If a publish round-trip cannot complete without a real paho listener thread, fall back to asserting `start()` resolves / `stop()` cleans up the state — do NOT weaken to zero tests.
8. `websocket/tests/test_ws_client_lifecycle.py`:
   - connect via the fake session (ws_connect resolves the fake ws) → `is_connected True`, `_listener_task` created.
   - double-connect: pin the current early-return behavior (read the code) — assert exactly one ws_connect call and `_is_connected` state per the code.
   - disconnect: waiters unblocked, `is_connected False`, ws `close()` called on the fake.
   - connect-failure: `ws_connect` raises `aiohttp.ClientError` → `connect()` returns per the code (False or raise — pin what exists), `is_connected False`.

**Steps:**
- [x] Add the seams (`client_factory` × 2, broker `_reset_for_tests`) and verify `VIRTUAL_ENV=.venv uv run pytest custom_components` is still green (default behavior unchanged).
- [x] Create `tests/test_mqtt_client.py` with `FakeAiomqttClient` + the five test groups; run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_mqtt_client.py`.
  - Did all pass (characterization = pin current behavior)? Any failure = a previously-unknown bug: stop, report (do NOT silently change production code in this task).
- [x] Create `cc2/tests/test_client_factory.py` (reuse the fake via import); same run; same red gate.
- [x] Create `tests/test_coordinator.py`; run; same red gate.
- [x] Create `tests/test_entity_setup.py`; run; same red gate.
- [x] Create `tests/test_broker.py`; run; same red gate (fallback per item 7 allowed).
- [x] Create `websocket/tests/test_ws_client_lifecycle.py`; run; same red gate.
- [x] `VIRTUAL_ENV=.venv uv run pytest custom_components` (full suite), `make format`, `make lint` — all green.
- [x] Commit with message: `test: cover ws/cc2/mqtt clients, coordinator, 8 entity platforms, broker (+ minimal seams)`

**Acceptance criteria:**
- [x] `ElegooCC2Client` / `ElegooMqttClient` accept an optional `client_factory`; default path unchanged (all pre-existing tests still pass).
- [x] `ElegooMQTTBroker._reset_for_tests()` exists and is used by `test_broker.py`.
- [x] The eight new/updated test files above exist and pass; the mqtt pin asserts the exact 5 leading-slash topic strings.
- [x] Full `make test` green; total test count up by ~30 functions (suite baseline: 398 tests before this tranche).

---

### Task 4: Extract the shared SDCP transport base (F4 Stage 2)

**Context:**
Tasks 1–3 left the three clients hardened and pinned. cc2 is a distinct wire (its own id scheme, registration, heartbeat, delayed disconnect) and does NOT inherit the base (that graft is the optional, deferred F4 Stage 3 — out of scope). This task extracts the shared machinery between `ElegooMqttClient` and `ElegooPrinterClient` — the SDCP `_send_printer_cmd` framing + response-event registry, the shared push handlers, the shared task-accessor methods, the UDP discovery skeleton, and the connect/disconnect plumbing — into a new `sdcp/transport/` package. IMPORTANT reviewer-corrected facts: the SDCP pair has only FOUR shared push handlers — `_print_history_handler`, `_print_history_detail_handler`, `_print_video_handler`, `_attributes_handler` — and they are NOT byte-identical (mqtt carries extra debug-print lines; mqtt's `_attributes_handler` has a `Data→Attributes` wrapper extraction). There is NO `_ams_status_handler` in ws or mqtt (the ams path is CC2-specific; ws's `_canvas_handler` is Ack-gated + wrapped in try/except — DO NOT merge it into the attributes-style handler and do not drop its try/except). Dispatch differs per transport: ws routes via `_parse_response` topics + `_response_handler` by `cmd`; mqtt routes via a topic match on the leading-slash topics. The base is a plain class `SdcpPrinterClient` (NOT `abc.ABC`); both concrete classes stay in their modules and subclass it, so `api.py` and `config_flow.py` diff to ZERO. The base must be stdlib + sdcp-models only — no aiohttp, no aiomqtt, no paho (enforced by a STATIC import-line check, see item 4 — the component `__init__.py` imports aiohttp at runtime, so a `sys.modules` assertion is guaranteed red for unrelated reasons). Per-transport divergence is behavior-preserved and pinned by Task 3's tests: ws's `_parse_response` topic guard (Task 1) stays in ws; mqtt's leading-slash topic format stays in mqtt; the status-payload extraction becomes the 4-case union chain below (including the skip case) — write all 4 equivalence tests BEFORE the merge.

**Files:**
- Create: `custom_components/elegoo_printer/sdcp/transport/__init__.py`
- Create: `custom_components/elegoo_printer/sdcp/transport/base.py`
- Create: `custom_components/elegoo_printer/sdcp/transport/discovery.py`
- Modify: `custom_components/elegoo_printer/mqtt/client.py`
- Modify: `custom_components/elegoo_printer/websocket/client.py`
- Modify: `custom_components/elegoo_printer/sdcp/const.py` (add `SDCP_COMMAND_TIMEOUT = 10`)
- Create: `custom_components/elegoo_printer/sdcp/tests/test_transport_base.py`
- Create: `custom_components/elegoo_printer/tests/test_client_conformance.py`
- Create: `custom_components/elegoo_printer/tests/test_import_sdcppack.py`
- DO NOT MODIFY: `api.py`, `config_flow.py`, `coordinator.py`, `cc2/client.py`

**What to implement:**

1. `sdcp/transport/discovery.py` (stdlib only: `socket`, `json`, `ipaddress`) — CORRECTED to match the real code (there is NO md5/thumbprint check anywhere; the three `discover_printer` methods are SYNCHRONOUS blocking-socket, invoked via `hass.async_add_executor_job` in `api.py`; ws parses via `Printer(printer_info)`; ws's real local filter is `p.ip_address == local_ip and ("None" in p.name or "Proxy" in p.name)`; mqtt has NO local-IP filter):
   - `def parse_printer_discovery_response(data: bytes) -> Printer | None` — module-level, synchronous; reproduces the current UDP response parsing (a malformed packet returns None).
   - `async def discover_printers(timeout: float) -> list[Printer]` — the UDP-broadcast skeleton (SO_BROADCAST socket → broadcast `DISCOVERY_MESSAGE` from `custom_components.elegoo_printer.const` → `recvfrom` loop until timeout → each frame through the parse function). NOTE: it is fine to implement the broadcast as a synchronous helper the per-transport methods call from an executor (the existing methods are sync); do NOT invent an async API nothing calls. Keep the per-transport `discover_printer` methods on the classes, delegating to the shared skeleton, and KEEP each transport's local filter in the class (ws name-based filter; mqtt's absence).
2. `sdcp/transport/base.py` — `class SdcpPrinterClient:` (plain class, NOT `abc.ABC`):
   - `def __init__(self, logger: Any, printer: Printer, gcode_proxy: Any = None) -> None:` — the shared state block (read the cc2 `__init` list at the top of the file for the exact field set per review): `self.logger = logger`; `self.printer = printer`; `self.printer_data = PrinterData(printer=printer)`; `self._gcode_proxy = gcode_proxy` (the base owns this private name — do NOT add a second `self.gcode_proxy` beside it); `self._is_connected: bool = False`; `self._listener_task: asyncio.Task | None = None`; `self._response_events: dict = {}`; `self._response_data: dict = {}`; `self._response_lock = asyncio.Lock()`. Fields that differ per transport (ws: `printer_websocket`, `_session`, ip, gcode-filename-keyed retry state; mqtt: `mqtt_host/port/advertise_host`; cc2-only ones: generation, board-id, heartbeat) stay in the subclasses — the base owns only the shared state + the shared helpers below. Where a concrete class' `__init__` currently sets these fields inline, it keeps doing so for its own extras and calls `super().__init__(logger, printer, gcode_proxy=...)` for the shared block (read each class' `__init__` to decide the minimal overlap; do NOT change constructor signatures — `api.py`/`config_flow.py` must not change).
   - `async def _send_printer_cmd(self, client: int, data: dict | None = None) -> None:` — shared body per review: create an `asyncio.Event()` under `_response_lock`, register it in `_response_events`, serialize the frame via the REAL request shape (read the current code — ws `client.py:447–460` / mqtt `client.py:706–719`: `{"Id": self.printer.connection, "Data": {"Cmd": …, "Data": data, "RequestID": token_hex(8), "MainboardID": …, "TimeStamp": …, "From": 0}, "Topic": f"sdcp/request/{id}"}` (ws) / `f"/sdcp/request/{id}"` (mqtt — leading-slash pin); no validator step exists), `await self._publish_frame(frame)`, `await asyncio.wait_for(event.wait(), SDCP_COMMAND_TIMEOUT)` (imported from `sdcp.const` — item 5), raise `ElegooPrinterTimeoutError` on timeout. (cc2 keeps `CC2_COMMAND_TIMEOUT = 10` locally — same value, different name; both coexist.)
   - Callback cleanup for the ws keyed-retry / `gcode_filament` state stays in ws (read the current `_response_handler`/gcode-retry code; only the shared event-registry + `_send_printer_cmd` move).
   - `async def _publish_frame(self, frame: str, topic_prefix: str = "") -> None:` — base body: `raise NotImplementedError("publish_frame must be implemented by the transport")`.
   - `def _request_topic(self, prefix: str) -> str:` — base: return `prefix`; mqtt overrides to `"/" + prefix` (leading-slash pin — comment: "leading slash is required to match the printer's subscription pattern — do NOT 'fix'"); ws keeps the base (`""`).
   - `def _status_payload_extract(self, data: dict[str, Any]) -> dict[str, Any] | None:` — the union chain with an explicit SKIP case (returns `None` = handler must skip/no-update):
     ```python
        inner = data.get("Data")
        if isinstance(inner, dict) and "Status" in inner:
            return inner["Status"]
        if "Status" in data:
            return data["Status"]
        if isinstance(inner, dict):  # has Data but no Status inside, no top-level Status
            return None  # the old mqtt _status_handler SKIPS this shape — preserve that
        return data  # flat payload (the old ws whole-frame shape is subsumed)
     ```
     EQUIVALENCE FIRST: before touching the clients, add 4 tests to `test_transport_base.py` proving (a) `{Data: {Status: {...}}}` → inner Status [old mqtt], (b) a frame dict with no `Data` key and no `Status` key → itself [old ws], (c) `{Status: {...}}` → inner [mqtt inner shape], (d) `{Data: <dict without Status>}` and no top-level `Status` → `None` (skip — the old mqtt no-update case; a regression the naive 3-case chain would turn into a near-default overwrite). Run against a standalone copy of the function (module-level, no client) before the merge. The base status handler checks for `None` and skips with a warning. CORNER JUDGMENT (reviewer-verified, intentional): a status-topic frame with NEITHER `Data` NOR `Status` keys is warn+SKIPPED by old mqtt (unknown-format branch) but the chain returns the whole frame (ws subsumption, case (b)); pin it with an (e) equivalence test "no-Data, no-Status frame → itself" and change the old mqtt unknown-format warning to at most a debug log in the base handler.
   - `def _handle_push_frame(self, topic: str, data: dict) -> None:` — router over the SHARED handler bodies moved here: `print_history` → `_print_history_handler(data)`; `print_history_detail` → `_print_history_detail_handler(data)`; `elegoo_video` → `_print_video_handler(data)`. The attributes dispatch is PER-TRANSPORT (read the three dispatch sites before moving: ws `_response_handler` routes by `cmd` + `_parse_response` by topic; mqtt routes by topic match; each has its own wrapper — ws's `_canvas_handler` is Ack-gated + try/except and STAYS in ws with its guard intact; do not merge it). Where ws/mqtt routing differs, keep the transport-specific routing in the class and move ONLY the true shared handler bodies + shared prerequisites.
   - Task accessors (moved verbatim): `get_printer_current_task`, `get_printer_last_task`, `async_get_printer_last_task`, `get_printer_task_detail`, `async_get_printer_historical_tasks`, `get_printer_video`, `set_printer_video_stream`, and the `_gcode_filament*` shared bits IF they are structurally identical across ws/mqtt (read first; where ws has the keyed-retry scheduler and mqtt does not, the scheduler stays in ws and only the shared HTTP fetch helper moves).
   - `async def disconnect(self) -> None:` — the Task-1 shape, now written ONCE: `_disconnect_pre()` hook → listener cancel + suppress-await + except-Exception containment → waiters unblock (per-class: `_response_events` clear, and `_response_data.clear()` where the class has it) → `await _on_disconnect()` → reset flags. Concrete hooks: `async def _disconnect_pre(self) -> None: pass`, `async def _on_disconnect(self) -> None: pass` — mqtt overrides `_disconnect_pre` (send `CMD_DISCONNECT` guarded by the Task-1 catch tuple) and `_on_disconnect` (the `__aexit__` cleanup, guarded as today); ws overrides `_on_disconnect` (the ws `close()`, guarded as today).
   - `async def _start_listener(self) -> None` / `async def _listen(self) -> None` — `task = asyncio.create_task(self._listen())` / base `_listen` raises NotImplementedError; ws `_listen` = current `_ws_listener` body (with Task-1 guard); mqtt `_listen` = current `_mqtt_listener` body.
3. Client edits: `ElegooMqttClient(SdcpPrinterClient)` and `ElegooPrinterClient(SdcpPrinterClient)` — remove the moved bodies; keep class names / module paths identical; `__init__` calls `super().__init__(logger, printer, gcode_proxy=...)` first (WS: preserve `ip_address`/`session`/config handling; MQTT: preserve host/port/advertise/factory handling; CC2 untouched).
4. Tests:
   - `sdcp/tests/test_transport_base.py`: the 3 equivalence tests for `_status_payload_extract` (above, standalone), `_send_printer_cmd` (event registration, future resolution via a fake `_publish_frame`, timeout raising `ElegooPrinterTimeoutError`).
   - `tests/test_client_conformance.py`: `CLIENT_FACTORIES` (ws: conftest-style `ws_client` construction; mqtt: `ElegooMqttClient(client_factory=fake)`; cc2: `ElegooCC2Client` construct-only — no connect). Per client: construction (no network), `is_connected False` pre-connect — PLUS, for ws + mqtt only, the 3 `_handle_push_frame`-routed handler pins (`_print_history_handler` → `printer_data.print_history`, `_print_history_detail_handler` → `printer_data.print_history_detail`, `_print_video_handler` → `printer_data.elegoo_video`) via each transport's own routing (ws `_parse_response`-style topic frame / mqtt leading-slash topic) with representative payloads, and status + attributes via each transport's own status routing hitting the 4-case-chain happy cases. cc2 has NO `_handle_push_frame` (its dispatch is `_handle_message` over `elegoo/…` topics — outside the base's contract) so it gets the construction/flag pins only. The AMS pin is ws-only via `_canvas_handler` (Ack-gated) — never via `_handle_push_frame`.
   - `tests/test_import_sdcppack.py` — IMPORT-TIME check via STATIC import-line inspection (the component `__init__.py` imports aiohttp + the ws client at runtime, so a `sys.modules` assertion is guaranteed red for unrelated reasons — the static check IS the spec): read `sdcp/transport/base.py` + `discovery.py` (e.g. via `ast`/`tokenize`) and assert their top-level import statements reference none of `aiohttp`/`aiomqtt`/`paho` (module name or `from …` source).
     Tooling note: `VIRTUAL_ENV=.venv uv run ty check .` (ty lives in `.venv`; current repo baseline: 368 diagnostics).
5. `sdcp/const.py`: add `SDCP_COMMAND_TIMEOUT = 10` (cc2's `CC2_COMMAND_TIMEOUT` is already `10` — same value, different name; both coexist, base/ws/mqtt use the new one, cc2 keeps its own).
6. Verification "zero diff": `api.py`, `config_flow.py`, `cc2/client.py`, `coordinator.py` source-identical to pre-task state.

**Steps:**
- [ ] Read the 3 clients' moved bodies + `api.py`/`config_flow.py` import blocks (exact public surface to preserve).
- [ ] Create `sdcp/transport/__init__.py` (docstring-only) + `discovery.py` + `base.py`; add `SDCP_COMMAND_TIMEOUT` to `sdcp/const.py`.
- [ ] Write `sdcp/tests/test_transport_base.py` (4 status-extract equivalence tests + 2 send_command tests + 2 structural tests); run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/sdcp/tests/test_transport_base.py`
  - Did all pass (the base is already written — these pin it)? Any failure = the base diverges from the pinned behavior, fix before continuing.
- [ ] Rewrite `mqtt/client.py` / `websocket/client.py` to inherit the base (remove moved bodies).
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_mqtt_client.py custom_components/elegoo_printer/cc2/tests/ custom_components/elegoo_printer/websocket/tests/`
  - Did all pass (Task-3 + Task-1 tests are the behavior pins — any failure = a behavior change; stop and investigate)?
- [ ] Create `tests/test_client_conformance.py` + `tests/test_import_sdcppack.py`; run both.
- [ ] `VIRTUAL_ENV=.venv uv run pytest custom_components` (full), `make format`, `make lint` — all green.
- [ ] `git diff --stat -- custom_components/elegoo_printer/api.py custom_components/elegoo_printer/config_flow.py custom_components/elegoo_printer/cc2/client.py custom_components/elegoo_printer/coordinator.py`
  - Is the output empty (zero diff)? If not, stop — consumers must be untouched.
- [ ] Commit with message: `refactor(sdcppack): extract shared SDCP transport base + discovery module`

**Acceptance criteria:**
- [ ] `class ElegooMqttClient(SdcpPrinterClient)` / `class ElegooPrinterClient(SdcpPrinterClient)`; class and module paths unchanged; public method names/signatures unchanged.
- [ ] `sdcp/transport/base.py` + `discovery.py` import blocks contain no aiohttp/aiomqtt/paho (the static import check passes).
- [ ] All Task-1/Task-3 tests still green + new conformance/base tests green.
- [ ] `api.py`, `config_flow.py`, `cc2/client.py`, `coordinator.py` source-identical.
- [ ] Net line reduction in the two client files (~ −480 extracted, ~ +350 in `base.py`+`discovery.py`) — confirm with `wc -l` before/after.
- [ ] Base `__init__` field naming is settled: the base owns `self._gcode_proxy` (the per-transport private name — do NOT introduce a second `self.gcode_proxy` alongside it).

---

### Task 5: Typed wire contracts — SDCP + CC2 TypedDict tranche (F6)

**Context:**
~20 handler methods across the 3 clients take/return `dict[str, Any]` even though the wire shapes are stable. This task adds ~110 lines of `TypedDict(total=False)` definitions in two new modules and annotates the handler signatures — ZERO logic lines change (pure annotations + 2 model `__init__` param-union + 2 envelope-union). Wire-faithful typos stay (`CurrenCoord`, `PlatFormType`). `ty` (installed, no gate today; the repo has pre-existing diagnostics) is used only as a BASELINE gate: the task is green when `ty check` reports ZERO new diagnostics relative to the pre-task count. Full model-hierarchy TypedDicts (14-key `PrintHistoryDetail` + 40-key `SliceInformation`, `AMSBox`/`AMSTray`) and outbound request payloads are explicitly OUT (F6 tranche 2).

**Files:**
- Create: `custom_components/elegoo_printer/sdcp/types.py`
- Create: `custom_components/elegoo_printer/cc2/types.py`
- Modify: `custom_components/elegoo_printer/sdcp/transport/base.py` (annotations for the relocated shared handler bodies — WITH a `TYPE_CHECKING`-guarded import block so `base.py` stays transport-neutral per Task 4's static check)
- Modify: `custom_components/elegoo_printer/mqtt/client.py` (residual handler signatures + imports)
- Modify: `custom_components/elegoo_printer/websocket/client.py` (residual handler signatures + imports)
- Modify: `custom_components/elegoo_printer/cc2/client.py` (9 handler signatures + imports)
- Modify: `custom_components/elegoo_printer/sdcp/models/video.py` (`__init__` param union)
- Modify: `custom_components/elegoo_printer/sdcp/models/ams.py` (`__init__` param union)
- Modify: `custom_components/elegoo_printer/sdcp/models/status.py` (envelope union on `__init__`)
- Modify: `custom_components/elegoo_printer/sdcp/models/attributes.py` (envelope union on `__init__`)

**What to implement:**

1. `sdcp/types.py` — full module content (Python 3.13; `total=False` everywhere; runtime-importable, no `TYPE_CHECKING` needed):
   ```python
   """Wire-level TypedDicts for SDCP responses (received payloads only).

   Not exhaustive: only the fields actually read from the wire today are
   declared.  `total=False` models the firmware's inconsistent payload shapes
   (missing keys and key variants are normal).  These are TYPES ONLY —
   decoding stays in `sdcp.models`, and wire keys stay wire-faithful,
   including the firmware's typos (`CurrenCoord`, `PlatFormType`).

   To extend: add keys here as they are read, never ahead of the model.
   """

   from typing import Any, TypedDict

   class SDCPFrame(TypedDict, total=False):
       """A response frame: `{Id, Data, Topic}`."""

       Id: int
       Topic: str
       Data: dict[str, Any]

   class SDCPStatusWrapper(TypedDict, total=False):
       """The wrapper SDCP wraps the status payload in (`{Data: {...}}`)."""

       Data: dict[str, Any]

   class SDCPStatusPayload(TypedDict, total=False):
       """The status fields actually read by `PrinterStatus` (sparse)."""

       MachineStatus: Any
       MachineStatusReason: Any
       PrintStatus: Any
       CurrenCoord: list[Any]
       EstimatedTime: float
       CurrentProgress: float
       LastUserPlacementNozzlePreheat: Any
       LastUserPlacementBedPreheat: Any
       CurrentNozzleTemperature: float
       CurrentBedTemperature: float
       AxisZHeight: float
       AvgFilamentSpeed: float
       PrintUnit: Any
       "PlatFormType": Any  # firmware key variant (kept as-is on purpose)
       TotalTicks: Any
       TotalSeconds: Any
       Anchors: Any
       ReportedErrorTotal: Any
       ReportedError: Any
       TaskId: Any
       RemainingTime: Any

   SDCPStatusMessage = SDCPStatusWrapper | SDCPStatusPayload
   """The ws status handler decodes the full frame; the mqtt status handler
   decodes the inner payload."""

   class SDPPrintHistoryMessage(TypedDict, total=False):
       """A `print_history` frame (key -> sparse task-summary dicts)."""

       PrintHistory: dict[str, Any]

   class SDPPrintHistoryDetailFrame(TypedDict, total=False):
       """A `PrintHistoryDetail` frame (sparse; nested shape stays `Any`)."""

       PrintInfo: dict[str, Any]
       SliceInformation: dict[str, Any]

   class SDCPElegooVideoFrame(TypedDict, total=False):
       """An `ElegooVideo` frame (video stream status)."""

       FileName: str
       FileURL: str

   class SDCPElegooVideoUrlFrame(TypedDict, total=False):
       """A video-file URL response frame."""

       FileURL: str

   class SDCPAMSStatusFrame(TypedDict, total=False):
       """An `AMSStatus` frame (sparse; nested box/tray shapes stay `Any`)."""

       BoxCount: int
       TrayCount: int
       UpdateTime: Any
   ```
   Before finalizing, READ the 5 push handlers in `mqtt/client.py` + the read-sites in `sdcp/models/*.py` and reconcile the key list with the fields actually read — the keys above are the required minimum from the review; add any extra key a model reads that is not listed (never REMOVE a listed one). CC2 wire types do NOT live in this file.
2. `cc2/types.py` — CC2 wire `TypedDict`s (all `total=False`); read the read-sites in `cc2/client.py` to pin the exact key names before finalizing (if this list says a key is `X` but the code reads `Y`, follow the CODE):
   - `CC2Envelope`: `id: int | str`, `method: str`, `params: dict[str, Any] | None`, `result: Any`.
   - `CC2StatusFrame` (sparse) — keys MUST match the actual code reads (verify live, do not trust this list): `sequence`, plus the `print_status` / `machine_status` / `extruder` / `fans` / `led` / `gcode_move_inf` collections, read across BOTH `cc2/client.py` AND `cc2/models.py` (`CC2StatusMapper` owns the field mapping) — match the access patterns (total-wrapped collections can use `tuple[str, …]` / `list[…]` per item 2 rule (a)); flat keys the code never reads (the earlier `DeviceId`/`DeviceEra`/`DevVersion`/`State`/`MaxData`/`MaxFirmwareVersion`/`SerialNumber`/`Cmd` list was a wrong guess) must NOT appear.
   - `CC2StatusResult`: `status: CC2StatusFrame`.
   - `CC2Response` (sparse): `Id: int`, `Error: int`, `Msg: str`, `Data: Any`.
   - `CC2OledLCD` (sparse; the `ElegooOledClientSet` 2-element list of `{ElegooOledClient, Text}` dicts stays `Any`-ish); `CC2ControlStrategy` (sparse device-strings dict); `CC2FilamentList` (sparse `ElegooMaterialList`: list of dicts with `ElegooMaterial`/`platen` keys); `CC2FilamentStatus` (sparse tray dict); `CC2StatusInner` (sparse `ElegooStatus.XYZ` dict with `HomeStatus`/`NozzlePosition`/`ThermalStatus`; the rest `Any`); `CC2FilamentData` (sparse `ElegooAMS.*` dict).
   No cross-file imports — cc2 payload types stand alone.
3. Handler annotations (3 clients + the relocated base — NOTE: after Task 4 the shared handler bodies live in `sdcp/transport/base.py`; annotate them THERE, with the import under `if TYPE_CHECKING:` so the static neutral-import check stays green; keep per-transport residuals in their own files):
   - `sdcp/transport/base.py`: the relocated shared handler bodies — `_print_history_handler(data: SDPPrintHistoryMessage)`, `_print_history_detail_handler(data: SDPPrintHistoryDetailFrame)`, `_print_video_handler(data: SDCPElegooVideoFrame)`; the base status handler gets `_status_handler(data: SDCPStatusMessage) | None` handling (read the post-Task-4 final form of the handler first; the exact per-transport status-input difference is resolved by the union alias + the `None`-skip from Task 4).
   - `mqtt/client.py` (per-transport residuals — read the post-Task-4 file to enumerate the remaining handler signatures; expect: the video-file handler `(data: SDCPElegooVideoUrlFrame)`, the response-frame handler `(data: SDCPFrame)` if it stays in mqtt, plus any mqtt-specific wrapper the merge left behind).
   - `websocket/client.py` (per-transport residuals — same: read the post-Task-4 file; the `_parse_response` `data` stays `str` / `dict[str, Any]` as appropriate; the canvas/attributes ws-exclusive wrappers keep `dict[str, Any]`).
   - `cc2/client.py` (7, read the file to confirm the set): `_handle_full_status(data: CC2StatusFrame | dict[str, Any])`; `_handle_delta_status` STAYS plain `dict[str, Any]` — its delta/deep-merge cache is true-dynamic exception #1; `_handle_file_detail_response` STAYS plain `dict[str, Any]` — the layer-key lookup is true-dynamic exception #2; `_handle_file_thumbnail_response(data: CC2FileThumbnailResponse | dict[str, Any])`; `_handle_attributes(data: CC2Attributes | dict[str, Any])`; `_handle_video_response(data: CC2VideoResponse | dict[str, Any])`; `_handle_canvas_status(data: CC2CanvasStatus | dict[str, Any])`. NOTE: there is no `_handle_status_frame` and no `_handle_control_strategy` in this codebase — do not invent them.
   - Model bridges (2): `ElegooVideo.__init__(self, data: "SDCPElegooVideoFrame | dict[str, Any] | None = None")` (the actual signature today is a single-param `data: dict[str, Any] | None = None` — widen in place); `AMSStatus.__init__` the same (`"SDCPAMSStatusFrame | dict[str, Any] | None"`). Use a quoted forward-reference import under `if TYPE_CHECKING:` (none of the 4 model files uses `TYPE_CHECKING` today — add the block; `sdcp.types` imports nothing from models so no cycle either way; the quoted form is the conservative choice).
   - Envelope-union (2): `PrinterStatus.__init__(self, data, printer_type)` (read the actual signature) — `data` param becomes `"SDCPStatusMessage | dict[str, Any] | None"` (quoted forward reference under `TYPE_CHECKING`); `PrinterAttributes.__init__(self, data)` (actual: single param `data: dict[str, Any] | None`) — widen `data` to `"dict[str, Any] | None"`-equivalent that includes the wrapper shape only if the current shape excludes it (it does — it's exactly `dict | None`, so NO change is needed; leave it and document in a code comment: "envelope shape covered by dict[str, Any]"). Preserve every other parameter/behavior exactly.

4. GATE: record the `VIRTUAL_ENV=.venv uv run ty check .` diagnostic COUNT before any edit (baseline: 368 per Task 4's tooling note — verify live first); after all edits, the count must equal the baseline (±0 new diagnostics).

**Steps:**
- [ ] Record the `ty` baseline count (`VIRTUAL_ENV=.venv uv run ty check . | tail`); read the post-Task-4 `base.py` + the 3 clients' handler blocks + the 4 model `__init__` signatures.
- [ ] Create `sdcp/types.py` (finalized key list per item 1).
- [ ] Create `cc2/types.py` (per item 2).
- [ ] Create/annotate `sdcp/transport/base.py` (shared handlers + `TYPE_CHECKING` import block).
- [ ] Annotate per-transport residuals in the 3 client files (imports under `TYPE_CHECKING`).
- [ ] Apply the 2 model bridges + the PrinterStatus envelope-union (PrinterAttributes: comment-only, per item 3).
- [ ] `VIRTUAL_ENV=.venv uv run pytest custom_components` (behavior invariant — all tests green; a failure here = an annotation leaked into runtime/behavior — investigate and fix).
- [ ] `VIRTUAL_ENV=.venv uv run ty check .` — does the count equal the baseline? If more, fix the new diagnostics before continuing.
- [ ] `make format`, `make lint` — both green.
- [ ] Commit with message: `chore(types): add SDCP + CC2 wire TypedDicts (zero logic-line change)`

**Acceptance criteria:**
- [ ] `sdcp/types.py` and `cc2/types.py` created; `ty` diagnostic count identical to baseline.
- [ ] All shared handlers annotated in `sdcp/transport/base.py` (TYPE_CHECKING-guarded); per-transport residuals annotated; the 2 dynamic exceptions — `_handle_delta_status` and the file-detail layer-key site in `_handle_file_detail_response` — remain `dict[str, Any]`.
- [ ] All tests still pass; `make lint` green.

---

### Task 6: Split the definition entity-description tuples + unify the 3 config-flow option steps (F7)

**Context:**
`definitions.py` is ≈1283 code lines. The review's "≈683-line god function" is actually 12 module-level ENTITY-DESCRIPTION TUPLES spanning lines 436→1095 (the `standard` function `_print_status_sensor(options)` at line 413 is only ~14 lines — it returns a single description). Reviewer-verified real structure: `PRINTER_ATTRIBUTES_COMMON` (436), `PRINTER_ATTRIBUTES_BINARY_COMMON` (516), `PRINTER_BINARY_STATUS_RESIN_VAT_HEATER` (581), `PRINTER_ATTRIBUTES_RESIN` (597), `PRINTER_STATUS_COMMON` (628, ~146 lines), `PRINTER_STATUS_RESIN` (774), `PRINTER_STATUS_RESIN_VAT_HEATER` (796), `PRINTER_STATUS_FDM` (828, ~158 lines), `PRINTER_STATUS_FDM_TOTAL_EXTRUSION` (986), `PRINTER_STATUS_FDM_CURRENT_EXTRUSION` (1005), `PRINTER_BINARY_STATUS_CANVAS` (1027), `PRINTER_STATUS_CANVAS` (1045). This task splits the two BIG tuples (284 lines combined) into builder functions that reassemble IDENTICAL content (order/elements invariant), leaving the tuple constants in place with the same names. `config_flow.py`: the 3 option steps to unify are 1143 / 1222 / 1285 — ALL in `ElegooOptionsFlowHandler` (class at 1088). NOTE: `async_step_cc2_options` also exists at line 843, but in `ElegooFlowHandler` (the CONFIG-flow class at 378) and is LIVE (redirect call from line 532) — a homonym on a different class, NOT shadowed and NOT dead. LEAVE 843 and every `ElegooFlowHandler` method untouched. Shared skeleton of the 3 options steps: `current_settings` merge → `Printer.from_dict` → transport-specific input processing (incl. the network checks `_async_validate_gcode_proxy` / `_async_test_connection`) → `async_create_entry(title=…, data=…)` or `async_show_form(step_id, data_schema, suggested_values, …)`; the discriminator is the `TransportType` enum via `printer.transport_type` (members: `CC2_MQTT`/`MQTT`/`WEBSOCKET`); return type `config_entries.ConfigFlowResult`. Pinned by `tests/test_sensor_registration.py`, `tests/test_sensor_units.py`, `tests/test_extruder_sensors.py` (in `custom_components/elegoo_printer/tests/` — NOT `sdcp/tests/`) and the `tests/test_config_flow_*options*` files.

**Files:**
- Modify: `custom_components/elegoo_printer/definitions.py` (post-Task-1 F8 state — NOTE: F8's `__future__`/`TYPE_CHECKING` change lands in Task 7; do not preempt it here)
- Modify: `custom_components/elegoo_printer/config_flow.py`

**What to implement:**

1. `definitions.py` — split the two big entity-description tuple literals:
   - Read `PRINTER_STATUS_COMMON` (~146 lines) and `PRINTER_STATUS_FDM` (~158 lines). REALITY (reviewer-verified): `PRINTER_STATUS_COMMON` has NO group-level comments; `PRINTER_STATUS_FDM` has per-element `# --- X Sensor ---` headers but no group boundaries. Split instruction: choose ≥3 contiguous, non-overlapping slices per tuple by the `key` semantics of the elements (typically: timing/progress vs timestamps vs file/diagnostic in COMMON; temps vs fans vs speed/xyz in FDM — verify against the actual elements first), extract each slice into a builder function, and concatenate in the ORIGINAL element order — concatenation must reproduce the original tuple content exactly (test pins).
   - Extract each section into a module-level `def _build_<group>_sensor_descriptions(...) -> list[<the description base type the section elements use>]`, passing any parameters the tuple construction needs (e.g. the printer-data accessor closure is NOT needed — the tuples are static construction arguments; read the tuple to see if any element is computed at module import time).
   - Reassemble: `PRINTER_STATUS_COMMON = tuple(_build_temperature_descriptions() + _build_state_descriptions() + …)` — keep the constant NAME, the tuple TYPE (list vs tuple — match what consumers expect; check how the constant is consumed), and the element ORDER (test pins). The `_print_status_sensor(options)` small function stays untouched.
2. `config_flow.py` — unify the 3 option steps:
   - Read all three bodies (1143/1222/1285); extract the shared skeleton into `async def _async_step_transport_options(self, user_input, transport: TransportType) -> ConfigFlowResult` — the `current_settings` merge → `Printer.from_dict` → transport-specific input processing → `async_create_entry`/`async_show_form` flow. NOTE: the transport-specific input processing includes the NETWORK checks (`_async_validate_gcode_proxy` / `_async_test_connection`) — keep them in the per-method path or pass them as callables; do not flatten them into shared code they don't share.
   - The 3 thin methods each: build/forward their transport-identity; call the shared helper; return its result. The 1143 definition stays the LAST `async_step_cc2_options` in the class (shadowing the dead 843 one) — if the extraction leaves a body on 1143 that delegates, keep the method name/step_id exactly (`"cc2_options"` etc.).
   - Entry/data/return behavior invariant (the `test_config_flow_*options*` tests pin this).

**Steps:**
- [ ] Read the 12 tuple definitions (436→1095) + all 3 option steps + one representative `test_config_flow_*options*.py` + the 3 sensor test files.
- [ ] Split `definitions.py` (extract the per-group builder functions from the two big tuples; reassemble with identical order/content); after the split: `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_sensor_registration.py custom_components/elegoo_printer/tests/test_sensor_units.py custom_components/elegoo_printer/tests/test_extruder_sensors.py`
  - Did it pass? A failure = the tuple content/order changed — fix before continuing.
- [ ] Unify the 3 option steps (keep 1143 the effective `async_step_cc2_options`).
- [ ] `VIRTUAL_ENV=.venv uv run pytest $(ls custom_components/elegoo_printer/tests/test_config_flow_*option*.py | tr '\n' ' ')` (run every `test_config_flow_*options*` file that exists — list them with `ls | grep -i option` first)
  - Did all pass? A failure = behavior change — fix before continuing.
- [ ] `VIRTUAL_ENV=.venv uv run pytest custom_components`, `make format`, `make lint` — all green.
- [ ] Commit with message: `refactor(definitions): split big entity-description tuples; unify transport option steps`

**Acceptance criteria:**
- [ ] `PRINTER_STATUS_COMMON` / `PRINTER_STATUS_FDM` are reassembled from 3+ builder functions each; every other tuple constant, function, name unchanged; element order/content identical (pins green).
- [ ] The 3 option steps delegate to one shared helper (`TransportType` discriminator); 1143 remains the effective `async_step_cc2_options`.
- [ ] Full `make test` green.

---

### Task 7: Cleanup batch — import edges, dead code, naming, exception tuples (F8, F9, F10, F11)

**Context:**
Four low-risk cleanups, each grep-verified before and lint/test-verified after. F8: `definitions.py` imports `ElegooPrinterClient` at runtime but uses it only in 7 type annotations (the 7 `async def _*_action(client: ElegooPrinterClient)` functions, lines ~1318–1353); `coordinator.py` imports `ElegooCC2Client` at runtime (used in one `isinstance`) while `button`/`light`/`camera` already `TYPE_CHECKING`-guard the same. F9: 5 dead sync accessor methods + the never-referenced `DEFAULT_PORT` (ws) + one dead registry method + 2 of 3 orphan scripts (delete the 2 redundant with the Makefile, keep the dev tool). F9 KEEPS all unused protocol constants (they document the API surface). F10: naming fixes (wire-faithful typo rename preserving the wire mapping, class-casing fix, misleading method rename, job/task terminology doc note; the `elegoo_*` api prefix + `Gcode`/`GCode` casing are maintainer-taste — OUT). F11: replace the 4 scattered 3-tuple exception catches with a named tuple constant (behavior-identical: `TimeoutError` is a subclass of `ConnectionError`, tuple order is irrelevant in `except`).

**Files:**
- Modify: `custom_components/elegoo_printer/definitions.py` (F8 — post-Task-6 shape)
- Modify: `custom_components/elegoo_printer/coordinator.py` (F8 + F11)
- Modify: `custom_components/elegoo_printer/websocket/client.py` (F9 dead sync methods + `DEFAULT_PORT`)
- Modify: `custom_components/elegoo_printer/mqtt/client.py` (F9 dead sync methods + F10 class rename + command rename)
- Modify: `custom_components/elegoo_printer/cc2/client.py` (F9 dead property + F11 3-tuples)
- Modify: `custom_components/elegoo_printer/websocket/server/registry.py` (F9 dead method)
- Modify: `custom_components/elegoo_printer/sdcp/exceptions.py` (F11 `PRINT_TRANSPORT_ERRORS`)
- Modify: `custom_components/elegoo_printer/sdcp/models/print_history_detail.py` (F10 typo rename)
- Modify: `custom_components/elegoo_printer/sdcp/models/printer.py` (F10 terminology note)
- Modify: `debug.py` (REPO ROOT — NOT `custom_components/`; F10 ripple: `ElegooMqttClient` import at :14 + instantiation at :72)
- Modify: `custom_components/elegoo_printer/api.py` (F10 ripple: `ElegooMqttClient` import/union rename)
- Modify: any test file referencing the renamed names (grep first; NOTE: `config_flow.py` does NOT import `ElegooMqttClient` — do not touch it for F10)
- Delete: `scripts/lint`, `scripts/setup` (F9; keep `scripts/cc2_mqtt_debug.py`)
- Create: `custom_components/elegoo_printer/tests/test_import_time_edges.py` (F8 probes)

**What to implement:**

1. F8 (import edges):
   - `definitions.py`: add `from __future__ import annotations` (first importable line after the docstring); move `from .websocket.client import ElegooPrinterClient` under `if TYPE_CHECKING:` (extend the existing `typing` import with `TYPE_CHECKING` if absent — check the current import block first).
   - `coordinator.py`: replace `if not isinstance(api.client, ElegooCC2Client):` with `if api.printer.transport_type != TransportType.CC2_MQTT:` (read the exact expression first — the guard point is the `_replay_cc2_print_status_transitions` early-return; import `TransportType` from `sdcp.models.enums` — the canonical location — add it to the existing import from that module or add a new import line). Then remove the `from custom_components.elegoo_printer.cc2.client import ElegooCC2Client` line (grep the file first — remove ONLY if no other use remains).
2. F8 probes — `tests/test_import_time_edges.py` (EXACT content below; the pattern: pre-register the parent packages in `sys.modules` via `find_spec` so importing the submodule does not execute `custom_components/elegoo_printer/__init__.py`'s full stack — `importlib.import_module` of a submodule runs the parent `__init__`, which pre-shadows both client stacks, making a naive assertion meaningless):
   ```python
   """Import-time edge probes: annotation-only modules must not drag in the transport stacks.

   The package ``__init__.py`` pre-shadows both client stacks, so these probes
   pre-register the parent packages in ``sys.modules`` (via ``find_spec``) before
   importing the module under test.  A naive ``import module; assert "…client" not
   in sys.modules`` is meaningless without this.
   """

   import importlib.util
   import sys


   def _pre_register_parents(module: str) -> None:
       """Register every ancestor package in sys.modules without executing them."""
       parts = module.split(".")
       for i in range(2, len(parts) + 1):
           parent = ".".join(parts[:i])
           if parent in sys.modules:
               continue
           spec = importlib.util.find_spec(parent)
           assert spec is not None, f"spec for {parent} not found"
           sys.modules[parent] = importlib.util.module_from_spec(spec)


   def test_definitions_import_does_not_pull_ws_client():
       _pre_register_parents("custom_components.elegoo_printer.definitions")
       import custom_components.elegoo_printer.definitions  # noqa: F401

       assert "custom_components.elegoo_printer.websocket.client" not in sys.modules


   def test_coordinator_import_does_not_pull_cc2_client():
       _pre_register_parents("custom_components.elegoo_printer.coordinator")
       import custom_components.elegoo_printer.coordinator  # noqa: F401

       assert "custom_components.elegoo_printer.cc2.client" not in sys.modules
   ```
   NOTE: if the probes are red for reasons beyond the target assertion (e.g. the `homeassistant`/`httpx` imports in `coordinator.py` side-effecting the parent registration), extend `_pre_register_parents` to cover those module roots too — the two target assertions stay exactly as written.
3. F9 (dead code — GREP-VERIFY "zero references" before each deletion; a hit = stop):
   - `mqtt/client.py`: delete the sync `def get_printer_last_task` and the sync `def get_current_print_thumbnail` (post-Task-4 these may already live in `sdcp/transport/base.py` — locate by `grep -rn "def get_printer_last_task\|def get_current_print_thumbnail" custom_components/` and delete from the file that now owns it; the `async_get_printer_last_task` / `async_get_current_print_thumbnail` variants stay untouched).
   - `websocket/client.py`: delete the sync `def get_printer_last_task` (its Task-1 fix is not test-pinned — the regression tests target `async_get_printer_last_task`) and the sync `def get_current_print_thumbnail`; also delete `DEFAULT_PORT = 54780` (module constant, no references — verify by grep).
   - `cc2/client.py`: delete the `last_auth_failure` property (grep `last_auth_failure` — the only hits must be the property body; tests set the raw `_last_auth_failure` flag).
   - `websocket/server/registry.py`: delete `def get_all_printers_by_mainboard_id` (grep: zero references).
   - Delete `scripts/lint` + `scripts/setup` (verify redundancy with `Makefile` targets first — `diff` or visual check of the first lines); keep `scripts/cc2_mqtt_debug.py`.
4. F10 (naming):
   - `sdcp/models/print_history_detail.py`: `current_layer_tal_volume` → `current_layer_total_volume`; KEEP the wire mapping `data.get("CurrentLayerTalVolume")` verbatim; grep the repo for other `current_layer_tal_volume` references and update (expect: none outside this file).
   - `mqtt/client.py`: `class ElegooMqttClient` → `class ElegooMQTTClient`; ripples — `api.py` (the import line + any `... | ElegooMqttClient` type-union line, read the union/imports first), `debug.py` (the import line + the `ElegooMqttClient(...)` instantiation — a live usage), plus every test file referencing the name (`grep -r ElegooMqttClient .` → update ALL hits; `config_flow.py` does not reference it — do not touch it).
   - `mqtt/client.py`: `def _send_mqtt_connect_command` → `def _send_broker_redirect_command` (grep call-sites — usually one); extend its docstring (keep existing text, ADD one line): "NOTE: this is the M66666 `<host> <port>` UDP redirect command — it is not an MQTT connect frame."
   - `sdcp/models/printer.py`: extend the docstring/comment on the `current_job` field with: "Terminology: the SDCP wire + most of this codebase call it a `task` (task_id); CC2 firmware and some cc2 modules call it a `job`. Same concept." (docstring/comment only, no behavior change).
5. F11 (exception-tuple constant):
   - `sdcp/exceptions.py`: append:
     ```python
     PRINT_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
         ElegooPrinterConnectionError,
         ElegooPrinterNotConnectedError,
     )
     ```
   - `cc2/client.py`: the 3 `except (` blocks with the 3-tuple `(ElegooPrinterTimeoutError, ElegooPrinterConnectionError, ElegooPrinterNotConnectedError)` (the "Failed to get file details" / "Failed to get file thumbnail" / "Failed to request full status" handlers) → `except PRINT_TRANSPORT_ERRORS:`; add `PRINT_TRANSPORT_ERRORS` to the existing `sdcp.exceptions` import line; if the import of `ElegooPrinterTimeoutError` becomes unused in the file, remove it (grep first).
   - `coordinator.py`: the `except (` block (the 3-tuple in `async_refresh`'s failure path) → `except PRINT_TRANSPORT_ERRORS as e:` (preserve the `as e` binding); extend the existing `sdcp.exceptions` import with `PRINT_TRANSPORT_ERRORS`. IMPORTANT lint consequence: after the replacement, `ElegooPrinterNotConnectedError` becomes unused in `coordinator.py` (only `ElegooPrinterConnectionError` + `ElegooPrinterTimeoutError` remain, in the canvas-check 2-tuple) — DROP `ElegooPrinterNotConnectedError` from the file's import in the same edit, or `make lint` fails (F401). Do NOT touch the canvas-check 2-tuple itself.
   - In `cc2/client.py`, after replacing the 3 sites, all 3 exception names remain in use (raised elsewhere + other 2-tuple sites) — remove nothing there.
   - The mqtt disconnect catch-tuple was already extended in Task 1 (includes `ElegooPrinterNotConnectedError` + `OSError`) — leave it as-is (consolidation is a Stage-3/deferred nicety).

**Steps:**
- [ ] F8: the 2 import-edge fixes; create `custom_components/elegoo_printer/tests/test_import_time_edges.py` with the EXACT probe content above; run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_import_time_edges.py`
  - Did both probes pass? If red for unrelated reasons, extend `_pre_register_parents` per the NOTE — the two target assertions stay.
- [ ] F9: grep each deletion target; delete the 6 code sites + the 2 scripts; `make lint` + `VIRTUAL_ENV=.venv uv run pytest custom_components` mid-check (catch any missed reference).
- [ ] F10: the 4 renames + the doc note; `grep -r "current_layer_tal_volume\|ElegooMqttClient\|_send_mqtt_connect_command" .`
  - Do all three return zero (outside history/comments)? If not, finish the ripple before continuing.
- [ ] F11: append the constant; replace the 4 tuple sites; tidy imports per the notes.
- [ ] `make test`, `make format`, `make lint` — all green.
- [ ] Commit with message: `chore: import edges, dead methods/scripts, naming fixes, transport error tuple`

**Acceptance criteria:**
- [ ] Both import probes pass; `coordinator.py` has no runtime `cc2` import; `definitions.py` has `__future__`-annotations + a `TYPE_CHECKING`-guarded client import.
- [ ] `grep -r current_layer_tal_volume .` → zero; `grep -r ElegooMqttClient .` → zero (renamed to `ElegooMQTTClient`); `grep -r _send_mqtt_connect_command .` → zero.
- [ ] Zero references to the 6 deleted code sites; `scripts/lint` + `scripts/setup` deleted; `scripts/cc2_mqtt_debug.py` kept.
- [ ] `grep -n "ElegooPrinterNotConnectedError," custom_components/elegoo_printer/cc2/client.py` no longer shows the 3-tuple shape; the 4 sites read `except PRINT_TRANSPORT_ERRORS` / `except PRINT_TRANSPORT_ERRORS as e`.
- [ ] `make test` fully green; `make lint` green; `make format` clean.

---

## Review Manifest
- Plan execution review (round 1, 2026-08-31): FAIL — 6 blockers + 11 should-fix; all fixed (ws disconnect containment rework, real 8 top-level-module platforms, Task 6 retargeted to the 12 module-level entity-description tuples, conformance ams pin corrected, cc2 real handler names/keys, `VIRTUAL_ENV=.venv uv run pytest`/`ty` on every command, 398-test + 368-diagnostic baselines).
- Round 2, 2026-08-31: conditional PASS after fixes — 3 spec flaws (conformance ams/router, `config_flow` 843/1143 distinction, cc2 phantom handler names) + minor corrections (status-chain corner judgment, `debug.py` repo-root path, ws test red/green label, mqtt `connect_printer` signature follow-code, fake routing-topic precision).

_Review history: plan-001-codebase-improvement.md — executor: implement skill; findings it closes: `docs/reviews/2026-08-31-codebase-improvement.md`._
