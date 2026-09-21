---
status: current
last-verified: 2026-09-21
verified-by: web + local code research — 5 researcher angles, 2026-09-21
---

# Issue #414 — "Allow connecting to a CC2 proxy instead of requiring a direct connection"

## Executive Summary

The request is **feasible and well-scoped** — it is a small-to-moderate *integration-side* change, **not** a new relay project. The middlebox the reporter already runs (`lantern-eight/elegoo-printer-proxy`) already does transparent TCP pass-through for the CC2's MQTT (1883), MQTT-over-WS (9001), and MJPEG camera (8080), plus intercepts `PUT /upload` on port 80.

**Why the integration "checks and prohibits" the proxy today:** the CC2 config flow gates on **UDP 52700 discovery**, which the proxy does not answer → `no_printer_found`. The MQTT validation itself (connect + auth + `register_response == "ok"`) is already proxy-compatible.

**The fix is a `proxy_host` option** that points MQTT + upload + camera at a middlebox and skips discovery. Two design decisions must be settled: (1) how to obtain the **serial number** without discovery (it's baked into every MQTT topic), and (2) the **camera URL override** (the firmware hands the client a real-IP `video_url`; the client must ignore it and build from the proxy IP when `proxy_host` is set).

**Key caveat:** a transparent pass-through changes the *source IP* but does **not** reduce connection count (the printer still sees N client slots, cap ~4). If the reporter's real problem is connection *pressure*, a **mux (N→1)** is needed — a separate, larger project (prior art: OctoEverywhere `mqttmux`, `bambu-proxy`). The `proxy_host` option is the same integration change either way; the middlebox choice is the user's.

## Findings

### Q1: What does the CC2 integration connect to, and what blocks a proxy target?

Local code (`custom_components/elegoo_printer/`). Every direct connection the integration opens to a CC2:

| Connection | Port | Auth | Purpose | Code |
|---|---|---|---|---|
| UDP discovery | 52700 | none | setup only — **the gate a proxy fails** | `cc2/discovery.py:157-180` |
| MQTT (printer is the broker) | 1883 | user `elegoo`, pw = access code / `123456` | all control + status | `cc2/client.py:282-341` |
| HTTP `PUT /upload` | 80 | `X-Token` | g-code upload | `cc2/upload.py:82` |
| MJPEG camera | 8080 | none, **1 viewer** | built from `printer_ip` | `cc2/client.py:1248-1251` |

- The existing `gcode_proxy_url` option is a *passive* capture API (health + `/api/filament`), **not** a connection path — don't conflate the two proxy concepts (`cc2/gcode_proxy.py:65-86`).
- The CC1 "HA proxy" mode cannot extend to CC2: CC2 firmware has 3030/3031 closed and CC2 is printer-as-broker (HA is the client) — nothing to hook into (`api.py:413-415`, `docs/CC2_PROTOCOL.md:215`).
- Config-flow hard blocks: **only** the UDP-52700 discovery gate in `async_step_manual_ip` (`config_flow.py:596-700`, block ~631-662). The CC2 **options** flow (`config_flow.py:1205-1240`) has *no* discovery gate — it already lets you point `ip_address` at any host. `_sanitize_ip_address` does no IP-format validation (docker IPs pass).
- The serial number (`printer.id`) is used in **all** MQTT topics (`elegoo/{sn}/...`, `cc2/client.py:504-520,558-565,640,1347`) and as the config unique id. Without discovery it is unknown → topics break. This is the core design hazard of a skip-discovery path.

### Q2: Does the existing proxy relay what's needed?

`lantern-eight/elegoo-printer-proxy` @ `9bf34c5` (2026-07-17).

| Capability | Status |
|---|---|
| G-code capture + `/api/filament` | ✅ (the part the reporter already uses) |
| MQTT 1883 + 9001 | ✅ **transparent TCP pass-through** (`src/tcp_proxy.py:3`, `main.py:122-131`) |
| MJPEG 8080 | ✅ transparent pass-through |
| Video URL rewrite | ❌ **deliberately not done** — "video streams stay direct by design" (`README.md:86-90`) |
| MQTT *bridge/mux* | ❌ (bytes only, no broker) |
| UDP 52700 discovery relay | ❌ **not present** — the gap that blocks the integration |

Project health: active (last push 2026-07-17, v2.0.0), single maintainer, **zero issues filed**. The maintainer may be the same person as the #414 reporter (unverified).

### Q3: Is an MQTT middlebox a known-working pattern, and is the camera proxyable?

- **Camera:** MJPEG over plain HTTP :8080, no auth, **1 concurrent viewer**, trivially proxyable (any request to :8080 is the stream on stock firmware). Two independent projects already proxy it.
- **Two working designs:**
  1. **Mux/bridge (N→1)** — actually reduces pressure. Proven on CC2 (OctoEverywhere `mqttmux`, 96k+ users) and the closest analogue Bambu (`bambu-proxy`, "hundreds of connections").
  2. **Transparent pass-through** — single source IP, **no count reduction** (each proxied session still consumes a printer slot).
- **Gotchas a mux must handle:** ~4-connection cap (`too many clients`), stateful registration + re-subscribe on reconnect, 10 s heartbeat / 65 s drop, **retained-message caching** (the #1 naive-bridge bug), QoS 0, wildcard matching, client-ID format. No TLS on CC2 (unlike Bambu X1C), so bridging is simpler.

### Q4 (deep-dive): Would "point the CC2 client at a transparent proxy" actually work?

- **MQTT + upload: YES, fully.** Plain TCP/HTTP only (no TLS anywhere in `cc2/` or `camera.py`), QoS 0, unique client-ID, no source-IP/retained dependency. A verbatim byte tunnel is transparent.
- **Camera: NOT with a pure tunnel — but fixed by a one-line client-side override.** The 1042 response hands the client `{"url": "http://<real_ip>:8080/?action=stream"}` (confirmed by a live OrcaSlicer capture, fw 02.01.00.00). The integration **prefers** the printer-supplied `video_url` over client-side construction (`cc2/client.py:1240-1258`), so a pure tunnel lets the camera bypass the proxy. **Fix:** when `proxy_host` is set, ignore the supplied `video_url` and always build `http://{proxy_host}:8080/?action=stream`. No proxy-side change needed.
- **Serial acquisition (the real design decision):** options — (a) serial as a config field, (b) wildcard subscribe `elegoo/+/...` and learn the SN from the first envelope, (c) proxy relays UDP 52700.

### Q5 (deep-dive): Does the CC2 hand the client a real-IP camera URL?

**Yes (firmware 02.01.00.00+).** The 1042 `VIDEO_STREAM` response is `{"error_code": 0, "url": "http://<printer_real_ip>:8080/?action=stream"}`. On older firmware 01.03.02.51, 1042 is non-responsive (client-side construction only). The official `elegoo-link` SDK has 1042 commented out (no authoritative schema); the field name is uncertain (`url` vs `video_url` vs `VideoUrl`). Other payloads: 1046 thumbnail is a base64 blob (not a URL), 1036 has `thumbnail_url` (task thumbnails), 1057 `url` is client-supplied — so the **camera is the only printer-supplied URL that matters**.

## The concrete change (a `proxy_host` option)

| # | File | Change |
|---|------|--------|
| 1 | `const.py` | add `CONF_PROXY_HOST` |
| 2 | `sdcp/models/printer.py` | persist `proxy_host` |
| 3 | `cc2/client.py` | point MQTT hostname + `UploadTarget` + **camera URL (override)** at `proxy_host` when set |
| 4 | `config_flow.py` | options field (easy path — no discovery gate) + optional skip-discovery manual entry (needs serial strategy) |
| 5 | `api.py` | reachability check + client construction use the proxy host |

~5 files, moderate. The options-flow path is already open (no discovery), so the minimal version is smaller still.

## Unresolved Contradictions
- Reporter: "I use the feed provided through the proxy" vs. proxy: "video stays direct by design." **Resolved:** for CC2 the camera URL the *client* builds is proxy-friendly; the "direct by design" note is about the printer-supplied embedded-IP URL (which the client-side override in change #3 neutralizes).

## Gaps / What Remains Unknown
- What the reporter actually runs (stock proxy? fork? env?) — only they can answer.
- Whether dwolfuk == the proxy's author — unverified (would enable direct coordination).
- Hard limits are "usually 1" / "~4" in all sources — firmware may vary.
- Exact 1042 response field name (`url` vs `video_url`) and whether it's present on `enable: false` — needs a live capture to pin down (the client-side override makes this non-blocking).
- "OpenPrintedSkylake" — no public project found under that name.

## Recommendation

1. **Reply to the reporter** and confirm: (a) is a transparent pass-through enough (single egress point) or is connection-count reduction (a mux) needed? (b) what's actually flaky and the exact error? (c) are they the proxy's author?
2. **Implement the `proxy_host` option** (the 5-file change above), with the camera URL override and a chosen serial-acquisition strategy (recommend (a) serial-as-config-field for v1 — simplest and explicit).
3. **Document:** run `lantern-eight/elegoo-printer-proxy` (or any transparent pass-through / mux), set the option.
4. **If connection-count reduction is the real need**, that is a separate, larger project (mux) — punt to the proxy project or build it; the `proxy_host` option already lets a user point at a mux.
