# Spec: Land PR #404 (`update_ip` service) in the correct locations

- **Status:** Completed (merged via [#404](https://github.com/danielcherubini/elegoo-homeassistant/pull/404) → main `9f8b6cd`; release 2.13.0 per §7 pending)
- **Date:** 2026-09-01
- **Source:** Design discussion re: [PR #404](https://github.com/danielcherubini/elegoo-homeassistant/pull/404) (contributor caohuongls)
- **Review:** 2 rounds (all findings resolved; round-2 verdict: pass with 4 minor nits — applied)

## Context

PR #404 (contributor `caohuongls`, HACS integration `elegoo_printer` v2.12.1) adds an
`update_ip` service so a DHCP-changed printer IP can be fixed via a service call or the
Developer Tools UI instead of deleting and re-adding the config entry. The idea is good and
the contributor solved a real pain point, but **both files were added at the repository
root** — dead code under HACS, because only `custom_components/elegoo_printer/` is installed
(HACS rule: all integration files must live under
`ROOT_OF_THE_REPO/custom_components/INTEGRATION_NAME/`). Greptile's automatic review already
flagged the same issue.

Plan: update the contributor's PR directly (`maintainerCanModify: true` is enabled on the
PR), moving the feature into the integration package and polishing the handler.

## Changes

### 1. `custom_components/elegoo_printer/__init__.py` (modify)

Imports:

- `voluptuous as vol`
- extend the existing `homeassistant.exceptions` import with `ConfigEntryError`
- `ConfigEntryState` from `homeassistant.config_entries`
- `SupportsResponse` from `homeassistant.core`
- `ServiceCall` in the existing TYPE_CHECKING block (file has
  `from __future__ import annotations`)

Constants:

- `SERVICE_UPDATE_IP = "update_ip"`
- `SERVICE_UPDATE_IP_SCHEMA = vol.Schema({vol.Required("entry_id"): str, vol.Required(CONF_IP_ADDRESS): vol.Length(min=1)})`
  (min-length so a script passing empty IP fails loudly; plain `str`, no IP regex —
  hostnames are allowed, matching the config flow's plain `TextSelector`)

`async_setup` (placed before `async_setup_entry`):

```python
async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Elegoo Printer component."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_IP,
        _async_update_ip,
        schema=SERVICE_UPDATE_IP_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True
```

`_async_update_ip(hass, call) -> dict` handler contract:

1. `entry = hass.config_entries.async_get_entry(call.data["entry_id"])`; if
   `entry is None or entry.domain != DOMAIN` → error response. Code comment: the
   `services.yaml` selector constrains only the UI picker; programmatic/automation calls
   can pass any `entry_id`, so this domain check is the real guard, not dead code.
2. `hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_IP_ADDRESS: <new>})` —
   **update `entry.data` only**. Rationale: the integration has no options flow; the config
   flow and all migrations write the IP into `data`; the options-write from the original PR
   is intentionally dropped. *Pin note: `async_update_entry` is a sync `@callback` on HA
   2025.4.0 — do **not** await; if the HA pin is ever advanced, re-verify this call form.*
3. Explicit `await hass.config_entries.async_reload(entry.entry_id)`. Code comment documents
   the known behavior: on a still-`LOADED` entry, the data write also fires the entry's
   `add_update_listener(async_reload_entry)` reload; HA serializes both on the entry's setup
   lock (final state deterministic), but a second full unload→setup cycle runs (extra printer
   API connect/disconnect, MQTT broker stop/start, entity re-registration churn, ~2×
   latency). In the headline use case (printer unreachable at old IP → entry in
   `SETUP_RETRY`/`SETUP_ERROR`, no live update listener — listeners register only on
   successful setup and unregister on unload) the explicit reload is the only cycle. No
   awaitable public API exists to wait for the listener-driven reload (verified on HA 2025.4
   and 2026.8), so the explicit one is the only way for the handler to return a definitive
   result.
4. `try/except ConfigEntryError` around the reload → error response. Labeled **DEFENSIVE** in
   a code comment: on HA 2025.4 the entry setup flow catches `ConfigEntryError` internally
   (marks the entry `SETUP_ERROR`) instead of propagating; the state check (step 5) is the
   real failure signal. The catch guards other HA versions/paths.
5. `if entry.state is not ConfigEntryState.LOADED:` →
   `{"success": False, "error": "Printer unreachable at new address (entry state: …)"}`.
6. Otherwise → `{"success": True, "message": "IP updated to … and entry reloaded"}`. The
   plain dict response shape is verified safe across HA 2025.4 → 2026.8 (only an
   isinstance-dict check is enforced; no required `"result"` key).

`git rm` the contributor's root-level `__init__.py`.

### 2. `custom_components/elegoo_printer/services.yaml` (new, in the integration folder)

```yaml
update_ip:
  name: Update IP Address
  description: Update the IP address of an Elegoo Printer config entry and reload it.
  fields:
    entry_id:
      name: Config Entry
      description: Choose the printer who needs to change its IP.
      required: true
      selector:
        config_entry:
          integration: elegoo_printer
    ip_address:
      name: IP Address
      description: Type the new IP address (Example: 192.168.1.3).
      example: "192.168.1.3"
      required: true
      selector:
        text:
```

Typos fixed vs the original ("adress" → "address" ×2, "Chooose" → "Choose"). No `target`
block: intentional — the config-entry selector in the field keeps the service callable with
a raw `entry_id` in data (watcher-script case). `git rm` the root-level `services.yaml`.

### 3. `custom_components/elegoo_printer/tests/test_services.py` (new)

`MagicMock` + `asyncio.run` convention (see `test_migration_v4_to_v5.py`). Mock hygiene (to
prevent landmines):

- `entry.data` MUST be a **real dict** (the handler does `{**entry.data}` — raises on a raw MagicMock)
- `entry.entry_id` / `entry.domain` real strings
- `hass.config_entries.async_reload` MUST be an `AsyncMock` (plain MagicMock not awaitable)
- `entry.state` a real `ConfigEntryState` value
- `async_update_entry` asserted via its call kwargs (the mock doesn't apply the update itself)

Six tests:

1. `async_setup` registers with `DOMAIN`, `"update_ip"`, `SERVICE_UPDATE_IP_SCHEMA` (identity
   assert), `supports_response=SupportsResponse.OPTIONAL`
2. Happy path: `async_update_entry` called with data containing the new IP;
   `async_reload` awaited on the entry_id; response `success: True`
3. Unknown `entry_id` → `success: False`; neither `async_update_entry` nor `async_reload`
   called
4. Entry from another domain → refused; no writes/reload
5. `async_reload` raises `ConfigEntryError` → `success: False`, message surfaces the cause.
   Test comment: "defensive path — on HA 2025.4 a failed reload surfaces via entry state
   instead (see test 6); this guards versions/paths where `ConfigEntryError` propagates"
6. `async_reload` completes with `entry.state != ConfigEntryState.LOADED` (e.g.
   `SETUP_ERROR`) → `success: False`, "unreachable" message

File docstring: under mocks only the handler's explicit reload fires; the production
double-cycle (listener + explicit reload, serialized by HA) is not observable here and is
documented in code comment + README instead.

### 4. `README.md` (modify)

- New **Services** bullet in the README index list (~lines 13–21, bulleted, matching
  existing anchor style)
- New "Services" section: what `update_ip` does; invocation styles — UI (Developer Tools →
  Actions, `elegoo_printer.update_ip`, config-entry dropdown + IP field, "return response"
  available) and automation (`elegoo_printer.update_ip` with `entry_id` + `ip_address` in
  data); watcher-script motivation (external IP-detection script triggers the update);
  known-behavior note: on a still-`LOADED` entry the reload runs twice (listener +
  explicit, serialized by HA); in the common case (printer unreachable at the old IP) it's
  exactly one; the entry ends in `SETUP_ERROR`/`SETUP_RETRY` and the service reports
  failure when the new IP is unreachable.

### 5. `CHANGELOG.md` (modify)

`[Unreleased]` → `### Added`: one-line entry for the new `update_ip` service.

### 6. PR mechanics

1. Add the contributor's fork (`caohuongls/elegoo-homeassistant`) as a remote in this repo;
   fetch their `main` head (the PR head ref is `main` on the fork)
2. Branch from their head; if behind current `main`, rebase onto `main` so the PR diff is a
   clean superset
3. Apply changes §1–§5; `make test && make lint && make format`
4. Create the commit(s) with `--author` for `caohuongls` (attribution in the PR diff/commits
   stays honest)
5. **Force-push to the fork's `main` with `--force-with-lease`** (maintained branch edit on
   the author's fork; updates open PR #404 — maintainer edits enabled)
6. Reply on the PR with a single kind, explanatory comment: one line on what was wrong
   (files at repo root = not installed by HACS); what we adjusted mechanically (files moved
   into `custom_components/elegoo_printer/` + polish: `vol.Schema`, domain check,
   data-only update, failure reporting, typo fixes, tests, docs); who rewrote the branch and
   why; a thank-you for the feature
7. When CI on the author's fork is green, the maintainer merges

### 7. Post-merge release step (AGENTS.md workflow)

1. Pre-flight: confirm `main` is green (latest Lint/Test/Validate runs) and the working tree
   is clean — per AGENTS.md
2. Bump `manifest.json` + `pyproject.toml` (+ `uv.lock` via the make target) to **2.13.0** —
   new user-facing service = minor bump
3. `make format && make lint && make test`
4. Commit `chore: bump version to 2.13.0` (include the `uv.lock` change); push to `main`
   (never push a tag)
5. `release.yml` creates tag + release; then curate the release notes per AGENTS.md: group
   by impact, explain the why, thank @caohuongls specifically for the feature (the service
   that made this fix unnecessary for their users)

## Explicit non-goals

- No `entry.options` update (no options flow; the IP lives in `entry.data`)
- No re-discovery feature (different problem; the config flow already rescans the network)
- No `services.py` module split (single service; inline keeps the diff close to the original)
- No `target` block in `services.yaml` (preserves raw `entry_id` data calls for watcher scripts)

## Review history

- **Round 1:** ❌ fail — 1 blocking (`EntryState` does not exist in HA; the enum is
  `ConfigEntryState`), 4 important (double-reload undocumented; no release/version-bump step;
  `SupportsResponse.OPTIONAL` missing; the `ConfigEntryError` catch is a dead path on
  2025.4 — the state check is the real failure signal), 7 minor (test-mock landmines;
  `return True`; `vol.Length(min=1)`; domain-check rationale; CHANGELOG entry; README index;
  `--author` flag). All fixed.
- **Round 2:** ⚠️ pass with issues — all 12 round-1 fixes verified as genuinely resolved
  against installed HA 2025.4.0 source, the live PR #404 diff, and repo conventions; 4 minor
  spec-text nits (force-push after rebase; README index is a list not a table; AGENTS.md
  pre-flight in §7; pin-dependency note on the `async_update_entry` call form). All applied
  in this spec.
