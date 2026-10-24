---
status: staging
updated: 2026-10-24
---

# triage-2026-10 — FOLLOW-UPS

**Staging artifact.** `finish` deletes the whole `docs/roadmap/triage-2026-10-*` family on ship. Fold the
durable content here into `docs/features/` or an ADR **before** running `finish`, or it is lost.

Each item: what it is, its **status**, and why it was left.

---

## Hardware verification owed (three issues must NOT be closed on a green gate)

- **#435 — the tray mapping.** Status: **open work (a run, not a code change).** `print_start` now maps every
  G-code tool the file reports instead of only tool 0, and an end-to-end test pins the tray reaching the wire.
  But the tray choice *already* reached the wire for single-tool files (measured during grounding: A2 produced
  `{"t": 0, "canvas_id": 0, "tray_id": 1}`), so if the reporter's file was single-tool this change does not
  alter their payload and the issue survives. What is unknown: what the printer does with a tool absent from
  `slot_map`, whether a complete map is required or merely sufficient, and multi-Canvas `canvas_id`. The
  protocol doc's two-entry example is elegoo-link SDK serialisation, not a packet capture, and that doc section
  carries no confidence markers — absence of "Untested" there is not evidence of verification.
  **The run:** start a known multi-tool G-code with a tray other than A1 and watch which tray is consumed.
  Also ask @jsbrich how many tools their file used.

- **#432 — `cleanup_files` (method 1047).** Status: **open work (a run).** 1047 is documented "Untested" and
  the protocol doc records **no response shape at all**. Every assertion in the suite runs against a reply shape
  this repo invented from the shared envelope. The design is built so the unknown is never load-bearing (dry run
  by default, confirm by fresh 1044 re-list, partial success reported as failure), so shipping it is safe — but
  nobody has seen a printer answer. `docs/CC2_PROTOCOL.md` carries a placeholder for the first real capture.
  **The run:** one dry run then one real delete, assigned to @tvidal-net, who has the hardware and designed the
  include/exclude scheme. Ask them whether the printer's file list ever contains video files.

- **#429 — manual IP without discovery.** Status: **open work (a run).** No CC1 was touched. Which attributes
  frame shape a real CC1 sends is genuinely unknown — the repo's own evidence disagrees (`scripts/test_server.py`
  nests under `Data`; `test_client_conformance.py` and `mqtt/client.py`'s "WebSocket fallback format" comment are
  flat), so the probe reads both. **The run:** @SketchyUnicorns confirms setup on the printer that reported it.

## Real bugs found during this run and deliberately NOT fixed

- **`select.py` restore never retried after offline setup.** Status: **recorded, not open work in this run;
  genuinely open as a bug.** `ElegooPrintTraySelect.async_added_to_hass` returns early when `coordinator.data`
  is `None` — the #171 offline-setup path — and nothing ever retries, so a saved tray choice is lost for the
  life of the entry after a restart with the printer powered off. Same shape in `ElegooPrintFileSelect`
  (`select.py:152-162`). Left out of #435 because it is a different bug with a different fix; bundling it would
  have made the tray change look like it fixed something it did not.

- **`ElegooSimpleButton.__init__` caches `api.client` once.** Status: **recorded, not open work.** A post-setup
  client replacement would leave every action button pressing against a stale `PrinterData`. No production path
  replaces `api.client` today, so it is latent, not live.

- **`PrinterFile.colors` has no production consumer.** Status: **accepted.** Grep finds only its own definition
  and tests; the extruder sensors read `gcode_filament_data.color_map` instead. Not a defect; noted for whoever
  next trims the model.

- **`async_step_manual_ip` with a blank-but-non-null IP raises
  `ElegooPrinterConfigurationError`** from the `ElegooPrinterClient(...)` constructor, before any guard runs.
  Status: **pre-existing, measured identical on `main`** — this run neither introduced nor worsened it.
  `_async_validate_input` remains dead code sitting behind the same wall.

- **Wall 2 in `reconnect()` has no dedicated test.** Status: **recorded, thin pin.** `async_create` is pinned
  directly; `reconnect()` shares the one TCP-reachability helper that three tests do pin, so the behaviour is
  covered indirectly but nothing names `reconnect()` specifically.

- **T4's removal hook can warn twice.** Status: **accepted, recorded.** When a teardown retry notices the removal
  itself, both the retry and `forget_pending_video_release` log a warning. Chosen deliberately: the alternative
  was silencing the operator's only notice that a removed printer's video is stuck on.

## Off-branch tracker actions (cannot be commits — do these on the tracker)

- **#431 — wiki section.** Status: **open work, off-branch.** The GitHub wiki is not in this repo, so it cannot
  ride the branch. Needs: the ports the page asks users to open, and the gateway-ping behaviour (a CC1 stops
  answering SDCP discovery when it cannot ping its default gateway). Do **not** close #431 on the code commit.
- **#419 — close.** Shipped long ago (`config_entry=` passed to `DataUpdateCoordinator`, plus a newest-HA CI leg).
  Needs the nixpkgs packager's confirmation before closing.
- **#414 — verdict + close.** Proxy routing shipped (`CONF_PROXY_HOST` through config flow, api, camera). The
  camera **multiplexer** half of that thread belongs to `elegoo-printer-proxy`, not this repo — say so plainly
  rather than letting it read as unfinished here.
- **PR #430 — comment + close.** Superseded by `aae757f`; credit @caohuongls (the fix originates with them).
- **#429 / #432 / #435 — comment, do not close.** Each needs its hardware run.

## Notes for the next reader

- The `## [Unreleased]` section of `CHANGELOG.md` is **rolling** — it still lists items shipped in earlier
  releases. Write release notes from `git log v<prev>..v<cur>`, never from it (AGENTS.md).
- Three issues that looked open at triage time were already shipped (#419, #414, and #431's code half). Verify
  against the tree before planning from the tracker.
- `translations/` was predicted to need 18-file changes for two tasks and needed **neither**: services have no
  translation surface (no `strings.json`, no `services` key in any locale), and #429 reused the existing
  `no_printer_found` key. Only a *new config-flow error key* forces the 18-file fan-out.
