---
status: approved
done-when: A CC2 can be added and fully driven (MQTT control, g-code upload, camera) through a user-hosted forward proxy by setting `proxy_host`; discovery is skipped when `proxy_host` is set; the serial is auto-learned or prompted; the camera streams through the proxy; unset `proxy_host` behaves exactly as today.
---

# CC2 `proxy_host` — connect the integration through a forward proxy

Resolves issue #414.

## Summary

Add a `proxy_host` option to the Centauri Carbon 2 (CC2) integration so the whole integration (MQTT control, g-code upload, camera) can connect through a user-hosted middlebox instead of the printer directly.

## Problem

CC2 uses an inverted MQTT architecture: the printer runs its own MQTT broker on port 1883 and HA connects to it as a client. The integration also uploads g-code over HTTP (port 80) and streams the camera over MJPEG (port 8080) — all directly to the printer. Users with flaky printers want to route the integration through a docker middlebox (e.g. `lantern-eight/elegoo-printer-proxy`, which already does transparent TCP pass-through for 1883/9001/8080 and intercepts port-80 uploads for g-code capture).

Today the integration "checks and prohibits" this: the CC2 config flow gates on **UDP 52700 discovery**, which the proxy does not answer → `no_printer_found`. The MQTT validation itself (connect + auth + `register_response == "ok"`) is already proxy-compatible.

## Design

### 1. Config surface & data model

- New optional field **`proxy_host`** (a host: IP or DNS name) on the CC2 config entry, alongside `ip_address`, the access code, and `gcode_proxy_url`.
- **Ports unchanged** — a transparent pass-through listens on the printer's own ports (1883 MQTT, 80 upload, 8080 camera). (Non-standard-port support is a follow-up.)
- Stored on the `Printer` model. A single derived **`connection_host = proxy_host or ip_address`** is the one target every CC2 connection uses — one rule, no per-connection special-casing.
- When `proxy_host` is empty/absent, behavior is identical to today (`connection_host == ip_address`).

### 2. Connection routing

- **MQTT:** `ElegooCC2Client` connects to `connection_host:1883` (was `ip_address:1883`). Auth unchanged (access code / `123456` fallback). The transparent tunnel is transparent to this (plain TCP, QoS 0, no TLS, unique client-ID).
- **Upload:** `upload_gcode` PUTs to `connection_host:80/upload` (was `ip_address`).
- **Camera (the one real fix):**
  - *Today:* `_handle_video_response` **prefers** the printer-supplied `video_url` (real IP embedded) and only builds `http://{ip}:8080/?action=stream` as a fallback.
  - *New:* when `proxy_host` is set, **always build the camera URL from `connection_host` and ignore the printer-supplied `video_url`** — a client-side override that makes the camera work through a transparent tunnel regardless of firmware field-name quirks. When `proxy_host` is unset, behavior is unchanged.
  - The camera platform's initial `mjpeg_url` (currently from `printer.ip_address`) also switches to `connection_host`.
- **Reachability check** (`api.py`, the TCP connect during setup) targets `connection_host:1883`.
- **Discovery skipped** when `proxy_host` is set — this removes the only hard block (the UDP 52700 gate). The MQTT validation (connect + auth + `register_response == "ok"`) is unchanged and already proxy-compatible.
- **No proxy-side changes required** — the tunnel is transparent to MQTT + upload, and the camera is fixed client-side.

### 3. Serial acquisition (hybrid)

The serial is baked into every MQTT topic (`elegoo/{sn}/...`); discovery (skipped) is how it's learned today.

- **When it runs:** only when `proxy_host` is set **and** the serial is unknown (adding fresh via the proxy). If the user supplies the serial, or the printer was already discovered directly (serial known), skip straight to register + validate.
- **Auto-learn (best-effort, during the setup connection test):**
  1. Connect to `connection_host:1883` (auth with the access code).
  2. Subscribe to `elegoo/+/api_status` (wildcard).
  3. Wait up to **5 s** for a status message; extract the SN from the topic (`elegoo/{sn}/api_status`).
- **Fallback:** if auto-learn times out, the config flow re-prompts: *"Could not auto-detect the serial — enter it"* → user types it → resubmit → register + validate.
- **After the SN is known** (learned or typed): save it, register (`elegoo/{sn}/api_register`), validate (`register_response == "ok"`).
- **Runtime:** the serial is already stored from setup — no re-learning. Auto-learn is setup-only.
- **Known caveat (handled by the fallback):** the broker may only publish `api_status` *while a client is registered*, and registration needs the SN — a chicken-and-egg. If so, auto-learn always times out and we always fall back to the prompt. The design is correct either way; auto-learn is a UX bonus when the firmware cooperates.

### 4. Validation & error handling

When `proxy_host` is set, the setup connection test performs, in order:
1. **Reachability** — TCP connect to `connection_host:1883`.
2. **Auth** — MQTT with the access code (`123456` fallback).
3. **Serial** — the hybrid (auto-learn 5 s, else prompt).
4. **Registration** — `register_response == "ok"`.

| Failure | Message |
|---|---|
| Can't reach `connection_host:1883` | *"Cannot reach the proxy at {host}:1883 — is it running?"* |
| Auth fails | *"Authentication failed — check the access code"* (existing) |
| Auto-learn times out | *"Could not auto-detect the serial — enter it"* (fallback prompt) |
| Registration fails / "too many clients" | *"Registration failed — the printer may have too many connected clients"* (existing) |

### 5. Scope boundaries

**In v1:** the `proxy_host` option; `connection_host`; all CC2 connections route through it; camera client-side override; discovery skipped; serial hybrid; validation + per-step errors; docs.

**Out of scope (explicitly deferred):**
- **Mux / connection-count reduction** — a separate, larger project (N→1 multiplexing). The `proxy_host` option already lets a user point at a mux (it's just a host); we're not *building* one.
- **Proxy-side changes** — none required.
- **Non-standard ports** — `proxy_host` is host-only.
- **Runtime re-learning** — serial auto-learn is setup-only.
- **`gcode_proxy_url`** — untouched; it stays a distinct, passive concept.

## Testing

- `connection_host` derivation (proxy set / unset).
- Camera URL override (proxy set → built from `connection_host`; unset → printer-supplied `video_url` preferred).
- Config flow: `proxy_host` set → discovery skipped; serial auto-learn success vs. timeout→prompt.
- Validation per-step errors.
- (No live-proxy CI test — manual verification documented.)

## Follow-ups (not v1)

- Mux (N→1 connection-count reduction) — separate project.
- Non-standard port support for `proxy_host`.
- Runtime serial re-learning on reconnect.

## Notes

- Research source: `docs/research/issue-414-cc2-proxy-connection.md` (5 angles, 2026-09-21).
- Terminology: `proxy_host` = a **forward** proxy to connect through (HA dials it). Distinct from the CC1-only `proxy_enabled` "HA proxy mode" (inverted — the printer dials into HA's `ElegooPrinterServer`) and the passive `gcode_proxy_url` (g-code capture API).
