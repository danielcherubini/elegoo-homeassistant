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
