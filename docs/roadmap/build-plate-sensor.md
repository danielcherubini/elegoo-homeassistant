---
status: committed
issue: https://github.com/danielcherubini/elegoo-homeassistant/issues/450
done-when: A Centauri Carbon owner can put a "Build Plate" card on a dashboard and see which plate side the current print is using; a printer that never reports the field shows Unknown rather than guessing "B".
---

# Build plate sensor for FDM printers (#450)

## Spec

**Problem:** The Centauri Carbon reports which build plate side is selected through the `PlatFormType` field in its SDCP status message. The integration already parses it into `PrinterStatus.platform_type` (`custom_components/elegoo_printer/sdcp/models/status.py:288`) and then never reads it. Verified with `grep -rn "platform_type" custom_components/elegoo_printer --include=*.py`, which returns exactly two lines — the assignment at `:288` and its docstring at `:239`. Nothing on a dashboard tells the user which plate a print is running on.

**Solution:** One new sensor, `build_plate`, in the FDM status sensor tuple, reading `printer_data.status.platform_type` and presenting it as an enum. Two supporting changes make it honest: the model stops inventing a value when the printer omits the field, and the translation keys land in every locale so the entity name is not English-only.

**Out of scope:**
- **No plate-aware automation, service, or select.** This exposes a reading; it does not let anything act on it.
- **No CC2-specific plate parsing.** `CC2StatusMapper.map_status()` (`custom_components/elegoo_printer/cc2/models.py`) does not set `platform_type`, and no CC2 wire field for it is known. Adding CC2 parsing is a separate change that needs a hardware capture, not an inference.
- **No `video/…`, tray, or filament work** — unrelated issues.

**Decisions:**
- **CC2 keeps the sensor, and its owners get a permanently-Unknown entity until a capture lands.** CC2 is `PrinterType.FDM`, so it gets the FDM set like every other FDM machine; excluding it would be a category error. This is a decision, not a side effect: the repo's largest FDM audience gains an entity that reads Unknown forever unless someone captures a CC2 status payload showing a plate field. The alternative — hiding it — was rejected because "this printer type does not have this feature" is the wrong claim to make from an absence of evidence in our mapper rather than from the firmware.
- **`status.py:288` default changes from `1` to `None`, typed `int | None`.** Today `status.get("PlatFormType", 1)` means a printer that never sends the key reports `1`, which under the 0→A / 1→B mapping displays **"Plate B"** — a specific, wrong answer. The blast radius is the two lines the grep above finds; there is no third reader. The reporter of #450 proposed this and it is the difference between an honest Unknown and a fabricated reading.
- **The annotation is aspirational, not enforced.** `sdcp/types.py:52` declares `PlatFormType: Any`, and `status.get()` passes through whatever the wire sent. So the real domain is "`None`, or whatever the firmware sent". `value_fn` therefore guards with `isinstance(..., int)` rather than trusting the annotation — a string `"0"` must not become "Plate A" by accident, and must not raise.
- **`device_class=ENUM` with a bounded option set, not free text.** The reporter's snippet maps unknown ints through `str(platform_type)`. That is incompatible with `options`: Home Assistant raises `ValueError: Sensor ... provides state value '<v>', which is not in the list of options provided` (confirmed in the pinned `2025.4.0` at `sensor/__init__.py:603-619` and unchanged in `2026.10.4`). So unknown values map to a declared `"unknown"` option. `iometer` ships the same shape in HA core (`options=["battery","wired","unknown"]`), so this is an established pattern rather than a workaround.
- **No `state_class`.** HA's `DEVICE_CLASS_STATE_CLASSES[SensorDeviceClass.ENUM]` is the empty set (`sensor/const.py:653`), so any `state_class` on an enum sensor is invalid by construction. The repo's existing enum sensors (`current_status` at `definitions.py:812-827`, and the `_print_status_sensor` builders at `:466-487`) correctly declare none.
- **`translation_key="build_plate"` with a `state` map, in all 19 locale files.** The repo enforces translation parity bidirectionally (`custom_components/elegoo_printer/tests/test_translations.py:238` and `:252`): every `en.json` leaf must exist in every locale and vice versa. Non-English values follow the machine-translation convention already used for the 21-string fill-in and are flagged for native-speaker review.

**Evidence:** Issue #450 (open, author `ohnoitsfraa`, no comments), which states observed values `0 = Plate A`, `1 = Plate B` on a Centauri Carbon at firmware V1.4.49 and notes the `1` fallback problem. Grounded against `sdcp/models/status.py:228-288`, `sensor.py:55-158`, `definitions.py:317-334`/`812-827`/`1050-1114`, `sdcp/tests/test_status.py`, `tests/test_translations.py:40-49`/`238-263`, `tests/test_entity_setup.py:117-134`, `tests/test_sensor_registration.py:41-124`, `tests/test_fdm_print_status.py:50-63`. Plan reviewed and re-executed by a reviewer subagent in a scratch tree; the file-placement, lint and wave findings are folded into the tasks below.

**UNVERIFIED:** `0 = Plate A / 1 = Plate B` is one reporter's observation on one model at firmware V1.4.49. Nothing in this repo corroborates or contradicts it for other Elegoo FDM models. The mapping is isolated in one named constant so a correction is a one-line change.

## Plan

**Goal:** Expose the selected build plate as an enum sensor on FDM printers, with unknown reported honestly.
**Architecture:** A single `ElegooPrinterSensorEntityDescription` appended to the FDM builder chain in `definitions.py`, reading a model attribute whose default is corrected to `None` in `sdcp/models/status.py`. Translation keys are added across all 19 locale files to satisfy the repo's parity tests.
**Tech Stack:** Python 3.13/3.14, Home Assistant `SensorEntityDescription`, pytest, Ruff (`select = ["ALL"]`), `uv`.

---

## Parallelisation

**Verdict:** PARALLEL — 2 waves

- **Wave 1:** Task 1, Task 2, Task 3 — file sets verified pairwise disjoint by the reviewer's execution, not by inspection. Task 1 owns `sdcp/models/status.py` + `sdcp/tests/test_status.py`; Task 2 owns `definitions.py`, `tests/test_entity_setup.py` and the new `tests/test_build_plate_sensor.py`; Task 3 owns only `translations/*.json` (19 files). No path appears twice, no `__init__.py`/`conftest.py`/registration file is shared, and `uv run` was verified not to rewrite `uv.lock`.
- **Wave 2:** Task 4 — the integrated proof. **Not** because parity needs the sensor (it does not: parity walks JSON leaf keys only, and Task 3 alone was measured passing `test_translations.py` 7/7 and the full suite 946/946 in a tree with no sensor). Task 4 exists because only the integrated tree can show that the model's `None`, the mapping, and the translation keys describe the same entity, and because the whole-tree format/lint/test trio must run once on the merged result.

Gate check for Wave 1: files disjoint (1); no cross-imports — Task 2 reads an existing attribute, creates nothing Task 1 owns (2); no shared mutable artifact, no lockfile/fixture/barrel/registration (3); each provable alone — Task 1 measured 946 passed, Task 2 measured 953, Task 3 measured 946 (4); no tree-wide formatter or codegen — **wave children must use the per-path commands written below and must NOT run `make format` / `make lint`, which are whole-tree** (5); width 3 ≤ 4 (6).

> ⚠️ Wave children verify with per-path commands only. `make format` runs `ruff format .` across the tree; two children doing that collide at fan-in on every file.

---

### Task 1: Stop inventing a platform type when the printer omits it

**Parallel:** wave 1 — owns `sdcp/models/status.py` and `sdcp/tests/test_status.py`; disjoint from Task 2 (`definitions.py`, `tests/`) and Task 3 (`translations/`)

**Context:**
`PrinterStatus.__init__` does `self.platform_type: int = status.get("PlatFormType", 1)`. A printer that does not send `PlatFormType` therefore reports `1`, which under the plate mapping is "Plate B" — a confident wrong answer. Task 2 adds the sensor that reads this field; this task makes the absence of the field representable first.

`grep -rn "platform_type" custom_components/elegoo_printer --include=*.py` returns exactly two lines today — `sdcp/models/status.py:239` (the docstring) and `:288` (the assignment). There is no third reader, so this is a one-line behavioural change with one docstring to match. Do not expect that grep to return nothing: it is supposed to return those two.

Two lint facts that decide where the test goes: put it in `custom_components/elegoo_printer/sdcp/tests/test_status.py`, which is titled "Tests for the PrinterStatus model" and already carries `# ruff: noqa: PLR2004` at the top. It does **not** go in `sdcp/models/tests/test_printer.py`, which is about the `Printer` model and whose per-file-ignores are `["S101"]` only — asserting `== 2` there trips `PLR2004 Magic value used in comparison`.

`sdcp/types.py` declares `PlatFormType: Any` and its module docstring says wire keys stay wire-faithful including the firmware's typo — **do not rename `PlatFormType`**.

**Files:**
- Modify: `custom_components/elegoo_printer/sdcp/models/status.py`
- Test: `custom_components/elegoo_printer/sdcp/tests/test_status.py`

**What to implement:**

1. In `status.py`, change the assignment at line ~288 from:
   ```python
   self.platform_type: int = status.get("PlatFormType", 1)
   ```
   to:
   ```python
   self.platform_type: int | None = status.get("PlatFormType")
   ```
2. Update the attribute doc in the same class's docstring at line ~239, currently the single line `platform_type (int): The platform type.`, to say the value is `None` when the printer does not report it, and that the value is passed through from the wire unvalidated.
3. Do **not** touch `sdcp/types.py`, do not rename `PlatFormType`, and do not add coercion here — the model's job is to stop inventing a default; Task 2's `value_fn` guards the type.

**Steps:**
- [ ] In `custom_components/elegoo_printer/sdcp/tests/test_status.py`, add a test asserting: a `PrinterStatus` built from a payload **without** `PlatFormType` has `platform_type is None`; one built with `"PlatFormType": 0` has `platform_type == 0`; one built with `"PlatFormType": 2` has `platform_type == 2`. Match the file's existing payload-construction style. Any new import goes at **module top level**, not inside the test body (`PLC0415`).
- [ ] Run `uv run pytest custom_components/elegoo_printer/sdcp/tests/test_status.py`
  - Did the "no key → None" case **fail** with `assert 1 is None`? Good. If it passed already, stop and investigate — the default changed under you.
- [ ] Apply the change in `status.py` (assignment + docstring).
- [ ] Run `uv run pytest custom_components/elegoo_printer/sdcp/tests/test_status.py`
  - All pass? If not, fix and re-run before continuing.
- [ ] Run `uv run pytest custom_components/elegoo_printer/sdcp/`
  - The whole SDCP suite must pass. If a reader surfaces that the grep missed, report it rather than papering over it.
- [ ] Run `uv run ruff check custom_components/elegoo_printer/sdcp/models/status.py custom_components/elegoo_printer/sdcp/tests/test_status.py`
- [ ] Run `uv run ruff format --check custom_components/elegoo_printer/sdcp/models/status.py custom_components/elegoo_printer/sdcp/tests/test_status.py`
  - `--check`, never bare `format` — a wave child must not rewrite the tree.
- [ ] Commit: `fix(sdcp): report platform_type as unset when the printer omits it (#450)`

**Acceptance criteria:**
- [ ] `PrinterStatus` from a payload with no `PlatFormType` yields `platform_type is None`.
- [ ] `"PlatFormType": 0` yields `0`; `"PlatFormType": 2` yields `2` (passed through, not clamped).
- [ ] The full SDCP test directory passes.
- [ ] `PlatFormType` spelling unchanged everywhere; `ruff check` and `ruff format --check` clean on both files.

---

### Task 2: Add the build_plate sensor to the FDM sensor set

**Parallel:** wave 1 — owns `definitions.py`, `tests/test_entity_setup.py`, and the new `tests/test_build_plate_sensor.py`; disjoint from Task 1 (`sdcp/models/`) and Task 3 (`translations/`)

**Context:**
Adds the entity #450 asks for. It goes in the FDM builder chain because the plate is an FDM concept; CC2 is an FDM printer and keeps the FDM set.

The critical constraint the reporter's snippet misses: with `device_class=SensorDeviceClass.ENUM` and an `options` list, Home Assistant **raises** if `value_fn` returns anything outside `options`. So unknown values map to a declared `"unknown"` option instead of passing through as `str(value)`. `SensorDeviceClass` is already imported at `definitions.py:19` and `options=` is used at `:476`, `:818`, `:834`, `:851` — **no new imports are needed**.

The `options` values and the `value_fn` outputs below are a **contract with Task 3**, which writes matching translation keys — use these exact strings: `"a"`, `"b"`, `"unknown"`.

> **In your own worktree, `PrinterData().status.platform_type` is still `1`**, because Task 1's change is on a sibling branch. So `value_fn(PrinterData())` returns `"b"` there, not `"unknown"`. Build `PrinterStatus` from an explicit payload in your tests (shown below) rather than relying on a bare default — otherwise a test you wrote correctly will fail for a reason outside your task.

**Files:**
- Modify: `custom_components/elegoo_printer/definitions.py`
- Modify: `custom_components/elegoo_printer/tests/test_entity_setup.py`
- Test: `custom_components/elegoo_printer/tests/test_build_plate_sensor.py`

**What to implement:**

1. Add one named constant immediately **above** `def _build_fdm_speed_position_sensor_descriptions()` (which starts at `definitions.py:1050`). A single constant, because the 0→A / 1→B mapping comes from one reporter on one firmware and may need correcting:
   ```python
   # Build plate side, from the SDCP `PlatFormType` field. Observed on a
   # Centauri Carbon at firmware V1.4.49 by the reporter in #450; not
   # corroborated on other models, hence one named map rather than inline
   # literals. An unrecognised value becomes "unknown", never a passthrough:
   # the sensor declares `options`, and Home Assistant raises on a state value
   # outside them.
   _BUILD_PLATE_BY_PLATFORM_TYPE: dict[int, str] = {0: "a", 1: "b"}
   BUILD_PLATE_UNKNOWN = "unknown"
   ```
   **Separate this block with exactly two blank lines from the `]` that closes the preceding builder above it, and exactly two blank lines before the `def` below it.** One blank line either side fails `ruff check` with `E303 Too many blank lines` under this repo's `select = ["ALL"]`; two and two is what `ruff format` expects.
2. Append the description to the list returned by `_build_fdm_speed_position_sensor_descriptions()` (`definitions.py:1050-1106`), which feeds `PRINTER_STATUS_FDM` at `:1109-1114`. `print_speed_pct` at `:1056-1068` is the nearest neighbour in shape:
   ```python
   ElegooPrinterSensorEntityDescription(
       key="build_plate",
       translation_key="build_plate",
       name="Build Plate",
       icon="mdi:layers-outline",
       device_class=SensorDeviceClass.ENUM,
       options=["a", "b", BUILD_PLATE_UNKNOWN],
       value_fn=lambda printer_data: (
           _BUILD_PLATE_BY_PLATFORM_TYPE.get(
               printer_data.status.platform_type, BUILD_PLATE_UNKNOWN
           )
           if printer_data
           and printer_data.status is not None
           and isinstance(printer_data.status.platform_type, int)
           else BUILD_PLATE_UNKNOWN
       ),
   ),
   ```
   Edge cases this must satisfy: `platform_type is None` → `"unknown"`; an unrecognised int such as `2` → `"unknown"`; a non-int wire value such as `"0"` → `"unknown"` (that is what the `isinstance` guard is for — `sdcp/types.py` declares the field `Any`); falsy `printer_data` → `"unknown"`. The falsy-`printer_data` and `status is not None` branches are **defensive and currently unreachable** — `PrinterData()` has no `__bool__`/`__len__` so it is always truthy, and `PrinterStatus` always constructs — but they match the file's existing `value_fn` idiom, so keep them and do not describe them as live paths. **Never return `None`** from this `value_fn`.
3. In `custom_components/elegoo_printer/tests/test_entity_setup.py:117-127`, change `FDM_ENTITY_COUNTS["sensor"]` from `34` to `35`. The resin assertion is a floor (`>= 15`) and must **not** change.
4. Do **not** add `state_class` (invalid on an ENUM by construction), do not add the description to `PRINTER_STATUS_COMMON` or `PRINTER_STATUS_RESIN`, and do not add a passthrough for unknown ints.

**Steps:**
- [ ] Write `custom_components/elegoo_printer/tests/test_build_plate_sensor.py` covering:
  - `value_fn` returns `"a"` for `platform_type == 0`, `"b"` for `1`, `"unknown"` for `2`, `"unknown"` for `None`, `"unknown"` for the string `"0"`;
  - the description is in `PRINTER_STATUS_FDM` and **not** in `PRINTER_STATUS_COMMON` or `PRINTER_STATUS_RESIN`;
  - `options == ["a", "b", "unknown"]` and `device_class == SensorDeviceClass.ENUM`;
  - **the structural guarantee**: `set(_BUILD_PLATE_BY_PLATFORM_TYPE.values()) | {BUILD_PLATE_UNKNOWN} <= set(description.options)` — this is what makes "can never return a value outside `options`" an assertion rather than a claim;
  - a payload-level test using the `PrinterStatus(payload, PrinterType.FDM)` shape from `tests/test_fdm_print_status.py:50-63`, with `"PlatFormType": 0` inside the `"Status"` dict, asserting the sensor reads `"a"`.
  Reuse the `next(d for d in PRINTER_STATUS_FDM if d.key == key)` pattern from `tests/test_job_timestamps.py:18-27`.
- [ ] Run `uv run pytest custom_components/elegoo_printer/tests/test_build_plate_sensor.py`
  - Fails with "no description with key 'build_plate'"? Good. If it passes, stop and investigate.
- [ ] Implement steps 1-3 above.
- [ ] Run `uv run pytest custom_components/elegoo_printer/tests/test_build_plate_sensor.py custom_components/elegoo_printer/tests/test_entity_setup.py custom_components/elegoo_printer/tests/test_sensor_registration.py custom_components/elegoo_printer/tests/test_sensor_units.py`
  - All pass? The registration and units tests must stay green **without edits** — if one fails, the sensor leaked into a shared tuple. Fix and re-run.
- [ ] Run `uv run ruff check custom_components/elegoo_printer/definitions.py custom_components/elegoo_printer/tests/`
  - Watch specifically for `E303` at the constant block (fix with two blank lines, per step 1).
- [ ] Run `uv run ruff format --check custom_components/elegoo_printer/definitions.py`
  - `ruff check` passing does **not** mean the formatter agrees; this catches the blank-line drift that only `--check` reports.
- [ ] Commit: `feat: expose the selected build plate as a sensor for FDM printers (#450)`

**Acceptance criteria:**
- [ ] `build_plate` is registered for `PrinterType.FDM` and absent for `PrinterType.RESIN`.
- [ ] Reads `a` / `b` / `unknown`, and `unknown` for `None`, for an unrecognised int, and for a non-int wire value.
- [ ] The structural assertion proves every value `value_fn` can emit is in `options`.
- [ ] FDM sensor entity count is 35; resin count unchanged; no existing sensor test required an edit.
- [ ] `ruff check` and `ruff format --check` both clean.

---

### Task 3: Add the build_plate translation keys to all 19 locales

**Parallel:** wave 1 — owns only `custom_components/elegoo_printer/translations/*.json`; disjoint from Task 1 (`sdcp/models/`) and Task 2 (`definitions.py`, `tests/`)

**Context:**
The repo enforces translation parity in both directions (`tests/test_translations.py:238` and `:252`): every leaf key in `en.json` must exist in every locale, and no locale may carry a key `en.json` lacks. `_leaf_keys` (`:40-49`) records dot-separated leaves, so `entity.sensor.build_plate.name` plus one leaf per state entry must land in **all 19** files (`en.json` + `ar, cs, da, de, el, es, fr, it, ja, ko, nl, pl, pt, ru, sk, th, zh-Hans, zh-Hant`). All 19 currently carry an identical `entity.sensor` key set — do not break that.

Adding the keys to every file at once is what makes this provable in its own worktree: parity compares key **sets**, not values, and `_leaf_keys` never reads `definitions.py`, so the test passes before the sensor exists. The key strings are a **contract with Task 2**: `name`, `state.a`, `state.b`, `state.unknown`.

The `unknown` leaf is load-bearing, not cosmetic: because the state value is the literal string `"unknown"` and not HA's `STATE_UNKNOWN` sentinel, core's `async_translate_state` short-circuits and does **not** supply a built-in label. What a user reads for Unknown comes from these files, in every language shipped.

**Files:**
- Modify: `custom_components/elegoo_printer/translations/en.json`
- Modify: `custom_components/elegoo_printer/translations/{ar,cs,da,de,el,es,fr,it,ja,ko,nl,pl,pt,ru,sk,th,zh-Hans,zh-Hant}.json`

**What to implement:**

1. In `en.json`, under `entity.sensor`, add:
   ```json
   "build_plate": {
     "name": "Build Plate",
     "state": {
       "a": "Plate A",
       "b": "Plate B",
       "unknown": "Unknown"
     }
   },
   ```
2. Add the **same key structure** to each of the other 18 locale files, **appended as the last key of `entity.sensor` in every file**. Do not try to match a position: the 19 files share an identical key *set* but their orderings already differ, so "same position" is undefined and order is irrelevant to both the JSON loader and `_leaf_keys`.
3. Translate the values. For `a`/`b`, keep the Latin letters A/B as the existing locale files do for identifier-like content, and check each file's own style (e.g. `zh-Hans`/`zh-Hant` typically render this 平台 A / 平台 B). For `unknown`, reuse the word that locale already uses elsewhere for unknown/unavailable.
4. Machine-translated values are acceptable and match how the previous 21-string fill-in shipped; **flag them for native-speaker review in the PR body**, as that change did.
5. Do not reorder existing keys, do not touch anything outside `entity.sensor`, and preserve each file's 2-space indent, UTF-8 literals, and trailing newline.

**Steps:**
- [ ] Use this exact recipe — a throwaway script, **not committed**:
  ```python
  path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
  ```
  `ensure_ascii=False` is not optional. Verified: `indent=2` **without** it rewrites 344-444 lines per file by `\u`-escaping every non-ASCII character, which turns a 4-line addition into a wall of diff. With it, the round-trip is **byte-identical for all 19 files** as they stand today, so a large diff always means the recipe was wrong — not that the file legitimately changed.
- [ ] Run `uv run python -c "import json,glob; [json.load(open(f)) for f in glob.glob('custom_components/elegoo_printer/translations/*.json')]; print('all parse')"`
- [ ] Run `uv run pytest custom_components/elegoo_printer/tests/test_translations.py`
  - A locale left behind fails `test_every_en_key_exists_in_every_locale`; an over-added locale fails `test_no_locale_has_keys_missing_from_en`.
- [ ] Run `git diff --stat -- custom_components/elegoo_printer/translations/`
  - Exactly 19 files, each with a handful of added lines? A file showing hundreds of changed lines means the `ensure_ascii` escape happened — revert that file and redo it.
- [ ] Run `uv run ruff check custom_components/elegoo_printer/` — must be untouched; this task changes no Python.
- [ ] Commit: `feat(i18n): add build plate sensor translations to all locales (#450)`

**Acceptance criteria:**
- [ ] All 19 locale files carry `entity.sensor.build_plate.name`, `.state.a`, `.state.b`, `.state.unknown`, appended last in `entity.sensor`.
- [ ] `test_translations.py` passes with no test file edited.
- [ ] Every locale file parses; `git diff --stat` shows a small addition per file, no reformatting.

---

### Task 4: Integrate, validate, and document

**Parallel:** none — runs after Wave 1 fan-in; owns the whole-tree validation and the user-facing docs

**Context:**
Wave 1 leaves the model, the sensor, and the translations on three branches. This task proves the integrated tree behaves and records the feature where users look for it.

One gap to close here, because nothing in the suite catches it: **no test cross-checks a `translation_key` in code against `en.json`.** A reviewer confirmed that a deliberately misspelled `translation_key` leaves the full suite green — the entity would silently fall back to its raw `name`. Task 4 adds that pin.

Also note the fan-in shape: `test_build_plate_sensor.py` is **created by Task 2 only**. Task 4 must not create it; it appends to the file Task 2 made, in the integrated tree where Task 1's `None` default is actually present.

**Files:**
- Modify: `custom_components/elegoo_printer/tests/test_build_plate_sensor.py` (append)
- Modify: `CHANGELOG.md`
- Modify: `README.md`

**What to implement:**

1. **Append** to `custom_components/elegoo_printer/tests/test_build_plate_sensor.py`:
   - the integrated assertion: a `PrinterStatus` built from a payload with **no** `PlatFormType` key yields a `build_plate` reading of `"unknown"` — this needs Task 1's `None` default and Task 2's mapping together, and is the one behaviour no single wave child could prove;
   - the translation pin: for the `build_plate` description's `translation_key`, assert `en.json["entity"]["sensor"][<key>]` exists and carries `name` plus a `state` entry for every value in `description.options`. This is what stops a mistyped key from silently degrading the entity name.
2. Add one bullet under `## [Unreleased]` → `### Added` in `CHANGELOG.md`, in the file's existing voice: a **Build Plate** sensor for FDM printers showing which plate side the print is using, reporting Unknown when the printer does not report it. Credit the reporter of #450 by handle, as other entries do.
3. In `README.md`, add a **fourth bold-lead paragraph** to `## 📊 Entities` (line ~299), matching the voice of the existing **Firmware update**, **Filament / Canvas**, and **Print file (CC2)** paragraphs. There is no per-entity table to edit — the section is prose. Say what a user sees, and say plainly that a printer which does not report the field shows Unknown.

**Steps:**
- [ ] After fan-in, run `make test`
  - 946 baseline plus the new tests all pass? If the integrated `unknown` assertion fails, Task 1's `None` did not survive fan-in.
- [ ] Run `uv run ruff format . --check`
  - **`--check`, not `make format`.** `make format` runs `ruff format .`, which *rewrites* — it can never report drift, only hide it. `--check` is what CI gates on (`.github/workflows/lint.yml:33`). If it reports `Would reformat`, fix the file rather than auto-formatting blind.
- [ ] Run `make lint`
- [ ] Run `make test` once more after any formatting fix.
- [ ] Confirm no regression in the FDM/resin split: `uv run pytest custom_components/elegoo_printer/tests/test_entity_setup.py -k "fdm or resin"`
- [ ] Commit: `docs: note the build plate sensor (#450)`

**Acceptance criteria:**
- [ ] `uv run ruff format . --check`, `make lint`, and `make test` all pass on the integrated tree.
- [ ] A payload with no `PlatFormType` yields `"unknown"`; payloads with `0`/`1` yield `"a"`/`"b"`.
- [ ] A `translation_key` that has no matching `en.json` block now fails a test.
- [ ] FDM shows 35 sensors, resin unchanged.
- [ ] CHANGELOG and README mention the sensor; the reporter is credited.

---

## Open questions to raise on the PR

1. **Is 0 = Plate A / 1 = Plate B true beyond the reporter's CC1 at firmware V1.4.49?** The mapping is isolated in `_BUILD_PLATE_BY_PLATFORM_TYPE` so a correction is one line. Worth asking #450's reporter and any Neptune/other FDM owner before stable ships.
2. **Does any CC2 field carry the equivalent?** `CC2StatusMapper.map_status()` never sets `platform_type`, so every CC2 owner gets a permanently-Unknown entity. If someone captures a CC2 status payload showing a plate field, wiring it is a small follow-up — and it needs the capture, not an inference.
3. **Do the `state` labels render for a brand-new entity in the frontend?** Nothing in this repo pins it, and there is no `strings.json` — `hassfest` derives its translation source from `en.json`, so these keys are load-bearing for both the app and the validator. Worth one manual look at the entity card before release.
