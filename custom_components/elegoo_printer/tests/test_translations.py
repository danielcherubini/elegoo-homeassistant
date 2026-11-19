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

import pytest

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


# Known false-friend label values, keyed by locale so an entry for one locale
# cannot fire on another.
#
# This is a REGRESSION GUARD for specific, documented mistranslations -- it is
# NOT a translation-quality check. It cannot tell a good translation from a bad
# one and does not try to; it only refuses the two values that were actually
# shipped, reviewed, and fixed, so they cannot silently come back on the next
# re-translation pass. Every other locale is unguarded because nobody has
# reviewed it: an unverified entry is a false positive waiting to happen, and
# this guard's whole value is that it never cries wolf. Add to it when a real
# mistranslation is found and confirmed, and name the incident so the next
# reader knows why the entry exists:
#
# - es: `Plata` means SILVER (or money) in Spanish, never a printer build
#   plate. Shipped as `Plata de impresión` / `Plata A` / `Plata B`; the plate is
#   a `Placa`. Fixed in 7b2ca0c.
# - el: `Πλάτα` is the PLANE TREE (from *platanus*). Shipped as
#   `Πλάτα Κατασκευής` / `Πλάτα A` / `Πλάτα B`; the plate is a `Πλάκα`.
#   Fixed in 7b2ca0c.
#
# Both patterns are word-boundary anchored on purpose: `"plata" in value` would
# flag `pt.json`'s `Plataforma de impressão`, which is correct Portuguese,
# because `\b` will not match inside `Plataforma` (the `f` is a word character).
_MISTRANSLATED_LABELS = {
    "es": (r"\bPlata\b",),
    # The alpha in the el pattern is deliberate -- it has to be the Greek word
    # as it was actually shipped, and a Latin look-alike would never match.
    "el": (r"\bΠλάτα\b",),  # noqa: RUF001
}


def _sensor_label_values(data):
    """
    Return every sensor label as ``(key_path, value)`` pairs.

    Covers ``entity.sensor.<key>.name`` and every string under
    ``entity.sensor.<key>.state`` -- the whole label surface of a sensor, not
    just the build plate block. A false friend that reaches one label of a
    locale can reach the others, and it can reach a sensor that did not exist
    when the blocklist was written.
    """
    out = []
    for sensor, block in sorted(data.get("entity", {}).get("sensor", {}).items()):
        if not isinstance(block, dict):
            continue
        name = block.get("name")
        if isinstance(name, str):
            out.append((f"entity.sensor.{sensor}.name", name))
        states = block.get("state")
        if isinstance(states, dict):
            for state_key, value in sorted(states.items()):
                if isinstance(value, str):
                    out.append((f"entity.sensor.{sensor}.state.{state_key}", value))
    return out


@pytest.mark.parametrize(
    "locale_file", sorted(TRANSLATIONS.glob("*.json")), ids=lambda p: p.stem
)
def test_no_known_mistranslated_sensor_label(locale_file):
    """
    A label that matches a blocklisted false friend fails the build (#457).

    One case per locale so a failure names the locale. The rest of this file
    checks KEY parity only, and ``test_build_plate_sensor.py`` checks only that
    ``en.json`` carries a label per option -- neither looks at translated
    CONTENT, so a machine re-translation could reintroduce `Plata de impresión`
    and CI would stay green. Locales with no blocklist entry pass here: they are
    unchecked, not certified.
    """
    locale = locale_file.stem
    patterns = [re.compile(p) for p in _MISTRANSLATED_LABELS.get(locale, ())]
    if not patterns:
        return

    data = json.loads(locale_file.read_text())
    offenders = [
        f"{path} = {value!r}"
        for path, value in _sensor_label_values(data)
        for pattern in patterns
        if pattern.search(value)
    ]
    assert not offenders, (
        f"{locale_file.name}: sensor label(s) matched a known false-friend "
        f"translation (wrong word shipped, see _MISTRANSLATED_LABELS): "
        f"{'; '.join(offenders)}"
    )


def test_blocklist_does_not_flag_portuguese_plataforma():
    """
    The `es` pattern must not flag `pt.json`'s correct `Plataforma`.

    `Plata` is a legitimate substring of `Plataforma`, so a naive containment
    check reports a correct Portuguese label as a bug -- the exact false
    positive that would get this guard deleted. Pins both halves: the value this
    guard is protecting really is in the file (so the check is not vacuous if
    `pt` is ever re-translated), and the word boundary rejects it.
    """
    pt = json.loads((TRANSLATIONS / "pt.json").read_text())
    values = [value for _, value in _sensor_label_values(pt)]
    assert "Plataforma de impressão" in values, (
        "pt.json no longer labels the build plate 'Plataforma de impressão'; "
        "this test was pinning the boundary behaviour of that exact value"
    )

    es_patterns = [re.compile(p) for p in _MISTRANSLATED_LABELS["es"]]
    flagged = [
        f"{path} = {value!r}"
        for path, value in _sensor_label_values(pt)
        for pattern in es_patterns
        if pattern.search(value)
    ]
    assert not flagged, f"the es blocklist false-flagged pt.json: {flagged}"
