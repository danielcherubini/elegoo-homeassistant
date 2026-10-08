---
status: committed
done-when: >-
  v2.14.1 is released with: en.json containing manual_ip_no_valid_ip; all 17
  non-English locale files carrying the same key set as en.json;
  test_translations.py committed, green, and mutation-verified; the release
  notes stating the translations are machine-generated and unreviewed.
---

# Translation Parity Plan

**Goal:** Fix the raw-translation-key bug (`manual_ip_no_valid_ip`) and fill the 20 keys missing from all 17 non-English locale files, with a committed parity test that makes both bug classes structurally impossible to recur. Ship as 2.14.1.

**Architecture:** Three independent concerns: (1) a static-asset test that compares key sets across the 18 JSON translation files and against code references in `config_flow.py`; (2) the one-key English fix in `en.json`; (3) 357 machine-translated strings inserted into the 17 locale files by a script, preserving JSON formatting. No runtime behavior changes — translation JSON is read by HA at runtime, nothing in Python changes except the new test.

**Tech Stack:** Python 3.13, pytest (auto-async mode, no HA fixture needed for this test), ruff, JSON (2-space indent, raw UTF-8, trailing newline).

**Background (self-contained — the executing agent has no prior context):**
`custom_components/elegoo_printer/translations/` holds 18 JSON files, one per locale. `en.json` is the base: HA falls back to it when a key is absent from a locale file. A key referenced in code but absent from `en.json` is a **hard bug** — the raw key text is shown to every user (this is `manual_ip_no_valid_ip`, referenced at 3 sites in `config_flow.py`). A key present in `en.json` but absent from a locale file is **degraded** — that locale falls back to English. Verified facts (re-verify by running the commands in Task 1 step 1 if in doubt): all 17 non-English locales are missing exactly the same 20 keys; no locale has any key `en.json` lacks; `en.json` has 159 leaf keys; none of the 20 missing keys contains a placeholder; all 18 files use 2-space indent, raw UTF-8 (no `\u` escapes), and a trailing newline.

**The 21 key paths in scope** (20 present in `en`/missing from locales + the 1 bug fix):

| key path | English value |
|---|---|
| `config.error.manual_ip_no_valid_ip` | `Please enter a valid IP address.` *(new — add to en too)* |
| `config.error.cc2_proxy_unreachable` | `Could not connect through the proxy. Check that it is running and reachable, and that it forwards the printer's MQTT (1883), web (80) and camera (8080) ports` |
| `config.error.cc2_serial_required` | `The serial number is required` |
| `config.error.mqtt_external_port_invalid` | `Invalid port. Enter a whole number between 1 and 65535.` |
| `config.step.manual_ip.data.proxy_host` | `Proxy Host (optional)` |
| `config.step.manual_ip.data_description.proxy_host` | `Host of a forward proxy to connect through (IP or hostname, e.g. the elegoo-printer-proxy). Leave blank to connect directly to the printer. When set, discovery is skipped and the integration routes MQTT control, g-code uploads and the camera through the proxy on the printer's usual ports.` |
| `config.step.cc2_serial_input.title` | `Enter Serial Number` |
| `config.step.cc2_serial_input.description` | `The serial number could not be read through the proxy.\n\nEnter it manually — it is shown on the label under the printer, and on the printer itself at Settings → Network.` |
| `config.step.cc2_serial_input.data.serial` | `Serial Number` |
| `options.step.cc2_options.data.proxy_host` | `Proxy Host (optional)` |
| `options.step.cc2_options.data_description.proxy_host` | `Host of a forward proxy to connect through (IP or hostname). Leave blank to connect directly to the printer. When set, MQTT control, g-code uploads and the camera are routed through the proxy.` |
| `options.step.mqtt_options.data.external_ip` | `External Address (optional, for advanced network setups)` |
| `options.step.mqtt_options.data.mqtt_external_host` | `External MQTT Broker Host (optional)` |
| `options.step.mqtt_options.data.mqtt_external_port` | `External MQTT Broker Port (optional)` |
| `options.step.mqtt_options.data_description.external_ip` | `Override auto-detected address with an IP address or hostname. Leave blank for automatic detection. Use for Kubernetes/Docker setups or when behind a reverse proxy. Ports 3030 and 3031 will be appended automatically.` |
| `options.step.mqtt_options.data_description.mqtt_external_host` | `Point at an existing MQTT broker (e.g. Mosquitto) instead of starting the embedded one. Leave blank to use the built-in broker.` |
| `options.step.mqtt_options.data_description.mqtt_external_port` | `Port for the external MQTT broker specified above. Defaults to 1883 if left blank.` |
| `entity.select.print_file.name` | `Print file` |
| `entity.select.print_tray.name` | `Print tray` |
| `entity.button.refresh_file_list.name` | `Refresh file list` |
| `entity.button.print_selected_file.name` | `Print selected file` |

---

### Task 1: Red — the parity test

**Context:** This task adds `test_translations.py` and proves it fails for the *right* reasons before any fix exists. Two bug classes are guarded: (A) a code-referenced error key missing from `en.json` — the `manual_ip_no_valid_ip` bug; (B) a key missing from (or extra in) a locale file relative to `en.json`. The test is a static-asset check: it reads JSON files from disk. No HA runtime fixture, no `hass` object, no network. It must be a plain `pytest` test that runs under the project's `asyncio_mode = "auto"` without error (plain sync functions are fine).

**Files:**
- Create: `custom_components/elegoo_printer/tests/test_translations.py`

**What to implement:**

One module, three test functions, plus two helpers.

```python
"""
Parity checks for the translation files.

`en.json` is the base: HA falls back to it when a key is absent from a
locale file. Two failure classes are guarded:
- a base error key referenced in code but missing from `en.json` (the raw
  key is then shown to every user, in every language);
- a key missing from (or extra in) a non-English locale relative to
  `en.json` (missing → English fallback; extra → shown nowhere).

Known limitation of check A: base keys set via a parenthesized/ternary
expression (e.g. ``"base": ("a" if cond else "b")`` — see
``cc2_proxy_unreachable``/``cc2_authentication_failed`` in config_flow.py)
are not matched by the literal-form regex. Both keys exist in `en.json`
today; if that form starts carrying keys not in `en.json`, extend the
regex.
"""

import json
import re
from pathlib import Path

TRANSLATIONS = Path(__file__).parent.parent / "translations"
CONFIG_FLOW = Path(__file__).parent.parent / "config_flow.py"


def _leaf_keys(data, prefix=""):
    """Return the set of dot-separated leaf key paths in a nested dict."""
    out = set()
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out |= _leaf_keys(value, path)
        else:
            out.add(path)
    return out


def _referenced_base_error_keys():
    """
    Return the base error keys referenced as string literals in config_flow.py.

    Covers both literal forms used in the codebase: ``"base": "x"`` and
    ``_errors["base"] = "x"``. See the module docstring for the known
    parenthesized-form limitation.
    """
    code = CONFIG_FLOW.read_text()
    return set(re.findall(r'base["\']?\]?\s*[:=]\s*["\']([a-z_0-9]+)["\']', code))


def test_error_keys_referenced_in_code_exist_in_en():
    """Assert every code-referenced base error key exists in en.json (Bug A)."""
    en = json.loads((TRANSLATIONS / "en.json").read_text())
    en_errors = set(en["config"]["error"])
    missing = _referenced_base_error_keys() - en_errors
    assert not missing, (
        f"error key(s) referenced in config_flow.py but missing from "
        f"en.json config.error (raw key shown to every user): {sorted(missing)}"
    )


def test_every_en_key_exists_in_every_locale():
    """Assert every en.json leaf key exists in every other locale (Bug B)."""
    en = _leaf_keys(json.loads((TRANSLATIONS / "en.json").read_text()))
    problems = {}
    for locale_file in sorted(TRANSLATIONS.glob("*.json")):
        if locale_file.name == "en.json":
            continue
        keys = _leaf_keys(json.loads(locale_file.read_text()))
        missing = en - keys
        if missing:
            problems[locale_file.name] = sorted(missing)
    assert not problems, f"locales missing keys vs en.json: {problems}"


def test_no_locale_has_keys_missing_from_en():
    """Assert no locale carries a key that en.json lacks (inverse of Bug B)."""
    en = _leaf_keys(json.loads((TRANSLATIONS / "en.json").read_text()))
    problems = {}
    for locale_file in sorted(TRANSLATIONS.glob("*.json")):
        if locale_file.name == "en.json":
            continue
        keys = _leaf_keys(json.loads(locale_file.read_text()))
        extra = keys - en
        if extra:
            problems[locale_file.name] = sorted(extra)
    assert not problems, f"locales with keys absent from en.json: {problems}"
```

Notes for the executor:
- The repo's ruff config has `D212` ignored but `D213` active — multi-line docstrings (module, or a function docstring with a body) start with a bare `"""` line, summary on the second line; a docstring with a single line of content goes on one line (D200). Function docstrings must be imperative (`Return ...`, `Assert ...` — D401 is active). The code above already conforms — it lint-clean under the repo config (verified); do not reformat the docstrings.
- Do NOT add any HA fixture, `hass` mock, or `import homeassistant` — none is needed; keep it stdlib-only.
- Do NOT check translated *content* (no "is this valid German?"). Key sets only.

**Steps:**
- [ ] Create `custom_components/elegoo_printer/tests/test_translations.py` exactly as above.
- [ ] Run `make test` (or `uv run pytest custom_components/elegoo_printer/tests/test_translations.py -v`)
  - `test_error_keys_referenced_in_code_exist_in_en` must FAIL with `manual_ip_no_valid_ip` in the assertion message.
  - `test_every_en_key_exists_in_every_locale` must FAIL listing all 17 locales, each missing the 20 keys.
  - `test_no_locale_has_keys_missing_from_en` must PASS.
  - If `test_error_keys_referenced_in_code_exist_in_en` unexpectedly passes, stop: the regex is not matching the code — run `grep -c base custom_components/elegoo_printer/config_flow.py` (expect ≥11) and debug the regex in a Python REPL against the file contents before continuing.
- [ ] Run `make lint` — must pass (the new file is lint-clean under `select = ["ALL"]`; if ruff complains about e.g. docstring style, adjust minimally and re-verify the red state is unchanged).
- [ ] Run `make format` — must pass.
- [ ] Commit with message: `test(translations): parity checks for locale files (red)`

**Acceptance criteria:**
- [ ] `test_error_keys_referenced_in_code_exist_in_en` fails naming `manual_ip_no_valid_ip`.
- [ ] `test_every_en_key_exists_in_every_locale` fails naming all 17 locales.
- [ ] `test_no_locale_has_keys_missing_from_en` passes.
- [ ] All pre-existing tests still pass (the new file only adds failures of the 2 checks above).
- [ ] `make lint` and `make format` pass.

---

### Task 2: Fix `en.json`

**Context:** Add the one missing English base value. With Task 1's test in place, check A (code→en) turns green in this task; check B (en→locale) still fails — the 17 locales now each miss 21 keys. This is the minimal, independently-ship-able half of the user-facing fix: after this commit, every user sees a real message instead of the raw key, via the new en value (and, for non-English users, via HA's English fallback until Task 3 lands — same as the 20 other keys do today).

**Files:**
- Modify: `custom_components/elegoo_printer/translations/en.json`

**What to implement:**
Add exactly one key to the `config.error` object, **appended as the last entry of that object** (the object's existing entries are not alphabetically ordered; appending keeps the diff to 1 line and touches no other key):

```json
"manual_ip_no_valid_ip": "Please enter a valid IP address."
```

Do not re-order, re-indent, or re-serialize the file — edit the file in place (the surrounding style is 2-space indent; the new line matches its siblings: `      "manual_ip_no_valid_ip": "Please enter a valid IP address."` at the closing of `config.error`, i.e. indent 6 spaces, comma on the preceding last entry). If you use a script, it must preserve key order (Python dicts preserve insertion order; `json.dump(..., indent=2, ensure_ascii=False)` + trailing newline reproduces the file style exactly — verified: all 18 files use 2-space indent, raw UTF-8, trailing newline).

**Steps:**
- [ ] Add the key to `custom_components/elegoo_printer/translations/en.json` as described.
- [ ] Run `uv run pytest custom_components/elegoo_printer/tests/test_translations.py -v`
  - `test_error_keys_referenced_in_code_exist_in_en` must now PASS.
  - `test_every_en_key_exists_in_every_locale` must still FAIL (17 locales × 21 missing keys).
  - `test_no_locale_has_keys_missing_from_en` must still PASS.
- [ ] Run `make format`, `make lint` — must pass.
- [ ] Commit with message: `fix(translations): add manual_ip_no_valid_ip to the en base`

**Acceptance criteria:**
- [ ] `git diff` for this commit: exactly one added key line in `en.json` plus one comma modification on the preceding `cc2_proxy_unreachable` line (2 insertions / 1 deletion).
- [ ] Check A passes; check B fails; check C passes.
- [ ] Full `make test` shows only the one failing translation test (check B).

---

### Task 3: Generate and insert the 357 locale translations

**Context:** Fill the 21-key gap in all 17 non-English locales. The strings are machine-translated (by you, the executing agent — one language batch at a time so a bad batch is isolated). Provenance is a hard requirement: these translations are machine-generated and structurally validated, **not reviewed by native speakers**; the CHANGELOG (Task 4) and PR (Task 5) must say so.

**Files:**
- Modify: `custom_components/elegoo_printer/translations/{ar,cs,da,de,el,es,fr,it,ja,ko,nl,pl,pt,ru,sk,th,zh-Hans}.json` (17 files)

**What to implement:**

1. **Build the translation table.** For each of the 17 locales, translate the 21 English values from the plan's key table. Preservation rules (violating any of these is a defect):
   - Port numbers stay verbatim: `1883`, `80`, `8080`, `3030`, `3031`, `1 and 65535`.
   - Product/tech names stay verbatim: `elegoo-printer-proxy`, `Mosquitto`, `Kubernetes/Docker`.
   - The `→` glyph and the `Settings → Network` navigation pattern stay (localized words around it, but the arrow and the pattern structure are HA convention).
   - The `\n\n` line break inside `config.step.cc2_serial_input.description` stays (translate both segments, keep the blank-line separator).
   - No placeholders exist in any of the 21 strings — do not introduce any (`{...}` would be a defect).
   - Keep tone consistent with each file's existing translations (read ~5 existing strings from the file first per language to match register — e.g. formal `Sie` in `de.json`).
   - Short labels (`Print file`, `Serial Number`, `Proxy Host (optional)`): a few may legitimately equal the English; that is allowed and flagged (not failed) by the validator.
   - `ar.json`, `zh-Hans.json`, `ja.json`: raw UTF-8, no `\u` escapes (all 18 files are written this way today).

2. **Insert via script** (not by hand — 17×21 is error-prone). For each locale, load the JSON (key order preserved by Python's ordered dicts), set each of the 21 key paths **creating any missing intermediate dicts via `dict.setdefault`** — verified needed: the 4 `config.error` keys appended to the end of `config.error`; 7 structural keys under parents that already exist (`manual_ip.data`, `manual_ip.data_description`, `cc2_options.data`, `cc2_options.data_description`, `mqtt_options.data`); and 10 keys under parents that must be created: `config.step.cc2_serial_input` + its inner `data`, `options.step.mqtt_options.data_description`, `entity.select`, `entity.button` (neither exists in any locale — every locale's `entity` contains only `sensor`), and the 4 per-entity objects. Appending the new objects at the end of their parents reproduces `en.json`'s key order exactly (verified: `cc2_serial_input` is last in en's `config.step`, `data_description` last in en's `mqtt_options`, `select`/`button` last in en's `entity`). Write back with `json.dump(data, f, indent=2, ensure_ascii=False)` + one trailing newline. This reproduces the verified file style exactly.

3. **Validate** with the checks below, in this order, before considering the task done.

**Validation (all must be clean before commit):**
1. `uv run pytest custom_components/elegoo_printer/tests/test_translations.py -v` — all three tests PASS.
2. Automated structural check (run as a one-off `python3 -c` or scratch script; do not commit it):
   - All 18 files parse as JSON.
   - No empty or whitespace-only values among the 21 new per-locale values.
   - Flag (print, do not fail) any new value that is byte-identical to its English source — manually confirm each flagged one is a legitimate match (short label) and not an untranslated copy.
   - In each locale's `config.error.cc2_proxy_unreachable` and both `data_description.proxy_host` strings, the port numbers `1883`, `80`, `8080` and the name `elegoo-printer-proxy` (where present in the English source) are present verbatim.
   - `git diff --stat` shows exactly 17 changed files, each with **~45 insertions / ~6 deletions** (21 new leaf keys plus brace lines for the new nested objects; 6 pre-existing dicts that gain a child each show one comma-modified line). If a file's diff deviates substantially from that, inspect it.
3. Manual spot-review of the 4 `config.error` keys in `de`, `fr`, `ja`, `zh-Hans`, `ar` for obvious sense errors (e.g. a negation inverted, a wrong port, broken sentence). Record in the commit message body that the spot-review was done.

**Steps:**
- [ ] Translate one language batch at a time (17 batches), inserting via the script.
- [ ] Run validation 1 (tests green) after every 4–5 batches, not just at the end.
- [ ] Run validation 2 (structural) — all checks clean.
- [ ] Run validation 3 (spot-review) — done, recorded.
- [ ] Run `make format`, `make lint`, full `make test` — all pass (612+ tests, no new failures).
- [ ] Commit with message: `feat(translations): fill 21 missing keys in all 17 locales` with a body line: `Spot-reviewed the error keys in de/fr/ja/zh-Hans/ar. Machine-generated, not native-reviewed.`

**Acceptance criteria:**
- [ ] All three parity tests pass.
- [ ] `git diff --stat` (Task 3 commit) touches exactly the 17 locale files.
- [ ] No new value is an untranslated English copy (flagged ones manually confirmed).
- [ ] Ports/product names/glyphs preserved in the 2 long strings per locale.
- [ ] `make test` fully green.

---

### Task 4: Mutation-verify + full gate + CHANGELOG

**Context:** Prove the test actually guards what it claims (a green test that stays green when a key is deleted proves nothing), run the repo's full quality gate, and record the change in the CHANGELOG's Unreleased section.

**Files:**
- Modify: `custom_components/elegoo_printer/translations/de.json` (temporarily, for the mutation)
- Modify: `CHANGELOG.md`

**What to implement:**

1. **Mutation A (check B guard):** remove the key `"connection"` from `de.json` `config.error` (temporarily) → run `uv run pytest custom_components/elegoo_printer/tests/test_translations.py -v` → `test_every_en_key_exists_in_every_locale` must FAIL naming `de.json`. Restore `de.json` → test green again.
2. **Mutation B (check A coverage):** run `_referenced_base_error_keys()` (import it from the test module in a scratch Python session) and assert its output **equals the independently-established list of the 11 base keys** referenced in `config_flow.py` today: `connection`, `cc2_access_code_required`, `cc2_serial_required`, `init_no_printer_found`, `invalid_printer_selection`, `manual_ip_no_valid_ip`, `manual_options_no_printer_found`, `no_printer_found`, `no_printer_selected_or_ip_provided`, `unknown`, `validation_no_printer_found`. (Check A's own red was already proven in Task 1; this proves the extractor sees exactly the keys a human reads off the file — not a regex-vs-grep tautology.)
3. **CHANGELOG:** under the top `## [Unreleased]` section (bracketed header — match the file exactly), add (the Unreleased section already contains `### Fixed` and `### Added` subsections with prior entries, including 2.14.0-shipped content — merge into the existing subsections, do not create duplicate headers):

   ```md
   ### Fixed
   - The "invalid IP" error during manual IP entry now shows a proper message
     instead of a raw translation key (the key was missing from the English base
     and every locale).

   ### Added
   - Filled in 21 previously-untranslated UI strings (proxy host, serial entry,
     MQTT options, file-select entities) across all 17 non-English locales.
     Machine-translated and structurally validated; not yet reviewed by native
     speakers — corrections welcome (PR to the `translations/` directory).
   - Translation parity tests: every code-referenced error key must exist in
     `en.json`, and all locale files must carry the same key set as `en.json`.
   ```

   (If `## [Unreleased]` already has `### Fixed`/`### Added` subsections — it does — merge into them rather than duplicating the headers.)

**Steps:**
- [ ] Run mutation A (red → restore → green).
- [ ] Run mutation B (regex vs grep — sets equal).
- [ ] Update `CHANGELOG.md` as above.
- [ ] Run `make format`, `make lint`, full `make test` — all pass.
- [ ] Commit with message: `chore(translations): mutation-verify gate + changelog` (include the CHANGELOG; `de.json` must be byte-identical to before — `git diff` must show only `CHANGELOG.md` in this commit).

**Acceptance criteria:**
- [ ] Mutation A demonstrated red, then green after restore; `de.json` unchanged in the commit.
- [ ] Mutation B: extractor output == the 11-key list.
- [ ] CHANGELOG `[Unreleased]` section updated; the "Added" entry contains the native-review caveat.
- [ ] Full gate green: `make format`, `make lint`, `make test`.

---

### Task 5: Push, PR, merge

**Context:** Ship the change via the repo's PR flow. Squash merge only (merge commits are disabled); squash subjects carry the `(#NNN)` suffix convention; the branch name uses the `feature/` prefix (this is an internal change, no ticket number — `feature/translations-parity`).

**Files:** (none — process task)

**What to implement:**

1. Push the branch (all of Tasks 1–4's commits).
2. Open the PR (draft first). The PR description **must** contain the provenance caveat: the locale fills are machine-translated and structurally validated, not native-reviewed, with a link to the `translations/` directory for corrections; and that the `en.json` fix is the user-facing part (raw-key bug).
3. Ready the PR. Wait for all CI checks to pass (the repo's `Lint`, `Test`, `Validate` — plus the newest-HA leg; poll `gh pr checks`).
4. Squash-merge with subject `fix(translations): manual_ip error key + 17-locale parity (#423)` — use the actual issue/PR number if one is filed; otherwise `fix(translations): manual_ip error key + 17-locale parity`. Delete the branch on merge.

**Steps:**
- [ ] `git push -u origin feature/translations-parity`
- [ ] `gh pr create --draft ...` with the caveat in the body.
- [ ] `gh pr ready <n>`; poll `gh pr checks <n>` until all pass.
- [ ] `gh pr merge <n> --squash --delete-branch --subject "fix(translations): manual_ip error key + 17-locale parity"`.

**Acceptance criteria:**
- [ ] PR merged (squash), branch deleted, `main` advances by the merge commit.
- [ ] PR body contains the machine-translation caveat.
- [ ] All CI checks were green at merge.

---

### Task 6: Release 2.14.1

**Context:** The standard AGENTS.md release process. A direct push to `main` (including the version bump commit) relies on maintainer rule-bypass — this is the established pattern: every prior release bump commit (v2.12.1, v2.12.2, v2.13.0, v2.13.1) is a direct `main` commit, and `git push` prints `Bypassed rule violations for refs/heads/main`. **Never push the tag** — `release.yml` creates the tag and the release together; a pre-pushed tag makes the workflow skip release creation.

**Files:**
- Modify: `custom_components/elegoo_printer/manifest.json` (read the current `"version"` — it is `2.14.0` after the prior release — and bump the patch → `2.14.1`)
- Modify: `pyproject.toml` (`version = "2.14.0"` → `"2.14.1"`)
- `uv.lock` auto-updates via the first `make` target.

**What to implement (exact AGENTS.md sequence):**
1. **Pre-flight:** latest `Lint`/`Test`/`Validate` on `main` green (they will be — Task 5's CI); `git status` clean.
2. **Bump:** `manifest.json` + `pyproject.toml` to `2.14.1` (patch — a bug fix; the locale fills ride along).
3. **Validate:** `make format`, `make lint`, `make test` — all pass.
4. **Publish:** commit `chore: bump version to v2.14.1` (including the `uv.lock` change); push to `main` only — **no tag**.
5. **Watch:** `gh run list --workflow release.yml` — the run validates versions match, re-runs lint+tests, and creates the `v2.14.1` tag + GitHub Release.
6. **Curate notes** (`gh release edit v2.14.1 --notes-file ...`): a user-facing summary — Fixed: the invalid-IP message; Added: the locale fills (with the machine-translation / not-native-reviewed caveat and an invitation to correct) + the parity tests. No contributors to thank (self-contained change).
7. **Verify:** `git ls-remote --tags origin | grep v2.14.1`, `gh release view v2.14.1`, `git status` clean.

**Acceptance criteria (the plan's done-when):**
- [ ] `v2.14.1` tag on the remote, created by the workflow (not pushed).
- [ ] Release page exists with curated notes containing the machine-translation caveat.
- [ ] `en.json` contains `manual_ip_no_valid_ip`; all 17 locales carry the full key set (re-run the parity test on the merge commit).
- [ ] `test_translations.py` committed and green on `main`.
- [ ] Working tree clean.
