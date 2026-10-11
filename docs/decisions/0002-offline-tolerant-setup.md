---
status: accepted
date: 2026-10-10
supersedes:
superseded-by:
---

# Offline-tolerant setup: a loaded entry with a disconnected client

**Status:** accepted

## Context

`async_setup_entry` raises `ConfigEntryNotReady` whenever the printer cannot be reached, which Home Assistant renders as "Failed setup, will retry". That is what the HA integration guidelines advise, and for a printer that is permanently powered it is right. But an Elegoo printer is normally switched off — #171's reporter powers theirs on rarely and restarts HA far more often, so the integration sits red indefinitely. The asymmetry that convinced us: switching the printer off *after* setup is a supported, self-healing state (entities go `unavailable`, polling slows, it recovers on power-on), while the identical state reached *during* setup is an error. Same world, two different outcomes.

Options:

1. Keep `ConfigEntryNotReady` and only downgrade the log level (what v1.4.10 did for the log spam). Leaves the red integration.
2. Make a failed connection at setup always non-fatal. Silent for people whose printer is always on: a typo'd IP or a dead mains board would present as an offline printer forever, with no signal.
3. Opt-in per config entry: when the entry says "this printer is often switched off", setup completes with a disconnected client and every entity comes up `unavailable`.

## Decision

Option 3, as `allow_printer_offline` (default off, so existing behaviour is byte-for-byte unchanged). Setup completes when `ElegooPrinterApiClient.async_create` reports a *deliberately* offline client, and the coordinator is started with `async_start_offline()` — which only marks state — instead of `async_config_entry_first_refresh()`, because the latter raises `ConfigEntryNotReady` on a failed refresh and would put the entry straight back into the state the option exists to avoid. The reconnect-on-power-on path is deliberately *not* new: the coordinator's existing 30-second retry loop does it — same loop, same cadence, only quieter now (the failure is reported once per outage rather than every poll). It is worth being exact about the latency that loop gives: the failed poll reconnects and *then* fails, so the poll that finds the printer again still ends in `UpdateFailed` and the first real data arrives on the following poll — a minute or so after power-on, not 30 seconds.

The branch keys on the client reporting a tolerated-offline setup, not on `is_connected`, so any path we did not consider still falls back to today's fail-loud `ConfigEntryNotReady`.

## Considered Options

Rejected: a **dedicated reconnect task** started by `async_start_offline`. The coordinator already retries on a failed poll and already recovers, so a second mechanism would race it and duplicate the 30-second cadence.

Rejected: **`ConfigEntryState.SETUP_RETRY` + fewer retries**, or marking the device `unavailable` at the device-registry level. Neither is a state HA offers for "intentionally not set up"; both fight core.

## Consequences

- **The poll has to be honest, or the option is a regression.** Entities derive availability from `coordinator.last_update_success`, and the MQTT and CC2 transports answer `get_printer_status`/`get_printer_attributes` from a cache *without raising* — so a poll of an offline printer succeeds and every entity renders fabricated values (`0 °C`, `idle`, fans off) as live truth. `_async_update_data` therefore refuses to poll while `api.is_connected` is false, routing all three transports through the existing quiet failure branch. WebSocket behaviour is unchanged, because it already raised. This gate sees what a transport can see: for WebSocket and CC2 that is the printer itself (a CC2 hosts its own broker, so powering it off drops our link), while for plain MQTT it is the **broker** — so a printer that goes silent while its broker outlives it, which includes our own embedded broker, still answers from cache. That gap is unchanged from before this decision and is recorded in `docs/roadmap/offline-tolerant-setup.md`.
- **The embedded MQTT broker and proxy server references stay held** on the offline exit path. The entry stays loaded until the user turns the option off, so releasing them there would leak the reference (only `async_unload_entry` releases what `runtime_data.api` holds, and it releases only what that client genuinely acquired — an entry that came up offline never started the proxy, so it must not release a reference that belongs to another entry's live server) and would deny the printer its reconnect path back to a broker that is ours and listening.
- **Availability semantics get stricter in one place**: a dropped Canvas or file-list side-check request no longer flips a *connected* printer to unavailable. `ElegooPrinterNotConnectedError` is a sibling of `ElegooPrinterConnectionError`, not a subclass, so those two `except` clauses missed it and a Canvas hiccup failed the whole update.
- `coordinator.data` is `None` until the printer is first reached, so anything reading it during `async_added_to_hass` must tolerate that (the CC2 restore-state selects did not).
