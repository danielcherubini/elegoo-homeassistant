---
status: approved
done-when: "`uv run pytest` passes with the coordinator passing `config_entry` explicitly and no `current_entry` contextvar shim anywhere in the tests; a CI leg runs the full suite against the newest HA on Python 3.14 and is green; issue #419 is closed with the packager's 2026.9.4 run confirmed passing."
---

# HA 2026 compatibility (#419) — Spec

**Issue:** [#419 — Test failures against 2026.8.0](https://github.com/danielcherubini/elegoo-homeassistant/issues/419)
**Reporter:** nixpkgs packager (runs our suite against HA 2026.9.4 downstream)

## Problem

The nixpkgs build runs our test suite against Home Assistant **2026.9.4**. Nine tests in
`custom_components/elegoo_printer/tests/test_coordinator.py` die with:

```
RuntimeError: Frame helper not set up
```

They pass in our CI because `pyproject.toml:9` pins `homeassistant==2025.4.0`.

## Root cause (verified)

`ElegooDataUpdateCoordinator.__init__` (`coordinator.py:46`) calls the HA base class
**without** `config_entry`:

```python
super().__init__(hass, LOGGER, name=f"{entry.title}", update_interval=timedelta(seconds=2))
```

`self.config_entry = entry` is assigned just above it, so the attribute ends up correct —
but HA's base class never sees the argument and falls into its legacy branch. In HA
2026.9.4 (`homeassistant/helpers/update_coordinator.py:95-108`) that branch is:

```python
if config_entry is UNDEFINED:
    frame.report_usage(
        "relies on ContextVar, but should pass the config entry explicitly.",
        core_behavior=frame.ReportBehavior.ERROR,
        core_integration_behavior=frame.ReportBehavior.ERROR,
        custom_integration_behavior=frame.ReportBehavior.IGNORE,   # <-- custom integrations exempted
    )
    self.config_entry = config_entries.current_entry.get()
```

`frame.report_usage()` raises `RuntimeError("Frame helper not set up")` when HA's frame
helper has not been installed via `frame.async_setup(hass)`. Our tests use a bare
`MagicMock` hass (see the `hass` fixture in `custom_components/elegoo_printer/conftest.py`),
so the helper is never set up and the call raises.

## Two facts that reframe the issue

**1. This is a test-only failure — no user's printer is broken.** For *this specific*
report HA passes `custom_integration_behavior=ReportBehavior.IGNORE`, and the upstream
comment states enforcement is "not planned to enforce for custom integrations". In a real
Home Assistant the frame helper is set up and the ContextVar path still resolves the entry
correctly. Nothing in production is misbehaving today; only a bare-`MagicMock` test harness
crashes.

**2. Bumping the HA pin is not a one-line change — it drags in a Python major-version
migration.** PyPI `requires_python` per release:

| HA version | Requires Python |
|---|---|
| 2025.4.0 … 2026.2.x | `>=3.13.2` |
| **2026.3.0 … 2026.9.4** | **`>=3.14.2`** |

This project pins `.python-version = 3.13`, `requires-python = ">=3.13"`, and ruff
`target-version = "py313"`. So "support HA 2026" silently means "migrate to Python 3.14".

## Terminology — three separate knobs (the source of the confusion)

"Supporting HA 2026" conflates three independent settings. Keeping them distinct is the
point of this spec:

| Knob | File / key | Current | Meaning |
|---|---|---|---|
| **dev pin** | `pyproject.toml` → `homeassistant==2025.4.0` | 2025.4.0 | What *we* install and test against locally + in CI |
| **floor** | `hacs.json` → `"homeassistant"` | `2024.12.0` | Minimum HA a **user** needs. A lower bound — *not* a maximum, and *not* what we test |
| **python floor** | `.python-version`, `requires-python`, ruff `target-version` | 3.13 | Which HA versions are even installable here |

The **floor** has only ever moved `2024.8.1` → `2024.12.0` and is untouched by a dev-pin
bump. Raising it strands users on older HA for no benefit, because nothing in this change
requires a newer HA. **This spec leaves the floor at `2024.12.0`.**

## Evidence

Same commit (`c86ef27`), same throwaway venv, one line differing:

| | HA 2025.4.0 / Python 3.13 (repo venv) | HA 2026.9.4 / Python 3.14 (probe venv) |
|---|---|---|
| Current code | 545 passed | **9 failed**, 536 passed |
| `config_entry=entry` added, shim deleted | 545 passed | **545 passed** |

The 9 failures reproduced in the probe are **the reporter's exact 9 test names**. The fix is
therefore proven sufficient against the real 2026.9.4 target, not merely inferred.

## Design

### Part A — the compat fix (required; this is what closes #419)

1. **`coordinator.py`** — pass the entry to the base class:
   ```python
   super().__init__(
       hass,
       LOGGER,
       config_entry=entry,
       name=f"{entry.title}",
       update_interval=timedelta(seconds=2),
   )
   ```
   `config_entry: ConfigEntry | UndefinedType | None` has existed since at least HA
   **2024.12.0** (verified against the 2024.12.0 and 2025.4.0 tags), so this is backward
   compatible to our `hacs.json` floor. Passing it also means HA skips the legacy branch
   entirely, so `frame.report_usage` is never reached and the `RuntimeError` cannot occur.

2. **`tests/test_coordinator.py`** — delete the ContextVar workaround, which exists only to
   satisfy the legacy path we are removing:
   - `_make_coordinator` collapses to building the coordinator directly (keep
     `_ensure_entry_shim`, which supplies `async_on_unload` / `pref_disable_polling` — those
     are still touched by the base class).
   - Remove the `current_entry as _current_entry_var` import and the
     `set`/`reset` token dance.
   - Rewrite `_make_coordinator`'s docstring, which currently *documents* reliance on the
     ContextVar and would otherwise become a lie.

   Net: production code gains a correct explicit dependency; test code loses a hack. Deleting
   the shim is the part that proves the fix is real — leaving it in place would let the tests
   pass for the wrong reason.

### Part B — keep 2026 breakage visible (recommended)

Part A fixes today; it does not stop the *next* HA-2026 breakage from arriving as another
issue from a downstream packager. Add a **second CI leg** to `.github/workflows/test.yml`
that runs the suite against the **newest** HA on Python 3.14, while the primary dev pin stays
`2025.4.0`:

- primary job: `.python-version` (3.13) + `uv sync` (dev pin 2025.4.0) — unchanged
- forward-compat job: Python `3.14`, `uv pip install homeassistant` (latest), then `pytest`

This buys early warning at the cost of one workflow job, and deliberately **does not**
migrate the project's Python floor.

**Verified prerequisite for Part B** — in a bare `pip install homeassistant` environment
(HA installs component requirements lazily from integration `manifest.json` at runtime),
three modules our import chain needs are missing once HA ≥ 2026.3, because they stopped being
HA *base* dependencies:

| Module | Needed by | Base dep in 2025.4.0? | Base dep in 2026.9.4? |
|---|---|---|---|
| `haffmpeg` (`ha-ffmpeg`) | `camera.py:9` | yes | **no** |
| `numpy` | `homeassistant.components.camera.img_util` | no | **no** |
| `turbojpeg` (`PyTurboJPEG`) | `homeassistant.components.camera.img_util` | no | **no** |

So the forward-compat job must install `ha-ffmpeg numpy PyTurboJPEG` explicitly. Production
is unaffected either way: `manifest.json` declares `dependencies: ["ffmpeg", ...]`, so a real
HA loads the `ffmpeg` integration and installs its requirements.

## Non-goals

- **Do not bump the dev pin to HA 2026.x** as part of this change. It forces Python
  `>=3.14.2` and a migration of `.python-version`, `requires-python`, ruff `target-version`,
  and CI. If we later want that, it is its own change; Part A stays a prerequisite either way.
- **Do not raise the `hacs.json` floor** (`2024.12.0`) — it is a minimum, and lowering user
  support is not needed to fix this.
- **Do not touch `gcode_proxy_url`, CC1 `proxy_enabled`, or anything in #414's plan** —
  unrelated, and `PROXY_HOST = "127.0.0.1"` in `const.py` is the CC1 HA-proxy mode, not this.

## Open decisions (deliberately left for implementation time)

1. **Part B in or out?** Part A alone closes #419. Part B is what stops recurrence.
   Recommend including it — it is one workflow job plus three packages to install.
2. **Dev pin: stay at 2025.4.0, or move to the newest HA that still supports Python 3.13
   (HA 2026.2.x)?** Staying is the smallest diff; 2026.2.x tests against 11 more months of HA
   churn without the Python migration. With Part B in place, staying is defensible because
   latest-HA coverage comes from the extra CI leg instead.

## Verification

```bash
make format && make lint && make test          # green on the repo's 3.13 / 2025.4.0 venv
grep -rn "current_entry" custom_components/    # no hits — the shim is gone
grep -n "config_entry=entry" custom_components/elegoo_printer/coordinator.py
```

Forward-compat proof (what nixpkgs does), using a throwaway 3.14 venv:

```bash
uv venv --python 3.14 /tmp/probe/.venv
uv pip install --python /tmp/probe/.venv/bin/python homeassistant pytest pytest-asyncio \
    aiomqtt loguru colorlog websockets websocket-client ha-ffmpeg numpy PyTurboJPEG
cd <repo> && /tmp/probe/.venv/bin/python -m pytest custom_components -q   # expect 545 passed
```

Then reply on #419 with the 2026.9.4 result and close.
