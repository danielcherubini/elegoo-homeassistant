---
status: accepted
date: 2026-11-19
supersedes:
superseded-by:
---

# Enum sensors report Unknown rather than passing through an unrecognised value

**Status:** accepted

## Context

Issue #450 asked for a sensor showing which side of the build plate a Centauri Carbon print is running on, read from the SDCP `PlatFormType` field. The reporter's proposed `value_fn` mapped unrecognised values through `str(value)`. That is incompatible with declaring `options`: Home Assistant raises `ValueError: Sensor ... provides state value '<v>', which is not in the list of options provided` for any state outside the list (confirmed in the pinned `2025.4.0` at `sensor/__init__.py:603-619`, unchanged through `2026.10.4`). Following the snippet would have produced an entity that errors on any value we did not already predict — the exact situation a fallback exists to survive.

Two related traps surfaced while building it:

`PrinterStatus` did `status.get("PlatFormType", 1)`. Under the 0→A / 1→B mapping, a printer that never sends the field reported `1` and rendered **"Plate B"** — a specific, confident, wrong answer rather than an absent one. An absent value has to be representable before it can be displayed honestly.

The field's type annotation is aspirational, not enforced. `sdcp/types.py` declares `PlatFormType: Any` and the model passes through whatever the wire sent, so the real domain is "`None`, or whatever the firmware sent". A string `"0"` must not silently become "Plate A", and must not raise.

## Decision

**A bounded option set with a declared `unknown` member, never a passthrough.** The sensor declares `options=["a", "b", "unknown"]` and every value we cannot map — `None`, an unrecognised int, a non-int wire value — resolves to the declared `"unknown"` option. `iometer` ships this same shape in HA core (`options=["battery","wired","unknown"]`), so it is an established pattern rather than a workaround.

The model's default changed from `1` to `None`, typed `int | None`, so absence is representable at the source instead of being papered over downstream. The field had exactly two references in the whole integration, so the blast radius was those two lines.

`value_fn` guards with `isinstance(..., int)` rather than trusting the annotation, because the annotation describes what we hope the wire sends.

**No `state_class`.** `DEVICE_CLASS_STATE_CLASSES[SensorDeviceClass.ENUM]` is the empty set, so any `state_class` on an enum sensor is invalid by construction. The repo's pre-existing enum sensors correctly declare none.

**The `unknown` label is ours to supply, in every locale.** Because the state value is the literal string `"unknown"` and not HA's `STATE_UNKNOWN` sentinel, core's `async_translate_state` short-circuits and provides no built-in label. What a user reads for Unknown comes from our translation files, in all 19 languages shipped.

**CC2 keeps the sensor and its owners get a permanently-Unknown entity until a capture lands.** CC2 is `PrinterType.FDM`, so it receives the FDM sensor set like every other FDM machine; excluding it would be a category error. Hiding it was rejected because "this printer type does not have this feature" is the wrong claim to make from an absence of evidence in *our mapper* rather than from the firmware. If a CC2 payload turns up carrying an equivalent field, populating it is additive and needs no decision here.

## Considered Options

Rejected: **`str(value)` passthrough** (the issue's suggestion) — raises on any unpredicted value, defeating the purpose of a fallback.

Rejected: **fabricating a default** — the previous `1` meant a silent printer displayed "Plate B". An invented reading is worse than no reading, because automations act on it.

Rejected: **omitting `options` and emitting free text** — would avoid the raise, but loses the enum picker in the HA UI and the ability to compare states in automations, for the sake of values we have never observed.

## Consequences

The 0→A / 1→B mapping is **unverified beyond one reporter on one model at firmware V1.4.49**. It is isolated in a single named constant, `_BUILD_PLATE_BY_PLATFORM_TYPE`, so a correction is a one-line change rather than a hunt through a lambda.

Machine-translated labels can be confidently wrong. Two shipped in this change and both were caught in review: Spanish `Plata` (silver/money, not a build plate) and Greek `Πλάτα` (plane tree). `test_translations.py` enforces key-set parity only and never reads translated content, so nothing prevented their return; a blocklist of documented false friends now guards the sensor labels, and it is meant to grow as new ones are found. Locales with no blocklist entry pass by absence of a rule, not by inspection.
