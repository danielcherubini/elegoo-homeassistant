"""
Parity checks for the translation files.

`en.json` is the base: HA falls back to it when a key is absent from a
locale file. Four failure classes are guarded:
- a base error key referenced in code but missing from `en.json` (the raw
  key is then shown to every user, in every language);
- an error key reachable from the OPTIONS steps but missing from
  `en.json`'s `options.error` section. Home Assistant resolves an
  OPTIONS-flow field error as `component.<domain>.options.error.<key>`
  with a raw-key fallback -- it does NOT fall back to `config.error` --
  so every key an options step can surface needs its own `options.error`
  entry (#171 review);
- a key missing from (or extra in) a non-English locale relative to
  `en.json` (missing → English fallback; extra → shown nowhere);
- `options.error` wording that diverged from the same locale's
  `config.error` for a shared key -- the copy relationship that #171
  deliberately introduced.

The two `_referenced_*_error_keys` scanners are themselves pinned (an empty
scan would satisfy every "found keys exist" check above), so a scanner that
stops matching the code shapes fails loudly instead of silently passing.

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
# A top-level statement that ends the options-flow class body.
_END_OF_CLASS_BODY = re.compile(r"^(?:class |def |async def )", re.MULTILINE)


def _options_flow_source(code: str) -> str:
    """
    Return only the source of the ``ElegooOptionsFlowHandler`` class body.

    The slice starts at the class statement and stops at the next TOP-LEVEL
    (column 0) ``class`` / ``def`` / ``async def``, so keys reachable from the
    config flow -- or from anything defined after the options flow -- cannot
    leak in. Without the closing boundary the claim rests on the options flow
    being the last top-level statement in ``config_flow.py``, which is true
    today but is not something this file controls.
    """
    start = code.index(OPTIONS_CLASS)
    # Skip the class statement itself: its first line is the only one a
    # column-0 scan could mistake for a boundary.
    tail = code[code.index("\n", start) + 1 :]
    boundary = _END_OF_CLASS_BODY.search(tail)
    return tail[: boundary.start()] if boundary else tail


def _referenced_options_error_keys(code: str | None = None):
    """
    Return the error keys reachable from the OPTIONS flow.

    Scans only the ``ElegooOptionsFlowHandler`` class body — see
    ``_options_flow_source`` for how the slice is bounded — because the options
    flow cannot reach keys assigned in the config-flow class. Forms matched:

    - ``_errors[<anything>] = "literal"``, field-keyed as well as
      ``base``-keyed: HA looks up the *value* under ``options.error``
      regardless of which field carries it;
    - ``_errors[<field>] = <variable>`` where the key is handed back by a
      validation helper as ``return <expr>, "literal"`` (e.g. the
      ``proxy_error`` of ``_async_validate_gcode_proxy``). The helper-return
      form is only scanned when at least one variable-keyed assignment
      exists, so unrelated single-line tuple returns cannot leak in.

    ``code`` lets a test feed a synthetic ``config_flow.py`` source; the real
    file is read when it is omitted.
    """
    options_code = _options_flow_source(
        CONFIG_FLOW.read_text() if code is None else code
    )
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


def test_options_flow_scan_finds_every_known_options_error_key():
    """
    Pin the scanner's own output so it cannot silently degrade.

    Every other check here is "the keys found must exist somewhere", which
    passes vacuously if the scanner stops matching anything -- an empty set is
    a subset of every set. These are the keys the options steps set today; a new
    options error means adding it here, which is the point: the change has to be
    noticed rather than quietly unchecked.
    """
    assert _referenced_options_error_keys() == {
        "connection",
        "gcode_proxy_invalid",
        "gcode_proxy_unreachable",
        "init_no_printer_found",
        "manual_ip_no_valid_ip",
        "mqtt_external_port_invalid",
        "unknown",
    }


def test_options_flow_scan_stops_at_the_next_top_level_definition():
    """
    A key defined AFTER the options flow must not be attributed to it.

    The scanner claims to read only the options class body. With an open-ended
    slice that holds only while ``ElegooOptionsFlowHandler`` is the last
    top-level statement in ``config_flow.py`` -- true today, but not a property
    this test file controls. The synthetic source below is the arrangement that
    breaks the claim, so what gets pinned is the closing boundary.
    """
    synthetic = "\n".join(
        [
            "class ElegooConfigFlowHandler:",
            "    async def async_step_user(self, user_input):",
            '        _errors["base"] = "config_only_key"',
            "",
            f"{OPTIONS_CLASS}:",
            "    async def async_step_init(self, user_input):",
            '        _errors["base"] = "options_key"',
            "",
            "class ElegooLaterFlowHandler:",
            "    async def async_step_anything(self, user_input):",
            '        _errors["base"] = "defined_after_options_key"',
            "",
            "async def module_level_helper():",
            '    _errors["base"] = "module_level_key"',
            "",
        ]
    )

    assert _referenced_options_error_keys(synthetic) == {"options_key"}

    # The boundary is doing real work: scanning the unbounded slice -- what the
    # scanner did before -- also picks up the trailing definitions.
    unbounded = synthetic[synthetic.index(OPTIONS_CLASS) :]
    leaked = set(
        re.findall(r'_errors\[[^\]]+\]\s*=\s*["\']([a-z_0-9]+)["\']', unbounded)
    )
    assert leaked == {"options_key", "defined_after_options_key", "module_level_key"}


def test_options_error_text_is_the_same_locale_config_error_text():
    """
    The ``options.error`` strings must stay verbatim copies of ``config.error``.

    HA has no ``config.error`` fallback for an OPTIONS flow (#171), so every
    error an options step can raise needs an entry in both namespaces. They are
    deliberately the same sentence per locale -- one error condition, one
    message, so a user is not told two different things about the same failure
    depending on which form they opened. If a key's wording ever must differ
    between the flows, narrow this check to that key rather than dropping it.
    """
    problems = {}
    for locale_file in sorted(TRANSLATIONS.glob("*.json")):
        data = json.loads(locale_file.read_text())
        config_errors = data.get("config", {}).get("error", {})
        for key, text in data.get("options", {}).get("error", {}).items():
            if key in config_errors and config_errors[key] != text:
                problems.setdefault(locale_file.name, []).append(key)
    assert not problems, (
        f"options.error wording diverged from the same locale's config.error "
        f"(same error, two messages): {problems}"
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
