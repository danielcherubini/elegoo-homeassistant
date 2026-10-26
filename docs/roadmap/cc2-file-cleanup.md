---
status: implemented
issue: https://github.com/danielcherubini/elegoo-homeassistant/issues/432
shipped: PR #444 (bda7d5f) delivered Tasks 1-3; Task 4's capture landed 2026-10-26 and row 2 fired (no parameter shape deletes on stock firmware) — docs half done, code decision deferred by maintainer
done-when: A volunteer runs one script against a real Centauri Carbon 2 and returns raw 1047 replies that say definitively which parameter shape the printer accepts; the delete path is then either proven working or declared dead, and no user-facing text anywhere claims the opposite of what the capture showed.
---

> **Status — done-when met (2026-10-26).** @MrHaku81 ran `scripts/cc2_probe_delete.py
> --run` on real hardware: all 11 parameter shapes plus the empty control answered
> `1003`, no file ever removed — the `file_name` hypothesis is dead, and the capture
> leaves it at "either 1047 is not served on stock firmware or `1003` is its generic
> not-handled answer" ([capture](https://github.com/danielcherubini/elegoo-homeassistant/issues/432#issuecomment-6412925224)).
> The delete path is declared dead for practical purposes — nothing in the tested matrix
> deletes, and no user-facing text oversells it — which is what this plan existed to
> achieve. Two follow-ons live outside this plan now: (a) the
> service's fate — capability-gate vs removal — deferred by the maintainer ("leave it,
> decide later", decision table kept below); (b) the videos half, where @MrHaku81
> measured a working LAN path (1036 + `/download`, documented in `docs/CC2_PROTOCOL.md`)
> and has open offers to PR the docs and the feature himself. Kept, not deleted, until
> both settle.

# CC2 file cleanup — make `cleanup_files` true on real firmware

**Goal:** Resolve why `method 1047` deletes nothing on firmware 02.01.01.07, and stop shipping documentation that oversells a service we have now measured failing.

**Architecture:** Three unblocked tasks and one blocked one. First, correct every user-facing claim the hardware data has already falsified — those are wrong *today*, whatever the probe finds. Second, make the in-repo printer simulator a usable proving ground: it answers unhandled methods `1001 unknown interface` with no shape sensitivity, has no HTTP upload endpoint, and boots mid-print — all three must change before it can validate anything. Third, ship a standalone probe script a volunteer runs against real hardware to capture 1047 replies across parameter variants. Only then does code change, and its shape depends on the capture.

**Tech Stack:** Python 3.13, `aiomqtt`, `aiohttp`, Home Assistant service schema (`vol.Schema`), pytest, ruff. `scripts/` and `debug.py` are excluded from ruff (`pyproject.toml` `[tool.ruff] exclude`) and from pytest (`testpaths = ["custom_components"]`, `norecursedirs = ["scripts"]`), so a probe script there cannot break the gate.

---

## What the hardware data actually says

From @MrHaku81 on issue #432 (comment `6398977756`, 2026-10-25T14:37:51Z), Centauri Carbon 2 Combo, firmware `ota_version = 02.01.01.07`, access-code pairing, printer idle:

- Existing file → 1047 → `{"result": {"error_code": 1003}}` in ~13 ms; re-list with 1044 still shows the file; **confirmed still visible on the printer's own display**.
- Non-existent file → **the same `1003`** in ~10 ms.
- Control: the same file deleted fine from the printer's UI. So the file was not locked — **the LAN delete does nothing**.
- 1044 does not list the two timelapse videos visible under the display's camera tab, for `storage_media` of `local`, `sdcard` or `udisk`.
- Not tested: the never-set-clock / 1970 case (their printer's clock is set, UTC+1).

## The hypothesis this plan is built around

`1003` is `CC2_ERROR_INVALID_PARAMETER` (`custom_components/elegoo_printer/cc2/const.py:98`) — a *request-shape* rejection, not a refusal to delete. And this integration already sends inconsistent key names to sibling file methods:

| Method | Params the code actually sends | Verified status |
|---|---|---|
| 1045 `GET_FILE_THUMBNAIL` | `{"storage_media": "local", "file_name": filename}` — `custom_components/elegoo_printer/cc2/client.py:1890-1891` | ✅ Works on stock firmware (`docs/CC2_PROTOCOL.md:1455`) |
| 1046 `GET_FILE_DETAIL` | `{"storage_media": "local", "filename": filename}` — `custom_components/elegoo_printer/cc2/client.py:1810-1811` | ✅ Works (`docs/CC2_PROTOCOL.md:1456`) |
| **1047 `DELETE_FILE`** | `{"storage_media": storage_media, "filename": filename}` — `custom_components/elegoo_printer/cc2/client.py:2625-2626` | ❌ `1003`, deletes nothing |

So the printer's file methods do **not** share one parameter spelling — 1045 is proof. `filename` may simply be wrong for 1047. Supporting signal: a request rejected on shape returns the identical code whether or not the file exists, which is what was observed; and `1003` is never referenced anywhere in production code, only in tests.

Nobody has been offered this variant. @MrHaku81 offered `storage_media="sdcard"`, "different filename encodings", and "extra fields" — not the key-name swap. It is the cheapest decisive test available, against a CC2 they say is still paired and idle.

**Treat it as a hypothesis, not a conclusion.** 1046 works with `filename`, so per-method naming could still leave 1047 wanting something else entirely. The probe must discriminate, not confirm.

---

## Parallelisation

**Verdict:** SERIAL — Task 1 and Task 2 have disjoint `Files:` lists, but both verify through `uv run` / `make test`, which makes uv sync the project and can rewrite `uv.lock` and prune `.venv` (observed directly in this repo on 2026-10-25: a `uv sync` pruned installed packages mid-session). Two children sharing one working tree and one lockfile fail gate 3, and a concurrent sync makes a child's RED/GREEN reading untrustworthy. Task 3 additionally needs Task 2's simulator to be meaningful. With one task already blocked on hardware, the serial cost is small.

---

### Task 1: Correct the user-facing claims the hardware data already falsified

**Parallel:** none — runs first; see the SERIAL verdict above.

**Context:**
`cleanup_files` shipped in v2.16.0-beta.1 described as merely *untested*. It has since been tested, and it failed. Four places now tell users something the evidence contradicts, one of them being the protocol document other client authors build from. This task changes **no behaviour** — the service's re-list-to-confirm design is correct and is what made the failure visible rather than silent (it already reports `still listed after delete` and `success: false`). It changes only what we claim.

Do this before the probe, not after: the claims are false right now, and a beta.2 that ships them a third time is worse than one that ships nothing.

**Files:**
- Modify: `README.md` (the `cleanup_files` section, lines 247–294; specifically line 261 "Run the dry run first" rationale and line 267 the videos bullet)
- Modify: `custom_components/elegoo_printer/services.yaml` (the `cleanup_files:` block, lines 115–199; the `description:` at 117–133)
- Modify: `docs/CC2_PROTOCOL.md` (line 1457 table row `| DELETE_FILE | 1047 | Untested | — |`; the `### Delete File [Both]` section at 1603–1634; the Addendum line at ~2445 "never verified on hardware"; and the 1045 param-spelling inconsistency found during review — see step 5)
- Modify: `CHANGELOG.md` (the `cleanup_files` bullet under `[Unreleased]`, line ~26, which says "Never exercised on hardware, so **#432 stays open** until one real run confirms it")
- Test: `custom_components/elegoo_printer/tests/test_services.py` (existing file — no new tests; see the pin below)

**What to implement:**

1. **`docs/CC2_PROTOCOL.md` line 1457** — change the 1047 row from `Untested` to record the observation, matching the table's existing vocabulary (`✅ Works` / `Untested`):
   `| DELETE_FILE | 1047 | ❌ No-op | Returns `error_code: 1003` and deletes nothing on fw 02.01.01.07; the same file deletes fine from the printer UI (#432) |`
2. **`docs/CC2_PROTOCOL.md` `### Delete File [Both]` (1603–1634)** — the section currently says "nothing in this repository has ever received a 1047 reply" and carries a "**Placeholder for the first real capture**" block. Replace the placeholder with the **actual capture**, keeping the section's existing shape (request JSON, then `Response:` JSON, then a `| Field | Type | Description |` table, as 1044/1046/1048 do). Record verbatim: both request and reply for the existing-file case and the non-existent-file case, the ~13 ms / ~10 ms round-trips, `storage_media` values tried, firmware `02.01.01.07`, model, pairing mode, and that the file was confirmed still present on the printer's display. Credit @MrHaku81 and link #432 — the document already credits them by name for the `delay_video` correction at line 2002, so match that style. State the `file_name` hypothesis **as an untested hypothesis**, and state plainly that the identical code for existing and non-existent files is what a request-shape rejection looks like.
3. **`docs/CC2_PROTOCOL.md` Addendum (~2445)** — the line ending "Unticked until someone captures a real reply" has been captured; update it to say a reply exists, that it is `1003`, and that the delete does not happen. Leave the checkbox unticked — the capability is not working.
4. **`services.yaml` `cleanup_files` description** — it currently says the delete command "is documented as untested on hardware and its reply shape is not documented at all". Replace with the measured fact, **using the response shape's own vocabulary rather than a looser paraphrase**: on firmware 02.01.01.07 the printer refuses every delete, so a real run reports each selected file in `failed` as `printer refused the delete (error_code 1003)` and answers `success: false`; the dry run is currently the only useful mode. Keep it user-facing — no method numbers beyond what the user literally sees in the response, no function or file names. Do **not** write "every file fails" as a bare claim; describe the `failed` map and `success: false`, which is what the caller actually receives.
5. **`docs/CC2_PROTOCOL.md` 1045 param spelling (found during plan review — fix while in this file).** The document contradicts the shipped client: line 665 and the `### File Thumbnail (Method 1045)` section (~2766–2790) both document 1045 taking `filename`, but `_request_file_thumbnail` sends `file_name` (`client.py:1891`) and 1045 is recorded as ✅ Works. Reconcile the doc to the working client — this is the same class of error as the 1047 row, and it is actively misleading to anyone writing a client from the document, which is exactly the audience this doc exists for.
6. **`README.md`** — same correction at line 261 (why the dry run defaults on) and line 267 (videos). For videos, the firmware data now answers the open question the bullet poses ("if yours does, say so on #432"): on 02.01.01.07 timelapse videos are **not** in the 1044 list for any `storage_media`, so state that as observed rather than as an invitation. Keep the "no extension filter, so a list that does contain videos will match them" behaviour intact — that is still true and still the right design.
7. **`CHANGELOG.md`** — correct the "Never exercised on hardware" clause to record the result.

**Do NOT change:** any code under `custom_components/` other than `services.yaml`. Not `delete_file`, not `_async_delete_each`, not the schema, not the re-list confirmation. Also do **not** remove the service or add firmware gating — both were considered and deferred (see Task 4).

**The pin you must not break:** `test_the_day_count_ceiling_matches_the_one_the_ui_advertises` (`custom_components/elegoo_printer/tests/test_services.py:1648`, assertion at `:1673`) reads `services.yaml` as raw text and asserts `f"max: {_MAX_OLDER_THAN_DAYS}"` — i.e. the literal `max: 3650` — is present. Keep that text.

**No other test pins these files.** Verified by grepping the test tree: the day-count test is the only one that reads `services.yaml`, and no test reads `README.md`, `CHANGELOG.md`, or `docs/CC2_PROTOCOL.md`. `test_translations.py` covers `translations/` only and does not touch service descriptions.

**Steps:**
- [ ] Read `README.md:247-294`, `custom_components/elegoo_printer/services.yaml:115-199`, `docs/CC2_PROTOCOL.md:1440-1470`, `1603-1634`, `2439-2446`, the 1045 section at `2766-2790` and the method table at line 665, and `CHANGELOG.md`'s `[Unreleased]` `cleanup_files` bullet, before editing any of them.
- [ ] Make the seven edits above.
- [ ] Run `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_services.py`
      → All pass. If `test_the_day_count_ceiling_matches_the_one_the_ui_advertises` fails, you removed `max: 3650` from `services.yaml`; restore it.
- [ ] Run `make lint` → clean. Run `VIRTUAL_ENV=.venv uv run ruff format . --check` → clean. **Use `--check`, not `make format`**: CI (`lint.yml:33`) runs `ruff format . --check`, and a bare `format` rewrites files and hides drift instead of reporting it. If a file needs reformatting, run `VIRTUAL_ENV=.venv uv run ruff format <that file>` on that file only, so the diff stays confined to files this task touched.
- [ ] Run `make test` → the full suite passes unchanged. It reports **949 passed** at plan time (2026-10-25). A docs task must not change the count; if it moves, something else got touched — investigate rather than accepting the new number.
- [ ] Grep for leftovers of the old claim:
      `grep -rn "untested on hardware\|Untested\|never verified on hardware\|Never exercised on hardware\|never seen one on a printer\|ever received a 1047 reply\|No response is documented for 1047\|does not treat the reply as proof" README.md CHANGELOG.md docs/CC2_PROTOCOL.md custom_components/elegoo_printer/services.yaml`
      → every remaining hit must be one you deliberately kept.
- [ ] Commit: `docs: record the first real 1047 reply, and correct what cleanup_files claims`

**Acceptance criteria:**
- [ ] No user-facing text says 1047 is untested, or that its reply shape is undocumented.
- [ ] `docs/CC2_PROTOCOL.md` contains the raw reply payloads for both cases, with firmware, timings, and the display-confirmed "file still present".
- [ ] `docs/CC2_PROTOCOL.md` documents 1045 with the parameter key the working client actually sends.
- [ ] A reader of the README can tell, before calling the service, that a real delete currently does nothing on current firmware.
- [ ] Full suite passes unchanged; `make lint` and `ruff format . --check` clean.
- [ ] Zero changes under `custom_components/` other than `services.yaml`.

---

### Task 2: Make the CC2 simulator answer 1044/1047 so a probe can be validated

**Parallel:** none — serial (see verdict). Task 3 depends on it.

**Context:**
The probe must not be handed to a volunteer untested — they'd be the only safety net, and a script that mis-parses replies would produce a confident wrong answer about a $500 printer. The repo already has a printer simulator, `scripts/test_cc2_printer.py` (814 lines: an `amqtt` MQTT broker, a UDP discovery server on port 52700 at `udp_discovery_server` line 652, registration, and a command dispatcher `handle_command` at line 305). It is the natural validation target.

The simulator needs three things to be a usable validation target, not one: parameter-shape sensitivity for 1047 (below), an **HTTP `PUT /upload` endpoint** — it serves MQTT and UDP discovery only, so the probe's disposable-file upload has nowhere to go and every variant would abort — and a way to **start idle**, because it boots mid-print (`STATUS_PRINTING` / `test_benchy.gcode`, lines 155-156 and 195-205) and the probe's idle gate correctly refuses to run against it. All three are added here; without them Task 3's validation steps are unrunnable and its acceptance criteria unprovable.

It cannot validate a 1047 probe today, and the reason matters for how you fix it. `handle_command` seeds `result = {"error_code": 0}` at line 324 and overwrites it in an `if/elif/…/else` chain. **1047 is declared as a constant (`CC2_CMD_DELETE_FILE = 1047`, line 71) but never handled**, so it reaches the final `else` at line 504, which prints `❓ Unknown command` and sets `{"error_code": 1001, "error": "unknown interface"}` (line 505). So the simulator answers 1047 with `1001 unknown interface` — a *different* code from the `1003` the real printer returns. There is no crash on this path: `method` is bound at line 308 (`method = payload.get("method", 0)`) and the response is published normally.

The consequence for the probe is the same either way and is the actual justification for this task: **the simulator cannot distinguish parameter shapes at all.** Every 1047 gets one fixed `1001` regardless of payload, so a probe validated against it would report nine identical rows and learn nothing — it would prove only that the probe can send nine requests. The probe needs a target whose answer *changes* with the parameter shape, so that a probe which fails to discriminate is caught before real hardware.

It also has no file list, so 1044 is unhandled and falls to the same `else`.

**Files:**
- Modify: `scripts/test_cc2_printer.py` (add an `elif` branch per method inside `handle_command`, alongside the existing branches at ~326-503; add module-level file-list state next to the existing `printer_attributes` / `printer_status` state)
- Test: none — `scripts/` is outside `testpaths` and outside ruff's include set, so no automated test is possible without moving the simulator, which is out of scope. Verify by running it (steps below).

**What to implement:**

1. **A mutable file list as simulator state**, e.g. `printer_files: list[dict]`, seeded with several entries shaped like real 1044 records — keys exactly as the printer spells them: `filename`, `size`, `create_time`, `layer`, `print_time`, `total_filament_used`, `color_map`, `type` (see `CC2FileEntry`, `custom_components/elegoo_printer/cc2/types.py:90-100`, and `PrinterFile`, `custom_components/elegoo_printer/sdcp/models/file_info.py:27-39`). **Match the parse gates exactly**, or entries get silently dropped and the probe's `still_listed` check lies: `filename` must be a `str` — anything else is dropped as unreadable (`_parse_file_entries`, `client.py:287-369`), and any dropped entry makes the listing `_FILE_LIST_UNREADABLE`, which the strict `get_verified_file_list` **raises** on (`:2199-2200`). Note the distinction: a `type` in `CC2_NON_FILE_TYPES` (`dir`/`directory`/`folder`, `client.py:132`) is *deliberately skipped* and leaves the listing trustworthy, whereas `filename: None` or `filename: 123` is a drop. Seed `type: "file"` (or omit it — absent is what measured firmware sends) and `create_time: 0` is safe and represents the never-set-clock case (`PrinterFile` maps it to `created=None`). Seed fresh on each simulator start so repeated probe runs are reproducible.
2. **Handle 1044** → return `{"error_code": 0, "file_list": printer_files}`.
3. **Handle 1047 with a deliberately strict, configurable policy** so the probe's discrimination can be *proven*, not assumed. Add a module-level switch, e.g. `DELETE_PARAM_KEY = "filename"`, and make 1047:
   - return `{"error_code": 1003}` when the expected key is absent from `params` — regardless of whether the named file exists;
   - return `{"error_code": 0}` and actually remove the entry from `printer_files` when the key is present and the file exists;
   - return `{"error_code": 1007}` when the key is present but the file does not exist (`CC2_ERROR_FILE_DELETE_FAILED`, `custom_components/elegoo_printer/cc2/const.py:103`).
   This mirrors the shape-sensitivity the real printer appears to show, so a probe that varies the key gets visibly different answers and a probe that varies nothing learns nothing.
4. **Print each 1047 request's full `params`** to stdout, matching the existing `📨 Command from {client_id}: method={method}, id={request_id}` style at line 322, so a run can be eyeballed for which variant produced which code.
5. **An HTTP `PUT /upload` endpoint**, so the probe can create its own disposable target against the simulator. `upload_gcode` (`custom_components/elegoo_printer/cc2/upload.py:64-139`) does `PUT http://{host}:80/upload` with `X-File-Name`, `X-File-MD5`, `X-Token` and `Content-Range` in 1 MiB chunks, and aborts on any non-200 or `error_code != 0`. Nothing in `scripts/` serves HTTP today, so without this every probe variant aborts at upload. Use `aiohttp` (already a project dependency; `debug.py:9` imports it). It must accept the chunked PUT, verify the token, and **append the completed file to `printer_files`** so the 1044 branch lists it — a file that never appears in the listing makes `still_listed: false` meaningless. Bind it to a port the probe can be told about; port 80 needs root, so make it configurable and have the probe take a matching flag.
6. **A way to start idle.** The simulator boots mid-print — `STATUS_PRINTING` with `filename: "test_benchy.gcode"` (`scripts/test_cc2_printer.py:155-156`, `195-205`) — so the probe's idle gate correctly refuses to run against a fresh simulator. Add a `--idle` start flag (preferred: explicit, and it keeps the printing-state default that other tests may rely on). Alternatively an executor can send `1022 STOP_PRINT`, whose branch at `:358` sets `STATUS_IDLE` and clears the filename — but a flag is less fragile than requiring every run to remember a command.
7. **Document both switches in the module docstring** — how to flip `DELETE_PARAM_KEY` to `"file_name"` to confirm the probe keys off the parameter rather than guessing, and how to start `--idle`.

**Do NOT** "fix" the `else` branch's `1001` behaviour or otherwise touch unrelated dispatch — it is correct (an unknown interface genuinely is `1001`) and out of scope.

**Steps:**
- [ ] Read `scripts/test_cc2_printer.py` fully before editing — match its existing state and logging idioms.
- [ ] Implement the state + two branches + the param-key switch.
- [ ] Start the simulator in one shell: `VIRTUAL_ENV=.venv uv run python scripts/test_cc2_printer.py` (confirm the real invocation from the file's own docstring first).
- [ ] **Drive it with a throwaway snippet, not a committed file.** `scripts/cc2_mqtt_debug.py` has a `send_command(method, params)` helper at line 257 but **no CLI flag to invoke an arbitrary method** — it always runs its fixed 1001/1002/1042 sequence. Do not edit it (out of scope) and do not hand-roll MQTT. Instead run, from the repo root, a short inline script through the real client, e.g. `VIRTUAL_ENV=.venv uv run python - <<'PY' … PY`, that does `sys.path.insert(0, ".")`, builds `ElegooCC2Client(printer_ip="127.0.0.1", serial_number=SERIAL_NUMBER, access_code=MQTT_PASSWORD)` using the simulator's own module constants — `SERIAL_NUMBER = "CC2TEST1234567890"` at `scripts/test_cc2_printer.py:33` and `MQTT_PASSWORD = "123456"` at `:45` (the simulator reports `token_status: 0` at `:257`, so no access code is genuinely required) — then `await connect_printer(...)`, then `await client._send_command(1047, {...})` for each payload, printing each reply. Discard it afterwards — it validates the simulator, it is not the deliverable.
- [ ] Send 1047 with `{"storage_media":"local","filename":"<a seeded name>"}` → expect `error_code: 0` and the file gone from a subsequent 1044.
- [ ] Send the same delete with the key renamed to `file_name` → expect `error_code: 1003`, file still listed.
- [ ] Send a delete for a name that does not exist, with the correct key → expect `1007`.
- [ ] Flip `DELETE_PARAM_KEY` to `"file_name"`, restart, and confirm the two results swap. **This is the step that proves the simulator discriminates.** If they do not swap, the probe built on it will not either.
- [ ] Verify the upload endpoint independently: `curl` a single-chunk `PUT /upload` with `X-File-Name`, `X-File-MD5`, `X-Token` and a correct `Content-Range`, expect HTTP 200 with `{"error_code": 0}`, then send 1044 and confirm the uploaded name is now listed. Include `-H 'Content-Type: application/octet-stream'` and `--data-binary` in the curl — omitting the content type makes the request body unreadable and yields a 400 that looks like an endpoint bug but is not. Then repeat with a **wrong token** and a **corrupt MD5** and confirm both are rejected — an upload endpoint that accepts anything will let the probe build its safety guarantee on nothing.
- [ ] Verify `--idle` actually idles: start with it, send 1002, confirm `machine_status` is idle and `print_status.filename` is empty.
- [ ] Run `make test` → full suite passes unchanged (949 at plan time). Nothing under `custom_components/` moved.
- [ ] Commit: `feat(scripts): 1044/1047 sensitivity, upload endpoint and idle start in the CC2 simulator`

**Acceptance criteria:**
- [ ] The simulator's answer to 1047 changes when the parameter key changes, and swaps when `DELETE_PARAM_KEY` is flipped.
- [ ] A successful 1047 actually removes the entry, and a following 1044 reflects it.
- [ ] A 1047 for a missing file (`1007`) is distinguishable from a 1047 with a bad parameter shape (`1003`).
- [ ] A chunked `PUT /upload` lands in `printer_files` and shows up in the next 1044; a wrong token or a corrupt MD5 is rejected.
- [ ] `--idle` starts the simulator reporting idle with no filename.
- [ ] Full suite passes unchanged.

---

### Task 3: The 1047 parameter-shape probe script

**Parallel:** none — serial; depends on Task 2's simulator.

**Context:**
This is the deliverable that unblocks everything else, and the reason beta.2 is held. Nothing in this repository had ever received a 1047 reply until @MrHaku81 ran it by hand; `docs/CC2_PROTOCOL.md:1623` literally asks whoever runs it first to paste the raw reply. The job now is to make that capture systematic and repeatable by a volunteer, across every parameter variant that could plausibly explain `1003`.

**Use the real `ElegooCC2Client`, not a hand-rolled MQTT client.** A self-contained paho-mqtt script (the `scripts/cc2_mqtt_debug.py` pattern) is easier to hand to someone without a checkout, but a probe that re-implements the protocol proves only that *its own* re-implementation works — a positive result would not transfer to our code path. Using the shipped client means a `file_name` success is immediately actionable as a one-line change to `delete_file`. Import outside Home Assistant works and `debug.py` already constructs `ElegooCC2Client` with no HA runtime (`debug.py:63`, run by `make debug`) — but note precisely *why*: importing `custom_components.elegoo_printer.cc2.client` executes the parent package's `__init__.py`, which imports `homeassistant` (`__init__.py:23`). It works because `homeassistant` is an installed project dependency, not because the client avoids it — so the script must run under `uv run` (or the repo venv), not against a bare interpreter on a volunteer's laptop.

**Files:**
- Create: `scripts/cc2_probe_delete.py`
- Modify: `DEVELOPMENT.md` (add a short "Probing a real printer" subsection next to the existing Debug Script section at lines 132–148)

**What to implement:**

A CLI script, `uv run python scripts/cc2_probe_delete.py --ip <addr> [--serial <sn>] [--access-code <code>] [--run]`, that:

0. **Starts with the repo's own path idiom**, before importing anything from the component: `sys.path.insert(0, str(Path(__file__).parent.parent))` — the established convention at `scripts/extract_cc2_data.py:32` and `scripts/test_embedded_mqtt_broker.py:11`. Without it the import depends on cwd and breaks when run from `scripts/`.

1. **Resolves the serial by UDP discovery first, and aborts cleanly if it cannot.** Use `CC2Discovery.discover_as_printers(...)` (`custom_components/elegoo_printer/cc2/discovery.py:307`), the same path `debug.py` uses, rather than going straight to `client.async_discover_serial()` — that method (`client.py:950`) only reads the serial off an incoming status **push**, so it returns `None` against a printer or simulator that has not registered us yet, and the script must not sit through its full timeout and then carry on with `None`. `--serial` short-circuits discovery entirely — when both the ip and the serial are given, skip the broadcast (it costs up to ~30 s of retries for nothing). If neither path yields a serial: print a clear "run with --serial" message and exit non-zero. A wrong serial is silent death — every topic is `elegoo/{sn}/…`, so registration and all commands 404.

2. **Connects — and does not let discovery silently retarget the printer.** `connect_printer` overwrites both connection details from whatever `Printer` you hand it: `self.printer_ip = printer.connection_host or self.printer_ip` and `self.serial_number = printer.id or self.serial_number` (`client.py:824-825`). So passing the `Printer` returned by discovery makes `--ip` and `--serial` dead arguments, and on a LAN with more than one CC2 — or one where a discovery broadcast is answered by a proxy or another VLAN — the probe would silently delete files on the **wrong printer**. Therefore: resolve ip and serial into plain strings first (from flags, or from discovery), construct `ElegooCC2Client(printer_ip=resolved_ip, serial_number=resolved_serial, access_code=…, logger=…)`, then call `await client.connect_printer(Printer())` with a **bare** `Printer` — whose `connection_host` and `id` are `None` (`sdcp/models/printer.py:180-187`, `256-258`), so the constructor values survive. If both `--ip` and discovery succeed and disagree, abort and say so rather than picking one. Credentials: the access code *is* the MQTT password and the HTTP `X-Token`; with no code the client already falls back to `""` then `"123456"` (`client.py:837-843`) — do not invent a derivation.

3. **Refuses to run unless the printer is idle — with the guard in the right order.** `await client.refresh_status()` (`client.py:2211`) returns **`None`**; the result is read back from `client.printer_data.status`. The real attribute path is **`client.printer_data.status.current_status`** (an `ElegooMachineStatus`) and **`client.printer_data.status.print_info.filename`** (`sdcp/models/printer.py:535` → `sdcp/models/status.py` `current_status`, `:316` `print_info`).

   **The filename is the primary guard, not the status code.** `CC2StatusMapper.map_status` resolves a machine code it does not recognise to `ElegooMachineStatus.IDLE` (`cc2/models.py:141-143`), so brand-new firmware reporting a new "printing-ish" code arrives here *already looking idle* — `current_status` is never "unknown", and there is no way to fail closed on it through the mapped object. The integration documents this exact limit at `custom_components/elegoo_printer/__init__.py:497-502`, and its own primary guard is the filename, which the mapper copies through untouched. So: treat `not status.print_info.filename` as the load-bearing check, and `current_status in {IDLE, STOPPED}` as the backstop for a printer that says busy while naming nothing. Do **not** write "anything unrecognised counts as busy" — that is not achievable from the mapped status; if it is genuinely wanted, judge the raw 1002 reply instead. Compare against `ElegooMachineStatus.IDLE` / `ElegooMachineStatus.STOPPED` (`sdcp/models/enums.py:78`) — **do not import `_IDLE_MACHINE_STATUSES`**, which lives in the component root `custom_components/elegoo_printer/__init__.py`, and that module imports `homeassistant` at line 23 and would drag the whole runtime in. Re-derive the two-member set locally and comment why. `refresh_status` raises on a refused or unreadable 1002 — catch it and **abort**, never treat it as idle.

4. **Creates its own disposable target, one per variant.** Generate a tiny G-code body and upload it with the standalone `upload_gcode` in `custom_components/elegoo_printer/cc2/upload.py:64-67` — `UploadTarget(host=ip, token=access_code or "123456")`, `UploadFile(name, size, md5)`, an async chunk iterator, `PUT :80/upload` (`CHUNK_SIZE` 1 MiB, `UPLOAD_PORT` 80). **Never delete a file the script did not create.** If an upload fails, abort that variant and record why — never fall back to an existing file. Take a `--upload-port` flag defaulting to 80: `UploadTarget` carries only `host` and `token`, and the port is the module-level `UPLOAD_PORT` interpolated into the URL (`upload.py:36`, `:82`), so the flag is implemented by setting `upload.UPLOAD_PORT = args.port` at probe startup — there is no other route to a non-80 endpoint. The real printer uses 80; the simulator cannot bind a privileged port.

   **Settle after uploading, before listing.** Nothing in the protocol guarantees a freshly uploaded file appears in the next 1044 immediately, and a transient absence reads as `still_listed: false` — a **false positive for a working delete**, which is the worst possible failure for this script. Re-list until the uploaded name appears, with a bounded wait (say 30 s), and **abort the variant if it never appears** rather than recording `false`. A variant that could not verify its own target must be reported as inconclusive, not as success.

   **One fresh upload per variant, not one file for the whole matrix.** The matrix below has ~11 entries; give each its own file named `cc2_probe_<variant-slug>_<8 hex>.gcode`. Reason: the probe's only trustworthy signal is "was the file still listed afterwards", and that signal is destroyed the moment one variant succeeds — with a single shared file, variant 1 deleting it makes the rest report `still_listed: false` for a reason that has nothing to do with their parameters, and the probe can no longer tell "rejected on shape" from "accepted but ignored". One file per variant makes each row an independent trial.

   **Flag the hand-written-file caveat in the report.** Whether the printer accepts a bare hand-written G-code body for upload *and* for delete is itself unmeasured — a real sliced file carries a slicer header and thumbnail block. So a `1003` against a hand-written file is ambiguous between "wrong parameter shape" and "this file is not deletable". Mitigate by also naming the probe files plausibly (`.gcode`, non-trivial size) and by stating the caveat in the report header, so a volunteer pasting it into #432 is not misread. If the control `{}` variant returns a different code from the shaped requests, the shape hypothesis is at least partly confirmed regardless.

5. **Sends the variant matrix** via `await client._send_command(1047, params)` (`client.py:2282`), which returns the raw envelope untouched — `_handle_response` (`client.py:1449`) has no 1047 branch and stores the full envelope for any waiter. For each variant `V` with its own uploaded file `N_V`: send, record reply, then `await client.get_verified_file_list()` (`client.py:2118`, strict — it raises rather than returning a stale listing) and record whether `N_V` is still present. Order:
   - `{"storage_media": "local", "filename": N}` — what we ship today
   - `{"storage_media": "local", "file_name": N}` — **the hypothesis**
   - `{"storage_media": "local", "filename": N, "path": "/"}` — 1044 sends a `path`; 1047 may want one
   - `{"storage_media": "local", "file_name": N, "path": "/"}`
   - `{"filename": N}` — is `storage_media` itself the offending key?
   - `{}` — control: what a bare request answers
   - `{"storage_media": "sdcard", "filename": N}`, `{"storage_media": "sdcard", "file_name": N}`
   - `{"storage_media": "udisk", "filename": N}`
   - **plus the hyphenated spellings `{"storage_media": "sd-card", "filename": N}` and `{"storage_media": "u-disk", "filename": N}`.** `sdcard`/`udisk` come from the HTTP download paths (`docs/CC2_PROTOCOL.md:1591-1597`), whereas the document's own Storage Media Types table (~line 1442) gives `local` / `u-disk` / `sd-card`. The vocabulary is genuinely uncertain, so probe both spellings — otherwise a difference in the `sdcard` rows cannot be told apart from a typo.

6. **Records the firmware properly.** `refresh_status` (1002) does not populate firmware; `printer_data.attributes.firmware_version` is filled from a **1001** attributes reply (`_handle_attributes`, `client.py:1940`; parsed at `cc2/models.py:333-334` from `software_version.ota_version`). Send 1001 and read it from there, or record the raw 1001 reply. A report without the firmware string it was captured on is not usable.

7. **Prints and saves a paste-ready report**: one block per variant with the exact request JSON, the full raw reply JSON, elapsed ms, and `still_listed: true|false`; plus firmware, model, `token_status`, and a timestamp. Write it to `cc2_probe_<timestamp>.json` beside the script, matching `cc2_mqtt_debug.py`'s capture-file idiom. End with a line telling the volunteer to paste the whole block into #432.

8. **Cleans up**: delete each variant's own probe file using whichever variant reported success; if none did, name every leftover file in the report so the volunteer can remove them from the printer's UI.

9. **`--run` is required for the destructive matrix; default is a non-destructive dry run** that connects, discovers, checks idle, uploads, prints the exact matrix it *would* send, and cleans up the uploads.

**Constraints:** `scripts/` is ruff-excluded, so style is free, but keep it readable — contributors read it. Only `aiomqtt` and `aiohttp` beyond the stdlib; both are existing project dependencies. State in the docstring that **Home Assistant must not be running**: registration fails with `too many clients` while another client holds the slot (`client.py:1269-1275`).

**Steps:**
- [ ] Read `scripts/cc2_mqtt_debug.py` and `debug.py` first and follow their connection, arg-parsing and capture idioms.
- [ ] Write the script with the dry run as the default and `--run` required for deletes.
- [ ] Validate against the Task 2 simulator **started with `--idle`** (it otherwise boots mid-print and the probe correctly aborts — see Task 2 item 6). With `--dry-run`: connects, resolves serial, uploads, prints the matrix, cleans up, aborts safely.
- [ ] Run the probe for real against the simulator with `--run` and `--upload-port` pointed at the simulator's endpoint. **Expected: the `filename` variants return `0` and their own file disappears; the `file_name` variants return `1003` and theirs remain** (simulator default is `DELETE_PARAM_KEY = "filename"`). If every variant returns the same code, or every `still_listed` is identical, the probe is not discriminating — fix it before going near hardware.
- [ ] Flip the simulator's `DELETE_PARAM_KEY` to `"file_name"`, re-run, and confirm the codes swap **and** that the `still_listed` column follows the key rather than the row order. **This is the probe's real acceptance test.**
- [ ] Confirm the probe aborts against a simulator started **without** `--idle` (printing, filename set) — and that it aborts on the *filename* check specifically, since that is the primary guard.
- [ ] Confirm the settle loop: point the probe at a simulator whose upload lands but whose 1044 never lists the file, and confirm the variant is reported **inconclusive**, not `still_listed: false`. A false positive here would ship a wrong conclusion to #432.
- [ ] Confirm the probe never deletes a file it did not upload, and that `--ip` is not silently overridden by discovery (start a simulator, pass a deliberately different `--ip`, confirm it aborts on the disagreement rather than connecting to the discovered host).
- [ ] Run `make lint`, `VIRTUAL_ENV=.venv uv run ruff format . --check`, and `make test` → clean, suite unchanged at 949. The script is excluded from lint and tests; this only proves you disturbed nothing.
- [ ] Add the DEVELOPMENT.md subsection: how to run it, HA must be off, it only ever deletes its own uploads, and the output is meant to be pasted into #432.
- [ ] Commit: `feat(scripts): probe 1047 parameter shapes against a real CC2`

**Acceptance criteria:**
- [ ] Against the simulator, the probe reports *different* results for `filename` vs `file_name`, and both the codes and the `still_listed` column swap when the simulator's key is flipped.
- [ ] Each variant is measured against its own uploaded file; no variant's result can be contaminated by an earlier variant's success.
- [ ] A variant whose uploaded file never appears in the 1044 listing is reported **inconclusive**, never as a successful delete.
- [ ] Default is non-destructive; deletes require `--run`.
- [ ] It can only ever delete a file it uploaded itself, and refuses to run when the printer names a file it is printing.
- [ ] `--ip` is never silently overridden by a discovery answer; a disagreement aborts.
- [ ] Discovery failure produces a clear error and a non-zero exit, never a silent `None` serial.
- [ ] Output is a paste-ready block containing request, raw reply, timing, `still_listed` per variant, the firmware string, and the hand-written-file caveat.
- [ ] Suite unchanged; `make lint` and `ruff format . --check` clean.

---

### Task 4: Row 2 fired — no 1047 parameter shape deletes on stock firmware; code decision deferred by maintainer

**Status (2026-10-26):** The capture exists — @MrHaku81 ran `scripts/cc2_probe_delete.py --run` on real hardware ([#432 comment 6412925224](https://github.com/danielcherubini/elegoo-homeassistant/issues/432#issuecomment-6412925224)). All 11 parameter shapes, including the empty-params control, answered the identical `error_code 1003`, and all 11 confirming 1044 re-listings saw the variant's own file still present. **Row 2 of the decision table below applies: the `file_name` hypothesis is dead** (a request with no parameters cannot be rejected for its key name), and no shape in the matrix deletes anything. The capture does not decide between "1047 is not served" and "`1003` is its generic not-handled answer" — both predict the identical replies it recorded — so the docs state the measured refusal, not a verdict on the method's presence. The decision-table code change — firmware-capability gate vs removing the service — was **explicitly deferred by the maintainer on 2026-10-26** ("leave it, decide later"): the service currently fails loudly and honestly on every call, which is tolerable for a beta. The docs half of row 2 is done: README, the protocol doc and the beta.2 release notes now state the capture's conclusion.

**Was:** BLOCKED — act on the capture. **Do not begin the code half without a real-hardware capture.** (Keep the table below for when the decision is un-deferred, or if a future capture on different firmware lands in another row.)

**Context:**
beta.2 is deliberately held for the hardware answer (maintainer decision, 2026-10-25). The code change depends entirely on which branch the capture lands in, so this task is a decision table, not a specification. Whichever branch applies gets its own plan pass — this task exists so the branch is chosen from evidence rather than guessed now.

**Files:** depend entirely on the branch — see below.

**Decision table:**

| Capture result | Action |
|---|---|
| A variant returns `0` **and** its own file disappears from the next 1044 | Change the params dict in `delete_file` (`custom_components/elegoo_printer/cc2/client.py:2625-2626`) to the winning shape. Update `docs/CC2_PROTOCOL.md` to `✅ Works` with the working shape. Add a client-level test to `custom_components/elegoo_printer/cc2/tests/test_delete_file.py` pinning the exact params sent, and a handler test in `custom_components/elegoo_printer/tests/test_services.py`. Re-evaluate whether the second 1044 listing is still worth its cost per file — **but only with a capture showing the reply is trustworthy**; keep it otherwise. Correct `README.md` / `custom_components/elegoo_printer/services.yaml` / `CHANGELOG.md` to say it works, and name the firmware it was proven on. |
| Every variant returns `1003` | 1047 is dead on stock firmware. Do **not** leave a service that reports refusal for every file. Decide between gating on firmware capability (**new territory — there is no version-comparison helper anywhere in the CC2 path; `awesomeversion` is used only in `custom_components/elegoo_printer/update.py`; firmware is read at `custom_components/elegoo_printer/cc2/models.py:333-334` and reaches `printer_data.attributes` only via a 1001 attributes reply, `client.py:1940`**), or removing the service for beta.2. Note both `test_async_setup_registers_cleanup_files_service` (`custom_components/elegoo_printer/tests/test_services.py:1020`) and `test_async_setup_registers_update_ip_service` (`:127`, asserts `call_count == 4`) pin the registration, so removal touches both. |
| Some variants return `0` but their files stay listed | The printer accepts and ignores. Treat as dead (row 2) and record the accept-but-no-op behaviour in the protocol doc — it is the trap that would make a trust-the-reply implementation silently wrong, and it vindicates the re-list design. |
| Only a `sdcard`/`udisk`/`sd-card`/`u-disk` row behaves differently from `local` | The `storage_media` vocabulary differs from what we send. Check whether the list we get from 1044 is even on the media 1047 addresses, and whether the file-select entities assume `local`. Compare against the doc's Storage Media Types table and update it to the observed truth. |

**Also in scope for this task, whichever branch:** the videos half of #432 moved on 2026-10-26. @MrHaku81 first reported 1044 never lists timelapse videos on 02.01.01.07 for any `storage_media` — confirmed — and then measured that the MP4s are **reachable over the LAN anyway** via method 1036's `time_lapse_video_url` plus the ordinary `GET /download` endpoint, with the full semantics (silence-not-404 on missing files, head-probe to confirm existence, 1051 render rules, 1038 being history-only) documented in `docs/CC2_PROTOCOL.md` under "Timelapse Videos over LAN" ([#432 comment 6414982376](https://github.com/danielcherubini/elegoo-homeassistant/issues/432#issuecomment-6414982376)). So a video *inventory and download* path exists on stock firmware; a video *delete* path still does not (only the display deletes). @MrHaku81 offered to open both a documentation PR and a feature PR (history sensor + download helper + optional render service) — the maintainer accepted both; if those land, this paragraph is satisfied by them and the "investigate 1046/1048" instruction is obsolete.

**Acceptance criteria:**
- [ ] A real capture exists on #432 before any code in this task is written.
- [ ] The chosen branch is recorded in this document with a link to the capture.
- [ ] No user-facing text overstates what the capture proved.

---

## Open questions to settle on #432 (owner: maintainer, who has reserved replies)

1. Was the delete attempted while the printer reported idle, and did a control request `{}` also return `1003`? A bare request returning `1003` would mean the whole envelope is wrong, not just a key.
2. Does `1046 GET_FILE_DETAIL` succeed on the *same* file that `1047` fails on? If yes, `filename` is provably correct for 1046 and wrong for 1047 — the strongest possible support for the key-name hypothesis.
3. Is `1048 GET_DISK_INFO` (also `Untested`) answering anything? Free information about the storage vocabulary.
4. Does the printer's UI delete work on a file uploaded over LAN, or only on one made on the printer? A source-dependent delete would be a different bug than a shape bug.

## Corrections made after the plan review

Recorded so a later reader knows these were checked, not assumed:

- **Simulator behaviour restated.** An earlier draft claimed the simulator answers `error_code: 0` for unhandled methods and would hand a probe a false positive. Wrong: unhandled methods reach the `else` at `scripts/test_cc2_printer.py:504` and answer `1001 unknown interface`. The task stands, but on the true reason — it cannot discriminate parameter shapes at all.
- **One-file-per-variant added.** An earlier draft reused a single probe file across the matrix, which makes every variant after the first success report a meaningless `still_listed: false`.
- **`_IDLE_MACHINE_STATUSES` is not importable by the probe.** It lives in `custom_components/elegoo_printer/__init__.py`, which imports `homeassistant` at line 23. The probe re-derives the two-member set from `sdcp/models/enums.py`.
- **`refresh_status()` returns `None`.** The status must be read back from `client.printer_data.status`; the earlier draft implied the call returned it.
- **Serial resolution changed** to UDP discovery first, with an explicit abort, because `async_discover_serial` depends on an incoming status push.
- **Storage-media spellings both probed** — `sdcard`/`udisk` are HTTP-path tokens; the MQTT doc's table says `sd-card`/`u-disk`.
- **`sys.path.insert` idiom added** (repo convention, `scripts/extract_cc2_data.py:32`).
- **CI's real format gate added** — `ruff format . --check` (`lint.yml:33`), not `make format`, which rewrites instead of checking.
- **1045 documentation inconsistency found and folded into Task 1** — the doc says `filename`, the working client sends `file_name`.
- **Two review findings rejected after checking:** the claim that `handle_command` raises `NameError` on unknown methods (`method` is bound at line 308; the response publishes normally), and the claim that the suite collects 1012 tests (that figure came from a stale `.pytest_cache/v/cache/nodeids`; the suite passes **949** as of 2026-10-25).

Corrections from the **second** review round, each independently verified before accepting:

- **The simulator has no HTTP endpoint at all** — MQTT broker and UDP discovery only — so the probe's upload would fail for every variant and no acceptance criterion was observable. Task 2 now adds a `PUT /upload` endpoint that appends to `printer_files`, and rejects a wrong token or a corrupt MD5.
- **The simulator boots mid-print** (`STATUS_PRINTING` / `test_benchy.gcode`, `scripts/test_cc2_printer.py:155-156`, `195-205`), so the probe's idle gate would abort every validation run. Task 2 adds an `--idle` start flag.
- **`connect_printer` silently retargets the printer** from the `Printer` it is given (`client.py:824-825` overwrites both ip and serial). Passing a discovery-returned `Printer` would make `--ip` a dead argument and could point the probe at a different machine on a multi-CC2 LAN. Task 3 now passes a bare `Printer()` and aborts on ip disagreement.
- **The idle gate's safety claim was wrong, and this one mattered.** The plan asserted "anything unrecognised counts as busy". It does not: `map_status` resolves an unknown machine code to `ElegooMachineStatus.IDLE` (`cc2/models.py:141-143`), so new firmware with a new printing-ish code arrives already looking idle. `current_status` can never be "unknown". The filename is the primary guard — which the integration itself documents at `__init__.py:497-502`. Task 3 now names `printer_data.status.current_status` and `.print_info.filename` and puts the guards in the right order.
- **A missing settle loop was a false-positive risk.** Listing immediately after upload can transiently miss the new file, which reads as `still_listed: false` — i.e. a *working delete* reported where there was none. Task 3 now waits for the file to appear and reports the variant inconclusive rather than successful if it never does.
- **The hand-written-G-code caveat is stated rather than assumed away** — a `1003` on a file the printer may not consider deletable is ambiguous, and a volunteer pasting that into #432 could be misread.
- **Import-outside-HA reasoning corrected.** It works because `homeassistant` is an installed dependency — the parent package's `__init__.py` imports it at line 23 — not because the client avoids it. The script must run under `uv run`, not a bare interpreter.
- **Entry-parse gates spelled out for the simulator seed**, since a dropped entry makes the strict listing raise and the `still_listed` check meaningless; and `type: "dir"` is a deliberate skip, not a drop.

## UNVERIFIED / gaps carried forward

- `UNVERIFIED: what parameter key 1047 actually wants.` The `file_name` hypothesis rests on one data point (1045 works with it) plus the shape-rejection signature of `1003`. 1046 works with `filename`, so naming is per-method and 1047 may want something not in the matrix.
- `UNVERIFIED: the exact module that first pulls homeassistant into sys.modules when importing the CC2 client.` Importing outside HA succeeds and `debug.py` has run this way for a long time, so it is not a blocker; the culprit was not located by reading the import chain, and no false culprit is named. Note this is distinct from the `__init__.py` case above, which *is* a real constraint.
- `UNVERIFIED: whether the volunteer has this repo checked out.` Task 3 deliberately uses the shipped client for transfer fidelity, which requires a checkout; if they do not, the fallback is a self-contained paho-mqtt variant that loses that fidelity. Flagged rather than silently chosen.
- `UNVERIFIED: whether the simulator accepts a second MQTT client` while the volunteer's own tooling is attached — registration caps clients (`client.py:1269-1275`), which affects the local validation loop, not real hardware.
- `UNVERIFIED: firmware coverage.` Every conclusion rests on one printer on 02.01.01.07. Nothing here shows 1047 fails on other builds, and no firmware gating exists to express a per-firmware difference.
