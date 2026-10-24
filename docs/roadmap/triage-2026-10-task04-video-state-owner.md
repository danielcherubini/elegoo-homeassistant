---
status: committed
parent: docs/roadmap/triage-2026-10-status.md
done-when: "Nothing in `custom_components/elegoo_printer` reads or writes an attribute on the `ConfigEntry` object that the integration does not own — after this commit the ONLY entry-object attribute the integration writes is `entry.runtime_data` (`__init__.py:516`), which is core's designated slot for integration data (grep-verified: `grep -rn 'entry\\.[a-z_]* = \\|setattr(entry' custom_components/elegoo_printer --include=*.py \\| grep -v 'tests/' \\| grep -v conftest.py` returns that one line and nothing else — the only other match anywhere is `conftest.py:196`, a test double assigning `runtime_data` itself, which is the allowed slot), and `grep -rn \"_elegoo_video_state\" custom_components/elegoo_printer` returns nothing but `.pyc` files; the shared \"is this printer's video enabled\" flag lives in a new module-level `_video_stream_state_by_entry: dict[str, dict[str, bool]]` in `camera.py`, keyed by `entry.entry_id`, as a third sibling of `_pending_video_releases` and `_video_viewers_by_entry`, and `forget_pending_video_release` pops it alongside them by entry id (never `.clear()`); it still survives a config-entry reload — and that clause is now pinned by a test that drives the real `async_unload_entry`, not by a claim; all 75 pre-existing tests in `custom_components/elegoo_printer/tests/test_video_lifecycle.py` still pass, plus 4 new pins in that file; and the gate is green from the 762 baseline — `make format` a no-op, `make lint` \"All checks passed\", `make test` **766 passed** (762 + 4 new pins, +4 explained in § Count), the lifecycle file 75 → 79; and `test_removal_forgets_the_shared_flag_but_never_touches_the_printer` replaces the current `test_removal_does_not_disturb_the_shared_stream_flag`, because constraint 6 (drop the key on real removal) and that test's `assert entry._elegoo_video_state == {\"enabled\": True}` are mutually exclusive — see § The one place the brief contradicts itself, and read § The removal hook now warns: this refactor silences one operator warning unless the hook takes it over, which is a deliberate, decided behaviour change and not a behaviour-preserving detail."
---

# T4 — #425 Move the video enabled flag onto an integration-owned, entry-id-keyed owner

**Issue:** [#425](https://github.com/danielcherubini/elegoo-homeassistant/issues/425), opened by the maintainer: the
shared printer-video enabled flag currently sits on the `ConfigEntry` **object**, which works but "is not a home we own".

**This is a storage refactor with one deliberate, decided log change.** The flag keeps surviving reloads, keeps being
shared per entry, keeps the detached-entity fallback, keeps sending exactly the same printer calls. What changes is
*where it is stored*: out of core's namespace and into a registry the integration owns. And what changes in behaviour —
loudly, on purpose, see § The removal hook now warns — is *which component owns the operator warning for a video
stream left on by a removed entry*.

Grounding was done inline against the tree at `feature/triage-2026-10` (commit `90ad375`). Every count, failure set,
warning and trap-pin in this plan was then **executed in throwaway copies under `/tmp`** (the real tree was left
byte-identical, `git status` clean apart from this file). The numbers below are measurements, not estimates: the
baseline was re-measured at 75 / 762, the four TDD steps were run in sequence end-to-end (3 reds → 75 → 79 → 766), and
each of the five implementation traps in § The traps was injected into the finished tree one at a time and confirmed to
turn named tests red — T1, T2 and T3 each fail exactly one, which is what makes them precise rather than noisy.

## Reviewer round 1 — what changed in this revision

The first revision of this plan was vetted and returned four blocking findings. All four were reproduced against the
tree and all four are fixed here. One non-blocking claim and one of the reviewer's two "confirmed positives" did **not**
survive verification, and § Reviewer findings that did not survive says so rather than quietly complying.

| Finding | Status | Where |
| --- | --- | --- |
| 1. The anti-trap claim was false — clearing the registry in `async_unload_entry` passed all 765 | **FIXED.** The claim is gone; a real pin now drives `async_unload_entry` and was verified to fail when the trap is injected | § The traps, each with a pin that actually catches it |
| 2. The new pop was not per-entry-pinned (`.clear()` passed all 765) | **FIXED**, by the cheaper route: two assertions folded into the existing `test_removal_only_forgets_its_own_entry`, no new test | § The change, items 4-5 |
| 3. A real unlogged warning regression, justified by a backwards reading of `_release_still_owed()` | **DECIDED + RECORDED.** The removal hook takes the warning over. Both halves of the original justification were wrong and are corrected | § The removal hook now warns |
| 4. TDD step 1 could not produce its stated evidence (6 `AttributeError`s, none of them the named tests) | **FIXED** by re-sequencing: production first, seam second. Re-measured: exactly the 3 predicted reds | § TDD order |

## Grounding (verified against the tree, not asserted)

`ElegooVideoStreamLifecycle._stream_state` (`camera.py:468-485`) is the only offending site:

```python
        entry = self._config_entry()
        if entry is None:
            return self._local_stream_state
        state = getattr(entry, "_elegoo_video_state", None)
        if state is None:
            state = {"enabled": False}
            setattr(entry, "_elegoo_video_state", state)  # noqa: B010
        return state
```

Complete list of `_elegoo_video_state` references (grep-verified; `_stream_state` has no other caller — only the
`_stream_enabled` getter at `:490` and setter at `:494` — and no test touches it directly, so the seam is exactly one
method plus its property pair at `:487-494`):

| Site | What |
| --- | --- |
| `camera.py:481`, `:484` | the implementation (getattr / setattr) |
| `tests/test_video_lifecycle.py:171` | helper `_leave_stream_enabled(entry)` — the seam, used by 5 tests (`:1310, :1350, :1391, :1451, :1738`) |
| `tests/test_video_lifecycle.py:1749` | `test_removal_does_not_disturb_the_shared_stream_flag` |
| `tests/test_video_lifecycle.py:2268` | `test_parking_leaves_the_entry_flag_the_adopter_reads` |

Three facts the plan depends on, each confirmed by reading:

- **The precedent is real and is the shape required.** `camera.py` already carries two module-level per-entry
  registries — `_pending_video_releases: dict[str, str]` (`:104`) and
  `_video_viewers_by_entry: dict[str, weakref.WeakSet[...]]` (`:136`) — both documented with the house-style
  "why here / what bounds it / an entry id is never reused" comment blocks, and both cleared by the public
  `@callback forget_pending_video_release(entry_id)` (`:162`), whose body pops both (`:188-189`).
- **`async_unload_entry` (`__init__.py:568`) touches neither registry** — verified: the only reference to
  `camera` in `__init__.py` outside comments is the deferred import in `async_remove_entry` (`:656`) and its call
  (`:658`). `async_remove_entry` (`__init__.py:626`) is the sole caller of the public edge. **Both stay as they are;
  `__init__.py` needs NO change** (see § Scope) — the public edge already pops by entry id, so a third registry is
  absorbed without widening the call.
- **`_config_entry()` has exactly three callers** — `:478` (`_stream_state`, the site being replaced), `:695`
  (`_config_entry_id`) and `:788` (`_current_printer_client`). After this change two remain. There is no method named
  `_live_client` anywhere in the repo; the first revision of this plan invented it.

Baseline, measured on this branch: `test_video_lifecycle.py` **75 passed**; full suite **762 passed**, lint clean,
format no-op.

## The change

### 1. A third registry in `camera.py`, immediately after `_video_viewers_by_entry` (`:136`)

```python
# Whether an entry's shared printer video is currently enabled, by config entry.
#
# The printer's video stream is a single on/off resource per printer, shared by
# every client (the MJPEG camera, still-image grabs, the native stream and
# foreign viewers such as the Elegoo Slicer), so "is it on" is the config
# entry's fact and not any one entity's: an entity inherits it at construction
# and a teardown retry that outlived its entity reads it through its successor.
#
# It cannot live in ``entry.runtime_data``, which core deletes on unload,
# because it has to survive the unload: while a foreign viewer holds the stream
# the unload cannot disable it, and the entity the reload builds must know it is
# still on instead of trusting constructor defaults. Module level is what
# survives. It must not live on the config entry object either — that object is
# core's, and an integration does not get to hang attributes on it (#425).
#
# One path bounds it, the same one its two siblings use: an entry that is
# removed has its flag dropped by the integration's ``async_remove_entry`` hook,
# which calls forget_pending_video_release() below; nothing can ever read the
# flag of an entry id that is gone.
#
# An entry id is never reused, so at most one boolean is held per printer that
# is between connections, and a reload reads the same key it had before.
_video_stream_state_by_entry: dict[str, dict[str, bool]] = {}
```

Match the siblings' reasoning density — these comment blocks are the house style here, and the "why not
`runtime_data`" paragraph is the whole point of the issue, so it belongs in the code and not only in this plan. (The
comment is prose across several paragraphs, so D213-style summary-on-second-line rules do not apply to it — it is a
`#` comment. The docstring rules in § Lint notes are a different matter.)

### 2. An accessor beside the other private helpers (after `_clear_pending_video_release`, `:156-158`)

```python
def _video_stream_state(entry_id: str) -> dict[str, bool]:
    """
    Return the shared video state of an entry, creating it on first use.

    Arguments:
        entry_id: Config entry whose shared printer video is being asked about.

    """
    state = _video_stream_state_by_entry.get(entry_id)
    if state is None:
        state = {"enabled": False}
        _video_stream_state_by_entry[entry_id] = state
    return state
```

Keep it a `dict[str, bool]` (not a bare `bool`) so the mutable dict handed back by `_stream_state()` keeps working
for the `_stream_enabled` setter and any future key — the same object-identity sharing the current code relies on for
`test_stream_enabled_state_is_shared_per_entry`.

### 3. `_stream_state` reads the registry (`camera.py:468-485`)

```python
        entry_id = self._config_entry_id()
        if entry_id is None:
            return self._local_stream_state
        return _video_stream_state(entry_id)
```

and update its docstring's last sentence from "The state lives on the config entry, which survives a reload." to point
at `_video_stream_state_by_entry`, keyed by entry id, with the detached entity keeping its own copy in
`_local_stream_state`.

**Why `_config_entry_id()` and not `_config_entry()`:** `_config_entry_id()` (`:693-696`) already does exactly the
`entry_id`-is-a-`str`-or-`None` resolution every other registry user in this class uses (`:370`, `:413`, `:428`), so
this makes the flag keyed the same way as its two siblings by construction. It reads through `_config_entry()`, so
the deliberate *non-caching* behaviour the current docstring defends — an entity whose entry was removed still
reports its entry — is preserved untouched. **`_config_entry()` must stay**: after this change it is still called at
`:695` (by `_config_entry_id` itself) and `:788` (`_current_printer_client`). Do not remove it.

### 4. `forget_pending_video_release` pops the third registry — by entry id, and loudly when the flag was on

```python
    _clear_pending_video_release(entry_id)
    _video_viewers_by_entry.pop(entry_id, None)
    state = _video_stream_state_by_entry.pop(entry_id, None)
    if state is not None and state.get("enabled"):
        LOGGER.warning(
            "Forgetting the video state of config entry %s while its shared "
            "printer video was still enabled: the entry has been removed, so "
            "nothing is left to disable the stream. The printer video may stay "
            "enabled until the printer is rebooted.",
            entry_id,
        )
```

Two things to get right here, and the first revision of this plan got both wrong:

- **`pop(entry_id, None)`, never `.clear()`.** The hook is per entry; a registry-wide clear would let one printer's
  removal wipe every other printer's flag. § The traps, each with a pin that actually catches it has the probe showing
  that `.clear()` was invisible to the whole suite until this revision added a pin for it.
- **The warning is load-bearing, not decoration.** § The removal hook now warns explains why this function, and not
  the retry, owns the operator's only notice that a removed printer's video is stuck on.

Update its docstring: "both per-entry registries" → "the three per-entry registries", and replace the sentence
"It deliberately touches neither the printer nor the entry's shared enabled flag — that is core's unload path." —
that sentence is what § The one place the brief contradicts itself is about. Keep the printer half ("touches no
printer"), drop the flag half, and record *why dropping the flag is safe on removal in a way it is not on unload*:
removal is terminal, so there is never a rebuilt entity to mislead.

Do **not** re-derive safety from `_release_still_owed()`. The first revision claimed the retry "short-circuits on
`_config_entry_removed()` before the flag matters", and that is backwards: `return self._stream_enabled and not
self._config_entry_removed()` (`:808`) evaluates the **flag first**. The path that actually ends a removed entry's
retry is the per-tick guard `if not self._stream_enabled: self._clear_pending_video_release(); return` (`:857-859`),
which returns *before* the unpayable/abandon branch at `:865-867` is ever reached. That is precisely why the pop
silences the warning, and it is measured, not argued — see the probe in § The removal hook now warns.

### 5. Tests — change the seam, not the 75 tests

- **New autouse fixture** `_isolated_video_state_registry` beside the two existing isolation fixtures (`:52`, `:64`),
  `monkeypatch.setattr(camera_module, "_video_stream_state_by_entry", {})`. **Label it what it is: defensive.**
  Measured — deleting it leaves the file at 79 passed and the suite at 766. Every entry double in this file already
  has a distinct `entry_id`, so nothing currently bleeds through it today; it is there for the same reason its two
  siblings are, and to stop the *new* per-entry tests from coupling to test order. Do not describe it as fixing a
  leak.
- **`_leave_stream_enabled`** (`:163-171`) becomes
  `camera_module._video_stream_state(entry.entry_id)["enabled"] = True`, with its docstring's "lives on the config
  entry" reworded to the registry. This is the seam: 5 call sites change with zero test-body edits.
- **New read helper** `_stream_state_of(entry)` →
  `camera_module._video_stream_state_by_entry.get(entry.entry_id, {})`, used by every flag assertion.
- **`:2268`** (`test_parking_leaves_the_entry_flag_the_adopter_reads`) needs no semantic change —
  `assert _stream_state_of(entry) == {"enabled": True}`. It passes as-is; it is the pin for constraints 1-3.
- **`:1749`** must change — see § The one place the brief contradicts itself.
- **Two entry doubles lack an `entry_id`** and must be given one: `SimpleNamespace()` at `:405`
  (`test_stream_enabled_state_is_shared_per_entry`) and `:422`
  (`test_new_camera_refreshes_url_despite_inherited_enabled`). Under the old code an id-less object still worked;
  under entry-id keying it silently becomes a *detached* entity and both tests fail (measured — they are 2 of the 3
  reds in TDD step 1). This is not a test smell being papered over — a real `ConfigEntry` always has an `entry_id`, so
  the double was under-specified. Give them distinct ids (`"entry-shared"`, `"entry-inherited"`) and comment the
  reason once.
- **Fold the per-entry drop-path pin into `test_removal_only_forgets_its_own_entry`** (`:1712`) rather than adding a
  test: record a second entry's flag alongside the first and assert the survivor. Two lines of setup, two assertions,
  and it pins the DROP path for free in the test whose entire subject is already "removal forgets only its own entry".

## The removal hook now warns (read this before starting — it is the one behaviour change)

**Decision: keep the pop on removal, AND move the operator warning into `forget_pending_video_release`.** Neither
alternative was chosen, and the reasoning is recorded here because the first revision of this plan shipped the silent
variant while claiming it was not a change.

### What the first revision got wrong

It claimed that popping the flag on removal left the retry behaving "exactly as
`test_removed_entry_gives_up_and_parks_nothing` already asserts", because `_release_still_owed()` "short-circuits on
`_config_entry_removed()` before the flag matters". Both halves are false:

- **Order.** `_release_still_owed()` is `return self._stream_enabled and not self._config_entry_removed()`
  (`camera.py:808`). Python evaluates `self._stream_enabled` **first**. Pop the flag and that operand is `False`, so
  the call to `_config_entry_removed()` never happens and the whole expression is `False`.
- **Consequence.** The guard that actually ends a removed entry's retry is the per-tick one at `:857-859`
  (`if not self._stream_enabled: … return`), which returns *before* the unpayable/abandon branch at `:865-867`. So the
  retry exits through the **quiet** door. `test_removed_entry_gives_up_and_parks_nothing` (`:2207`) asserts exactly the
  opposite — `len(warnings) == 1` and `"reboot" in warnings[0]` — so "behaves exactly as that test already asserts" is
  false. That test survives only because it never pops the flag: it models removal by pointing `async_get_entry` at
  `None` and leaves the flag set.

Measured in a scratch copy, driving the real `async_remove_entry` mid-retry (retry running, foreign viewer holding the
stream, core has dropped the entry, then the hook runs):

| Tree | Warnings | Operator-visible? |
| --- | --- | --- |
| Original tree (flag on the entry object, hook never touches it) | **1** — "Giving up releasing the printer video stream … may stay enabled until the printer is rebooted or the integration is reloaded" | yes |
| Pop the flag in the hook, no warning | **0** | **no** |
| This plan (pop in the hook + the hook warns) | **1** | yes |

The silenced case is the worst one to silence: the printer's video is genuinely still on and there is nobody left to
turn it off. That is not a detail of a refactor billed as behaviour-preserving; it is the one thing in this whole flow
an operator can act on.

### Why the hook, and not ordering the retry differently

Re-ordering the retry was the other candidate. It does not work: the guard at `:857-859` is correct for its own
purpose (a stream that went off really is no longer a debt, and must not be parked), and bending it so a removed entry
still warns would either re-park a debt nobody can pay or make the loop's exit conditions depend on where the flag
happens to be stored — dragging the unload/reload machinery into a storage refactor. Worse, it cannot cover the case
that matters most: **a retry that has already finished** (exhausted, or cancelled at teardown) is gone before core
drops the entry, so no ordering of the loop can warn for it. `forget_pending_video_release` is the only hook core
guarantees for a removal, and it already runs for every removal.

Measured properties of the chosen shape:

- **At least one warning in every terminal ordering.** Removal mid-retry → the hook warns (the retry never gets a tick
  that sees the flag). Retry notices the removal first, then the hook → 1 + 1 = two warnings, one from each owner;
  accepted as the cost of never under-warning, and recorded below as a limit.
- **The happy path stays silent.** Removal of an entry with no record, or with a record that says
  `{enabled: False}` (the common case — the flag is read far more often than it is set), logs nothing. Probed both.
- **The legitimate unload-then-remove path warns once, and the warning is true.** An entry whose retry parked the debt
  after a foreign viewer blocked the release, later removed by the user, does get a line. That is not spurious: the
  stream really is still on and nothing left can turn it off. It is a new line for a case that previously produced
  zero, never a replacement for one that produced a line.

If the maintainer would rather this stayed a `LOGGER.info`/`debug` than a `warning`, say so and change the level — the
pin asserts one warning naming the entry and the word "reboot", so lowering it means editing that assertion, and the
choice should be visible rather than silent.

## The one place the brief contradicts itself (read before starting)

The brief's constraint 6 says the key "must be dropped on real removal (that's `forget_pending_video_release`), while
NOT being dropped on unload". The test that exists today,
`test_removal_does_not_disturb_the_shared_stream_flag` (`:1733-1755`), asserts the opposite half of that from inside
the removal hook:

```python
            await self._hook()(hass, entry)
            assert camera_module._pending_video_releases == {}
            # The stream flag is the entry's own state and survives: only core's
            # unload path talks to the printer.
            assert entry._elegoo_video_state == {"enabled": True}
```

Those cannot both hold: the hook cannot both drop the entry's key and leave it set. **Constraint 6 wins, and the test
must be re-pointed** — it is the cheaper and more honest option, and unlike the alternative it keeps the leak guard
the issue's own precedent demands. Rename it to
`test_removal_forgets_the_shared_flag_but_never_touches_the_printer`, invert the flag assertion to
`assert _stream_state_of(entry) == {}`, add the loudness assertion (`len(warnings) == 1`, `"reboot" in warnings[0]`,
filtered on `entry.entry_id`) so the § The removal hook now warns decision is pinned rather than hoped for, and keep
the test's *actual* subject — the hook never reaches the printer, which is what its surviving
`hass.config_entries.async_get_entry.assert_not_called()` and the `hass.async_create_task` /
`async_create_background_task` assertions already pin. Rewrite its docstring to say why removal may forget what unload
may not (removal is terminal ⇒ no rebuilt entity can be misled into assuming "off").

**Do not** satisfy the old assertion by clearing the flag in `async_unload_entry` instead — that is trap T1 below, and
it breaks constraints 1-3 outright. Unlike the first revision of this plan, that trap is now actually caught.

If you disagree with this reading, **stop and raise it rather than silently picking a side**: the choice is visible in
the test name, so it must not be made quietly.

## The traps, each with a pin that actually catches it

Every row below was produced by injecting the bad implementation into the finished tree and running the **whole
766-test suite**. "Fails" names the test that went red; a trap with no failing test is an unpinned claim, and the
first revision of this plan had two of those.

| # | Trap | Injected result | Pinned by |
| --- | --- | --- | --- |
| T1 | `async_unload_entry` clears the registry | **1 failed, 765 passed** | `TestFlagOwnership::test_unload_leaves_the_flag_registry_alone` |
| T2 | The hook calls `.clear()` instead of `pop(entry_id, None)` | **1 failed, 765 passed** | `test_removal_only_forgets_its_own_entry` (widened) |
| T3 | The hook pops but stops warning | **1 failed, 765 passed** | `test_removal_forgets_the_shared_flag_but_never_touches_the_printer` |
| T4 | One shared record for every entry id (`setdefault("__global__", …)`) | **6 failed, 760 passed** | 5 of the new/widened pins + 1 pre-existing (`test_parking_leaves_the_entry_flag_the_adopter_reads`) |
| T5 | Reintroduce `setattr(entry, "_elegoo_video_state", …)` | **8 failed, 758 passed** | `test_shared_flag_never_writes_through_to_the_entry_object` + the 2 per-entry tests + 5 pre-existing handover/parking tests |

Also true, and worth keeping in mind while implementing:

- **Keying by anything other than entry id** (host/IP, `entry.data`, unique id, the entry object) breaks constraint 4,
  and also breaks reload for IP-keyed variants because `entry.data` can be updated by options flow while the id cannot.
  Note that `entry.data`-keying is *not* invisible: it collides on every `_make_entry()` double (they all carry
  `data={}`) and fails 3 pre-existing tests.
- **Caching the entry id on the entity** breaks the removed-entry path that `_config_entry()`'s docstring explicitly
  defends ("deliberately NOT cached").

### T1 deserves its own paragraph, because the first revision asserted it was already caught

It was not. `test_reload_sequence_releases_the_held_stream` and `test_retry_disables_through_the_reloaded_client`
**do not catch it** — both model unload by hand (`entry.runtime_data = None`, `self._unloaded(...)`) and
`test_video_lifecycle.py` never imports or calls the real `async_unload_entry`. Injecting the clear into the real
hook left all 765 passing. So the done-when clause "it still survives a config-entry reload" was satisfiable while
broken, and the plan's safety claim was false.

The fix copies a pattern the file already uses: `TestEntryRemovalForgetsTheParkedRelease._hook()` (`:1654`) resolves
the removal hook off the integration module "as core does". `test_unload_leaves_the_flag_registry_alone` does the same
for the unload hook — `integration_module.async_unload_entry(hass, entry)` with
`hass.config_entries.async_unload_platforms` returning `True`, `entry.runtime_data = SimpleNamespace(api=client)`
(the shape the hook actually reads: `runtime_data.api` *is* the client, so `server=None` plus a patched
`ElegooPrinterServer.is_printer_registered` keeps the proxy bookkeeping out of the test), then asserts the entry's
flag record survived and the client really was disconnected. It is the only test in the file that drives that hook.

## Constraints the new owner must preserve, and what pins each

| # | Constraint | Pinned by |
| --- | --- | --- |
| 1 | Survives a config-entry reload (module level survives; `runtime_data` does not) | `test_unload_leaves_the_flag_registry_alone` (**new** — the only test that drives the real `async_unload_entry`), `test_reload_sequence_releases_the_held_stream` (models it by hand), `test_parking_leaves_the_entry_flag_the_adopter_reads` (`:2241`; it asserts nothing about `runtime_data` — the only `assert entry.runtime_data is None` in the file is `:1311`, inside `test_new_entity_completes_the_pending_release`) |
| 2 | A retry outliving its entity acts through the *replacement* entity's client and sees the same flag | `test_retry_disables_through_the_reloaded_client`, `test_new_entity_completes_the_pending_release` (`:1296`; `assert cam._stream_enabled is True` at `:1317`, after a new client is attached) |
| 3 | A foreign viewer holding the stream keeps the flag set across unload so the rebuilt entity does not assume "off" | `test_parking_leaves_the_entry_flag_the_adopter_reads` (`:2268` → `_stream_state_of(entry)`), `test_new_camera_refreshes_url_despite_inherited_enabled` (`:419`) |
| 4 | Per-entry isolation — one entry's flag is never visible to another | **Read § Gap: constraint 4 was unpinned — the pre-existing coverage is thinner than the first revision claimed but not empty.** New pins: `TestSharedFlagIsPerEntry::test_flag_of_one_entry_is_not_visible_to_another`, `test_detached_entities_do_not_share_the_attached_flag`, and the widened `test_removal_only_forgets_its_own_entry` (isolation on the DROP path, T2). The brief attributes this to `test_detached_entity_does_not_adopt_another_entrys_release` (`:1424`), but that test pins the **`_pending_video_releases` handover**, not the enabled flag — it never touches `_stream_enabled`. |
| 5 | Entity never attached to an entry keeps working via `_local_stream_state` | every bare `_VideoLifecycleSubject` test, plus the new `test_detached_entities_do_not_share_the_attached_flag` |
| 6 | Key dropped on real removal (per entry, loudly), NOT dropped on unload | `test_removal_forgets_the_shared_flag_but_never_touches_the_printer` (rewritten, + loudness) + `test_removal_only_forgets_its_own_entry` (widened, T2) + `test_removal_is_idempotent_and_never_raises` (`:1699`) + `test_unload_leaves_the_flag_registry_alone` (T1) |
| 7 | Nothing the integration does not own is written to the entry object | `TestFlagOwnership::test_shared_flag_never_writes_through_to_the_entry_object` (T5) — without it the done-when criterion is unfalsifiable |

## Gap: constraint 4 was unpinned (with the claim corrected)

The first revision asserted that "a single-global-flag implementation would have passed the whole suite". **That
specific claim is false and was measured as such.** The fairest version of it — one record shared by every entry, still
mirrored onto the entry object so the two tests that read `entry._elegoo_video_state` keep passing, plus an isolation
fixture so cross-test leakage is not what is being punished — fails **4 of the 75** pre-existing tests:
`test_new_entity_completes_the_pending_release`,
`test_new_entity_waits_while_external_viewer_holds_stream`,
`test_completed_release_is_not_reattempted_by_a_later_entity` and
`test_adopter_paying_the_debt_in_its_own_teardown_clears_the_key`. They fail because they leave one entry's stream
enabled and then expect a *different* entry's entity to see it off. Per-entry isolation is therefore partly pinned
already, by accident of the handover tests rather than by intent.

The gap is real, but it is narrower and subtler than stated. The implementation that slips through all 75 is: *honour
an entry-local dict when one already exists on the entry object, otherwise fall back to one shared record* — plus the
isolation fixture. Measured: **75 passed**. Nothing in the file writes one entry's flag and reads another's **through
the flag's own accessor**, so an implementation that is per-entry only where it is forced to be is invisible.

So: add the small `TestSharedFlagIsPerEntry` class, but describe it honestly in the commit message as closing a hole
the handover tests happened to leave, not as the first test ever to care about isolation.

```python
class TestSharedFlagIsPerEntry:
    """The enabled flag of one printer is invisible to another."""

    def test_flag_of_one_entry_is_not_visible_to_another(self) -> None: ...
    def test_detached_entities_do_not_share_the_attached_flag(self) -> None: ...
```

The first asserts `ea._stream_enabled is True` / `eb._stream_enabled is False` for two entries, and that
`_stream_state_of(entry_b) == {"enabled": False}` — **not** `== {}`: reading an entry's flag *creates* its default
record, exactly as the old `getattr`/`setattr` pair did. Asserting `{}` fails, and asserting it would be asserting an
aspiration rather than the behaviour, so keep `{enabled: False}` and comment that asking is what creates the record.
The second asserts a detached subject keeps `_local_stream_state == {"enabled": False}` while the attached entry's
registry key is set.

Then add `TestFlagOwnership` beside it, holding the two tests that pin the issue's actual demand rather than its
consequences:

```python
class TestFlagOwnership:
    """The flag lives where the integration owns it, and nowhere else."""

    def test_unload_leaves_the_flag_registry_alone(self) -> None: ...
    def test_shared_flag_never_writes_through_to_the_entry_object(self) -> None: ...
```

The second one is:

```python
    def test_shared_flag_never_writes_through_to_the_entry_object(self) -> None:
        """
        The entry object gains no attribute from the shared flag.

        #425 is about ownership: the integration may not hang attributes on a
        core-owned object. Every other test still passes if a setattr creeps
        back, so this is the only thing that falsifies the claim.
        """
        entry = _make_entry("entry-not-mutated")
        before = dict(entry.__dict__)
        subject = _lifecycle_subject(_make_client()[0], entry=entry)

        subject._stream_enabled = True

        assert subject._stream_enabled is True
        assert entry.__dict__ == before, "the entry object must gain no attributes"
```

Without it, "nothing we do not own is written to the entry object" is unfalsifiable — every other test still passes if
someone reintroduces a `setattr(entry, ...)`.

Two classes rather than one big one, because the questions differ: "can printer B see printer A's flag?" versus "does
this state live somewhere we own?". Both new tests use `_lifecycle_subject(client, entry=…)` rather than wiring a
`MagicMock` coordinator by hand, so they exercise the same entry-resolution path production uses.

## Count

**766 passed** = 762 baseline + **4** new tests:

| New test | Why it exists |
| --- | --- |
| `test_flag_of_one_entry_is_not_visible_to_another` | closes the constraint-4 read-path gap |
| `test_detached_entities_do_not_share_the_attached_flag` | constraint 5 vs the registry |
| `test_unload_leaves_the_flag_registry_alone` | makes the done-when reload clause real (T1) |
| `test_shared_flag_never_writes_through_to_the_entry_object` | makes the done-when ownership clause falsifiable (T5) |

Two existing tests are **changed, not removed** (`test_removal_does_not_disturb_the_shared_stream_flag` renamed and
inverted; `test_removal_only_forgets_its_own_entry` widened), so the lifecycle file goes **75 → 79**.

**This is +4, not the +3 the first revision promised, and the difference is reviewer finding 1.** Fixing the false
anti-trap claim required a test that drives the real `async_unload_entry`; there is no way to pin that hook without
one. The reviewer's own arithmetic ("Terminal 765/78 is correct") assumed the +3 set and so pre-dates that fix. The
drop-path pin (finding 2) was deliberately folded into an existing test instead of added, which is what keeps this at
+4 rather than +5. If your count differs from 766, explain why rather than adjusting the number.

## TDD order and exact commands

Work in the real tree; nothing here needs a copy (the copies were used only to pre-measure this plan). The four step
counts below were produced by running this sequence end-to-end in a fresh copy.

1. **Production in `camera.py`, then move the seam, and watch it go red for the *predicted* reasons.** Registry +
   accessor + the `_stream_state` re-key + the pop-with-warning, then *only* the seam: `_leave_stream_enabled`, the new
   `_stream_state_of` helper, and the two assertions at `:1749`/`:2268` re-pointed at it. No test bodies, no ids, no
   fixture, no renames.
   `VIRTUAL_ENV=.venv uv run pytest custom_components/elegoo_printer/tests/test_video_lifecycle.py -q`
   → **3 failed, 72 passed**: `test_stream_enabled_state_is_shared_per_entry` and
   `test_new_camera_refreshes_url_despite_inherited_enabled` (both `assert False is True` — id-less doubles read as
   detached) and `test_removal_does_not_disturb_the_shared_stream_flag` (`assert {} == {'enabled': True}` — constraint
   6 vs the old assertion).
   **Why the production change comes first.** Moving the seam against an untouched `camera.py` is *not* a usable red:
   the accessor does not exist yet, so `_leave_stream_enabled` raises `AttributeError` and takes down all 5 tests that
   call it. Measured: **6 failed, 69 passed**, all `AttributeError: module has no attribute '_video_stream_state'`,
   **none of them the two tests this step is meant to explain**. A red that names the wrong tests teaches you nothing.
2. **Make the 75 green again**: the isolation fixture, the two doubles' `entry_id`s, rewrite the removal test (rename +
   inverted flag assertion + the loudness assertion), and fold the flag pair into
   `test_removal_only_forgets_its_own_entry`. → **75 passed** (three reds repaired, one test renamed, nothing added —
   so 75, not 76).
3. **Add the four new pins** (both classes) → **79 passed** in the file.
4. **Gate from repo root**: `make format` (must be a no-op — the pre-measured end state was checked with
   `ruff format --check` and needs no reformat), `make lint` ("All checks passed"), `make test` → **766 passed**.

## Lint notes (both rules, stated correctly)

`select = ["ALL"]` is on, and `D203`/`D212` are ignored in `pyproject.toml` — which leaves **both** `D200` and `D213`
enforced, and they pull in opposite directions. Verified with isolated probes:

- A **single-sentence** summary written in multi-line style fails **`D200`** ("One-line docstring should fit on one
  line"). Use `"""Summary."""` inline. This is the case that actually bites when adding small tests, and the first
  revision of this plan had the rule backwards.
- A docstring with **multiple paragraphs** whose summary sits on the same line as the opening quotes fails **`D213`**
  ("Multi-line docstring summary should start at the second line"). Put the summary on the second line.

Rule of thumb: one sentence → inline; two or more paragraphs → summary on its own line. `ruff format` does **not**
rewrite either form into the other (verified), so lint, not format, is what tells you which one you wrote.

## Scope

- `custom_components/elegoo_printer/camera.py` — the registry, the accessor, the per-entry pop + warning,
  `_stream_state`, three docstrings.
- `custom_components/elegoo_printer/tests/test_video_lifecycle.py` — the seam, the fixture, two doubles, one rewritten
  test, one widened test, four new tests in two new classes.
- `__init__.py` — **no change.** `forget_pending_video_release(entry.entry_id)` (`:658`) already pops the whole entry,
  so a third registry is absorbed inside the function. Widening the call would be strictly worse: it would put the
  registry's shape in a second module. `async_unload_entry` (`:568`) stays untouched — and that is now pinned by T1,
  not merely asserted.
- `data.py` — **no change, and I agree with the brief.** grep-verified: `data.py` has no reference to
  `_elegoo_video_state`, `_stream_enabled` or `_stream_state`. `ElegooPrinterData` never held the flag; that is the
  issue's premise (`runtime_data` is deleted on unload).

## Deliberately NOT doing

- Not touching `api.py` or `coordinator.py` (out of the declared seam; T4 is serial in wave 2 but the seam is still a
  promise to the reviewer).
- Not replacing the `dict[str, bool]` with a dataclass/NamedTuple. It would read nicer but changes what
  `_stream_state()` hands back and drags 20+ `_stream_enabled` call sites into the diff for no behavioural gain.
- Not clearing the registry from `async_unload_entry`, `async_setup_entry`, or a coordinator hook "just in case".
  Removal is the one bound, matching the two siblings.
- Not making the removal hook quiet, and not re-ordering the retry loop to make the retry warn instead. See
  § The removal hook now warns for why the hook is the only place that covers every terminal ordering.
- Not pruning a key when its last entity tears down (the viewer registry does prune empty groups). The flag is not
  entity-shaped — an entry between connections genuinely *does* have a flag, and that is the state a reload reads.
- Not migrating any persisted state. The flag was never persisted; it is in-RAM either way.

## Limit notes (recorded, not silently paid)

- **Cost of this plan:** ~1 h of implementation, plus the grounding and the pre-measurement (baseline, the four-step
  TDD sequence, and five trap-injection runs of the full suite). The cheap correct-enough fix was chosen throughout:
  4 small production edits, and the test seam touched at 3 sites rather than 75.
- **Two warnings are possible where today emits one.** If a retry notices the removal *and* the hook then forgets an
  enabled flag, the operator sees both `_abandon_video_release`'s line and the new one. Deliberate: under-warning was
  the failure being fixed, the two lines name different things (the entity vs the entry id), and suppressing the
  second would mean the hook guessing what the retry already said.
- **The new warning also fires on the legitimate unload-then-remove path** (debt parked by a foreign viewer, entry
  removed later). The stream really is still on, so the notice is true, but it is a line that did not exist before and
  someone may read it as noise. Recorded, and easy to demote if the maintainer disagrees.
- A `dict[str, dict[str, bool]]` for one boolean is over-shaped. Kept for object-identity sharing with the
  `_stream_enabled` setter and to keep `_stream_state()`'s contract unchanged; a plain `dict[str, bool]` would work
  only if the setter wrote through the registry instead of the returned dict.
- Reading an entry's flag creates its default record, so the registry grows one key per entry that is merely *asked*
  about, not only per entry that enabled video. Bounded by "one entry id per printer, never reused", and identical to
  the old `getattr`/`setattr` behaviour — recorded rather than "fixed", because making reads non-mutating would need a
  separate read path and a second way to be wrong.
- `test_video_lifecycle.py` asserts against the private module-global `_video_stream_state_by_entry` (as it already
  does for both siblings). Consistent with the file's existing style; it does mean renaming the global breaks tests
  instead of being caught by behaviour.
- The `_isolated_video_state_registry` fixture is **defensive, not load-bearing** — deleting it leaves 79 passed / 766
  passed, because every entry double already has a distinct id.
- The rewritten removal test now asserts the flag is **gone**. If the maintainer intended #425 to preserve the flag
  across *removal* as well, this plan is wrong on that one point and the fix is to drop constraint 6 instead — see
  § The one place the brief contradicts itself.
- `test_unload_leaves_the_flag_registry_alone` builds its own client/runtime-data double rather than reusing
  `_make_client`/`_attach_client`, because `async_unload_entry` reads `runtime_data.api` as the *client* while the
  file's helper builds `runtime_data.api.client`. That asymmetry is the helpers' existing shape, not something this
  task should change; the pin matches the hook.
- **UNVERIFIED:** whether HA core ever reuses an `entry_id` (the sibling comments assert it never does; this plan
  inherits that claim rather than re-deriving it). The leak bound depends on it.
- **UNVERIFIED:** no real printer or HA instance was involved — everything here is unit-level. Constraints 1-3 are
  pinned by doubles that model reload (T1 excepted: it drives the real unload hook, still against a mocked hass).

## Parallelisation

SERIAL — single task, wave 2 (`T4 -> T5 -> T6`); `camera.py` is yours alone for its duration. `__init__.py` is
**not** touched, so the wave-1 note that T4 must wait for T2's `__init__.py` (`triage-2026-10-status.md:68`) no
longer binds this task — though T4 still runs in wave 2 for its `camera.py` seam.

## Reviewer findings that did not survive verification

Recorded rather than silently obeyed, per the instruction not to comply blindly:

- **"the per-entry-isolation gap is real — a single-global-flag implementation passes all 75 pre-existing tests."**
  The gap is real; the mechanism is wrong. A single global record fails 4 of the 75. The implementation that actually
  slips through is the fallback-to-shared-record variant, measured at 75 passed. § Gap: constraint 4 was unpinned has
  the measurements and the corrected wording.
- **"Line drift: `async_unload_entry` is `:568` not `:567`."** The plan already said `:568` at all three places it
  cites the hook; there was no `:567` anywhere in it. Nothing to change. (`_pending_video_releases` *was* `:105` and
  is `:104` — fixed; `test_new_entity_completes_the_pending_release` is `:1296` with its inherited-flag assert at
  `:1317`, not `:1316` — `:1316` is the `_live_fdm_camera` line above it.)
- **"`_live_client` does not exist anywhere in the repo, and `:746` is misattributed."** Confirmed and fixed: the real
  remaining callers are `:695` (`_config_entry_id`) and `:788` (`_current_printer_client`). `:746` is not a call site
  at all — it is the docstring line of the `_clear_pending_video_release` method (`:745`).
- **"`test_parking_leaves_the_entry_flag_the_adopter_reads` asserts `entry.runtime_data is None`."** Confirmed absent;
  the only such assertion is `:1311` in `test_new_entity_completes_the_pending_release`. Constraint row 1 rewritten.
- **"Step 2's intermediate count is 75, not 76."** Confirmed by running it. **"Terminal 765/78 is correct"** — not
  applicable any more: finding 1's fix adds a 4th test, so the terminal is 766/79. See § Count.
- **The `_isolated_video_state_registry` fixture is not load-bearing.** Confirmed (79 passed / 766 passed with it
  deleted) and labelled defensive. Note it *looks* load-bearing if you probe it against a scratch copy that still has
  an injected trap in it — re-check any such probe against a clean tree.
- **The lint note was inverted for the case that bites.** Confirmed with isolated probes: a single-sentence summary in
  multi-line style fails **D200**, and D213 fires only when there is more than one line of prose. § Lint notes states
  both. (`ruff format` rewrites neither form into the other, so this is a lint-only failure.)
- **The parent status row (`triage-2026-10-status.md:79`) overstates T4's seam.** Confirmed — `__init__.py` and
  `data.py` need no change. **Applied**, since the reviewer named it as a correction to make: the row now reads
  `camera.py` + `tests/test_video_lifecycle.py`, in the same style as wave 1's two corrections. The shared-file note
  at `:68` was left alone (its premise is stale but the ordering it enforces is still right) — see § Other notes for
  the orchestrator.
- **The two bare `SimpleNamespace()` doubles at `:405`/`:422` need real ids.** Confirmed — they are 2 of the 3 reds in
  step 1, and a real `ConfigEntry` always has an `entry_id`.

## Other notes for the orchestrator

- **The parent status row (`triage-2026-10-status.md:79`) is corrected** — T4's seam is now recorded as
  **`camera.py` + `tests/test_video_lifecycle.py` only**. It previously claimed `camera.py`, `__init__.py`, `data.py`
  and the test file; `__init__.py` and `data.py` are no-ops, because `forget_pending_video_release(entry.entry_id)`
  already pops by entry id and absorbs a third registry — widening that call would leak the registry's shape into a
  second module. Corrected in the same style as wave 1's two corrections, and that is the only change made to that
  file. **Deliberately left alone:** the shared-file note at `:68` ("T4 needs T2's `__init__.py` settled (both edit it)
  -> T4 after wave 1"). Its premise is now false — T4 does not edit `__init__.py` — but the ordering it enforces is
  still correct (T4 is a wave-2 serial task on its own account), and re-deriving the wave-2 chain is the
  orchestrator's call, not a side effect of a plan refinement.
- #425 is a maintainer-opened housekeeping issue with no user-visible symptom, **but this is not a zero-behaviour
  change**: an operator who removes an entry whose video is still enabled now gets a warning line they did not
  previously get (and would previously have got one only in the narrower case a live retry happened to notice the
  removal). The changelog bullet should say the flag moved to an integration-owned registry, that behaviour is
  otherwise unchanged, and that removal now warns about a stream left enabled. Do not phrase it as a bug fix — no user
  was observing a failure here.
