# Follow-up Plan — F6 Tranche 2 (typed model hierarchy + outbound payloads) + F4 Stage 3 (CC2 grafts the SDCP base)

---
status: committed
done-when: `ElegooCC2Client(SdcpPrinterClient)` is true, consumers and both SDCP clients diff to zero, model-hierarchy + outbound wire shapes carry TypedDicts, and `make test` (≥480), `make lint`, `make format` are green on main with `ty check .` showing ZERO new diagnostics vs the pre-plan baseline (351)
---

**Goal:** Finish the plan-001 deferrals — typed full model hierarchy + outbound wire payloads (F6 tranche 2), then graft the CC2 client onto the shared transport base (F4 stage 3) — delivered as two sequential PRs (types first, graft second).

**Architecture:** PR-1 is a zero-logic-line annotation tranche over `sdcp/types.py` / `cc2/types.py` + model `__init__` bridges. PR-2 is a mechanical inheritance re-plumbing: `ElegooCC2Client` subclasses `SdcpPrinterClient`, the shared state block moves to `super().__init__`, the base `disconnect()` skeleton becomes the single implementation with CC2 split across the existing `_disconnect_pre`/`_on_disconnect` hooks, and only verified-identical task-accessor bodies merge. CC2 keeps its distinct wire 100% (`_send_command` counter-ID + CC2 envelope + `api_response` routing, registration, heartbeat, delayed disconnect, `_handle_message` dispatch, password fallback). ADR `docs/decisions/0001-cc2-grafts-sdcp-base.md` was recorded with the design.

**Tech Stack:** Python 3.13, pytest (+pytest-asyncio, `asyncio_mode="auto"`), ruff via `make format` / `make lint`, `make test`, `ty` as a diagnostic gate, aiohttp/aiomqtt/paho untouched. Baseline PRE-PLAN (verify live before Task 1): **480 tests passed**, `ruff check`/`ruff format --check` clean, `ty check .` = **351 diagnostics**. The recorded baseline is 351; the operational gate is **ZERO new diagnostics vs the pre-task recorded count**, not a fixed number.

**Ground rules for every task:**
- Test command convention: `VIRTUAL_ENV=.venv uv run pytest <paths>` (bare `python -m pytest` cannot run in this repo; `make test` = full suite).
- Behavior-preserving unless a task explicitly says otherwise. Never change serialized wire shapes (SDCP/CC2/MQTT payloads) or config-entry state shape. Log-string normalization inside shared code is allowed; exception types and control flow are not.
- Do not reformat untouched files: `make format` diffs must only touch files the task modified.
- `ty` gate: run `VIRTUAL_ENV=.venv uv run ty check . | tail -1` **BEFORE and AFTER the task**; the post-task count must be ≤ the pre-task count (ZERO new diagnostics). The pre-plan baseline is **351** — if a PRE-task run shows a different count, STOP and report. A rising count means YOUR change introduced a diagnostic: fix it (e.g. narrow a widened field back to `Any`) — never "adjust" by deleting legitimate pre-existing diagnostics.
- Run the exact commands listed in Steps; on any unexpected failure STOP and report.
- Verify file anchors by CONTENT, not line numbers (they shift between tasks).
- All test files under `custom_components/elegoo_printer/tests/` (e.g. `custom_components/elegoo_printer/tests/test_client_conformance.py`, `custom_components/elegoo_printer/tests/test_import_sdcppack.py`, `custom_components/elegoo_printer/tests/fakes.py`, `custom_components/elegoo_printer/tests/test_client_hardening.py`) and `custom_components/elegoo_printer/cc2/tests/` (e.g. `custom_components/elegoo_printer/cc2/tests/test_client_factory.py`).

---

### Task 1: Model-hierarchy TypedDicts + model `__init__` bridges (F6 tranche 2, part 1)

**Context:**
F6 tranche 1 (plan-001) typed the *sparse* wire shapes: `SDCPFrame`, the full `SDCPStatusPayload`, `SDCPPrintHistoryMessage`, `SDCPPrintHistoryDetailFrame`, `SDCPElegooVideoFrame`, `SDCPElegooVideoUrlFrame`, `SDCPAMSStatusFrame`, the `SDCPStatusMessage` union, and the CC2 equivalents in `cc2/types.py` — all `total=False` in `sdcp/types.py` (plain `typing.TypedDict`, runtime-importable) with a module policy of "declare keys as they are read, never ahead of the model". Tranche 2 (this task) adds the *nested model-hierarchy* shapes that tranche 1 deliberately left `Any`: the payload `PrintHistoryDetail.__init__` reads, the keys `SliceInformation.__init__` reads, and the box/tray shapes under `SDCPAMSStatusFrame` (replacing its `Any`-ish nested box/tray fields with real types). ZERO logic lines change: TypedDict definitions + `__init__` parameter-union bridges only. The wire keys stay wire-faithful, including firmware typos (`CurrenCoord`, `PlatFormType` — never "fix" them). Note: `PrintHistoryDetail.__init__` reads ~13 keys (trust the live code).

**Files:**
- Modify: `custom_components/elegoo_printer/sdcp/types.py`
- Modify: `custom_components/elegoo_printer/sdcp/models/print_history_detail.py`
- Modify: `custom_components/elegoo_printer/sdcp/models/ams.py`

**What to implement:**

1. In `sdcp/types.py` append (after the existing frame types, keeping the file's docstring style; all `total=False`):
   - `SDCPPrintHistoryDetailTask(TypedDict, total=False)` — EXACTLY the keys `PrintHistoryDetail.__init__` in `sdcp/models/print_history_detail.py` (class at ~line 9) actually reads via `data.get(…)` — read the init body first and list them (trust the code: add every key it reads, never ahead of it; nested values that are not read stay `Any`).
   - `SDCPSliceInformation(TypedDict, total=False)` — the keys `SliceInformation.__init__` (class at ~line 59 of the same file) actually reads; the class has ~40 wire keys but declare only read-sites; nested `Any` stays `Any`.
   - `SDCPAMSTray(TypedDict, total=False)` / `SDCPAMSBox(TypedDict, total=False)` — READ `sdcp/models/ams.py` first: `AMSTray` (class at ~line 9) and `AMSBox` (class at ~line 78) — declare each with the keys its `__init__` reads.
   - Widen `SDCPAMSStatusFrame`'s box/tray-bearing fields from `Any` to `list[SDCPAMSBox]` / `list[SDCPAMSTray]` ONLY where a model actually iterates a list of boxes/trays (verify the read site in `ams.py` before widening; if a field is a single object, type it as the object type). If widening a field would add a `ty` diagnostic, keep `Any` for that field and note in a one-line comment — the zero-new-diagnostics gate is non-negotiable. In the SAME edit, update the `SDCPAMSStatusFrame` docstring clause that says nested box/tray shapes stay `Any` so it no longer contradicts the widened fields.
2. Model bridges (tranche-1 pattern, quoted forward references under `if TYPE_CHECKING:`, one block per file — neither model file imports `sdcp.types` at runtime):
   - `PrintHistoryDetail.__init__(self, data: "SDCPPrintHistoryDetailTask | dict[str, Any] | None = None")` — read the ACTUAL current signature first and widen `data` in place; preserve every other parameter and behavior exactly.
   - `SliceInformation.__init__` the same (widened to `SDCPSliceInformation | dict[str, Any] | None` as appropriate for its real signature).
   - `AMSStatus.__init__` / `AMSTray.__init__` / `AMSBox.__init__` — widen each `data`/payload parameter to the matching new `TypedDict | dict[str, Any] | None` union (only where the parameter's real shape matches; read each signature first).
   - Do NOT touch `sdcp/models/video.py`, `status.py`, `attributes.py` (already bridged in tranche 1). Do NOT change any OTHER `__init__` code — signatures only.

**Steps:**
- [ ] Read `sdcp/models/print_history_detail.py` (both `__init__` bodies) and `sdcp/models/ams.py` (all three `__init__` bodies + `AMSStatus`) to enumerate the exact read-sites.
- [ ] `VIRTUAL_ENV=.venv uv run ty check . | tail -1` — record the PRE-task count (expected 351; if different, STOP and report).
- [ ] Implement the 4 new TypedDicts + the `SDCPAMSStatusFrame` widenings (+ docstring update) + the `__init__` bridges.
- [ ] `VIRTUAL_ENV=.venv uv run pytest custom_components 2>&1 | tail -1`
  - Did it pass with the same total (480)? A failure = a bridge leaked into runtime behavior — fix and re-run.
- [ ] `VIRTUAL_ENV=.venv uv run ty check . | tail -1`
  - Is the count ≤ the PRE-task count? If higher, identify the NEW diagnostic(s) and adjust (narrow a widened field back to `Any`) until none remain — do not touch pre-existing diagnostics.
- [ ] `make format` → no unexpected reformat of untouched files.
- [ ] `make lint` → clean.
- [ ] Self-audit the diff: `git diff -- custom_components/elegoo_printer/sdcp/models/` — is it signatures/decorators-only (zero logic lines)? If not, fix before committing.
- [ ] Commit with message: `chore(types): add model-hierarchy TypedDicts (F6 tranche 2, part 1)`

**Acceptance criteria:**
- [ ] `SDCPPrintHistoryDetailTask`, `SDCPSliceInformation`, `SDCPAMSTray`, `SDCPAMSBox` exist in `sdcp/types.py`, `total=False`, keys = read-sites (no more, no fewer).
- [ ] `SDCPAMSStatusFrame` box/tray fields typed where the model iterates real lists; its docstring consistent with the widening.
- [ ] The 4–6 model `__init__` bridges use quoted forward refs under `TYPE_CHECKING`; `make test` = 480; `ty` = no new diagnostics; ruff clean.
- [ ] The model-file diff contains zero non-signature, non-import lines.

---

### Task 2: Outbound payload types + send-path annotations (F6 tranche 2, part 2)

**Context:**
Tranche-1/Task-1 covered *received* payloads and the model hierarchy. This task types the *outbound* request shapes the clients serialize, completing F6 tranche 2. The SDCP outbound frame is built in `SdcpPrinterClient._send_printer_cmd` (`sdcp/transport/base.py`): read the current shape — `{"Id": self.printer.connection, "Data": {"Cmd": …, "Data": data, "RequestID": token_hex(8), "MainboardID": …, "TimeStamp": …, "From": 0}, "Topic": <per-transport request topic (ws `sdcp/request/…`, mqtt `/sdcp/request/…` leading-slash pin — do NOT 'fix')>}`. The CC2 outbound request is built in `ElegooCC2Client._send_command` (`cc2/client.py`, ~line 1267). VERIFIED SITUATIONAL FACT for the cc2 decision: the live cc2 request dict is exactly `{"id": request_id, "method": method, "params": params or {}}` — a STRICT SUBSET of the already-declared `CC2Envelope` (`id`, `method`, `params`; `total=False`). Therefore the correct cc2 outcome is the **doc-note branch** (do NOT create a redundant `CC2RequestEnvelope`); only if the live code has changed to something NOT covered by `CC2Envelope` do you add a new type. Still ZERO logic lines.

**Files:**
- Modify: `custom_components/elegoo_printer/sdcp/types.py`
- Modify: `custom_components/elegoo_printer/cc2/types.py`
- Modify: `custom_components/elegoo_printer/sdcp/transport/base.py` (annotation + `TYPE_CHECKING` import block only)

**What to implement:**

1. `sdcp/types.py` — append:
   - `SDCPRequestData(TypedDict, total=False)`: the `Data` inner keys the base writes — EXACTLY `Cmd`, `Data`, `RequestID`, `MainboardID`, `TimeStamp`, `From` (verify against the base's actual frame dict first — if the code writes more/fewer keys, trust the code).
   - `SDCPRequestFrame(TypedDict, total=False)`: `Id`, `Data` (→ `SDCPRequestData`), `Topic`.
2. Annotate `SdcpPrinterClient._send_printer_cmd`'s local frame variable as `SDCPRequestFrame` (quote it if the import must stay under `if TYPE_CHECKING:` — `base.py` already has a `TYPE_CHECKING` block from tranche 1; extend it, do not duplicate), so the static neutral-import check in `custom_components/elegoo_printer/tests/test_import_sdcppack.py` stays green (no top-level `aiohttp`/`aiomqtt`/`paho` imports may appear in `base.py`/`discovery.py` — the check is AST-based on top-level imports, and `sdcp.types` imports only `typing`, so the `TYPE_CHECKING` extension cannot trip it).
3. `cc2/types.py` — per the verified situational fact above: add the one-line doc note that the cc2 request reuses `CC2Envelope` (the request dict is a subset: `id`, `method`, `params`); do NOT add a redundant type.
4. Do NOT annotate `aiomqtt`/`aiohttp` publish calls, do NOT touch ws/mqtt modules, do NOT change the leading-slash topic pin.

**Steps:**
- [ ] Read the base `_send_printer_cmd` frame dict and cc2 `_send_command` request construction to lock the exact key sets.
- [ ] `VIRTUAL_ENV=.venv uv run ty check . | tail -1` — record the PRE-task count.
- [ ] Implement `SDCPRequestData`/`SDCPRequestFrame` + the base annotation (+ `TYPE_CHECKING` extension) + the cc2 doc note.
- [ ] `VIRTUAL_ENV=.venv uv run pytest custom_components 2>&1 | tail -1`
  - Same total (480) and `custom_components/elegoo_printer/tests/test_import_sdcppack.py` still among the passes? If the static import check broke, you added a top-level third-party import — fix.
- [ ] `VIRTUAL_ENV=.venv uv run ty check . | tail -1` → no new diagnostics vs pre-task.
- [ ] `make format` → no untouched-file reformat. `make lint` → clean.
- [ ] Self-audit: base.py diff = `TYPE_CHECKING` import + one annotation only.
- [ ] Commit with message: `chore(types): add outbound request payloads (F6 tranche 2, part 2)`

**Acceptance criteria:**
- [ ] `SDCPRequestFrame`/`SDCPRequestData` declared with keys exactly matching the base's frame dict (verify by diff).
- [ ] `_send_printer_cmd`'s frame annotated under the existing `TYPE_CHECKING` guard; `test_import_sdcppack` green.
- [ ] The cc2 `CC2Envelope` doc note present in `cc2/types.py`; no redundant request type added.
- [ ] 480 tests green; `ty` = no new diagnostics; ruff clean; zero logic lines in the diff.

---

### Task 3: CC2 graft plumbing — subclass + shared `__init__` block + inherited connection state (F4 stage 3, part 1)

**Context:**
PR-1 (Tasks 1–2) is merged and main is green. This task re-plumbs `ElegooCC2Client` (`cc2/client.py`, ~1490 lines) to subclass `SdcpPrinterClient` (`sdcp/transport/base.py`). Design (approved; ADR `docs/decisions/0001-cc2-grafts-sdcp-base.md`): shared state moves to the base `super().__init__`; CC2's distinct wire — `_send_command` (counter `id`, CC2 `method` envelope, `api_response` routing, `CC2_COMMAND_TIMEOUT`), `_register`, `_heartbeat_loop`, `_delayed_disconnect` machinery, `_handle_message` dispatch, password fallback, file detail/thumbnail caches — stays 100% in cc2. Class NAME and MODULE PATH are unchanged (`ElegooCC2Client`, `custom_components/elegoo_printer/cc2/client.py`), so `api.py`, `config_flow.py`, `coordinator.py`, `debug.py` and the ws/mqtt modules diff to ZERO. Verified ground truth (read the live code to confirm before editing): CC2's `__init__` signature is `(self, printer_ip: str, serial_number: str, access_code: str | None = None, logger: Any = LOGGER, printer: Printer | None = None, gcode_proxy: GCodeProxyClient | None = None, client_factory: Callable[[dict[str, Any]], Any] | None = None)`; CC2 normalizes `self.printer = printer or Printer()` and then builds `PrinterData(printer=self.printer)` — the base's `__init__(self, logger, printer, gcode_proxy=None)` (base `printer` is non-Optional `Printer`) therefore needs NO signature change: normalize FIRST, then `super().__init__`. All three production call sites (`api.py`, `config_flow.py`, `debug.py`) pass a non-None `printer=`, and all test constructions with `printer=None` hit the identical `printer or Printer()` path as today.

**Files:**
- Modify: `custom_components/elegoo_printer/cc2/client.py`
- Modify: `custom_components/elegoo_printer/sdcp/transport/base.py` (DOCSTRINGS ONLY — the class note (item 4) and the module-docstring sentence replacement (item 5); zero logic changes; `test_import_sdcppack.py` must stay green)
- Modify: `custom_components/elegoo_printer/tests/test_client_conformance.py` (ONE pin + its module docstring, item 6)
- DO NOT MODIFY: `api.py`, `config_flow.py`, `coordinator.py`, `debug.py`, `websocket/client.py`, `mqtt/client.py`

**What to implement:**

1. `class ElegooCC2Client(SdcpPrinterClient):` — add the import of `SdcpPrinterClient` from `custom_components.elegoo_printer.sdcp.transport.base` (ruff-sorted position in the existing cc2 import block; no circular-import risk from this edge, verified).
2. `__init__` re-plumb, preserving the EXACT field set and the `# noqa: PLR0913`:
   a. Keep the cc2-arg assignments first: `self.printer_ip`, `self.serial_number`, `self.access_code`, `self._client_factory`.
   b. Normalize: `printer = printer or Printer()` (replace the current `self.printer: Printer = printer or Printer()` line).
   c. `super().__init__(logger, printer, gcode_proxy=gcode_proxy)` — the base then sets `logger`, `printer`, `printer_data`, `_gcode_proxy`, `_is_connected`, `_listener_task`, `_background_tasks`, `_response_events`, `_response_lock`.
   d. DELETE exactly these 8 now-duplicated inline lines: `self.logger = …`, `self.printer = …` (the old normalized line), `self.printer_data = …`, `self._gcode_proxy = …`, `self._is_connected = …`, `self._listener_task = …`, `self._background_tasks = …`, `self._response_lock = …`. **Every other `__init__` line survives in order**, including the cc2-specific state: `self.mqtt_client`, `self._is_registered`, `self._connection_generation`, `self._listener_generation`, `self._last_auth_failure`, `self._disconnect_delay_task`, `self._heartbeat_task`, `self._response_data`, `self._request_counter`, the `_client_id` generation block (uses `time`/`secrets`), and every other retained state field in the live body (e.g. `_cached_status`, `_integration_data`, `_status_sequence`, `_non_continuous_count`, `_print_status_transition_queue`, `_registration_event`, `_registration_result`, `_last_pong_time`, `_request_id` — whatever the live code has; the rule is: delete the 8 in (d), keep everything else).
   e. **Shape gotcha (mandatory):** the base creates `self._response_events: dict[str, asyncio.Event]` (SDCP `token_hex` keys); CC2 needs `dict[int, …]` (counter IDs) — so AFTER `super().__init__`, RE-ASSIGN `self._response_events: dict[int, asyncio.Event] = {}` with a one-line comment: cc2 response events are keyed by counter id (distinct from the base's token-hex SDCP ids). Verified safe: `_response_events` consumers in cc2 are key-type agnostic (`_send_command` get/set/pop with counter ids; the base's unblock loop only iterates `.values()`).
3. Connection-state inheritance: DELETE cc2's own `is_connected` property and `_transport_open()` method ONLY IF they read identically to the base's composition — READ BOTH first. Base: `is_connected` = `_is_connected and _transport_open()`; cc2's current `is_connected` is textually the identical expression (verify); cc2's current `_transport_open` = `self._is_registered and self.mqtt_client is not None` (read the live body — match it exactly). Result: cc2 KEEPS its `_transport_open` override (distinct semantics: registered + client object) and inherits the `is_connected` property. The live match is verified true — if the live code differs, STOP and report (do not paper over a semantic change).
4. `ssdcp/transport/base.py` → `custom_components/elegoo_printer/sdcp/transport/base.py` (CORRECTING PATH): the `SdcpPrinterClient` **class docstring** gains one or two lines (the note's anchor — stated ONCE here only; Task 5 verifies rather than re-places it): the base is the shared SDCP state/skeleton; CC2 subclasses it (shared `__init__` state + the one `disconnect` skeleton + verified-identical accessors) but keeps its distinct wire (`_send_command`, registration, heartbeat, delayed disconnect, `_handle_message` dispatch) — see `docs/decisions/0001-cc2-grafts-sdcp-base.md`.
5. `sdcp/transport/base.py` **module docstring**: replace the sentence asserting "CC2 does not inherit this base (distinct wire: …)" so it matches the new contract (CC2 grafts the shared state/skeleton but keeps its distinct wire) — docstring only.
6. `custom_components/elegoo_printer/tests/test_client_conformance.py`: the one cc2 non-inheritance pin — LIVE at ~line 121 inside `test_cc2_client_constructs_disconnected` as `assert not isinstance(cc2_client, SdcpPrinterClient)`. UPDATE it to the new contract: `assert isinstance(cc2_client, SdcpPrinterClient)` with a comment that plan-002 grafts cc2 onto the base; the pin's PURPOSE stays (construction + pre-connect flag assertions unchanged). ALSO update the file's MODULE docstring sentence that says "CC2 does not inherit the shared base … so cc2 gets construction/flag pins only and inherits nothing" to match the new contract.
7. Do NOT touch anything else in cc2 (`connect_printer`, `_send_command`, handlers, caches, delayed disconnect, heartbeat) — those are Tasks 4/5. Do NOT change any base method body.

**Steps:**
- [ ] Read cc2 `__init__` (live), base `__init__`, cc2 `is_connected`/`_transport_open`, and the base equivalents; confirm the ground truth above matches live code.
- [ ] Apply items 1–7.
- [ ] `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/cc2/tests/ 2>&1 | tail -1`
  - All cc2 suite tests pass (11 test files, 77 tests; the existing delayed-disconnect + auth + light + queue tests are the pins)? Any failure = a state/init regression — stop and fix.
- [ ] `VIRTUAL_ENV=.venv uv run pytest custom_components 2>&1 | tail -1`
  - Same total (480)? `custom_components/elegoo_printer/tests/test_client_conformance.py` included — with item 6's pin update applied, it passes.
- [ ] `git diff --stat -- custom_components/elegoo_printer/api.py custom_components/elegoo_printer/config_flow.py custom_components/elegoo_printer/coordinator.py debug.py custom_components/elegoo_printer/websocket/client.py custom_components/elegoo_printer/mqtt/client.py`
  - Empty output (zero diff)? If not, stop.
- [ ] `VIRTUAL_ENV=.venv uv run ty check . | tail -1` → no new diagnostics vs pre-task.
- [ ] `make format` → only cc2 + base docstrings changed. `make lint` → clean.
- [ ] Commit with message: `refactor(sdcppack): cc2 grafts the shared transport base (state block + connection state)`

**Acceptance criteria:**
- [ ] `class ElegooCC2Client(SdcpPrinterClient)`; name/module path unchanged; constructor signature unchanged (callers in `api.py`/tests unaffected — the full suite proves it).
- [ ] Exactly the 8 item-(d) lines are deleted; every other `__init__` line survives in order; the re-assigned int-keyed `_response_events` exists with the comment; the `_client_id` generation block intact.
- [ ] `is_connected` inherited; `_transport_open` override preserved with identical semantics (`_is_registered and mqtt_client is not None`).
- [ ] The base class docstring note + the base module docstring + the conformance module docstring all state the new contract; `test_import_sdcppack.py` green.
- [ ] Consumers + both SDCP clients: zero diff. 480 suite green; conformance pin updated to the new contract; `ty` = no new diagnostics; ruff clean.

---

### Task 4: CC2 `disconnect()` grafted onto the base one-in-a-system + disconnect-tuple tidy (F4 stage 3, part 2)

**Context:**
The base `disconnect()` is the single heartbeat shape (read the live base method first to confirm): `logger.info` → `await self._disconnect_pre()` → cancel + suppress-await + `except Exception` containment on the listener → unblock waiters (`_response_events` set+clear under `_response_lock`) → `await self._on_disconnect()` → `self._is_connected = False`. The current cc2 `disconnect()` (read the live body before editing) does, in order: generation increment → log → cancel pre-existing delay task (null it) → cancel+await heartbeat (suppress-CancelledError, null it) → cancel+await listener (same containment) → POST-AWAIT cancel of delay task (because the listener's `finally` may have created a new one) → unblock waiters incl. `self._response_data.clear()` (same lock block as the sets) → `mqtt_client.__aexit__` close (except `(asyncio.TimeoutError, OSError, aiomqtt.MqttError)`) → `mqtt_client = None` → flags reset (`_is_connected`, `_is_registered`) + `_print_status_transition_queue.clear()`. The graft splits cc2 across the base's existing hooks so the containment text lives ONCE in the base: `_disconnect_pre()` runs BEFORE the listener cancel (so generation + heartbeat + pre-delay-cancel keep their exact current order — including the generation increment landing before the listener's `finally` runs, which keeps the `finally`'s generation guard behaving exactly as today), and `_on_disconnect()` runs AFTER waiter-unblock (so the post-await delay-task cancel and the mqtt close land there; the listener's `finally` has already completed at that point, so the cancel still catches any task it created).

**Files:**
- Modify: `custom_components/elegoo_printer/cc2/client.py`
- Modify: `custom_components/elegoo_printer/mqtt/client.py` (tidy ONLY, item 4)
- Create: `custom_components/elegoo_printer/cc2/tests/test_disconnect_graft.py`
- DO NOT MODIFY: `sdcp/transport/base.py`, any other module

**What to implement:**

1. **DELETE cc2's `disconnect()` in full** (the base's inherited one is now the single implementation) and add the two overrides:
   - `async def _disconnect_pre(self) -> None:` — (a) `self._connection_generation += 1` (keep the generation comment); (b) cancel pre-existing `_disconnect_delay_task` (and null it) — same as today's pre-listener step; (c) cancel+await `_heartbeat_task` with `contextlib.suppress(asyncio.CancelledError)` and null it.
   - `async def _on_disconnect(self) -> None:` — (a) **post-await delay-task cancel** (the listener's `finally` may have scheduled a new one — the base has already awaited the listener at this point; keep today's comment "The listener's finally may have created a new delay task — cancel it"); cancel `_disconnect_delay_task` + null it; (b) `_response_data.clear()` **inside `async with self._response_lock:`** — behavior note (accurate, keep in a comment): the woken waiters' continuations are merely *scheduled* by the base's `ev.set()` and cannot run until the disconnect task yields, so this clear always lands BEFORE any woken waiter's read — exactly as today, where the clear sits in the same lock block before any waiter can re-acquire the lock; the race status is IDENTICAL to current code; (c) the mqtt close: `if self.mqtt_client:` → `await self.mqtt_client.__aexit__(None, None, None)` with the SAME except-tuple `(asyncio.TimeoutError, OSError, aiomqtt.MqttError)` and same debug logging; (d) `self.mqtt_client = None`; (e) `self._is_registered = False`; (f) `self._print_status_transition_queue.clear()`. (The base sets `_is_connected = False` itself, after `_on_disconnect` — same relative order as today.)
2. **Tidy (mqtt only, behavior-identical):** in `mqtt/client.py`'s disconnect-command send (the `_disconnect_pre` override added by plan-001 task 1), the current catch tuple is `except (ElegooPrinterConnectionError, ElegooPrinterNotConnectedError, ElegooPrinterTimeoutError, OSError):`. Replace with `except (*PRINT_TRANSPORT_ERRORS, OSError):` — behavior-identical because (verify before editing: read `sdcp/exceptions.py`) `ElegooPrinterTimeoutError` subclasses `ElegooPrinterConnectionError`, so the 4-member tuple ⟺ `PRINT_TRANSPORT_ERRORS` (the connection + not-connected pair) + `OSError`. Add `PRINT_TRANSPORT_ERRORS` to the file's `sdcp.exceptions` import; drop imports that become unused (grep first: `ElegooPrinterConnectionError` and `ElegooPrinterNotConnectedError` remain in use elsewhere in the file — `ElegooPrinterTimeoutError` becomes unused and drops).
3. New test file `custom_components/elegoo_printer/cc2/tests/test_disconnect_graft.py` (characterization pins, plan-001 task-1 style — sync test functions driving coroutines via `asyncio.run`, `unittest.mock.MagicMock`/`AsyncMock`; NO conftest fixtures needed — construct `ElegooCC2Client(printer_ip="192.168.1.100", serial_number="serial1", access_code=None)` directly, the same construction pattern as plan-001's `test_cc2_disconnect_suppresses_terminal_listener_exception` in `custom_components/elegoo_printer/tests/test_client_hardening.py`):
   - `test_cc2_grafted_disconnect_suppresses_terminal_listener_exception`: construct the client, then **`client.logger = MagicMock()` FIRST** (the constructor's `logger` defaults to the real `LOGGER`, which has no `.called` attribute — a real logger would make the assertion error, so the mock is mandatory); set `client._is_connected = True`; `client._listener_task = asyncio.create_task(_terminal())` where `_terminal` raises `RuntimeError("terminal")`; `await asyncio.sleep(0)`; `await client.disconnect()` must NOT raise; assert `client._is_connected is False`, `client._is_registered is False`, `client._disconnect_delay_task is None`, `client._heartbeat_task is None`, `client.logger.exception.called` (pre-graft this test is behaviorally GREEN — the cc2 suite already pins the containment; post-graft it pins the INHERITED path, i.e. the containment text surviving the graft — that is the point).
   - `test_cc2_grafted_disconnect_cancels_delay_task_after_await`: same construction + logger mock; seed a REAL `client._disconnect_delay_task` (`asyncio.create_task` of a no-op), a terminal listener task, and ONE entry in `client._response_data` (e.g. `{1: {"ok": True}}`); `await client.disconnect()` → assert `client._disconnect_delay_task is None` (and the seeded task was cancelled), `client._response_data == {}`, `client._response_events == {}`.
   - `test_cc2_grafted_disconnect_is_base_skeleton` (conformance pin): assert `type(client).__mro__` contains `SdcpPrinterClient` and `ElegooCC2Client.disconnect` is the inherited `SdcpPrinterClient.disconnect` (i.e. `"disconnect" not in type(client).__dict__`).
   - If any test requires behavior not yet true, mark it as the RED expectation, record, then graft.
4. Do NOT change the base `disconnect()`. Do NOT touch ws/mqtt `disconnect` (the mqtt tidy in item 2 is the ONLY mqtt change). The `is_connected` property in cc2 is INHERITED after Task 3 — do not re-add it.

**Steps:**
- [ ] Read the LIVE base `disconnect()` + cc2 `disconnect()` + cc2 `_delayed_disconnect`/`_on_disconnect_delay_done`; confirm `PRINT_TRANSPORT_ERRORS` exists in `sdcp/exceptions.py` and the exception hierarchy.
- [ ] Create `test_disconnect_graft.py` with the three tests; run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/cc2/tests/test_disconnect_graft.py` — on un-grafted code the first two are behaviorally GREEN (cc2's own disconnect already contains; that is expected and recorded), the third is RED (no graft yet).
- [ ] Apply the graft (item 1) and the mqtt tidy (item 2).
- [ ] Re-run `custom_components/elegoo_printer/cc2/tests/test_disconnect_graft.py` — all GREEN.
- [ ] `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/cc2/tests/ 2>&1 | tail -1`
  - The 11-file cc2 suite (incl. `test_client_factory.py`'s `test_disconnect_unblocks_waiters_and_closes` — it seeds int-keyed events and asserts set/`{}`/`mqtt_client is None`/`_is_registered is False`/`fake.connected is False`, and must survive the graft) all pass?
- [ ] `VIRTUAL_ENV=.venv uv run pytest custom_components 2>&1 | tail -1` → total 483 (480 + 3 new).
- [ ] `VIRTUAL_ENV=.venv uv run ty check . | tail -1` → no new diagnostics vs pre-task. `make format` → only cc2 + mqtt changed. `make lint` → clean.
- [ ] Commit with message: `refactor(sdcppack): graft cc2 disconnect onto the base skeleton; tidy mqtt catch tuple`

**Acceptance criteria:**
- [ ] cc2's own `disconnect()` deleted; `_disconnect_pre`/`_on_disconnect` overrides exist with the splits above; "disconnect" is not in `type(cc2_client).__dict__` (inherited); the containment text lives once (base).
- [ ] A terminal-listener cc2 disconnect: no leak, all state reset (connected/registered/delay/heartbeat/mqtt), `_response_data` cleared, generation incremented, and the post-await delay-task cancel pinned.
- [ ] The 483-test suite green.
- [ ] Mqtt disconnect-catch reads `except (*PRINT_TRANSPORT_ERRORS, OSError):` with no unused imports; `ty` = no new diagnostics.

---

### Task 5: Task-accessor convergence + note/ADR verification (F4 stage 3, part 3)

**Context:**
After the plumbing (Task 3) and the `disconnect` graft (Task 4), the last DRY item is the cc2 accessors whose bodies are STRUCTURALLY IDENTICAL to a base/SDCP counterpart. VERIFIED OVERLAP MAP (re-verify live before executing): the accessors present in BOTH base and cc2 are exactly FIVE — `set_printer_video_stream`, `get_printer_video`, `get_printer_current_task`, `async_get_printer_last_task`, `get_printer_task_detail`. NOTE: the base's `get_printer_task_detail` is a `raise NotImplementedError` STUB (added in #408), so "diff the cc2 body vs the base body" is not applicable for this name — the keep-rule covers it. `async_get_printer_historical_tasks` is CC2-ONLY (no base counterpart) and always stays. CC2-ONLY accessors `get_printer_status`/`get_printer_attributes` (not on the base) always stay. The rule (approved): a cc2 accessor merges ONLY if its body is line-equivalent to the base/SDCP counterpart modulo naming and log strings — otherwise it STAYS in cc2 (behavior preservation beats DRY; plan-001 task 4 made the same call for the ws/mqtt variants). Note: even when a base accessor (e.g. `get_printer_video`) internally dispatches to `set_printer_video_stream`, dispatch is dynamic — a merged base `get_printer_video` will call CC2's KEPT `set_printer_video_stream` override, so a "merge" of `get_printer_video` is only safe if the base body's call chain is identical; if in doubt, KEEP (rule above).

**Files:**
- Modify: `custom_components/elegoo_printer/cc2/client.py` (accessor deletions/comments only)
- DO NOT MODIFY: `sdcp/transport/base.py` (the class docstring note was placed in Task 3 — this task VERIFIES it, not places it; if a merged accessor would require a base method-body change, the accessor STAYS instead — the base is frozen for this task), ws/mqtt clients, `api.py`, everything else

**What to implement:**
1. For EACH of the 5 overlapping names (`set_printer_video_stream`, `get_printer_video`, `get_printer_current_task`, `async_get_printer_last_task`, `get_printer_task_detail`): diff the cc2 body vs the base body (arguments first, then body; exception types and control flow must match; log strings are ignored for the equivalence call). Identical modulo naming → DELETE the cc2 copy (inherited). Any divergence — including "the base body is a NotImplementedError stub" (`get_printer_task_detail`) or "the base body calls a method whose cc2 override differs" (dispatch-chain case above) → KEEP the cc2 copy and add a one-line comment above it: `# cc2 variant: <one-clause why> (distinct from the base body — kept on graft)`.
2. `get_printer_status`/`get_printer_attributes`/`async_get_printer_historical_tasks` remain in cc2, untouched.
3. ACCESSOR any base method-body change is OFF-LIMITS in this task.
4. Verify the base CLASS docstring note (placed in Task 3 item 4) and the ADR file state: `git status` — `docs/decisions/0001-cc2-grafts-sdcp-base.md` should be committed by now; if it is STILL untracked (it was created during the design discussion), include it in THIS commit (it is part of the graft deliverable).
5. Do NOT touch ws/mqtt `disconnect`.

**Steps:**
- [ ] Re-verify the overlap map against live base + cc2; read the 5 cc2 bodies and base bodies (the 3 concrete base bodies + the 1 stub).
- [ ] Apply the merge/keep decisions + comments; verify the base docstring note exists; check the ADR file state.
- [ ] `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/cc2/tests/ 2>&1 | tail -1`
  - 11-file cc2 suite green (accessors are pinned by `test_client_factory.py`'s send_command + push tests and the new graft tests)?
- [ ] `VIRTUAL_ENV=.venv uv run pytest custom_components 2>&1 | tail -1` → same total (483). `VIRTUAL_ENV=.venv uv run ty check . | tail -1` → no new diagnostics.
- [ ] `make format` → only cc2 changed (if no accessor merged, cc2 may be untouched — a comments-only diff is fine; if truly nothing changed, skip the format step's expectation). `make lint` → clean.
- [ ] `git diff --stat -- custom_components/elegoo_printer/api.py custom_components/elegoo_printer/config_flow.py custom_components/elegoo_printer/coordinator.py debug.py custom_components/elegoo_printer/websocket/client.py custom_components/elegoo_printer/mqtt/client.py custom_components/elegoo_printer/sdcp/transport/base.py`
  - Empty (zero diff, base included — frozen for this task)?
- [ ] Commit with message: `refactor(sdcppack): converge verified-identical cc2 accessors; ADR verification`.

**Acceptance criteria:**
- [ ] Each of the 5 overlapping accessors: merged (deleted from cc2) OR kept with an explanatory comment — zero "silent" ones; `get_printer_task_detail` kept (base stub).
- [ ] `get_printer_status`/`get_printer_attributes`/`async_get_printer_historical_tasks` still in cc2, untouched.
- [ ] 483-test suite green; consumers + both SDCP clients + base: zero diff; ADR file committed; `ty` = no new diagnostics; ruff clean.
- [ ] The convergence outcome (merge list + keep list with reasons) is visible in the git diff (comments) — record no hidden states.

---

## Delivery order (the two PRs)
- **PR-1** = Tasks 1–2 → merge → main green → **PR-2** = Tasks 3–5 (branch builds on the merged PR-1). Commit messages per task above.
- Post-ship when `done-when` (front-matter) is true: update `docs/plans/README.md` — plan-002 has NO row today, so ADD a new row to the Completed Plans table (`| [Follow-up: Typed Model Hierarchy + CC2 Graft](plan-002-followup-typed-models-cc2-graft.md) | ✅ COMPLETED | <date> | <PR refs> |`), set Quick Stats to Total 5 / In Progress 0 / Completed 5, then commit `docs: mark plan-002 as completed` (repo convention — mirrors plan-001's ship)
