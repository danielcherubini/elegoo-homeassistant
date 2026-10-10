---
status: committed
done-when: With "Printer is often switched off" ticked, a printer that is powered off at Home Assistant startup leaves the integration **loaded** (not "Failed setup, will retry"), every entity **unavailable** (not fabricated values), nothing above INFO in the log, and the entities recover on their own within one poll when the printer is powered on — without a restart or reload. Unticked, behaviour is byte-for-byte what it is today.
---

# Offline-tolerant setup for switched-off printers (#171) — Plan

**Goal:** let a config entry finish setting up with a disconnected client when the user says "this printer is often switched off", so entities show `unavailable` instead of the integration reporting a permanent failed setup.

**Architecture:** one per-entry boolean, `allow_printer_offline` (default off), stored in `entry.options`. `ElegooPrinterApiClient.async_create` returns a *deliberately offline* client instead of `None` on the reachability/connect failures it now tolerates; `async_setup_entry` sees that and starts the coordinator with a new state-only `async_start_offline()` instead of `async_config_entry_first_refresh()` (which raises `ConfigEntryNotReady`). Recovery is **not** new — the coordinator's existing 30 s retry loop already reconnects and recovers.

**Tech Stack:** Python 3.13, Home Assistant 2025.4 (`DataUpdateCoordinator`, config entries, options flows), `pytest` (`asyncio_mode = auto`), `ruff` (`select = ["ALL"]`).

**Rationale / rejected alternatives:** [docs/decisions/0002-offline-tolerant-setup.md](../decisions/0002-offline-tolerant-setup.md).

## Facts the executor needs (all verified against this checkout)

- `PRINT_TRANSPORT_ERRORS = (ElegooPrinterConnectionError, ElegooPrinterNotConnectedError)` (`sdcp/exceptions.py:33`). `ElegooPrinterTimeoutError` **subclasses** `ElegooPrinterConnectionError`, so that one tuple covers all three. Do not "add" `NotConnectedError` to it — it is already there.
- Availability is automatic: `CoordinatorEntity.available == coordinator.last_update_success`, and HA computes `unavailable` **before** touching `self.state` (`helpers/entity.py:1025-1028`, `_stringify_state` called from `__async_calculate_state` at `:1082`), and only reads attributes under `if available:` (`:1087-1089`). No platform needs an availability change.
- `DataUpdateCoordinator.last_update_success` starts as `True`; `async_set_update_error(err)` sets it `False` and notifies listeners (`helpers/update_coordinator.py:490-497`) — **but it logs `logger.error(...)` when transitioning from success** (`:493-495`). At setup time it is still `True`, so calling it on the offline path would print `Error requesting <printer> data: Printer is off` — exactly the alarming ERROR this feature exists to stop. `async_start_offline` must therefore set `self.last_update_success = False` directly (a public attribute that `CoordinatorEntity.available` reads); there are no listeners yet at setup, so there is nothing to notify.
- `async_add_listener` arms the refresh timer **only for the first listener** (`update_coordinator.py:152`). So `async_start_offline` must not try to schedule refreshes itself — the first entity attaching arms it. `_schedule_refresh` no-ops unless `update_interval` is already set (`:223-229`), so `async_start_offline` must set the 30 s interval **before** platforms are forwarded (it does: setup order). Polling is additionally skipped when the user has `pref_disable_polling` set on the entry — expected HA behaviour, not a bug.
- `async_config_entry_first_refresh` raises `ConfigEntryNotReady` when the refresh fails. Never call it on the offline path.
- MQTT/CC2 answer `get_printer_status`/`get_printer_attributes` **from cache without raising** (`mqtt/client.py:337,362`, `cc2/client.py:1514,1518`); WebSocket raises `ElegooPrinterNotConnectedError` (`sdcp/transport/base.py:209`). This is why the poll needs a connection gate.
- For an MQTT printer, the reachability test probes **our own embedded broker** (`api.py:349-350`; the CC2 probe is `:325-326`), which is listening — so the printer being off fails at `connect_printer` (`api.py:412/414`), not at reachability. Both exits need handling.
- `Printer.to_dict()` does **not** round-trip unknown keys. `allow_printer_offline` must be written into the saved dict explicitly in every flow that saves.
- `Printer.from_dict` ignores unknown keys — storing the flag in entry data/options is safe.
- Translation **parity is tested in both directions** across all 18 locale files (`tests/test_translations.py`, `CONTEXT.md`). A key added to `en.json` must be added to all 17 others.
- Saving options calls `async_update_entry(entry, options=...)`, which fires the entry's update listeners; `async_setup_entry` registers `async_reload_entry` as one, so **ticking the box reloads the entry immediately** — no manual restart, and the user sees the effect of the change right away.
- Test harness: `pytest` with the `hass`/`entry` fixtures in `custom_components/elegoo_printer/conftest.py`; `entry.runtime_data.api` is a `MagicMock` with the async methods back-patched to `AsyncMock`.

## Already on disk (do not re-create)

Four RED test files exist and are the acceptance criteria for Tasks 1–3 and 5. Run them; expect the mix of pass/fail noted per task.

| File | Covers |
|---|---|
| `custom_components/elegoo_printer/tests/test_offline_setup_api.py` | Task 1 |
| `custom_components/elegoo_printer/tests/test_offline_setup_polling.py` | Task 2 |
| `custom_components/elegoo_printer/tests/test_offline_setup_entry.py` | Task 3 |
| `custom_components/elegoo_printer/tests/test_offline_setup_options.py` | Task 5 |

`CONF_ALLOW_PRINTER_OFFLINE = "allow_printer_offline"` already exists in `const.py` (added on this branch).

---

### Task 1: `async_create` returns a deliberately offline client

**Context:** Today every unreachable-printer path in `ElegooPrinterApiClient.async_create` returns `None`, and `async_setup_entry` turns that into `ConfigEntryNotReady`. When the entry opts in, those paths must instead return the (disconnected) client so setup can complete. Two correctness details make this safe: the offline exit must **keep** the embedded MQTT broker reference (while the entry stays loaded, `async_unload_entry` is the release point, and the printer's reconnect path needs the broker), and it must **not** zero `_proxy_server_enabled`, or a proxy user who came up offline would never re-establish the proxy on reconnect.

`async_get_printer_data` also gains a connection gate: with a cached-data transport (MQTT/CC2) a poll of a printer that is off currently *succeeds*, so every entity would render fabricated `0 °C` / `idle` values as live truth — worse than today's red entry. Raising routes all three transports through the coordinator's existing quiet failure branch.

**Files:**
- Modify: `custom_components/elegoo_printer/const.py` (constant already added — verify only)
- Modify: `custom_components/elegoo_printer/api.py`
- Test: `custom_components/elegoo_printer/tests/test_offline_setup_api.py` (already written)

**What to implement:**

1. In `__init__` (near `api.py:117` where `_proxy_server_enabled` / `_mqtt_broker_enabled` are read from `config`), add:
   - `self._allow_offline: bool = config.get(CONF_ALLOW_PRINTER_OFFLINE, False)`
   - `self.offline_setup: bool = False` — the public flag `async_setup_entry` branches on. Add a short comment: set only when setup was *allowed* to complete with no connection.
2. Add `CONF_ALLOW_PRINTER_OFFLINE` to the `.const` import block at `api.py:22-31`.
3. Add a private helper on the class:
   ```python
   def _as_offline(self) -> ElegooPrinterApiClient:
       """Mark this client as intentionally offline and return it (#171)."""
   ```
   It sets `self.offline_setup = True` and returns `self`. **It must not** disconnect, release references, zero `_proxy_server_enabled`/`_mqtt_broker_enabled`, or clear `self.server`/`self.mqtt_broker` — the caller has already done whatever teardown is correct for its path.
4. Use it at the **three** failure exits, each gated by `self._allow_offline`, and each with one INFO log naming the printer and that offline mode is active (INFO, never WARNING — this state is expected and may last for weeks):
   - the `if not printer_reachable:` exit (`api.py:374`): keep the existing `disconnect()` + broker release **only when not `_allow_offline`**; when tolerant, `disconnect()` the client but **keep `self.mqtt_broker`**, and return `self._as_offline()`. Note this path runs before the proxy is ever set up, so there is no proxy reference to consider here.
   - the `if not connected:` exit (`api.py:417`) and the `except (ConnectionError, TimeoutError, ElegooPrinterConnectionError):` exit (`api.py:436`): same shape — keep proxy/broker references and the two `_enabled` flags, return `self._as_offline()`.
   - Do **not** change the `_setup_proxy_if_enabled` / `_setup_mqtt_broker_if_enabled` failures (port already in use): those are real misconfigurations and must still return `None`.
5. At the top of `async_get_printer_data` (`api.py:863`), before any client call:
   ```python
   if not self.is_connected:
       msg = "Printer not connected, cannot fetch data."
       raise ElegooPrinterNotConnectedError(msg)
   ```
   `ElegooPrinterNotConnectedError` is already importable in `api.py` (`from .sdcp.exceptions import ElegooPrinterConnectionError` — extend that import).
6. **Do not** touch `reconnect()`, `is_connected`, the config-flow connection test, or any platform.

**Steps:**
- [ ] Read `custom_components/elegoo_printer/tests/test_offline_setup_api.py` and confirm you understand each test's intent.
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_offline_setup_api.py -q`
  - Expect **3 passed, 6 failed**. The 3 passing are characterizations of today's behaviour (option-off paths). If a different number fails, stop and find out why before implementing.
- [ ] Implement points 1–5 above.
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_offline_setup_api.py -q` → expect 9 passed.
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest -q` → expect **zero failures**. The `async_get_printer_data` gate is behaviour-visible for every transport, not just opt-in users; if any pre-existing test fails, it was relying on a disconnected client serving cached data — read it and fix the *test only if* it asserted the fabricated-data behaviour, otherwise fix the implementation.
- [ ] Run `make format && make lint` → both must succeed.
- [ ] Commit: `feat(api): return an offline client when the entry tolerates a powered-off printer`

**Acceptance criteria:**
- [ ] With the flag off, all three transports still return `None` when unreachable (asserted).
- [ ] With the flag on, WebSocket / CC2 (probe refused) and MQTT (`connect_printer` returning `False` **and** raising) all return a client with `offline_setup is True` and `is_connected is False`.
- [ ] With the flag on for an MQTT printer, `ElegooMQTTBroker.release_instance` is **not** awaited and `api.mqtt_broker` is still set; with the flag off it **is** awaited.
- [ ] A dead printer never starts the proxy server.
- [ ] `async_get_printer_data` raises `ElegooPrinterNotConnectedError` while disconnected, even with a populated cache.

---

### Task 2: `async_start_offline()` and side-check tolerance

**Context:** The offline path cannot use `async_config_entry_first_refresh` (it raises `ConfigEntryNotReady`), but the coordinator still has to end up in the same state a lost connection already produces, or entities will be "available" with `data is None`. The whole job is to mark state; arming the retry timer is the first entity listener's doing, and reconnecting is the existing poll's doing — do not add a reconnect mechanism here.

Separately: the Canvas (`coordinator.py:99`) and file-list (`coordinator.py:161`) side-checks catch `(ElegooPrinterConnectionError, ElegooPrinterTimeoutError)`, which **misses** `ElegooPrinterNotConnectedError`. So a Canvas request dropped because the printer briefly went away fails the entire update and flips a *connected* printer to `unavailable` — and for a CC2 with Canvas that happens once every 30 s.

**Files:**
- Modify: `custom_components/elegoo_printer/coordinator.py`
- Test: `custom_components/elegoo_printer/tests/test_offline_setup_polling.py` (already written)
- Test: `custom_components/elegoo_printer/tests/test_offline_setup_entry.py` → `TestOfflineStartOfTheCoordinator` only

**What to implement:**

1. Add a `@callback` method `async_start_offline(self) -> None`:
   - `self.online = False`
   - `self.update_interval = timedelta(seconds=30)`
   - `self.last_update_success = False` — **assign directly, do NOT call `async_set_update_error`**: that helper logs `logger.error("Error requesting %s data: %s")` when transitioning from success, and `last_update_success` is still `True` at setup, so it would print the exact ERROR this feature exists to remove. Nothing is listening yet, so there is no notification to send.
   - Docstring must say why this is not `async_config_entry_first_refresh` (it raises `ConfigEntryNotReady`) and that it performs **no I/O** — the first entity listener arms the refresh and the normal poll reconnects.
2. Replace both side-check `except (ElegooPrinterConnectionError, ElegooPrinterTimeoutError)` clauses with `except PRINT_TRANSPORT_ERRORS`, keeping the `LOGGER.debug` bodies and the `finally:` rate-limit blocks. Import `PRINT_TRANSPORT_ERRORS` (already imported) and drop now-unused names from the import list if ruff complains.
3. **Do not** gate the side-checks on `api.is_connected` — Task 1's poll gate already prevents them from running while disconnected. **Do not** change the `except OSError` branch, the 2 s/30 s logic, or `_replay_cc2_print_status_transitions`.

**Steps:**
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_offline_setup_polling.py "custom_components/elegoo_printer/tests/test_offline_setup_entry.py::TestOfflineStartOfTheCoordinator" -q`
  - Expect **9 failed, 1 passed**: 5 from the polling file (1 pass) + 4 from `TestOfflineStartOfTheCoordinator`, all `AttributeError: async_start_offline`. Anything else → investigate first.
- [ ] Implement points 1–2.
- [ ] Re-run the same command → all pass.
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest -q` → zero failures. `test_coordinator.py` pins existing refresh behaviour; if it breaks, the change is wrong, not the test.
- [ ] Run `make format && make lint`.
- [ ] Commit: `fix(coordinator): add async_start_offline and stop side-checks failing the update`

**Acceptance criteria:**
- [ ] `async_start_offline()` sets `last_update_success is False`, `online is False`, interval 30 s, and awaits nothing on the api.
- [ ] Attaching a listener after `async_start_offline()` does not restore `last_update_success`.
- [ ] A disconnected client with a populated cache fails the poll on **all** of CC2, MQTT and CC2+Canvas, and consecutive polls agree (no flicker).
- [ ] A connected printer whose Canvas request raises `NotConnectedError` keeps `last_update_success is True` and logs nothing at WARNING+.
- [ ] A genuinely lost link still produces `UpdateFailed` and the 30 s interval.

---

### Task 3: finish `async_setup_entry` without a connection

**Context:** This is where the user-visible outcome lands. The branch must key on `client.offline_setup`, **not** on `client.is_connected`, so any failure path Task 1 did not consider still falls back to today's fail-loud `ConfigEntryNotReady`. `entry.runtime_data` must be published before anything else because all 10 platforms read `runtime_data.api.printer` — a `Printer` built from stored config, which is why entity composition works with no connection at all.

Note `async_setup_entry` currently merges `{**entry.data, **(entry.options or {})}` before calling `async_create`; the options flow writes to `entry.options`, so that merge is what lets the flag reach the api at all.

**Files:**
- Modify: `custom_components/elegoo_printer/__init__.py` (`async_setup_entry`, around lines 490-525)
- Test: `custom_components/elegoo_printer/tests/test_offline_setup_entry.py` (already written)

**What to implement:**

1. Leave the `if client is None: raise ConfigEntryNotReady(msg)` block exactly as is.
2. After `entry.runtime_data = ElegooPrinterData(...)` is assigned, replace the unconditional
   `await coordinator.async_config_entry_first_refresh()` (and its `except Exception` cleanup wrapper) so that:
   - when `client.offline_setup` is true → `coordinator.async_start_offline()` and skip the first refresh entirely (no cleanup path needed: nothing was started that we own).
   - otherwise → the existing first-refresh block, unchanged, including its cleanup on failure.
   Keep `await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)` and `entry.async_on_unload(entry.add_update_listener(async_reload_entry))` running in **both** cases.
3. When taking the offline branch, log one INFO line that says the entry was set up while the printer was unreachable and that entities stay unavailable until it is reachable — this is the only clue a user gets in the log when their IP is actually wrong.
4. **Do not** touch `async_unload_entry`, `async_remove_entry`, `async_migrate_entry`, or the services. No config-entry version bump: the flag lives in `entry.options`.

**Steps:**
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_offline_setup_entry.py -q`
  - Task 2 has landed, so expect **2 failed, 8 passed** — the two remaining failures are `test_offline_client_with_option_completes_setup` and `test_offline_path_skips_the_first_refresh`. Confirm both fail on `ConfigEntryNotReady`, not a harness error.
- [ ] Implement points 1–3.
- [ ] Re-run → all pass.
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest -q` → zero failures.
- [ ] Run `make format && make lint`.
- [ ] Commit: `feat: complete setup with a disconnected client when opted in`

**Acceptance criteria:**
- [ ] Flag off + unreachable → `ConfigEntryNotReady`, no platform forwarding (unchanged).
- [ ] Flag on + offline client → returns `True`, `entry.runtime_data.api`/`.coordinator` are the passed doubles, `async_forward_entry_setups` awaited once.
- [ ] Offline branch calls `async_start_offline()` and never `async_config_entry_first_refresh()`.
- [ ] A **connected** client still gets exactly one awaited first refresh, flag on or off.
- [ ] The flag reaches `async_create` via the merged config.

---

### Task 4: make the CC2 restore-state selects survive `data is None`

**Context:** With setup allowed to finish offline, `coordinator.data` is legitimately `None` until the printer is first reached — a state that could never happen before, because the first refresh gated it. `ElegooPrintFileSelect.async_added_to_hass` (`select.py:157`) and `ElegooPrintTraySelect.async_added_to_hass` (`select.py:217`) restore the last choice by assigning straight onto `self.coordinator.data`. On `None` that raises `AttributeError` inside HA's entity-platform update, which logs an error and **silently drops the entity**. Both also need the same guard when a user restores state while offline.

**Files:**
- Modify: `custom_components/elegoo_printer/select.py`
- Test: `custom_components/elegoo_printer/tests/test_file_select.py` (extend)

**What to implement:**

1. In both `async_added_to_hass` methods, skip the restore when there is no data yet. Keep the existing state-value validation. Suggested shape for the file select:
   ```python
   last = await self.async_get_last_state()
   if self.coordinator.data is None or last is None or last.state in (None, "unknown", "unavailable"):
       return
   self.coordinator.data.selected_file = last.state
   ```
   Mirror it in the tray select with its `PRINT_TRAY_OPTIONS` membership check.
2. **Do not** change `current_option`, `options`, `available`, or `async_select_option` — they already guard `self.coordinator.data`.

**Steps:**
- [ ] Write two tests in `custom_components/elegoo_printer/tests/test_file_select.py`: build each select with a coordinator whose `data` is `None` and whose `async_get_last_state` returns a restorable state (`MagicMock(state=BENCHY)` for the file select, `MagicMock(state="A2")` for the tray), await `async_added_to_hass()` on a real `Entity` — patch `async_on_remove`/`super().async_added_to_hass` as the existing tests in that file do for coordinator wiring — and assert no exception plus that nothing was written.
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_file_select.py -q`
  - Expect the two new tests to fail with `AttributeError: 'NoneType' object has no attribute 'selected_file'` (and `'selected_tray'`). If they pass, the guard is unreachable — investigate.
- [ ] Implement point 1.
- [ ] Re-run → all pass, including the pre-existing file-select tests.
- [ ] Run `make format && make lint`.
- [ ] Commit: `fix(select): tolerate a missing coordinator data on state restore`

**Acceptance criteria:**
- [ ] Both selects add themselves without error when `coordinator.data is None` and a restorable state exists.
- [ ] A restore still lands in `coordinator.data.selected_file` / `.selected_tray` when data exists.
- [ ] `make test` green.

---

### Task 5: expose the option, and let it be enabled while the printer is off

**Context:** The flag must appear on all three transports' options forms — all three share the failure mode. Only the WebSocket step validates a change by connecting to the printer (`config_flow.py:1666`), and that gate makes the option unusable for exactly its target user: someone who powers the printer on once a month cannot get through a connection test to tick the box that stops the failures. When the flag is ticked, a failed connection test must save instead of re-showing the form; the printer cannot be re-queried, so the stored identity (id/model/name/firmware/connection) must survive from the stored settings while the user's *typed* values (new IP, proxy, Canvas, gcode proxy URL) are applied.

**Files:**
- Modify: `custom_components/elegoo_printer/config_flow.py`
  - `_websocket_options_schema` (~line 1706), `async_step_mqtt_options` `data_schema` (~line 1533), `_cc2_options_schema` (~line 1612)
  - `_process_websocket_options_input` (~line 1637), `_process_mqtt_options_input` (~line 1554), `_process_cc2_options_input` (~line 1469)
- Test: `custom_components/elegoo_printer/tests/test_offline_setup_options.py` (already written)

**What to implement:**

1. Add to **each** of the three schemas:
   `vol.Required(CONF_ALLOW_PRINTER_OFFLINE, default=False): selector.BooleanSelector(selector.BooleanSelectorConfig())`
   and import `CONF_ALLOW_PRINTER_OFFLINE` from `.const`.
2. Suggested values: the three steps already feed `current_settings` (merged data+options) into `add_suggested_values_to_schema`, so an entry that never set the key renders `False` from the schema default — verify with the test, do not hand-roll a separate default.
3. Persist the flag in all three `process_input` functions with
   `printer_data[CONF_ALLOW_PRINTER_OFFLINE] = bool(user_input.get(CONF_ALLOW_PRINTER_OFFLINE, False))`.
   `Printer.to_dict()` drops unknown keys, so this explicit write is required (see Facts). Write it unconditionally so unticking actually turns the option off.
4. In `_process_websocket_options_input` **only**, when the flag is ticked and `_async_test_connection` raises `ElegooConfigFlowConnectionError` / `ElegooConfigFlowGeneralError` / `PlatformNotReady` / `OSError`: build the save from the stored printer instead of the tested one — take the `printer` argument, apply `ip_address`, `proxy_enabled`, `has_canvas`, `external_ip` from `user_input`, `to_dict()`, add/pop `CONF_GCODE_PROXY_URL` exactly as the success path does, add the flag, and `return self.async_create_entry(title=printer.name, data=...)`. When the flag is not ticked, the existing error handling is untouched.
   - Do **not** weaken the gcode-proxy health check; a bad proxy URL still re-shows the form.
   - Do **not** change `async_step_manual_ip` or any *config* (add-a-printer) step: adding a printer still requires reaching it once.
5. Add a one-line comment in the offline-save branch explaining why the printer's identity comes from stored settings (it cannot be re-queried).
   - If ruff complains about returns/complexity in the closure, extract the branch into a method (`_async_save_offline(printer, user_input, proxy_url)`) returning a `ConfigFlowResult | None` — the same shape `cc2-proxy-host` used for the identical problem.

**Steps:**
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_offline_setup_options.py -q`
  - Expect **7 failed, 2 passed** (the 2 passing characterize today's gate). Anything else → investigate.
- [ ] Implement points 1–5.
- [ ] Re-run → all pass.
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_config_flow_websocket_options.py custom_components/elegoo_printer/tests/test_config_flow_mqtt_options.py custom_components/elegoo_printer/tests/test_config_flow_cc2_options.py -q` → zero failures. These assert existing schemas and save payloads; if one fails because a required field appeared, prefer updating the existing fixture over loosening the flow.
- [ ] Run `make format && make lint`.
- [ ] Commit: `feat(config-flow): offer offline tolerance and allow enabling it while offline`

**Acceptance criteria:**
- [ ] The flag appears on all three options steps.
- [ ] It renders `False` for an entry that never set it.
- [ ] Ticking it saves despite a failing connection test, keeping stored `id`/`model`/`name` and the newly typed IP.
- [ ] Unticking it while offline still re-shows the form (no silent save).
- [ ] A successful connection still persists the flag.
- [ ] A bad gcode-proxy URL still blocks the save even with the flag ticked.

---

### Task 6: translations, README, CHANGELOG

**Context:** All 18 locale files must carry the same key set as `en.json` — this is asserted bidirectionally by `tests/test_translations.py` and defined as a hard requirement in `CONTEXT.md`. The README must state the trade-off plainly: with the option on, a wrong IP or dead hardware looks exactly like a switched-off printer.

**Files:**
- Modify: `custom_components/elegoo_printer/translations/en.json` + the 17 other files in that directory
- Modify: `README.md` (the `## 🔧 Configuration` section, ~line 170)
- Modify: `CHANGELOG.md`

**What to implement:**

1. In **each** of the three option steps (`websocket_options`, `mqtt_options`, `cc2_options`) add under `data`:
   `"allow_printer_offline": "Printer is often switched off"` and under `data_description`:
   `"allow_printer_offline": "Set up even when the printer can't be reached. All entities stay \"Unavailable\" until it is powered on, instead of the integration reporting a failed setup. Note: a wrong IP address or unreachable printer then looks the same as a switched-off one."`
   Translate both strings for the other 17 locales (`ar cs da de el es fr it ja ko nl pl pt ru sk th zh-Hans`), matching the tone and terminology already used in each file (e.g. `zh-Hans.json` uses 打印机, `de.json` uses Drucker). Keep the escaped quotes valid JSON.
2. README: add the option to the Configuration section as a short subsection — what it does, when to use it, and the trade-off sentence above. Mention it applies to every printer type.
3. CHANGELOG: one entry under the unreleased/current section describing the option and crediting the reporter by name (`@oblivioncth`, issue #171).

**Steps:**
- [ ] Write the `en.json` keys, then the 17 translations.
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_translations.py -q` → all pass. A parity failure message lists exactly the missing keys per locale.
- [ ] Run `VIRTUAL_ENV=.venv uv run python -c "import json,glob;[json.load(open(f)) for f in glob.glob('custom_components/elegoo_printer/translations/*.json')]"` → no exception (catches hand-edited JSON).
- [ ] Read the rendered README section once; fix wording.
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest -q` → zero failures.
- [ ] Run `make format && make lint`.
- [ ] Commit: `docs: document the offline-tolerance option + translations`

**Acceptance criteria:**
- [ ] All 18 locale files have identical key sets.
- [ ] `test_translations.py` green.
- [ ] README documents the option and its trade-off; CHANGELOG credits @oblivioncth / #171.

---

## Out of scope (deliberate)

- **No dedicated reconnect task.** The coordinator's existing retry loop already reconnects and recovers; a second mechanism would race it.
- **No new device-registry or per-entity `unavailable` plumbing.** Coordinator availability is the single source.
- **No log-cadence work while offline** (the per-30 s INFO at `coordinator.py:118`, the ~5 s UDP broadcast inside `discover_printer`, the M66666 broadcast every 30 s for MQTT). Chosen to keep this PR reviewable; follow up separately if users still report noise.
- **No config-entry version bump** — the flag lives in `entry.options`.
