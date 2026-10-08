---
status: approved
done-when: >-
  v2.14.1 is released with: en.json containing manual_ip_no_valid_ip; all 17
  non-English locale files carrying the same key set as en.json;
  test_translations.py committed, green, and mutation-verified; the release
  notes stating the translations are machine-generated and unreviewed.
---

# Translation Parity: Fix the Raw-Key Bug + Fill the 20 Missing Locale Keys

## Background

`manual_ip_no_valid_ip` is referenced in `config_flow.py` (3 sites: the
WebSocket-discovery path, the CC2 options path, and the manual-IP re-entry
path) but is **absent from `en.json` and all 17 other locale files**. Because
`en.json` is the fallback base, no language resolves the key: any user who
enters a manual IP that sanitizes to nothing sees the raw key text in the UI
instead of a message. This is the "missing from base" class (see `CONTEXT.md`).

In the same set of files, **20 keys** present in `en.json` are absent from all
17 non-English locales (uniform gap, no divergence: `en` is a strict superset
of every locale). These fall back to English — degraded, not broken. The 20
are: 3 error keys added in v2.14.0 (`cc2_proxy_unreachable`,
`cc2_serial_required`, `mqtt_external_port_invalid`), 3 config-step keys
(`cc2_serial_input.*`), 2 step keys + 2 descriptions for `proxy_host`
(config `manual_ip` and `cc2_options`), 3 `mqtt_options` data keys + 3
descriptions, and 4 entity names (`print_file`, `print_tray`,
`refresh_file_list`, `print_selected_file`). None of the 20 contains
placeholders; every string is self-contained.

## Scope

1. **Bug fix**: add `manual_ip_no_valid_ip` to `en.json` `config.error` and to
   all 17 locale files (translated).
2. **Locale fill**: translate the 20 missing keys into all 17 languages —
   357 translations, plus the 1 English value, 358 strings total.
3. **Regression guard**: a committed test that makes both bug classes
   structurally impossible to recur.

## Section 1 — The bug fix

Add to `en.json` `config.error`:

```json
"manual_ip_no_valid_ip": "Please enter a valid IP address."
```

Wording follows the neighboring keys' house style (sentence case, short). It
matches the code intent: the key fires when manual IP input sanitizes to
nothing — an input problem, not a connection failure. The same key goes into
all 17 locale files, translated (Section 3), so every file is self-contained
rather than relying on fallback.

## Section 2 — The regression guard

New `custom_components/elegoo_printer/tests/test_translations.py`. Static file
reads (JSON parsing), no HA runtime fixture. Three checks, one test function
each so a failure names the broken invariant:

- **Check A — code→en parity.** Every base error key referenced in
  `config_flow.py` via the two literal forms (`"base": "x"` and
  `_errors["base"] = "x"`) must exist in `en.json` `config.error`. This is the
  check that would have flagged `manual_ip_no_valid_ip` at the commit that
  added it to code.
- **Check B — en→locale parity.** Every leaf key path (recursive walk,
  dot-separated) in `en.json` must exist in every other locale file.
- **Check C — locale→en parity.** No locale file may contain a leaf key that
  `en.json` lacks. (Feasible to pass immediately: no locale has extra keys
  today.)

Deliberately **not** checked: translation content, value length, or whether a
non-error key must be translated (English fallback is the correct HA behavior
for labels; the locale fill is a quality decision, not a requirement the test
enforces). Parity is about key sets only.

## Section 3 — Generating the 357 translations

- Produced by the agent (machine translation), one language batch at a time so
  a bad batch is isolated.
- Preserved verbatim in all translations: port numbers (1883, 80, 8080,
  3030/3031), product and technology names (`elegoo-printer-proxy`, Mosquitto,
  Kubernetes/Docker), UI glyphs (`→`), and the `Settings → Network`
  navigation pattern.
- **Automated validation before anything is committed:**
  1. All 18 files still parse as JSON; key set identical to `en.json` (the
     Section 2 test is green).
  2. No empty values.
  3. No value identical to its English source (a full match = almost certainly
     untranslated) — flagged for manual re-check, not auto-failed (a few short
     labels could legitimately match).
  4. Port numbers and `elegoo-printer-proxy` preserved in the two long proxy
     descriptions per language.
  5. No placeholders introduced.
- **Manual validation:** spot-review the 4 error keys across a representative
  spread (de, fr, ja, zh-Hans, ar) for obvious sense errors.
- **Provenance caveat (required, in CHANGELOG and PR description):** the
  translations are machine-generated and structurally validated, not yet
  reviewed by native speakers; corrections welcome via PR to the
  `translations/` directory.

## Section 4 — Shipping

- **Version:** 2.14.1 (patch — a user-facing bug fix; the locale fills ride
  along). No migration.
- **Sequence (TDD):**
  1. Branch `feature/translations-parity` from `main` (`1fd7715`).
  2. **Red:** add `test_translations.py`, run, confirm it fails (the en gap +
     357 locale gaps).
  3. Add `manual_ip_no_valid_ip` to `en.json`.
  4. Generate + validate the 357 locale translations (Section 3 checks).
  5. **Green + mutation-verify:** remove a key from one locale → red →
     restore → green. Then `make format`, `make lint`, full `make test`.
  6. Push, open PR, draft → ready → CI → squash-merge.
  7. Release 2.14.1 via the standard AGENTS.md process (bump manifest.json +
     pyproject.toml, push to `main`, workflow tags + publishes, curated notes).
- **CHANGELOG (Unreleased → 2.14.1):**
  ```
  ### Fixed
  - The "invalid IP" error during manual IP entry now shows a proper message
    instead of a raw translation key (the key was missing from the English
    base and every locale).

  ### Added
  - Filled in 20 previously-untranslated UI strings (proxy host, serial entry,
    MQTT options, file-select entities) across all 17 non-English locales.
    Machine-translated and structurally validated; not yet reviewed by native
    speakers — corrections welcome (PR to the `translations/` directory).
  - Translation parity tests: every code-referenced error key must exist in
    `en.json`, and all locale files must carry the same key set as `en.json`.
  ```

## Out of scope

- Reviewing the 357 translations with native speakers (the caveat invites this
  as a follow-up).
- The CC1 `ElegooPrinterServer` "bypass connection limits" README claim
  (separate investigation).
- Any i18n tooling (Crowdin etc.) — a one-off committed test is the guard.
