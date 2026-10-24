---
status: committed
parent: docs/roadmap/triage-2026-10-status.md
done-when: On an entry whose `entry.options` already carries an `ip_address`, `elegoo_printer.update_ip` writes the new address into BOTH `entry.data` and `entry.options` in a single `async_update_entry` call, so the `{**(entry.data or {}), **(entry.options or {})}` merge that runtime setup reads resolves to the new address — pinned in tests by `Printer.from_dict(merged).ip_address == "<new ip>"` as well as by the exact call kwargs — the entry still reloads and still reports unreachability honestly, the two currently-red CI tests pass, a new regression test fails without the source fix and passes with it, and the false "the integration has no options flow" comment is gone. Gate: `make test` 751 passed, `make lint` clean, `make format` no-op.
---

# T2 — `update_ip` must write the new IP into `entry.options` too (PR #430)

**One task.** Fix `_async_update_ip` so the new address survives the data/options
merge, and bring `tests/test_services.py` with it. Nothing else in the
integration changes.

## Grounding (verified against the tree at `50eb136`, not asserted)

I have no subagent tool, so this was grounded inline: every path below was read
and every claim below was executed, including the failure reproduction and the
candidate end-state (full suite run, then the tree restored to pristine —
`git status` is clean).

- Handler under fix: `custom_components/elegoo_printer/__init__.py:96-168`
  (`async def _async_update_ip(hass: HomeAssistant, call: ServiceCall) -> dict`).
  Schema `SERVICE_UPDATE_IP_SCHEMA` at `:88-93`; registered three times in
  `async_setup` (`:444`, `:451`, `:458`).
- **The bug is real and load-bearing.** Runtime config is read data-first,
  options-second: `__init__.py:489-492`, `coordinator.py:38`
  (`merged_config = {**entry.data, **(entry.options or {})}`), `sensor.py:81-84`.
  That merged dict is the only input to
  `ElegooPrinterApiClient.async_create(...)` → `Printer.from_dict(dict(config), config=config)`
  (`api.py:284`) → `printer.ip_address = attrs.get("MainboardIP", attrs.get("ip_address"))`
  (`sdcp/models/printer.py:428`) → `self._ip_address = printer.ip_address`
  (`api.py:121`) and every transport target. So a stale `ip_address` in
  `options` beats the freshly written `data`: **the service reloads the entry and
  then reconnects at the old address.**
- **Options really do hold `ip_address`, for any user who saves the options
  form today** — not only "older" entries (PR #430's comment gets this wrong):
  `config_flow.py:1316-1331 async_get_options_flow` and
  `:1333-1340 async_supports_options_flow → True`; HA writes the flow payload
  verbatim (`.venv/.../homeassistant/config_entries.py:3528`
  `async_update_entry(entry, options=result["data"])`); every options step saves
  `printer.to_dict()`, which contains `"ip_address"`
  (`sdcp/models/printer.py:321`; call sites `config_flow.py:1539-1542`,
  `:1759-1762`, `:1829-1832`). Already pinned by
  `tests/test_offline_setup_options.py:458` and `tests/test_config_flow_cc2_options.py:325`.
- The surviving comment at `__init__.py:119-120` — *"the integration has no
  options flow; the config flow and all migrations write the IP into
  entry.data"* — is false on its first clause and must be replaced.
- **Migrations are innocent**: `__init__.py:693-695`, `:719`, `:755`, `:809`,
  `:824-826` never pass `options=`; they only read options as a merge source
  (`:665-668`, `:726-729`). No `MIGRATION_VERSION` symbol exists; the constants
  are `CONFIG_VERSION_1..5` (`const.py:71-75`), flow `VERSION = CONFIG_VERSION_5`
  (`config_flow.py:413`).
- `homeassistant==2025.4.0` (`pyproject.toml`); `async_update_entry` is a **sync**
  `@callback` with keyword-only `data=` / `options=`
  (`config_entries.py:2308-2322`, `_async_update_entry` at `:2353`, options
  applied at `:2423-2425`). It must NOT be awaited. A real `ConfigEntry.options`
  is always a `MappingProxyType` (`:453`), never `None` — the `or {}` guard is
  for the test doubles and legacy/None shapes.
- Test file: `custom_components/elegoo_printer/tests/test_services.py`,
  `class TestUpdateIpService` (7 tests, module-level functions using
  `asyncio.run(_run())`). Helpers `_make_hass_with_entry` (`:48-57`,
  `async_update_entry` is a **sync** `MagicMock`) and `_make_config_entry`
  (`:59-72`), whose docstring already establishes that dict attributes must be
  real. It never sets `entry.options`, so `entry.options` is an auto-created
  `MagicMock` and `{**MagicMock()}` evaluates to `{}` — **measured**, which is
  why the merged dict in the current tests is address-only.
  The two red assertions are `:144-146` and `:247-249`.

### Reproduced, not assumed

| State | `tests/test_services.py` | Full suite |
|---|---|---|
| Pristine tree | 27 passed | **750 passed, 12 warnings** (matches the recorded baseline) |
| PR #430's source change only | **2 failed, 25 passed** — the same two CI failures | — |
| Test updates only (no source fix) | **3 failed, 25 passed** (new test fails `KeyError: 'options'`) | — |
| Source fix + test updates | **28 passed** | **751 passed, 12 warnings** |

`make lint` → "All checks passed!"; `make format` reflows two spots (see the
format pitfall in Step 3).

## Design decision, and the alternative rejected

**Adopt the contributor's approach: keep both stores synchronised.** The merge
direction is "options win", so writing `entry.data` alone cannot work by
construction — that is the whole bug.

Rejected: *delete `ip_address` from `entry.options` instead.* The options flow
merges `{**data, **options}` (`config_flow.py:1426-1429`), rebuilds a `Printer`
from it and saves the **whole** `to_dict()` payload back on every save
(`:1539`, `:1759`, `:1829`), so a deletion is silently undone the next time the
user opens the options form — and it would revert the address the flow just
displayed. Rejected.

Two deliberate deviations from PR #430's verbatim snippet, both evidenced:
1. `{**(entry.options or {}), ...}` rather than `{**entry.options, ...}` — the
   latter raises `TypeError: 'NoneType' object is not a mapping` on a `None`
   options store; the guard matches the idiom already used at `:490-491`.
2. PR #430's replacement comment says "older options flows". Wrong scoping — any
   options-flow save today creates the condition. The comment below states the
   real mechanism (merge direction) instead of a population.

Keep, unchanged, the rest of the handler: the explicit
`await hass.config_entries.async_reload(...)`, the defensive `ConfigEntryError`
catch, and the `entry.state is not ConfigEntryState.LOADED` failure check. Also
leave the reload-dedup comment at `:128-140` alone — "the data write above" still
reads correctly (both writes precede the reload, and HA fires one listener set
per changed `async_update_entry` call).

## Out of scope — do not touch

- `services.yaml` (`update_ip` block, `:1-18`) — the fields are unchanged.
- **Translations.** There is no `strings.json`; services carry no translation
  keys in any of the 18 locale files, and `tests/test_translations.py` checks
  `config.error` / `options.error` parity only.
- `README.md:192-205` — "updates the printer's IP address stored in a config
  entry" is still true (it now covers both stores). No user-visible behaviour
  change to document beyond the fix.
- `api.py` — see the recorded limit below. Its seam belongs to T6.
- `CHANGELOG.md` — **not in this task's declared seam** (`__init__.py` +
  `tests/test_services.py`), and T1/T3 ride the same wave; three siblings
  appending to `[Unreleased]` is a self-inflicted conflict. Hand the orchestrator
  this line for the loop's step-7 changelog instead:

  > `update_ip` now actually moves the printer when its address was saved in the
  > printer's options: the new address is written to both places Home Assistant
  > keeps it, so the reload connects where the printer now is instead of where it
  > used to be. Reported by @caohuongls in #430.

  **Credit is owed regardless of the merge path:** the fix originates from PR #430
  by `caohuongls` (per the status doc's decision log, T2 is a rewrite, not a
  merge, so nothing records the author automatically).

## Parallelisation

**SERIAL.** One task, one seam (`__init__.py` + `tests/test_services.py`); wave-1
disjointness against T1/T3 is the orchestrator's concern, and this change touches
neither of their files.

---

## TDD steps

### Step 1 — RED: update the tests first, prove they fail for the right reason

Edit `custom_components/elegoo_printer/tests/test_services.py` only.

**1a.** Give `_make_config_entry` (`:59-72`) a real default options store. A bare
`MagicMock` attribute splats to `{}`, so without this the new test would silently
exercise the no-options shape. Replace the signature and add one line:

```python
def _make_config_entry(
    *,
    entry_id: str,
    domain: str,
    data: dict,
    state: ConfigEntryState,
    options: dict | None = None,
) -> MagicMock:
    """
    Build a fake config entry with real string and dict attributes.

    ``entry.data`` MUST be a real dict (the handler does ``{**entry.data}`` —
    a bare MagicMock attribute raises) and ``entry.entry_id`` / ``entry.domain``
    MUST be real strings (the handler string-compares and does
    ``hass.config_entries.async_get_entry(...) is entry``).

    ``options`` defaults to a REAL empty dict for the same reason: left as a
    bare MagicMock attribute it splats to ``{}``, so a test silently exercises
    the no-options shape instead of the merge it means to pin.
    """
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.domain = domain
    entry.data = data
    entry.options = {} if options is None else options
    entry.state = state
    return entry
```

**1b.** Pin both kwargs at the two existing assertions — `:144-146`
(`test_update_ip_updates_entry_data_and_reloads`) and `:247-249`
(`test_update_ip_unreachable_printer_state_check`). Both become:

```python
            hass.config_entries.async_update_entry.assert_called_once_with(
                entry,
                data={"ip_address": "198.51.100.7", "id": "p1"},
                options={"ip_address": "198.51.100.7"},
            )
```

**1c.** Add one regression test as the last method of `TestUpdateIpService`
(before the module-level `_make_cc2_entry`), plus the `Printer` import. It pins
the **merge outcome at the layer that chooses the address we connect to**, not
just the call shape:

```python
    def test_update_ip_overwrites_a_stale_ip_in_entry_options(self) -> None:
        """
        A stale ``ip_address`` in ``entry.options`` must not win.

        The options flow saves the whole ``Printer.to_dict()`` payload —
        ``ip_address`` included — into ``entry.options``, and runtime setup
        reads ``{**entry.data, **entry.options}``, so options beat data on a
        conflicting key. An entry whose options were saved before the printer
        moved therefore reconnects at the OLD address unless the service
        writes both stores. (PR #430, reported by @caohuongls.)
        """

        async def _run() -> None:
            entry = _make_config_entry(
                entry_id="abc-123",
                domain=DOMAIN,
                data={"ip_address": "192.0.2.1", "id": "p1"},
                options={
                    "ip_address": "192.0.2.99",  # stale, saved by the options flow
                    "allow_printer_offline": True,
                },
                state=ConfigEntryState.LOADED,
            )
            hass = _make_hass_with_entry(entry=entry)

            call = MagicMock()
            call.data = {"entry_id": "abc-123", "ip_address": "198.51.100.7"}

            result = await _async_update_ip(hass, call)

            assert result["success"] is True
            _, kwargs = hass.config_entries.async_update_entry.call_args
            # Both stores carry the new address...
            assert kwargs["data"]["ip_address"] == "198.51.100.7"
            assert kwargs["options"]["ip_address"] == "198.51.100.7"
            # ...and the merge the runtime actually reads resolves to the new
            # address rather than the stale one.
            assert {**kwargs["data"], **kwargs["options"]}[
                "ip_address"
            ] == "198.51.100.7"
            # Options keys that are not the address survive the rewrite.
            assert kwargs["options"]["allow_printer_offline"] is True
            assert kwargs["data"]["id"] == "p1"
            # The merge outcome, at the layer that picks the address we connect
            # to: async_setup_entry hands Printer.from_dict the merged config,
            # and printer.ip_address is the address every transport targets.
            # Without the options write this reads the stale 192.0.2.99.
            merged = {**kwargs["data"], **kwargs["options"]}
            assert Printer.from_dict(merged).ip_address == "198.51.100.7"

        asyncio.run(_run())
```

Import (ruff `I001` — it sorts **after** the `sdcp.exceptions` block, not
before it; I hit this and fixed it that way):

```python
from custom_components.elegoo_printer.sdcp.models.printer import Printer
```

> Build the merge from the recorded `kwargs`, **not** from `entry.data` /
> `entry.options`: `async_update_entry` here is a bare `MagicMock`, so it never
> mutates the entry and reading the entry back would assert nothing.

**1d. Command — expect `3 failed, 25 passed`** (the two known assertions plus the
new test failing `KeyError: 'options'`, i.e. the handler passed no `options` at
all — a legible RED, not an import or fixture error):

```bash
cd /home/daniel/Coding/HomeAssistant/elegoo-homeassistant
uv run pytest custom_components/elegoo_printer/tests/test_services.py -q
```

### Step 2 — GREEN: fix the handler

In `custom_components/elegoo_printer/__init__.py`, replace `:119-125` verbatim:

```python
    # Update the entry data only: the integration has no options flow; the
    # config flow and all migrations write the IP into entry.data.
    # Pin note: async_update_entry is a sync @callback on the HA 2025.4.0 pin
    # — it must NOT be awaited. If the HA pin is ever advanced, re-verify
    # this call form.
    new_data = {**entry.data, CONF_IP_ADDRESS: new_ip}
    hass.config_entries.async_update_entry(entry, data=new_data)
```

with:

```python
    # The runtime config is read data-first, options-second
    # (``{**(entry.data or {}), **(entry.options or {})}`` — see
    # async_setup_entry, coordinator and sensor), so options win on a
    # conflicting key. The options flow saves the whole Printer.to_dict()
    # payload -- ip_address included -- into entry.options, so writing
    # entry.data alone leaves the stale address winning and the printer is
    # still sought at its old address. Both stores must be written.
    # Pin note: async_update_entry is a sync @callback on the HA 2025.4.0 pin
    # — it must NOT be awaited. If the HA pin is ever advanced, re-verify
    # this call form.
    new_data = {**entry.data, CONF_IP_ADDRESS: new_ip}
    new_options = {**(entry.options or {}), CONF_IP_ADDRESS: new_ip}
    hass.config_entries.async_update_entry(
        entry,
        data=new_data,
        options=new_options,
    )
```

One call, both stores: HA fires the update listeners once per changed call
(`config_entries.py:2437-2446`), so this does not add a second reload.

### Step 3 — Gate

```bash
cd /home/daniel/Coding/HomeAssistant/elegoo-homeassistant
uv run pytest custom_components/elegoo_printer/tests/test_services.py -q   # 28 passed
make lint      # All checks passed!
make format    # see pitfall below, then re-run the tests
make test      # 751 passed, 12 warnings (baseline 750 + this task's new test)
```

**Format pitfall (measured):** `ruff format` reformats two spots in the code as
first written — it explodes the keyword-only signature onto one-per-line (already
written that way above) and inserts a blank line after the new test's docstring
before `async def _run`. Run `make format`, then **re-run `make lint` and
`make test`** — don't call it green off the pre-format text.

## Acceptance criteria

- [ ] `git diff --name-only` shows exactly two files:
      `custom_components/elegoo_printer/__init__.py` and
      `custom_components/elegoo_printer/tests/test_services.py`.
- [ ] The string `the integration has no options flow` appears nowhere in the repo
      (`grep -rn "has no options flow" custom_components/`).
- [ ] `grep -n "options=new_options" custom_components/elegoo_printer/__init__.py` → 1 hit.
- [ ] `uv run pytest custom_components/elegoo_printer/tests/test_services.py -q` → 28 passed.
- [ ] Reverting **only** the source hunk makes the new test fail (it did:
      `KeyError: 'options'`) — the regression is pinned, not decorative.
- [ ] `make test` 751 passed · `make lint` clean · `make format` a no-op.
- [ ] The reload logic, both failure paths, the schema and the registration
      (3 services) are untouched.
- [ ] `@caohuongls` / `#430` credit handed to the orchestrator for the release notes.

## Recorded limitation (deliberately NOT fixed)

`api.py:1272-1323` `_update_config_entry_if_needed` compares
`dict(config_entry.data)` against `printer.to_dict()` over `fields_to_check`
(which includes `ip_address`, `:1298`) and writes **`data=` only** (`:1319-1322`)
— it never touches options.

Does that re-introduce drift after this fix? **On the normal path, no:** the
runtime `Printer` is built from the merged config and
`sync_from_attributes` (`sdcp/models/printer.py:358-408`) syncs only firmware,
model, name and brand — never `ip_address` — so the comparison sees the same
address the service just wrote and no rewrite happens.

**On the proxy-enabled path, yes, and this fix does not close it.**
`_setup_proxy_if_enabled` replaces the client's printer with the proxy
registry's instance (`api.py:1093 self.printer = proxy_printer`), whose
`ip_address` comes from a live discovery packet's `MainboardIP`
(`websocket/server/registry.py:130-132`), and that instance is what
`_update_config_entry_if_needed` is handed (`api.py:540`, `:704`). If the printer
self-reports an address different from the one the service was called with (NAT,
a second move, a hostname), `data` gets the self-reported value while `options`
keeps the service's — and options still win, so the integration goes back to
seeking the printer at the address the user typed. This is pre-existing behaviour
that this task neither creates nor worsens (before the fix, options were stale
permanently; now they are correct until a proxy discovery disagrees), and fixing
it properly means writing options from `api.py`, which is T6's seam in the same
run — so it stays out of a wave-1 single-task change. **Follow-up candidate:**
make `_update_config_entry_if_needed` keep both stores in sync the same way, or
drop `ip_address` from `fields_to_check` and make `update_ip` the single writer.

## Not verified

- **UNVERIFIED:** `parent: docs/roadmap/triage-2026-10.md` — that file does not
  exist; the roadmap status file is `docs/roadmap/triage-2026-10-status.md`
  (which names this exact plan path for T2). I used the `parent:` value as
  specified in the dispatch rather than inventing a different one.
- **UNVERIFIED:** no live-HA / real-printer confirmation. Everything here is
  unit-level plus source reading; the end-to-end claim ("connects at the new
  address") is pinned through `Printer.from_dict(merged).ip_address`, the value
  every transport targets, not through a real device.
- **UNVERIFIED:** PR #430's diff was not fetched; the "+11/-5, one file, no test
  changes" description and the snippet in the task text are what I reproduced
  (and reproduced faithfully — the same two CI failures, same messages).
- **UNVERIFIED:** the "newest HA, Python 3.14" CI leg could not be run locally
  (pinned `homeassistant==2025.4.0`, Python 3.13). `async_update_entry`'s
  keyword-only `options=` has been stable since long before 2025.4, so the risk
  is low, but it is the leg that is red in CI and it was not executed here.

## Validated end-state (reference — apply, don't re-derive)

I ran exactly this and it passed the whole gate before I restored the tree.

```diff
--- a/custom_components/elegoo_printer/__init__.py
+++ b/custom_components/elegoo_printer/__init__.py
@@ -116,13 +116,23 @@ async def _async_update_ip(hass: HomeAssistant, call: ServiceCall) -> dict:
     new_ip = call.data[CONF_IP_ADDRESS]
     LOGGER.info("Updating printer IP to %s for entry %s", new_ip, entry_id)
 
-    # Update the entry data only: the integration has no options flow; the
-    # config flow and all migrations write the IP into entry.data.
+    # The runtime config is read data-first, options-second
+    # (``{**(entry.data or {}), **(entry.options or {})}`` — see
+    # async_setup_entry, coordinator and sensor), so options win on a
+    # conflicting key. The options flow saves the whole Printer.to_dict()
+    # payload -- ip_address included -- into entry.options, so writing
+    # entry.data alone leaves the stale address winning and the printer is
+    # still sought at its old address. Both stores must be written.
     # Pin note: async_update_entry is a sync @callback on the HA 2025.4.0 pin
     # — it must NOT be awaited. If the HA pin is ever advanced, re-verify
     # this call form.
     new_data = {**entry.data, CONF_IP_ADDRESS: new_ip}
-    hass.config_entries.async_update_entry(entry, data=new_data)
+    new_options = {**(entry.options or {}), CONF_IP_ADDRESS: new_ip}
+    hass.config_entries.async_update_entry(
+        entry,
+        data=new_data,
+        options=new_options,
+    )
```

The test-file diff is Step 1 verbatim (helper signature + `options` line +
docstring paragraph, both assertion blocks, the new test, the `Printer` import),
as reformatted by `make format`.
