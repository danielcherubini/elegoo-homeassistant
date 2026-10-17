# Elegoo Printer Integration

Home Assistant custom component for Elegoo 3D printers (SDCP V1/V3/CC2).

## Language

### Translations

The 18 files in `custom_components/elegoo_printer/translations/` are one JSON file per locale, all mirroring the structure of `en.json`. `en.json` is the base: HA falls back to it when a key is absent from a locale file.

**Missing from base** (a hard bug):
A key referenced in code but absent from `en.json` itself. Because the base is the fallback, no language resolves it — the raw key (e.g. `manual_ip_no_valid_ip`) is shown verbatim to every user. _Avoid_: "missing translation" (ambiguous — could mean the milder case below).

**Missing from locale** (degraded, not broken):
A key present in `en.json` but absent from a non-English locale file. That locale's users see the English text via HA's fallback; English users are unaffected. _Avoid_: "missing translation", "broken translation".

**Translation parity**:
All locale files carry the same *key set* as `en.json` (bidirectionally: no locale lacks a key, no locale has a key `en.json` lacks). Parity is about key sets only — values are expected to differ per language, and the tests do not check translated content.
