---
status: approved
done-when: With "Printer is often switched off" ticked, a printer that is powered off at Home Assistant startup leaves the integration **loaded** (not "Failed setup, will retry"), every entity **unavailable** (not fabricated values), nothing above INFO in the log, and the entities recover on their own within one poll when the printer is powered on — without a restart or reload. Unticked, behaviour is byte-for-byte what it is today.
---

# Offline-tolerant setup for switched-off printers (#171)

## Problem

`async_setup_entry` raises `ConfigEntryNotReady` whenever the printer is unreachable (`__init__.py:501`), which HA renders as **"Failed setup, will retry"**. For a printer that is switched off most of the time that is a permanent error state (#171, comment [6088871351](https://github.com/danielcherubini/elegoo-homeassistant/issues/171#issuecomment-6088871351)).

The inconsistency the reporter named: powering the printer off **after** setup is a supported, self-healing state — entities go `unavailable`, the coordinator drops to a 30 s poll, and it recovers on power-on. The *same* state reached **during** setup is an error. Design decision recorded in [ADR 0002](../decisions/0002-offline-tolerant-setup.md).

## What we're building

One per-entry boolean, **off by default**, offered on all three transports' options forms:

> **Printer is often switched off** — Set up even when the printer can't be reached. All entities stay "Unavailable" until it is powered on, instead of the integration reporting a failed setup.

`allow_printer_offline` (const: `CONF_ALLOW_PRINTER_OFFLINE`).

### Behaviour

| Printer state at HA start | Option off (default) | Option on |
|---|---|---|
| Reachable | loads, polls | loads, polls — identical |
| Off | `ConfigEntryNotReady` → "Failed setup, will retry" | **loads**; every entity `unavailable`; recovers on power-on |
| Wrong IP / dead hardware | `ConfigEntryNotReady` | loads as "printer off" — accepted, and documented as such: it is the same signal as "off" |

## Design

### 1. Setup (`__init__.py`)

`async_setup_entry` branches on whether the api client *deliberately* came up offline — not on `is_connected`. Any path that isn't a considered offline exit keeps raising `ConfigEntryNotReady`, so an unanticipated failure still fails loudly.

When offline-tolerant:
- Set `entry.runtime_data` as today (all 10 platforms read `runtime_data.api.printer`, which is built from stored config, so entity composition works with no connection).
- Call `coordinator.async_start_offline()` **instead of** `async_config_entry_first_refresh()` — the latter raises `ConfigEntryNotReady` on a failed refresh and would undo the option.
- Forward platforms and register the update listener unchanged.

`async_create` returns a client in the offline case, keeping any embedded-broker / proxy reference it already acquired (see ADR consequences).

### 2. Coordinator (`coordinator.py`)

`async_start_offline()` — a `@callback`, no I/O: mark `online = False`, set the 30 s interval, and mark updates failed (`async_set_update_error`). Everything else — arming the retry timer via the first entity listener, reconnecting, recovering — is the existing polling machinery.

Two correctness fixes this depends on:
- `_async_update_data` refuses to poll while `api.is_connected` is false. Without it, MQTT and CC2 return **cached** data without raising, so the poll "succeeds" and entities render `0 °C` / `idle` as live truth — strictly worse than today.
- The Canvas (`:99`) and file-list (`:161`) side-checks treat a dropped request as "skip this check" rather than "the device is gone". `ElegooPrinterNotConnectedError` is a sibling of `ElegooPrinterConnectionError`, so both clauses currently miss it and fail the whole update.

### 3. Entities

Availability comes for free: `CoordinatorEntity.available == coordinator.last_update_success`, and HA renders `unavailable` before touching state or attributes, so no platform needs an availability change. One real bug surfaces: the CC2 print-file / print-tray selects write to `coordinator.data` in `async_added_to_hass`, and `data` is `None` until the printer is first reached — that raises and silently drops the entity.

### 4. Options flow (`config_flow.py`)

The flag appears on `websocket_options`, `mqtt_options` and `cc2_options`. Only the WebSocket step validates by connecting to the printer, and that gate makes the option unusable for exactly its target user — they cannot enable it while the printer is off. When the flag is ticked, a failed connection test saves (preserving stored printer identity) instead of re-showing the form. Unticked, the gate is untouched.

All 18 locale files gain the label + description: **translation parity** is a tested requirement (`CONTEXT.md`).

### 5. Docs

README (Configuration section) and CHANGELOG. The README states the trade-off plainly: with the option on, a wrong IP or dead hardware is indistinguishable from a switched-off printer.

## Explicitly not doing

- **No dedicated reconnect task.** The coordinator's existing retry loop already reconnects and recovers; a second mechanism would race it.
- **No new device-registry or entity-level `unavailable` plumbing.** Coordinator availability is the single source.
- **No poll-cadence or log-level rework** while offline (the 30 s INFO, the 5 s UDP discovery broadcast, the M66666 broadcast). Chosen as out of scope; can follow separately if users still report noise.
- **No config-entry version bump.** The flag rides in `entry.options`, which needs no migration.

## Done when

1. Off (default): the existing setup tests pass unchanged — including "unreachable printer ⇒ `ConfigEntryNotReady`".
2. On, printer off, on **each** of WebSocket / MQTT / CC2: entry loads; entities are `unavailable`; coordinator reports `last_update_success is False` and a 30 s interval; nothing at WARNING or above.
3. Powering the printer on recovers within one poll with no restart.
4. The option can be enabled from the UI while the printer is off, and disabled again.
5. All 18 locales pass parity.
6. `make format`, `make lint`, `make test` green.
