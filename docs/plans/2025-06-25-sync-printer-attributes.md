# Sync Live Attributes to Printer Object

**Goal:** Fix stale firmware (and model/name/brand) by syncing live MQTT/WebSocket attributes to the `Printer` object whenever attributes are received.

**Architecture:** Add `Printer.sync_from_attributes(PrinterAttributes) -> bool` that copies live values with guards against empty/unchanged data and re-derives dependent flags (`open_centauri`, `has_vat_heater`). Call from both CC2 and WebSocket attribute handlers.

**Tech Stack:** Python, Home Assistant custom component

---

### Task 1: Add `Printer.sync_from_attributes()` method

**Context:**
`Printer.firmware` (and `model`, `name`, `brand`) is set once from the stored config entry via `Printer.from_dict()` and never refreshed. After the user updates printer firmware, the integration reads the stale value, causing incorrect firmware update checks and stale device info in Home Assistant. This task adds a method to sync live attributes with guards and dependent flag re-derivation.

**Files:**
- Modify: `custom_components/elegoo_printer/sdcp/models/printer.py`
- Test: `custom_components/elegoo_printer/sdcp/models/tests/test_printer.py`

**What to implement:**
- Add `sync_from_attributes(self, attrs: PrinterAttributes) -> bool` method to the `Printer` class
- The method copies these fields from `PrinterAttributes` to `Printer`:
  - `attrs.firmware_version` → `self.firmware`
  - `attrs.machine_name` → `self.model`
  - `attrs.name` → `self.name`
  - `attrs.brand_name` → `self.brand`
- Only overwrite when source value is truthy (non-empty string) AND differs from current value
- After syncing, if `model` or `firmware` changed, re-derive:
  - `self.printer_type = PrinterType.from_model(self.model)`
  - `self.open_centauri = self._is_open_centauri(self.model, self.firmware)`
  - `self.has_vat_heater = self._has_vat_heater(self.model)`
- Return `True` if any field was updated, `False` otherwise
- Place `sync_from_attributes` immediately after `to_dict_safe()` and before `from_dict()`
- Do NOT modify `from_dict()` or any other existing method

**Steps:**
- [ ] Write unit tests in `custom_components/elegoo_printer/sdcp/models/tests/test_printer.py`:
  - `test_sync_from_attributes_updates_firmware` — verify firmware is updated from attrs
  - `test_sync_from_attributes_skips_empty_values` — verify empty strings don't overwrite existing values
  - `test_sync_from_attributes_skips_unchanged_values` — verify returning False when nothing changed
  - `test_sync_from_attributes_rederives_open_centauri` — verify open_centauri flag updates when firmware changes
  - `test_sync_from_attributes_rederives_printer_type` — verify printer_type updates when model changes
  - `test_sync_from_attributes_rederives_open_centauri` — verify open_centauri flag updates when firmware changes. Pre-condition: `printer.model = "Centauri Carbon"`, `printer.firmware = "V0.1.0"` (no O marker). Sync attrs with `FirmwareVersion = "V0.1.0 O"`. Verify `open_centauri` becomes `True`.
  - `test_sync_from_attributes_rederives_has_vat_heater` — verify has_vat_heater flag updates when model changes. Pre-condition: `printer.model = "Saturn 3"` (no vat heater). Sync attrs with `MachineName = "Saturn 4 Ultra 16K"`. Verify `has_vat_heater` becomes `True`.
  - `test_sync_from_attributes_syncs_all_fields` — verify model, name, brand are also synced
  - `test_sync_from_attributes_skips_all_empty_attrs` — create `PrinterAttributes({})` (no Attributes key, all fields default to `""`), verify nothing changes and returns `False`
  - Use `PrinterAttributes({"Attributes": {...}})` to create test attribute objects
  - Use `Printer()` to create empty printer instances, then set fields manually
- [ ] Run `make test` — confirm new tests fail (method doesn't exist yet)
- [ ] Implement `sync_from_attributes()` in `custom_components/elegoo_printer/sdcp/models/printer.py`
- [ ] Run `make test` — confirm all tests pass
- [ ] Run `make format` — confirm code is formatted
- [ ] Run `make lint` — confirm no lint errors
- [ ] Commit with message: "feat: add Printer.sync_from_attributes() to refresh live attributes"

**Acceptance criteria:**
- [ ] `sync_from_attributes()` updates firmware/model/name/brand from non-empty attrs
- [ ] `sync_from_attributes()` does NOT overwrite with empty strings
- [ ] `sync_from_attributes()` does NOT overwrite when value is unchanged
- [ ] `open_centauri` is re-derived when model or firmware changes
- [ ] `has_vat_heater` is re-derived when model changes
- [ ] Returns correct bool for changed/unchanged
- [ ] All tests pass, lint clean

---

### Task 2: Call sync from CC2 attribute handler

**Context:**
The CC2 client receives attributes via MQTT in `_handle_attributes()`. Currently it maps the CC2 nested format to `PrinterAttributes` and stores in `printer_data.attributes`, but never syncs to `self.printer`. This means `self.printer.firmware` remains stale. Adding a one-line call to `sync_from_attributes()` fixes this.

**Files:**
- Modify: `custom_components/elegoo_printer/cc2/client.py`

**What to implement:**
- In `_handle_attributes(self, attrs_data: dict[str, Any])`, after the line `self.printer_data.attributes = mapped_attrs`, add:
  ```python
  if self.printer:
      self.printer.sync_from_attributes(mapped_attrs)
  ```
- Guard with `if self.printer:` to avoid crash during disconnection race conditions
- This is inside the existing `try` block, same indentation level as the `self.printer_data.attributes = mapped_attrs` line
- Do NOT modify any other code in this file

**Steps:**
- [ ] Add the `self.printer.sync_from_attributes(mapped_attrs)` call in `cc2/client.py` `_handle_attributes()`
- [ ] Run `make format` — confirm code is formatted
- [ ] Run `make lint` — confirm no lint errors
- [ ] Run `make test` — confirm no regressions
- [ ] Commit with message: "fix: sync live attributes in CC2 client _handle_attributes"

**Acceptance criteria:**
- [ ] `_handle_attributes()` calls `self.printer.sync_from_attributes(mapped_attrs)` after setting `printer_data.attributes`, guarded by `if self.printer:`
- [ ] No test regressions
- [ ] Lint clean

---

### Task 3: Call sync from WebSocket attribute handler

**Context:**
The WebSocket client (for non-CC2 printers) receives attributes in `_attributes_handler()`. Same bug — it creates `PrinterAttributes` and stores in `printer_data.attributes` but never syncs to `self.printer`. Adding the same one-line call fixes this for WebSocket printers too.

**Files:**
- Modify: `custom_components/elegoo_printer/websocket/client.py`

**What to implement:**
- In `_attributes_handler(self, data: dict[str, Any])`, after the line `self.printer_data.attributes = printer_attributes`, add:
  ```python
  if self.printer:
      self.printer.sync_from_attributes(printer_attributes)
  ```
- Guard with `if self.printer:` to avoid crash during disconnection race conditions
- Do NOT modify any other code in this file
- Do NOT add logging for the sync call (the method itself doesn't log, and callers shouldn't need to)

**Steps:**
- [ ] Add the `self.printer.sync_from_attributes(printer_attributes)` call in `websocket/client.py` `_attributes_handler()`
- [ ] Run `make format` — confirm code is formatted
- [ ] Run `make lint` — confirm no lint errors
- [ ] Run `make test` — confirm no regressions
- [ ] Commit with message: "fix: sync live attributes in WebSocket client _attributes_handler"

**Acceptance criteria:**
- [ ] `_attributes_handler()` calls `self.printer.sync_from_attributes(printer_attributes)` after setting `printer_data.attributes`, guarded by `if self.printer:`
- [ ] No test regressions
- [ ] Lint clean

---

### Task 4: Call sync from legacy MQTT attribute handler

**Context:**
The legacy `ElegooMqttClient` (for non-CC2 MQTT printers) has the identical bug in `mqtt/client.py:_attributes_handler()`. It creates `PrinterAttributes` and stores to `printer_data.attributes` but never syncs to `self.printer`. This task fixes the same gap for legacy MQTT printers.

**Files:**
- Modify: `custom_components/elegoo_printer/mqtt/client.py`

**What to implement:**
- In `_attributes_handler()`, after the line that sets `self.printer_data.attributes`, add:
  ```python
  if self.printer:
      self.printer.sync_from_attributes(printer_attributes)
  ```
- Guard with `if self.printer:` to avoid crash during disconnection race conditions
- Do NOT modify any other code in this file

**Steps:**
- [ ] Add the guarded `self.printer.sync_from_attributes(printer_attributes)` call in `mqtt/client.py` `_attributes_handler()`
- [ ] Run `make format` — confirm code is formatted
- [ ] Run `make lint` — confirm no lint errors
- [ ] Run `make test` — confirm no regressions
- [ ] Commit with message: "fix: sync live attributes in legacy MQTT client _attributes_handler"

**Acceptance criteria:**
- [ ] `_attributes_handler()` calls `sync_from_attributes` with `if self.printer:` guard
- [ ] No test regressions
- [ ] Lint clean

---

### Task 5: Verify end-to-end and close issue

**Context:**
After Tasks 1-4, the full data flow works at runtime: live attributes → `PrinterAttributes` → `Printer.sync_from_attributes()` → `self.printer.firmware` updated → firmware update check uses correct version. Note: `_update_config_entry_if_needed()` is only called at connect/reconnect time, so the config entry won't be persisted to disk until the next HA restart or reconnection. This is acceptable for MVP — runtime behavior is correct immediately.

**Files:**
- No new files

**What to implement:**
- Verify the full chain works by checking:
  1. `api.py` `async_check_firmware_update()` uses `self.printer.firmware` — this is already correct, just verify
  2. `api.py` `_update_config_entry_if_needed()` compares `printer.to_dict()` which includes firmware — verify this picks up changes at reconnect time
  3. Run the full test suite one final time

**Steps:**
- [ ] Run `make test` — full test suite passes
- [ ] Run `make format` — all code formatted
- [ ] Run `make lint` — all lint clean
- [ ] Review all four changed files for correctness
- [ ] Commit with message: "chore: verify end-to-end attribute sync, close #377"
- [ ] Close GitHub issue #377 with explanation of the fix

**Acceptance criteria:**
- [ ] Full test suite passes
- [ ] All lint/format clean
- [ ] Issue #377 closed with fix explanation
