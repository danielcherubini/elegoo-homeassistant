"""
Parity checks for the translation files.

`en.json` is the base: HA falls back to it when a key is absent from a
locale file. Two failure classes are guarded:
- a base error key referenced in code but missing from `en.json` (the raw
  key is then shown to every user, in every language);
- an error key reachable from the OPTIONS steps but missing from
  `en.json`'s `options.error` section. Home Assistant resolves an
  OPTIONS-flow field error as `component.<domain>.options.error.<key>`
  with a raw-key fallback -- it does NOT fall back to `config.error` --
  so every key an options step can surface needs its own `options.error`
  entry (#171 review);
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


OPTIONS_CLASS = "class ElegooOptionsFlowHandler"


def _referenced_options_error_keys():
    """
    Return the error keys reachable from the OPTIONS flow.

    Scans only the ``ElegooOptionsFlowHandler`` class body (everything after
    the class statement -- the options flow cannot reach keys assigned in
    the config-flow class) for the forms the options steps use:

    - ``_errors[<anything>] = "literal"``, field-keyed as well as
      ``base``-keyed: HA looks up the *value* under ``options.error``
      regardless of which field carries it;
    - ``_errors[<field>] = <variable>`` where the key is handed back by a
      validation helper as ``return <expr>, "literal"`` (e.g. the
      ``proxy_error`` of ``_async_validate_gcode_proxy``). The helper-return
      form is only scanned when at least one variable-keyed assignment
      exists, so unrelated single-line tuple returns cannot leak in.
    """
    code = CONFIG_FLOW.read_text()
    options_code = code[code.index(OPTIONS_CLASS) :]
    keys = set(
        re.findall(r'_errors\[[^\]]+\]\s*=\s*["\']([a-z_0-9]+)["\']', options_code)
    )
    if re.search(
        r"_errors\[[^\]]+\]\s*=\s*[a-z_][a-z_0-9]*\s*$", options_code, re.MULTILINE
    ):
        keys |= set(re.findall(r'return\s+\w+,\s*["\']([a-z_0-9]+)["\']', options_code))
    return keys


def test_error_keys_referenced_in_code_exist_in_en():
    """Assert every code-referenced base error key exists in en.json (Bug A)."""
    en = json.loads((TRANSLATIONS / "en.json").read_text())
    en_errors = set(en["config"]["error"])
    missing = _referenced_base_error_keys() - en_errors
    assert not missing, (
        f"error key(s) referenced in config_flow.py but missing from "
        f"en.json config.error (raw key shown to every user): {sorted(missing)}"
    )


def test_options_error_keys_referenced_in_code_exist_in_en():
    """
    Assert every options-reachable error key exists in options.error (#171).

    HA resolves OPTIONS-flow errors as
    ``component.<domain>.options.error.<key>`` with a raw-key fallback and
    never falls back to ``config.error``, so keys set on options paths need
    an ``options.error`` section -- matching only ``config.error`` (the
    historical Bug A check) would miss every field-keyed options error.
    """
    en = json.loads((TRANSLATIONS / "en.json").read_text())
    options_errors = set(en.get("options", {}).get("error", {}))
    missing = _referenced_options_error_keys() - options_errors
    assert not missing, (
        f"error key(s) reachable from config_flow.py options steps but "
        f"missing from en.json options.error (raw key shown to every "
        f"user): {sorted(missing)}"
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
