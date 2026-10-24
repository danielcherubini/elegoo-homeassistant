---
status: committed
parent: docs/roadmap/triage-2026-10-status.md
done-when: "`print_start(name, tray_id=N)` for a file whose `color_map` carries tool indices other than 0 puts an entry for EVERY one of that file's tool indices on the wire, each `{\"t\": <tool>, \"canvas_id\": 0, \"tray_id\": N}`; with no file detail available the payload is byte-identical to today's `[{\"t\": 0, \"canvas_id\": 0, \"tray_id\": N}]`, and `tray_id=None` still sends `\"slot_map\": []`; an end-to-end test in `custom_components/elegoo_printer/tests/test_file_select.py` drives the real `ElegooPrintTraySelect` → `_print_selected_file_action` → real `ElegooCC2Client` and asserts the exact 1020 params, so the tray chosen in the UI is pinned all the way to the wire; `make format`/`make lint`/`make test` are green and the suite reads **754 passed** (750 baseline + 4 new tests); and both the `print_start` docstring and `docs/CC2_PROTOCOL.md` § Printing with Canvas state in plain words which parts of this are measured on hardware and which are inferred from the protocol doc. NONE OF THIS CLOSES #435 — **#435 STAYS OPEN** when this commit lands: a green suite proves the payload is right, not that the printer obeys it. The only thing that closes #435 is the hardware run named in § \"The caveat that must survive review\" (print a known multi-tool G-code with a tray other than A1 chosen and watch which tray is actually consumed), and only after asking the reporter how many tools their file uses — so leave the issue open and do not mark it fixed. The changelog / release-note bullet for this commit must use the wording in § \"Release-note wording — do not overstate this\": the mapping fix shipped and #435 remains open pending hardware confirmation. Never write \"the chosen tray is now used for every tool (#435)\" or any other phrasing that implies #435 is resolved."
---

# T3 — #435 Print Selected File always puts usage in A1

**Issue:** [#435](https://github.com/danielcherubini/elegoo-homeassistant/issues/435) — "Printer starts to warm up. All of
the filament usage information was populated in A1 instead of A2." Reporter chose **Print Tray A2**
(PETG in A2, PLA in A3, other two empty) and pressed **Print Selected File**.

## What the bug is NOT (established by the researcher — do not re-do)

The earlier hypothesis that the tray choice is lost to object aliasing is **disproved** by running the repo's own code:
`coordinator.data is api.client.printer_data` for CC2, `selected_tray` survives repeated polls, and the wire for A2 already
carries `{"t": 0, "canvas_id": 0, "tray_id": 1}`. **The mapping is sent; the printer does not honour it.** Do not touch
`select.py`'s state plumbing, `button.py`'s client reference, or the 0-based `tray_id` mapping — an off-by-one would land
on A3, not A1, so it does not fit the symptom either.

## Root cause (repo-grounded)

`ElegooCC2Client.print_start` (`custom_components/elegoo_printer/cc2/client.py:1698-1700`) maps **only tool 0**:

```python
        slot_map: list[dict[str, int]] = []
        if tray_id is not None:
            slot_map.append({"t": 0, "canvas_id": 0, "tray_id": tray_id})
```

Its own docstring, `client.py:1680-1684`, records the measured failure mode this produces:

> "a ``slot_map`` entry must carry ``t``, ``canvas_id`` and ``tray_id`` together. A partial entry, or an out-of-range
> ``tray_id``, is acknowledged with ``error_code`` 0 and the printer silently prints from tray 0 - so the response cannot
> be used to validate the mapping. **Only tool 0 is mapped.**"

Tray 0 = **A1**. "All usage in A1" is precisely what the documented ignore-the-map default looks like.

The protocol doc says the map is per tool, not per job — `docs/CC2_PROTOCOL.md:1815` "``config.slot_map`` maps **each tool
index** of the G-code to a physical tray", and its example (`:1827-1830`) carries **two** entries with **different**
trays. `t` is defined at `:1838` as "the same value as `color_map[].t` in the file list / file detail (methods 1044, 1046)".

So: a G-code that uses tools ≥1 leaves those tools unmapped today, and the reporter had filament in **two** trays — the
signature of a multi-tool file. That is the one defect in this repo's own evidence that the current code cannot explain and
this change can.

## The change (one task, two production lines)

### 1. `PrinterFile` exposes the file's tool indices

`custom_components/elegoo_printer/sdcp/models/file_info.py` — `PrinterFile` parses `color_map` today but keeps only
`color` (`:38-42`), throwing away the `t` that `slot_map` needs. Add a sibling `tools` attribute, right after `colors`:

```python
        self.tools: list[int] = sorted(
            {
                int(entry["t"])
                for entry in data.get("color_map") or []
                if isinstance(entry, dict)
                and isinstance(entry.get("t"), int)
                and not isinstance(entry.get("t"), bool)
                and int(entry["t"]) >= 0
            }
        )
```

Verified behaviour of exactly this expression (run against the real class):
`[{"t":3},{"t":0}] → [0, 3]` (**sorted** — the doc's measured 1046 example at `:1531-1533` is a single entry with `t: 3`,
so tool indices are NOT contiguous from 0 and must not be assumed to be); `[{"t":2},{"t":2},{"t":0}] → [0, 2]` (deduped);
missing `color_map`, `color_map: None`, non-dict entries, a missing `t`, and a string `t` all → `[]`; `t: True` → `[]`
and `t: -1` → `[]` (the two guards exist for exactly these — `bool` is an `int` subclass, and a negative tool would be an
out-of-range entry, which the docstring at `client.py:1682` records as a **whole-map reject**, so one bad entry must not
poison the map).

Also update the class docstring (`:14-16`) to mention `tools`, since it currently enumerates the parsed fields.

### 2. `print_start` maps every tool the file uses

`custom_components/elegoo_printer/cc2/client.py:1698-1700` becomes:

```python
        slot_map: list[dict[str, int]] = []
        if tray_id is not None:
            file = self.printer_data.file_list.get(filename)
            tools = file.tools if file is not None else []
            slot_map = [
                {"t": tool, "canvas_id": 0, "tray_id": tray_id}
                for tool in (tools or [0])
            ]
```

This is the whole production change. Both fallbacks are deliberate and both were run:

- **`tools or [0]`** — no file detail (`file_list` empty, or a file listed without a `color_map`) reproduces today's
  payload **byte-identically**, so single-tool and detail-less prints cannot regress. This is also why the fix reads
  `printer_data.file_list` and not the 1046 `_file_details` cache: `file_list` is already the source of truth on this
  path — `_print_selected_file_available` (`definitions.py:1529-1536`) gates the button on
  `data.selected_file in data.file_list`, so **for the button the entry is guaranteed present**, and 1044 is measured to
  return `color_map` (`docs/CC2_PROTOCOL.md:1495`, `cc2/tests/test_file_list.py:27`). `_file_details` is populated only
  opportunistically from `_update_current_job` (`client.py:1083-1085`) once a print is already running, i.e. too late to
  help at start time. Reading the 1046 cache would have meant a blocking round-trip inside a button press for data we
  already have.
- **`tray_id is None`** (Print Tray = Automatic) still sends `"slot_map": []` — the entry list is built only inside the
  `if`, so "leave the choice to the printer" is untouched.

`canvas_id` stays hard-coded `0`. Do not change it: the doc states 0-based unit IDs (`:1783`, `:1795`) and a single-Canvas
reporter has only unit 0. **UNVERIFIED: multi-Canvas `canvas_id` behaviour** — the doc never describes it and no reporter
has more than one unit.

### 3. Correct the statements this change falsifies

Each currently asserts tool-0-only and would become a lie. One-line edits, no behaviour.

**In this task's wave (files no sibling wave-1 child touches):**

| File | Line | Now says |
|---|---|---|
| `cc2/client.py` | `1665-1666`, `1684` | "maps G-code tool 0 to ``tray_id``"; "Only tool 0 is mapped." |
| `definitions.py` | `1434` | "label -> tray_id for G-code tool 0" |
| `select.py` | `197-198` | "``A1``-``A4`` map to tray 0-3 for G-code tool 0" |
| `services.yaml` | `45`, `95` | "Maps G-code tool 0 only." (both `tray` fields) |
| `README.md` | `219` | "Canvas tray 0-3 (A1-A4) for G-code tool 0" (the `tray:` example comment) |
| `README.md` | `224` | "**Only G-code tool 0 is mapped; this service does not preserve a complete multi-tool mapping.**" |

`README.md:224` is the worst of the six: it does not merely mention tool 0, it *promises the limitation as a documented
behaviour*. After § 2 that sentence is outright false, so it must go in the same commit as the code — a user reading the
README would otherwise avoid the service for a case that now works. `README.md:219`'s comment needs the same treatment.
Neither line is a T1/T2 seam (T1 owns `entity.py` + `coordinator.py`; T2 owns `__init__.py` + `tests/test_services.py`;
T2 explicitly declines `README.md:192-205`, the `update_ip` block, a different section), so both stay in-wave.

Verified safe: `en.json` has no `services` section (`options`/`config`/`entity` only) and no test reads `services.yaml`, so
these descriptions are untranslated and `test_translations.py` parity is unaffected — T6's `translations/*` seam is not
touched.

**DEFERRED — do NOT edit `__init__.py` in this task.** `__init__.py:194-195` (`_async_start_print`'s docstring: "optional
``tray`` (0-based Canvas tray for G-code tool 0)") is also falsified, but **T2 edits `__init__.py` at `:119-125` in the
same wave-1**, and two parallel children must not edit one file. This row is therefore a **serial follow-up for the
orchestrator to run after wave 1 closes**, alongside T4 — which the parent's graph already puts after wave 1 for the same
reason. Hand it to the same serial child as T4, or run it directly.

Safe to defer, and worth stating so the deferral is not read as a hole: the string lives in a **docstring**. The function
itself *is* tested — `tests/test_services.py` (T2's file) exercises `_async_start_print`'s behaviour — but **no test reads
`__doc__` anywhere in this component**, and this change does not alter that behaviour. So between this commit landing and
the deferred edit, the only cost is one stale sentence in a docstring no test and no user reads. Nothing depends on it, and critically the deferral does not leave a test red. The sweep is small and bounded: `grep -n
"tool 0" custom_components/elegoo_printer/__init__.py` has exactly **one** hit today (`:195`), so the deferred edit is a
one-line change.

### 4. Measured vs inferred — the honesty deliverable

Rewrite the `print_start` docstring paragraph at `client.py:1680-1684` so it separates the two, and add the matching note
to `docs/CC2_PROTOCOL.md` § "Printing with Canvas" (`:1813-1843`). Keep every measured claim already there — they are
good. Add, in words:

**Measured on firmware 02.01.00.00**
- an entry must carry `t` + `canvas_id` + `tray_id` together; a partial entry or an out-of-range `tray_id` is acked with
  `error_code` 0 and the printer prints from tray 0 → the response cannot validate the map.
- `color_map[].t` is the G-code tool index, not a tray (`:1502`).
- an empty `slot_map` leaves the choice to the printer.

**Inferred, NOT measured**
- that a **complete** map (an entry for every tool the file uses) makes the printer honour the tray. This is the protocol
  doc's own wording ("maps each tool index") plus the two-entry example — but that example is attributed to the
  elegoo-link SDK's serialisation, **not a packet capture**, and the slicer's own captured Start Print (`:1925-1953`)
  sends `"slot_map": []`. No packet capture of a real multi-tray job exists in this repo.
- what the printer does with a tool index **absent** from `slot_map`. The doc is silent. "It falls back to tray 0" is the
  natural reading of the reporter's symptom, not a documented behaviour.
- whether a complete map is *required* for any entry to be honoured, or merely sufficient.

Mark the doc addition with the repo's existing vocabulary — the doc has no confidence markers on this section at all
(`Untested` appears only in the method table, `:1457-1460`), so **absence of a marker there is not evidence of
verification**; say so rather than implying the section is measured.

## Tests

Extend, do not duplicate. **These tests go into three EXISTING files — this task creates no new test file** (see
§ Parallelisation for the accurate seam declaration).

Two lint conventions in these directories are easy to trip and both are measured, not assumed:

- **`cc2/tests/*.py` has only `S101`, `SLF001`, `PLC0415` in `per-file-ignores`** — `D103` is NOT ignored there. So every
  new top-level test function in `test_start_print.py` needs the file's existing trailing-marker convention:
  `def test_start_...() -> None:  # noqa: D103`. All six existing tests in that file carry it; a bare docstring-less
  `def` trips `D103 Missing docstring in public function` and `make lint` goes red. (Verified: a probe function without
  the marker reports D103 under this config; one with a docstring does not — so either the marker or a real docstring is
  required. Follow the file's own style and use the marker.) `tests/*.py` (where `test_file_select.py` lives) *does*
  ignore `D103`, so nothing new is needed there.
- **Import insertion must land in sorted position, or `make lint` fails and `make format` will not save you.** `I001
  Import block is un-sorted or un-formatted` is an isort rule: `ruff format` deliberately does not touch import order.
  Verified — a probe file with an unsorted block reports `I001 [*]`, `ruff format` answers "1 file left unchanged", and
  `ruff check` still reports `I001` with exit 1. `make lint` is `ruff check .` with **no `--fix`** (Makefile), so the
  naive "add the import at the bottom of the block, then run the gate" sequence ends red. Do it one of two ways:
  insert each import at its correct sorted position (order is `__future__` → stdlib → third-party (`pytest`,
  `homeassistant.*`) → first-party `custom_components.*`, alphabetical within each block, one blank line between blocks
  — which is what both target files already look like), **or** run `make fix` (`ruff check . --fix`) *before*
  `make lint`; `I001` is in the safe-fix set, so `make fix` resolves it. Note `make fix` touches the whole repo, so check
  `git diff` afterwards to be sure it changed only your lines.

**`custom_components/elegoo_printer/cc2/tests/test_start_print.py`** — add, reusing the existing `_client()` / `_response()`
helpers and the `patch.object(client, "_send_command", ...)` capture pattern:
- `test_start_maps_every_tool_the_file_uses` — seed
  `client.printer_data.file_list = {"multi.gcode": PrinterFile({"filename": "multi.gcode", "color_map": [{"t": 0, ...}, {"t": 2, ...}]})}`,
  call `print_start("multi.gcode", tray_id=1)`, assert the full params with
  `[{"t": 0, "canvas_id": 0, "tray_id": 1}, {"t": 2, "canvas_id": 0, "tray_id": 1}]`.
- `test_start_without_file_detail_falls_back_to_tool_0` — empty `file_list`, assert the payload is exactly today's
  `[{"t": 0, "canvas_id": 0, "tray_id": 1}]`. **This is the byte-identical regression pin; do not skip it.**
- `test_start_tool_order_and_garbage` — a file whose `color_map` is `[{t: 3}]` maps `t: 3` (not `t: 0`), and a `color_map`
  with a non-int / negative / duplicate `t` yields the clean sorted subset.

The three existing tests (`test_start_default_sends_empty_slot_map_and_printer_check`,
`test_start_with_tray_sends_full_slot_map_entry`, `test_start_without_bed_leveling_sends_printer_check_false`) must pass
**unedited** — none of them seeds `file_list`, so all three exercise the `tools or [0]` fallback. If one of them needs
changing, the change is wrong; stop and re-read § 2.

**`custom_components/elegoo_printer/tests/test_file_select.py`** — the real end-to-end pin. `test_print_button_passes_the_chosen_tray`
(`:186-190`) cannot cover the wire because its `client` is a `MagicMock` stub. Add a test that uses the **real**
`ElegooCC2Client`, the real `ElegooPrintTraySelect`, and the real `_print_selected_file_action`, capturing `_send_command`:

```python
def test_chosen_tray_reaches_the_wire_for_every_tool() -> None:
    """The tray picked in the Print Tray select must reach method 1020."""
    printer = Printer()
    printer.printer_type = PrinterType.FDM
    client = ElegooCC2Client("192.168.1.1", "TESTSN", printer=printer)
    client.printer_data.file_list = {
        MULTI: PrinterFile({"filename": MULTI, "color_map": [
            {"t": 0, "color": "#111111", "name": "PETG"},
            {"t": 2, "color": "#222222", "name": "PLA"},
        ]})
    }
    client.printer_data.selected_file = MULTI

    coordinator = MagicMock()
    coordinator.generate_unique_id = lambda key: f"testsn_{key}"
    coordinator.data = client.printer_data        # the SAME object the client reads
    select = ElegooPrintTraySelect(coordinator, PRINTER_TRAY_SELECT_CC2[0])
    select.async_write_ha_state = MagicMock()
    asyncio.run(select.async_select_option("A2"))

    with patch.object(client, "_send_command", new_callable=AsyncMock,
                      return_value={"result": {"error_code": 0}}) as mock_cmd:
        asyncio.run(_print_selected_file_action(client))
        mock_cmd.assert_called_once_with(CC2_CMD_START_PRINT, {
            "storage_media": "local", "filename": MULTI,
            "config": {"slot_map": [{"t": 0, "canvas_id": 0, "tray_id": 1},
                                    {"t": 2, "canvas_id": 0, "tray_id": 1}],
                       "printer_check": True}})
```

This sketch was **run as-is** against the tree: red before the change (only `t: 0` on the wire) and green after, which is
what makes it a real pin rather than a restatement of the implementation. It needs four names added to that module, and
because both files are already isort-clean the insertion is **two new statements plus two edits to existing ones**, each in
sorted position: new `from ...cc2.client import ElegooCC2Client` and `from ...cc2.const import CC2_CMD_START_PRINT`
statements above the existing `definitions` import (`cc2.*` sorts before `definitions`), `PrinterType` added to the
existing `sdcp.models.enums` import (`ElegooMachineStatus, PrinterType`), and `Printer` added to the existing
`sdcp.models.printer` import (`Printer, PrinterData`). Verified as imported-already, so do not re-add: `MagicMock`, `patch`,
`AsyncMock`, `asyncio`, `PrinterFile`, `ElegooPrintTraySelect`, `PRINTER_TRAY_SELECT_CC2`, `_print_selected_file_action`.
Define `MULTI = "multicolor.gcode"` beside the existing `BENCHY`/`CLIP` constants (`:28-29`). Note
`coordinator.data = client.printer_data` —
the aliasing the researcher disproved is load-bearing *in the correct direction* here, and this test documents that.

**`custom_components/elegoo_printer/cc2/tests/test_file_list.py`** — assert `file.tools` next to the existing
`file.colors` assertion (`:76`) for `SKULLBOWL` (`[0]`), so the `tools` parse is pinned on the real 1044 path, not only
through the constructor.

## Gate

From repo root, baseline **750 passed / lint clean** at `f7ff097` (re-measured on this branch: 750 passed, 12 warnings).

**Expected count after this task: 754 passed.** Derived, not guessed — the steps above add exactly four test functions and
change no existing one:

| File | New test functions | Running total |
|---|---|---|
| baseline | — | 750 |
| `cc2/tests/test_start_print.py` | +3 (`..._every_tool_the_file_uses`, `..._falls_back_to_tool_0`, `..._tool_order_and_garbage`) | 753 |
| `tests/test_file_select.py` | +1 (`test_chosen_tray_reaches_the_wire_for_every_tool`) | 754 |
| `cc2/tests/test_file_list.py` | +0 — one extra `assert file.tools == ...` **inside** an existing test, so no new test id | **754** |

I applied the whole change set (both production edits + all three test files) and ran the full suite: **754 passed, 0
failed**; `ruff check .` "All checks passed"; `ruff format .` "123 files left unchanged". Then I reverted the two
production files and kept only the tests, and the four new tests failed while the other 36 in those files passed — the
red/green proof that they pin real behaviour. **754 is the number to check the loop's gate against; 750 means the tests did
not land, and anything else means an existing test was edited** (which § Tests forbids).

    make format && make lint && make test
    VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/cc2/tests/test_start_print.py \
      custom_components/elegoo_printer/tests/test_file_select.py \
      custom_components/elegoo_printer/cc2/tests/test_file_list.py -q

(Remember from § Tests that `make format` does not fix `I001` — run `make fix` first if `make lint` reports it.)

**Blast radius measured, not assumed:** no existing test seeds a multi-tool `color_map` into `file_list`, so nothing else
observes the new behaviour; the only existing test touched at all is the one added assertion in `test_file_list.py`. As
this plan is written the tree is back at baseline — **750 passed**, `custom_components/` clean in `git status` — because a
plan child must not leave implementation behind.

## Release-note wording — do not overstate this

AGENTS.md requires the release notes to "cover every issue handled in the cycle", so the orchestrator's changelog step
(#435, step 7 of the loop) will reach for this commit. Its instinct will produce the sentence this plan exists to prevent:
"the chosen tray is now used for every tool (#435)" — which reads as *#435 is fixed*, and § "The caveat that must survive
review" explains why nobody knows that yet. So:

- **`CHANGELOG.md` `[Unreleased]` and the release-note bullet must both say the mapping changed and that #435 is still
  open.** Use this wording (edit for voice, keep both clauses and the open state):

  > `Print Selected File` and the `start_print` action now map **every** tool a multi-tool G-code uses to the tray you
  > chose, not just its first one; files whose tool list the printer never reported keep working exactly as before.
  > Reported by @… in #435, **which stays open until someone can confirm on a real printer which tray it consumes** —
  > a green test suite proves what we send, not what the printer obeys.

- **Never** write "fixed #435", "closes #435", "the tray is now honoured", or any bullet that implies the printer's
  behaviour was verified. AGENTS.md's own rule — "state known gaps plainly, briefly" — is satisfied by the one sub-clause
  above; that is the budget it gets.
- Keep the reporter credit (AGENTS.md: thank issue reporters, tied to their issue number). If the reporter's handle is not
  on the issue, credit them as "the reporter in #435" rather than dropping the attribution.
- If the hardware run in the caveat section has happened by release time and confirmed the tray is honoured, *then* the
  open-state clause can be dropped and #435 closed — in the same cycle, with the confirmation named.

## Parallelisation

**SERIAL.** One task, one commit. **Accurate seam — ten files edited in place, zero files created:**

- production (5): `sdcp/models/file_info.py` (§ 1 `tools`), `cc2/client.py` (§ 2 + § 3 + § 4), `definitions.py`,
  `select.py`, `services.yaml`
- user docs (2): `README.md` (`:219`, `:224`), `docs/CC2_PROTOCOL.md` (§ 4, § "Printing with Canvas")
- tests (3, **all pre-existing, extended in place — no new test file**): `cc2/tests/test_start_print.py`,
  `tests/test_file_select.py`, `cc2/tests/test_file_list.py`
- explicitly **not** edited by this task: `__init__.py` (deferred, § 3), `translations/*` (T6), `button.py` (follow-up #2)

So the parent status doc's T3 seam row ("`cc2/client.py`, `definitions.py`, new test file") is wrong in both directions:
there is **no new test file**, and it omits `select.py`, `services.yaml`, `README.md`, `docs/CC2_PROTOCOL.md` and
`sdcp/models/file_info.py`. The orchestrator should correct that row when it marks the task, since the doc says the Seam
column is load-bearing for resume.

Wave-1 disjointness holds **only because the `__init__.py` docstring edit is deferred** (§ 3): T2 owns `__init__.py` in
this same wave, so keeping it out of this task's steps is what makes the parallel wave safe. T1 owns `entity.py` +
`coordinator.py` — disjoint. The remaining shared-file risk is `README.md`: T2 lists `README.md:192-205` as out of scope
and T1 does not touch `README.md`, so `README.md:219`/`:224` are free — but if a sibling plan grows a README edit while
this one runs, the orchestrator serialises them.

Per the parent's dependency graph this is a wave-1 sibling of T1 and T2, but **T5 must not start until this lands** — both
add members to `ElegooCC2Client`. And the deferred `__init__.py` row from § 3 runs **after wave 1**, serially, alongside T4.

## Out of scope / follow-ups

0. **DEFERRED FROM § 3, still owed — not out of scope:** the `__init__.py:195` docstring correction (the only "tool 0" hit
   in that file). Run it **serially after wave 1** (alongside T4, which the parent graph already sequences there for the
   same shared-file reason), because T2 owns `__init__.py` in this wave. Until it lands, the only cost is one stale
   sentence in a docstring no test reads.
1. **The real bug #1, unfixed here:** `ElegooPrintTraySelect.async_added_to_hass` (`select.py:217-226`) returns early when
   `coordinator.data is None` — the offline-setup path from #171 — and never retries, so a saved tray choice is lost for
   the life of the entry after a restart with the printer off. Same shape in the file select's `async_added_to_hass`
   (`select.py:152-162`, guard-and-`return` at `:158-159`). Evidence: the guard is a plain `return` with no listener/hook
   to re-run it once the first poll lands data.
2. **The real bug #2, unfixed here:** `ElegooSimpleButton` (`button.py:69`) caches the client in `__init__` (`:72-91`) at
   `:84-86` — `self._elegoo_printer_client = coordinator.config_entry.runtime_data.api.client` — so a post-setup client
   replacement leaves the button pressing against a stale `PrinterData`, consumed at `:99` (`async_press`) and `:107`
   (`available`).
3. **One tray for a whole job is semantically weak for multi-tool files.** This change maps *all* of a file's tools to the
   single chosen tray, which is what one select can express — but the protocol supports per-tool trays, and the doc's own
   example sends two different `tray_id`s. A per-tool tray picker is the real UX fix; it needs a design decision, so it is
   not smuggled in here.
4. **`canvas_id` with more than one Canvas unit** stays hard-coded 0 — see § 2. UNVERIFIED.
5. **The simulator cannot validate any of this.** `scripts/test_cc2_printer.py:334-352` ignores `config` entirely on 1020,
   so `make test-cc2-printer` proves nothing about `slot_map`; wiring it to echo the received `config` back would make
   manual verification possible and is its own small task.
6. **Ask #435's reporter how many tools their file uses** before closing it. See the caveat below — this fix has a real
   chance of changing nothing for them.

## The caveat that must survive review

The multi-tool explanation is the best one the repo's evidence supports, and mapping every tool is correct per the
protocol doc **whatever** the reporter's file looks like — so the change is worth landing. But it is not certain to close
issue #435, and the plan should not pretend otherwise:

- **If the reporter's file was single-tool (only `t: 0`)**, then the mapping they needed was already on the wire
  (`{"t": 0, "canvas_id": 0, "tray_id": 1}` — the researcher proved it) and **this change does not alter their payload at
  all**. The issue would stay open, and the remaining explanation is that the printer declines a correct single-tool map,
  which is outside this repo's control and would need a firmware-side workaround.
- "All of the **filament usage information** was populated in A1" may describe the Canvas *usage display* attributing
  consumption to A1 rather than the printer physically drawing from A1. Those are different bugs with different fixes, and
  the report does not distinguish them.

So: land the change, pin the wire, and **do not mark #435 fixed on the strength of a green suite.** The only thing that
closes it is a hardware run — print a known multi-tool G-code with a tray other than A1 chosen and watch which tray the
printer actually consumes. Until someone does that, the causal link between "unmapped tool" and "usage in A1" is
**UNVERIFIED: no hardware access in this session, and the protocol doc does not specify the unmapped-tool behaviour.**

This paragraph is the reason § "Release-note wording — do not overstate this" exists and the reason `done-when` names
"#435 STAYS OPEN" explicitly: everything above is honest, but the orchestrator reads `done-when` and writes the changelog,
not this section. Keep the two in sync if either changes.
