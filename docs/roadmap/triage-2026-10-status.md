---
status: running
mode: autonomous
updated: 2026-10-24
---

# triage-2026-10 — STATUS

**READ THIS FIRST on every iteration. This file is the loop's only memory.**

## Mission

Clear the open GitHub issue/PR backlog that is real *code* work, in one PR. Six tasks, one commit each, all
on `feature/triage-2026-10`. Finished means: every task row below is `done`, the full gate is green, and the
non-code tracker items (#431 wiki, #419 close, #414 verdict+close) have been actioned as tracker work rather
than commits. The source of scope is the live tracker (`gh issue list` / `gh pr list`), surveyed 2026-10-24,
not any spec document — there is no spec doc for this run (entry was a direct request, not `discuss`/
`research`/`codebase-improvement`), so `finish` skips the spec file and there was no `issue:` key to mirror a
plan comment to.

## Operating mode

Loop protocol (repeat until `status: done`):
1. Read this file. Take the FIRST task whose Status is `todo`. Do not pick, reorder, or skip.
2. Verify the task against the tree before doing it. Trust the tree over the task text.
3. YOU dispatch `researcher` to ground the task (the plan child cannot — it has no subagent tool).
4. YOU dispatch `specify` in CONSUMED MODE (marker `SPECIFY CONSUMED MODE`, the task text, the grounding
   report, and `target: docs/roadmap/triage-2026-10-taskNN-<slug>.md`).
5. YOU dispatch `reviewer` on the plan file (max 3 rounds); findings -> re-dispatch consumed specify to refine
   the same target. Commit the plan doc yourself — the child is forbidden from committing.
6. Implement: wave siblings together per implement's wave protocol (children don't commit; you commit by
   pathspec; scoped tests in-wave). Serial tasks: one `general` subagent, one commit.
7. Run the gate (full suite once per wave). Green -> commit by pathspec -> mark `done` here -> one Changelog
   line. Red -> re-dispatch; never fix by hand in the orchestrator.
8. Update THIS FILE before starting the next task. Then loop.

## Gate — establish before task 1, re-run after every wave (and after each serial task)

    cd /home/daniel/Coding/HomeAssistant/elegoo-homeassistant
    make test     # pytest
    make lint     # ruff check
    make format   # ruff format (a linting command per AGENTS.md — run it too)

Baseline measured at `f7ff097`: **750 passed, 12 warnings**, `make lint` "All checks passed", 36 test files /
37 source modules. (Re-measured: the branch start pulled `2f314c6` + `f7ff097` in from origin after the first
reading at `d5c0e14`, which also read 750 — the number matched, but it was re-measured rather than assumed.)

## Already-done findings (do NOT re-do these)

Verified against the tree on 2026-10-24, so a future iteration does not re-plan shipped work:

- **#419 HA 2026 test failures — shipped.** `coordinator.py` passes `config_entry=entry` into
  `DataUpdateCoordinator.__init__`; `.github/workflows/test.yml` already runs a "Pytest (newest HA, Python
  3.14)" leg. Remaining action is a tracker close, not code.
- **#414 route through the CC2 proxy — shipped.** `CONF_PROXY_HOST` is wired through `config_flow.py`,
  `api.py`, `sdcp/models/printer.py` and `camera.py` (MJPEG URL built from `proxy_host` on the 8080
  pass-through). `docs/roadmap/cc2-proxy-host.md` records `status: implemented`. The camera *multiplexer*
  half of that thread belongs to the elegoo-printer-proxy repo, not this one — recorded as out of scope.
- **#431 wiki troubleshooting tips — not a code task.** The GitHub wiki (`has_wiki: true`) is not in this
  repo; it is edited on the wiki, so it cannot ride this branch.

## Dependency graph / waves

    Wave 1 (PARALLEL, disjoint seams): T1, T2, T3
    Wave 2 (SERIAL, one at a time):   T4 -> T5 -> T6

Forcing symbols / shared files:
- T4 needs T2's `__init__.py` settled (both edit it) -> T4 after wave 1.
- T5 needs T3's `cc2/client.py` settled (both add methods to `ElegooCC2Client`) -> T5 after T3.
- T6 touches `translations/*`, shared with T5 -> T6 after T5.

## Tasks

| # | Task | Plan file | Wave | Seam (disjointness evidence) | Status |
|---|------|-----------|------|------------------------------|--------|
| T1 | #436 device firmware version frozen at setup | `docs/roadmap/triage-2026-10-task01-device-firmware.md` | 1 | `coordinator.py` + `entity.py` + new `tests/test_device_firmware.py` (corrected from "entity.py owned outright" — the fix is *in* coordinator.py; entity.py alone regresses on reload) | `done` `7a4bdd8` |
| T2 | PR #430 `update_ip` service: stale `ip_address` in options, 2 red tests | `docs/roadmap/triage-2026-10-task02-update-ip-options.md` | 1 | `__init__.py` + `tests/test_services.py` | `done` `aae757f` |
| T3 | #435 Print Selected File ignores the chosen tray | `docs/roadmap/triage-2026-10-task03-tray-selection.md` | 1 | 10 existing files, no new file: `sdcp/models/file_info.py`, `cc2/client.py`, `definitions.py`, `select.py`, `services.yaml`, `README.md`, `docs/CC2_PROTOCOL.md`, `cc2/tests/test_start_print.py`, `tests/test_file_select.py`, `cc2/tests/test_file_list.py` (corrected — the row previously claimed "new test file", which does not exist) | `done` `e63ea94` (+ docstring row in `__init__.py`, + changelog `8c50208`) |
| T4 | #425 video lifecycle state onto an integration-owned, entry-id-keyed owner | `docs/roadmap/triage-2026-10-task04-video-state-owner.md` | 2 | `camera.py` + `tests/test_video_lifecycle.py` (corrected — the row previously claimed `camera.py`, `__init__.py`, `data.py`, `tests/test_video_lifecycle.py`; `__init__.py` needs no change because `forget_pending_video_release(entry.entry_id)` already pops by entry id and absorbs the third registry, and `data.py` never held the flag) | `done` `cd2b902` |
| T5 | #432 cleanup old printer files/videos (1047 DELETE_FILE is documented Untested) | `docs/roadmap/triage-2026-10-task05-file-cleanup.md` | 2 | new `cc2/cleanup.py`, `cc2/const.py`, `cc2/client.py`, `__init__.py`, `services.yaml`, `README.md`, `docs/CC2_PROTOCOL.md`, `tests/test_services.py`, new `cc2/tests/test_cleanup_selection.py` + `test_delete_file.py` (corrected — `translations/*` and `blueprints/` are NOT touched: services have no translation surface, and a blueprint was judged not to earn its keep) | `done` `d4549ba` |
| T6 | #429 CC1: manual IP setup when UDP 3000 discovery is unanswered | `docs/roadmap/triage-2026-10-task06-manual-ip-no-discovery.md` | 2 | `config_flow.py`, `api.py`, `translations/*` | `todo` |

The **Seam** column is load-bearing: on resume, wave 1-2 can only be re-verified against the tree from what is
written here. Wave 1's rows were corrected against the tree after the plans were written — both corrections
came from the plan authors, and both original rows were wrong.

## Blockers / open questions

- **#435 is NOT confirmed fixed and must not be closed on a green gate.** The multi-tool `slot_map` explanation
  is the best the repo's evidence supports and the mapping is correct per the protocol doc regardless, but the
  tray choice was already reaching the wire for single-tool files (measured). Only a hardware run — start a
  known multi-tool G-code with a tray other than A1 and watch which tray is consumed — closes it. The code,
  README and `CC2_PROTOCOL.md` all say this; the CHANGELOG line says this. Follow-up: ask the reporter how
  many tools their file uses.
- T5's 1047 DELETE_FILE is documented "Untested" — protocol verification is part of that task's grounding.

## Decisions log

- 2026-10-24 — `<feature>` = `triage-2026-10` (the branch topic), per Step 0. All artifacts named from it.
- 2026-10-24 — No issue-tracker mirror of the plan: no spec doc and no `issue:` key for this entry, and each
  task is already its own issue. Stated once; does not gate the run.
- 2026-10-24 — #431 kept in the mission but executed as a wiki edit, not a branch commit (wiki is not in the
  repo). #419 and #414 recorded as shipped above rather than re-implemented.
- 2026-10-24 — PR #430 is handled as T2 (a rewrite on this branch) rather than a merge of the contributor's
  branch: its `headRefName` is `main`, so it cannot be checked out as a normal head, and the fix needs a
  decision (sync both stores vs drop the stale options key) plus the 2 red tests. Contributor credit is owed
  regardless — record it for the release notes.
- 2026-10-24 — **Wave-1 decomposition corrected by the tree.** My original T3 hypothesis (object aliasing
  loses `selected_tray`) was disproved by measurement: `coordinator.data is api.client.printer_data` for CC2
  and the tray reaches the wire as `tray_id: 1` for A2. My original T2 framing ("needs a decision whether
  options are even written") was also wrong — an options flow exists and writes `ip_address` on every save.
  Both tasks were re-scoped from the grounding reports, not from my initial read.
- 2026-10-24 — T3's `__init__.py` docstring row **deferred out of the parallel wave** to keep T2 sole owner of
  that file; applied by the orchestrator and folded into T3's commit. First attempt at T2's commit swept the
  row in by accident (it described behaviour landing a commit later) — reset and re-committed clean.
- 2026-10-24 — T1 keeps a **second edit in `entity.py`** after the reviewer reproduced the regression: a reload
  re-runs `async_get_or_create(**device_info)`, which writes the frozen `sw_version` back over the registry.
  Registry-only fixes silently undo themselves. `entry.data` is deliberately NOT written on the poll path —
  that fires `add_update_listener(async_reload_entry)` = full reload. Limit recorded in the plan:
  `entry.data["firmware"]` stays the at-setup value; nothing user-visible reads it once `sw_version` is live.

## Changelog

One line per task: hash, what shipped, gate numbers at that moment.

- `7a4bdd8` T1 #436 — device registry `sw_version` follows the printer's live firmware off the poll, plus the
  `entity.py` read so a reload cannot undo it; new `tests/test_device_firmware.py` (7 tests). Gate at commit:
  scoped 7 passed.
- `aae757f` T2 #430 — `update_ip` writes the address to data *and* options in one call; false comment
  removed; 2 assertions updated + 1 merge-outcome test added. Scoped 28 passed.
- `e63ea94` T3 #435 — `PrinterFile.tools` from `color_map[].t`; `print_start` emits one `slot_map` entry per
  tool, byte-identical fallback; e2e test pins select→1020; README + `CC2_PROTOCOL.md` separate measured from
  inferred. Scoped 40 passed (36 → 40, +4, no existing test edited).
- `8c50208` docs — CHANGELOG lines for the three, each crediting its reporter.
- `cd2b902` T4 #425 — shared video-enabled flag moved off the ConfigEntry object into `_video_stream_state_by_entry`; 5 injected traps each caught by exactly one named test. Gate at commit: 766 passed (762 + 4), lifecycle file 75 -> 79.
- `d4549ba` T5 #432 — new `cleanup_files` service (dry-run by default, confirm-by-1044-relist, epoch floor from #426 applied to a destructive path). Gate at commit: **806 passed** (766 + 40), lint clean, format no-op. #432 deliberately NOT closed — 1047 is untested on hardware.
- **Wave 1 gate (once, after all three):** `make format` no-op, `make lint` "All checks passed",
  `make test` **762 passed, 12 warnings** (baseline 750 + 7 + 1 + 4). Tree clean.
