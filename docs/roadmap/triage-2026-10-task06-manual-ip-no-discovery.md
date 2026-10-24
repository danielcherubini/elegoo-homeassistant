---
status: committed
parent: docs/roadmap/triage-2026-10-status.md
done-when: "Typing a CC1's IP by hand sets the printer up when it answers TCP 3030 but ignores UDP 3000 discovery, and a genuinely unreachable IP still produces the existing `no_printer_found` — pinned by `test_manual_ip_succeeds_when_discovery_is_silent`, `test_the_live_probed_identity_reaches_the_created_entry` (which asserts `result[\"data\"][\"id\"] == \"000000000001d354\"`, i.e. the mainboard id the printer reported over the live socket reaches `entry.data`) and `test_dead_address_still_reports_no_printer_found`; **identity is never invented** — a printer that pushes no attributes frame, or a frame with no `MainboardID`, is REFUSED with the existing error rather than entered with a made-up id, pinned by `test_no_attributes_frame_refuses_the_printer` and `test_attributes_without_mainboard_id_refuses_the_printer`, and `test_manual_ip_succeeds_when_discovery_is_silent` pins `async_set_unique_id` awaited with the real id so duplicate protection is not weakened; **the runtime path is fixed too**, because setup and reconnect gate WebSocket reachability on the same UDP discovery (`api.py:446`/`:640` → `_discover_printer_with_fallback`), pinned by `TestRuntimeReachabilityProbe` plus `TestTcpProbeHelper`, which drive the helper against a real listening socket and a closed port so that neither a probe which rubber-stamps every address nor a wrong call signature at the `api.py` seam can pass; discovery-first is otherwise unchanged — a discovery reply means the probe never runs (`test_probe_is_skipped_when_discovery_answers`, `test_discovery_answered_skips_the_probe_entirely`) and the proxy short-circuit is untouched (`test_proxy_host_path_is_untouched`). Gate from the measured 806 baseline: `make format` no-op, `make lint` \"All checks passed\", `make test` **823 passed** (806 + 17, every addition itemised in § Count), **`translations/` untouched — zero new error keys**, which is what keeps all 18 locales out of this task."
---

# T6 — #429 / #431: manual IP when UDP 3000 discovery is unanswered

**Issues:** [#429](https://github.com/danielcherubini/elegoo-homeassistant/issues/429) (@SketchyUnicorns) and
[#431](https://github.com/danielcherubini/elegoo-homeassistant/issues/431) (@MatiasFernandez).

Read **#431 in full** before starting; it is the diagnosis, and it is unusually precise. CC1, firmware V1.1.29, IoT
VLAN. The printer **stops answering SDCP discovery (UDP 3000) when it cannot ping its default gateway** — it decides
the network is down. `M99999` goes unanswered whether sent unicast from another VLAN or unicast/broadcast from inside
the printer's own subnet; power cycles do not help; **TCP 3030 stayed open the whole time**. A router capture showed
the printer sending an ICMP echo to its gateway every ~5 s; the router's firewall dropped them. The reporter's fix
was one router rule allowing ICMP printer→router, after which discovery worked. They cite Elegoo's published
`hl_net.c` `net_lan_connect_detection_routine` and note the `lan_connected`→SDCP link is **observed behaviour, not
code they can point to** — the SDCP service is not in the published source. #429 is the same failure from a HA
container with two NICs, with no diagnosis of its own; @MatiasFernandez pointed the reporter at #431.

The valuable fact: **reachability and discoverability are different questions, and this integration conflates them.**

## Two walls, not one — the brief describes only the first

The brief scopes this to `config_flow.py`. Grounding found a **second, independent wall behind it**, and fixing only
the first would have produced an entry that is created and then fails to set up. Both are measured below.

**Wall 1 — setup (`config_flow.py:802-804`).** `async_step_manual_ip` requires a discovery *reply* before it will
describe the printer at all; no reply → `_errors["base"] = "no_printer_found"` (`:804`). This is the error the
reporters see.

**Wall 2 — runtime (`api.py:446` in `async_create`, `api.py:640` in `reconnect()`).** For a WebSocket printer, both
decide reachability with `_discover_printer_with_fallback` (`api.py:185`), which asks **only** UDP discovery —
direct-IP, then broadcast — and returns False if neither answers. Verified by reading it, and by
measurement: with discovery stubbed to `[]`, a `Printer` built for the CC1 in #431 comes back unreachable. So a
manual-IP entry that survived wall 1 would hit `if not printer_reachable:` at `api.py:448`, and with
`allow_printer_offline` off (the default) return `None` → `ConfigEntryNotReady` → **"Failed setup, will retry",
forever**. The user would be told the printer was found and then never see an entity. `reconnect()` is what the
coordinator's 30 s retry poll uses, so the printer could never recover either.

Wall 2 is the more serious of the two and is **not mentioned in either issue**, because neither reporter got past wall
1. It is in scope: without it, "a CC1's IP typed by hand sets the printer up" is false.

## The identity crux — measured, and it is the reason the cheap design is wrong

`async_create_entry` is reached with `data=printer.to_dict()`, and `printer.id` is simultaneously:

- the config entry's **unique id** (`async_set_unique_id(unique_id=printer_object.id)`, `config_flow.py:811`), and
- the **device identifier** (`identifiers={(DOMAIN, device_id)}` from `config_data[CONF_ID]`, `entity.py:41,67`) and
  the **entity unique-id prefix** (`machine_id = self.config_entry.data["id"]` then `machine_id + "_" + key`,
  `coordinator.py:344-345`).

Measured against the installed HA (`homeassistant 2025.4.0`):

- `Printer()` → `id is None`. **`_abort_if_unique_id_configured` returns immediately when `self.unique_id is None`**
  (`config_entries.py:2929-2930`), so a `None` unique id does not error — it **silently switches duplicate protection
  off**, exactly the thing the brief says must not be weakened. `device_registry.async_get_or_create` *accepts*
  `(DOMAIN, None)`, so nothing downstream complains either.
- `coordinator.generate_unique_id` with `data["id"] is None` raises `TypeError: unsupported operand type(s) for +:
  'NoneType' and 'str'` — measured. Every entity platform calls it in `__init__`, so a `None`-id entry **fails
  platform setup**, it does not merely look odd.
- `api.py`'s self-healing `_update_config_entry_if_needed` compares
  `["name", "model", "brand", "firmware", "protocol_type", "ip_address"]` — **`id` is not in that list** (verified).
  So nothing later writes the id into `entry.data`.
- **The options flow cannot repair it.** `async_step_websocket_options` never mentions `id`, and
  `_async_test_connection` neither reads attributes nor assigns `.id` (both verified by reading the source). So an
  entry created with `id: None` is **permanently broken** — no reload, no options save, no reconnect fixes it. There
  is no "identity arrives late" recovery path; the brief's third question has a negative answer, and that is why the
  design below refuses to create an entry without an id instead of creating one and fixing it up.

**The id is available on the live connection, but not through the obvious route.** The attributes frame carries the
mainboard id — `PrinterAttributes` reads `MainboardID` from `Attributes` (`attributes.py:126`) and the frame root
(`:154`, as `mainboard_id_root`) — and `Printer.from_dict` reads `attrs.get("MainboardID", attrs.get("id"))`
(`printer.py:447`). But **`sync_from_attributes` copies only `firmware_version`, `machine_name`, `name` and
`brand_name`** (`printer.py:358-409`); it never touches `id`. Measured directly: feeding a full CC1 attributes frame
through `ElegooPrinterClient._parse_response` into a hand-built `Printer` yields
`name='CC1', model='Centauri Carbon', firmware='V1.1.29'` and **`printer.id is None`**. So "just connect and let the
existing sync fill it in" does not work; the probe must read `MainboardID` itself.

**Wire-shape caveat, and why the probe reads both shapes.** Two attributes shapes exist in this repo's own evidence
and they disagree: `scripts/test_server.py:176-183` pushes `{Data: {Attributes: …}}` (nested), while
`tests/test_client_conformance.py:213` and `mqtt/client.py:671-673` (whose comment labels the flat form "WebSocket
fallback format") use `{Attributes: …}` at the frame root. `PrinterAttributes` reads `Attributes` at the **top level
only**, so I measured the nested frame through the real handler: it parses to an **entirely empty** attribute set
(every field blank, `mainboard_id=''`). **Which shape a real CC1 sends is UNVERIFIED: this repo has never captured
one, and the two in-repo sources disagree.** The probe therefore reads the flat shape and falls back to
`Data → Attributes`, so it works whichever the printer uses. This is the cheap way to be robust to an unknown, rather
than a guess about which unknown it is.

## Design

Keep discovery primary. When **both** discovery protocols come back empty for a manually-entered IP, do not error
immediately: ask the printer over the connection the integration actually uses, and describe it from what **it
reports about itself**. A dead address fails that too and keeps today's error.

1. **`const.py`** — one new constant `TCP_PROBE_TIMEOUT = 3.0`, with a comment that it runs only after both UDP
   attempts have already timed out.
2. **`sdcp/transport/discovery.py`** — `async_tcp_port_is_open(host, port, *, wait_timeout=TCP_PROBE_TIMEOUT)`:
   `asyncio.open_connection`, True only on a completed connect, `contextlib.suppress` on the close. Stdlib-only, so
   `tests/test_import_sdcppack.py` (which inspects the top-level imports of every `sdcp/transport/*.py`) stays green —
   verified by running it. Named `wait_timeout`, not `timeout`, for the same reason
   `ElegooCC2Client.async_discover_serial` names its bound that (`cc2/client.py:407,423`): ruff `ASYNC109` rejects an
   async function with a parameter called `timeout`. Follow that precedent rather than adding a `noqa`.
3. **`api.py`** — at the end of `_discover_printer_with_fallback`, when discovery found nothing, probe TCP 3030 and
   treat an open port as reachable, logging at INFO that the printer is proceeding on the live connection. **One
   insertion, after both discovery attempts, guarded by `if not printer_reachable`** — so a printer that answers
   discovery never pays the probe. This fixes setup *and* `reconnect()` for free, since both call this one method.
4. **`config_flow.py`** — `_async_probe_websocket_printer(ip_address) -> Printer | None`, modelled on the existing
   `_async_detect_canvas` (`:438`) in the same class: `asyncio.timeout` wrapping `ws_connect` **and** the frame loop
   (that method's comment explains why: a device that accepts TCP but never finishes the handshake would otherwise
   hang the flow on HA's shared session), reading the first `attributes` frame in either shape, then
   `Printer.from_dict(attributes)` — the existing reader of exactly these wire keys, which also derives
   `printer_type` and the transport. Then:
   - **Refuse when there is no `MainboardID`** (and when no attributes frame arrives at all): return `None`. Per the
     crux above, an id-less entry has duplicate protection silently off, breaks every entity's unique id, and is
     unrepairable. `no_printer_found` is the honest answer to "I reached something that will not say who it is".
   - `printer.ip_address = ip_address` and `printer.connection = ip_address` — the address the user typed wins over
     `MainboardIP`; a printer behind NAT or reporting a stale address must not overwrite what the integration dials.
     `connection` mirrors what discovery itself does (`Printer.__init__` sets it from the frame's `Id`, `:196`) and
     what `_apply_serial` does for CC2 (`:1121`); `None` would only put `null` in the request envelope.
   - `brand = brand or "ELEGOO"` (mirrors `CC2DiscoveredPrinter.to_printer`), `mqtt_broker_enabled = False`.
   - **Unlike** `_build_cc2_printer_from_proxy_input`, the name is *not* left empty — the attributes frame carries
     `Name`, so the title is real. Keep that method's reasoning in mind, but do not copy the empty name: here we
     actually have the value.
5. **`config_flow.py` call site** — in `async_step_manual_ip`, between the CC2 fallback and the `if not
   printer_object:` error, `if not printer_object and ip_address: printer_object = await
   self._async_probe_websocket_printer(ip_address)`. Everything after is unchanged: the same
   `async_set_unique_id(printer_object.id)` + `_abort_if_unique_id_configured()`, the same transport/type routing.
   A probed CC1 has `transport_type=WEBSOCKET` and `printer_type=FDM`, so it lands on `async_step_fdm_options`, which
   runs the existing connection test and creates the entry. **`manual_ip` itself never creates an entry** — measured,
   and it is why the tests assert `step_id == "fdm_options"` from `manual_ip` and only assert `create_entry` after
   driving `async_step_fdm_options` too.

**No new error key.** The refusal reuses `no_printer_found`, whose text ("No printer could be found at the specified
IP address.") stays true from the user's point of view. A key like `printer_did_not_identify` would be more precise
and cost **18 locale files** (`test_translations.py::test_every_en_key_exists_in_every_locale` and its inverse pin
the full leaf-key set across all 18), and `translations/*` is a file the status file already shares with T5. Not worth
it for a rare sub-case; the INFO logs say what happened.

## Deliberately NOT doing this

- **Do not widen `Printer.sync_from_attributes` to copy `MainboardID` → `id`.** It looks like the tidy fix (it would
  make identity arrive on every attributes push, on all three transports) and it is the one edit most likely to be
  reached for. It is out of scope: it changes identity semantics on a path **every existing entry** takes on every
  poll, and an attributes frame that ever arrives with a different or empty id would mutate the device identifier
  under a loaded entry. This task needs identity once, at setup, and the probe reads it explicitly. If identity-drift
  on live pushes is a real concern it is its own issue, not a rider on a bugfix.
- **Do not edit `websocket/client.py`'s attributes handler to unwrap the nested shape.** I implemented and measured it:
  reverting it fails **zero** tests (trap T3, § Traps), because the probe reads both shapes itself and the runtime
  reads attributes through the coordinator regardless. It is a plausible latent bug — the nested shape parses to an
  all-empty attribute set, so a CC1 sending that shape would show no firmware/name at all — but **whether any real
  printer sends it is UNVERIFIED**, and a change that no test can distinguish is not a change this task should make.
  Record it as a follow-up question (below) instead.

## Off-branch tracker action (not a code step)

**#431 additionally asks for wiki documentation and the wiki is not in this repo** (`has_wiki: true`, edited on the
wiki). The status file already records this ("#431 wiki troubleshooting tips — not a code task"). It still needs doing
by hand, and #431's body contains the proposed section as an attachment plus a ports table to reconcile with the
current page:

- Discovery: UDP 3000 (the reply comes back to the probe's **ephemeral source port**, so a stateful firewall's return
  rule covers it — no fixed UDP 3000 rule needed).
- Control and status: TCP 3030. Camera: TCP 3031.
- The gateway-ping behaviour: a CC1 that cannot ping its default gateway stops answering discovery while TCP 3030
  stays open; allowing ICMP printer→router fixes it. Cross-reference #240, #429, discussion #276, and
  OpenCentauri/cc-fw-tools#72, all cited in #431.
- Worth adding now that it is no longer strictly true: since this commit, **setup succeeds without discovery** if
  TCP 3030 answers, so "no printer found" now means "nothing answered on 3030 either".

Also owed: a reply on **#429** when this ships, and a CHANGELOG line. **Do not close #431 on this commit** — its
requested deliverable is the wiki section, which is not in this repo.

## The seam (declared)

`custom_components/elegoo_printer/const.py`, `sdcp/transport/discovery.py`, `api.py`, `config_flow.py`,
`tests/test_manual_ip_no_discovery.py` (new), plus two existing test files needing one-line harness updates (§ Harness
updates below).
**`translations/` is NOT touched** — the status file's T6 seam row says `translations/*`, and that is wrong; correct
the row. `camera.py`, `coordinator.py`, `entity.py`, `cc2/client.py`, `cc2/cleanup.py` untouched, as required.

`api.py` gains `from .sdcp.transport.discovery import async_tcp_port_is_open`; `config_flow.py` does not import `api`
and `api` does not import `config_flow` (both verified), so no cycle.

## Harness updates the seam forces on two existing tests

Adding the probe inside `_discover_printer_with_fallback` makes two pre-existing tests perform a **real** TCP connect
attempt to a documentation address (`10.0.0.9`, `192.0.2.10`). They still pass — nothing on those addresses answers —
but they pay the full connect timeout: measured **10.89 s → 17.20 s (+6.3 s)** when only the
`test_offline_setup_api.py` patch is removed (3 tests × ~3 s each), plus ~5 s more from
`test_api_discovery.py::test_unresolvable_hostname_still_uses_broadcast_fallback`. Pin the helper out instead:

- `tests/test_api_discovery.py` — add a `_no_tcp_probe()` helper (`patch.object(api_module, "async_tcp_port_is_open",
  new=AsyncMock(return_value=False))`) and use it in the one test that asserts *unreachable*.
- `tests/test_offline_setup_api.py` — add the same patch to `_offline_websocket()` (the shared
  "printer answers nothing" contextmanager at `:92`), with a comment saying why.

**Do not use `new=AsyncMock(False)`.** `AsyncMock(False)` passes `False` as `spec`, not `return_value`, and the mock
then returns a truthy `AsyncMock` — this cost me a debugging cycle and it fails *silently in the direction that
matters* (it makes everything look reachable). Always `new=AsyncMock(return_value=False)`.

**These two patches hide the seam, so one test must go unpatched.** Because every runtime test patches the helper out,
an argument-name mismatch between `api.py`'s call site and the helper passes the whole suite — I introduced exactly
that bug (`timeout=` vs `wait_timeout=`) in the implementation copy and **819/819 went green around it**.
`test_fallback_calls_the_real_helper_end_to_end` is the countermeasure: it drives the **real** helper through
`_discover_printer_with_fallback` against a real listening socket with no patch on the helper, so the call signature
at the seam is exercised. Trap T9 below is that bug, re-injected.

## Count — 806 → 823

New `tests/test_manual_ip_no_discovery.py`, **17 tests**, harness copied from
`tests/test_config_flow_cc2_options.py::TestManualIpWithProxyHost` (`ElegooFlowHandler()` + `MagicMock` hass +
`async_add_executor_job` run-inline) and the `_FakeWS` frame double from `tests/test_canvas_detection.py`:

- `TestProbeReadsIdentityFromTheLiveConnection` (5) — nested frame yields a printer **with the id**; flat frame still
  parses; no attributes frame → refused; no `MainboardID` → refused; refused connection → None.
- `TestManualIpWithoutDiscovery` (5) — silent discovery + live socket routes on with the real unique id; the probed
  id/identity survives the options rebuild into `entry.data`; dead address → `no_printer_found` and
  `async_set_unique_id` never awaited; a discovery reply means the probe is never awaited; the proxy path is
  untouched.
- `TestTcpProbeHelper` (3) — real listening socket → True; closed port → False; blank/None host → False.
- `TestRuntimeReachabilityProbe` (4) — TCP open + silent discovery → reachable; TCP closed → unreachable; discovery
  answered → probe never awaited; **and the real helper end-to-end through `_discover_printer_with_fallback`** (§ above).

## Traps — all against the finished copy at `/tmp/t6` (real tree left byte-identical)

Baseline re-measured on the real tree first: **806 passed**, `make lint` "All checks passed", `make format` no-op,
`git status --porcelain` empty. Everything below was built and measured in `/tmp/t6`; the real tree was never
modified.

| # | Injection | Caught by |
|---|-----------|-----------|
| T1 | Probe call removed from `manual_ip` (reporter's wall restored) | 2 — `test_manual_ip_succeeds_when_discovery_is_silent`, `test_the_live_probed_identity_reaches_the_created_entry` |
| T2 | Invent an id from the IP when `MainboardID` is absent | 1 — `test_attributes_without_mainboard_id_refuses_the_printer` |
| T3 | Accept a printer that pushes no attributes at all (id invented) | 1 — `test_no_attributes_frame_refuses_the_printer` |
| T4 | TCP probe rubber-stamps every address | 2 — `TestTcpProbeHelper::test_closed_port_is_not_reachable`, `test_blank_host_is_not_reachable` |
| T5 | `unique_id` set to `None` on the new path (protection silently off) | 1 — `test_manual_ip_succeeds_when_discovery_is_silent` |
| T6 | Dead IP treated as found | 4 |
| T7 | **Revert the `websocket/client.py` nested-shape unwrap** | **0** — which is why that edit is not in this plan |
| T8 | Runtime probe removed (wall 2 restored) | 1 — `TestRuntimeReachabilityProbe::test_tcp_open_counts_as_reachable_when_discovery_is_silent` |
| T9 | Signature bug at the `api.py` call site (`timeout=` instead of `wait_timeout=`) | 1 — `test_fallback_calls_the_real_helper_end_to_end` |

T4 is the one that changed the plan: my first version patched the helper in *every* test, so a probe that answered
"reachable" to everything passed the whole suite. `TestTcpProbeHelper` exists to close that hole — it is the only test
that drives the helper against a real socket.

T9 was **a bug I actually wrote**, not a hypothetical: the implementation copy called the helper with `timeout=` after
I renamed the parameter to `wait_timeout` for ruff, and **819/819 passed around it**, because every runtime test
patches the helper out and a patched mock accepts any kwargs. It would have raised `TypeError` on every unreachable
printer at setup and on every reconnect — turning #429 into a crash. `test_fallback_calls_the_real_helper_end_to_end`
is written for that failure and nothing else catches it.

**T8 is thin and I am calling that out rather than rounding it up.** Wall 2 — the more serious of the two walls — is
pinned by that one test plus the end-to-end one. An earlier run of T8 appeared to catch 12, but that injection also
broke an import, so those 12 were collateral damage from a module that failed to load, not evidence; the honest number
after a clean injection is 1.

Every trap was re-run against the final state (after the `websocket/client.py` edit was dropped and after the T9 fix),
not against the earlier wider implementation.

## Honest limitations

- **UNVERIFIED: which attributes frame shape a real CC1 sends.** The repo's two sources disagree (see § The identity
  crux). Mitigated, not resolved: the probe accepts both shapes. Hardware confirmation still owed.
- **UNVERIFIED end-to-end on real hardware.** Everything here is measured against doubles and local sockets. #429 and
  #431 both describe a state the reporters can reproduce, so one run each of "type the IP, no UDP 3000 rule" on a CC1
  is the real close. **Do not close #429 on a green gate alone** — ask @SketchyUnicorns to confirm against a
  pre-release, the same way #435 was handled.
- **The probe proves the port is open, not that it is a printer.** It reads an attributes frame before creating an
  entry, so a non-printer that merely accepts TCP 3030 and pushes nothing is refused; but a device that pushes
  something shaped like SDCP attributes would be entered. Accepted: it is the user's own typed IP.
- **A printer that answers TCP 3030 but is genuinely broken now gets a loaded entry** rather than a setup failure, if
  the entry has `allow_printer_offline` on. That is #171's documented trade-off, unchanged by this task.
- **Cost of the probe on a dead IP in the flow:** bounded by `_CANVAS_DETECT_TIMEOUT` (5 s, reused from
  `_async_detect_canvas` rather than duplicated — the two probes face the identical "accepts TCP, never handshakes"
  hazard). That 5 s is small next to what a failed manual-IP attempt already costs: SDCP discovery is
  `DISCOVERY_TIMEOUT` = 5 s and `manual_ip` calls `CC2Discovery.discover_as_printers(ip_address)` **with no
  arguments**, so it takes the defaults `CC2_DISCOVERY_TIMEOUT` = 10 s × (`CC2_DISCOVERY_RETRIES` + 1) = 30 s —
  **~35 s of existing dead time before this task's 5 s probe even starts.** (Measured from the constants, not
  wall-clocked.) The runtime probe likewise runs only after both UDP attempts have timed out. So the added latency
  lands on printers that were already slow to fail. Worth knowing, and worth its own follow-up: that 30 s CC2 default
  is why `async_step_user` already passes `CC2_CONFIG_FLOW_DISCOVERY_TIMEOUT` (3 s, single attempt) — `manual_ip` does
  not, and probably should. **Out of scope here** (it would change an unrelated step's timing), recorded so the next
  reader does not attribute the wait to this change.
- **`_async_validate_input` (`config_flow.py:307`) has the same wall** and is **dead code** — it has zero callers
  (`manual_ip` inlines its own logic). Left alone: deleting it is a separate cleanup and its `# noqa: PLR0912`
  suggests nobody has checked it in a while. Recorded so a future reader does not "fix" it and wonder why nothing
  changed.
- **Follow-up question, not this task:** does any real printer send the nested attributes shape? If yes,
  `PrinterAttributes` silently parses it to empty on the *poll* path too (T7 above), which would be a genuine bug for
  those printers and worth its own issue.

## Parallelisation

SERIAL. It is the last task of wave 2 after T4 and T5, and it edits `config_flow.py` and `api.py`, which T2's
`update_ip` work and T5's CC2 changes also touched; the two existing-test harness edits additionally couple it to
`test_offline_setup_api.py`. One agent, one commit.
