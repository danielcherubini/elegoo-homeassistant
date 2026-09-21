---
status: committed
done-when: A CC2 can be added and fully driven (MQTT control, g-code upload, camera) through a user-hosted forward proxy by setting `proxy_host`; discovery is skipped when `proxy_host` is set; the serial is auto-learned or prompted; the camera streams through the proxy; runtime setup (api.py) connects through the proxy; unset `proxy_host` behaves exactly as today.
---

# CC2 `proxy_host` — connect the integration through a forward proxy — Plan

**Goal:** Let a Centauri Carbon 2 (CC2) printer be added and driven (MQTT control, g-code upload, camera) through a user-hosted forward proxy by setting a new `proxy_host` option; the whole integration routes through `connection_host = proxy_host or ip_address`.

**Architecture:** CC2 uses an inverted MQTT architecture — the printer runs its own MQTT broker (port 1883) and HA connects to it as a client; g-code is uploaded over HTTP (port 80) and the camera streams over MJPEG (port 8080), all directly to the printer. A transparent forward proxy (e.g. `lantern-eight/elegoo-printer-proxy`) relays 1883/80/8080 to the printer and does not answer UDP discovery. The fix: a single derived `connection_host` on the `Printer` model that **every** CC2 connection targets (config-flow test, runtime `api.py` setup, client MQTT/upload, camera); the camera URL is built client-side from `connection_host` when `proxy_host` is set (the printer-supplied `video_url` embeds the real IP and must be ignored); discovery is skipped when `proxy_host` is set; the serial is auto-learned (wildcard subscribe) with a user-prompt fallback.

**Tech Stack:** Python 3.13, Home Assistant config flow (`voluptuous` + `hass` selector), `aiomqtt` (async MQTT), `asyncio` (TCP/UDP), pytest + ruff.

**Key invariants:**
- `proxy_host` is a **forward** proxy host (IP or DNS name); ports are unchanged (a transparent pass-through listens on the printer's own ports).
- `connection_host` is a **derived** property: `self.proxy_host or self.ip_address`. When `proxy_host` is empty/absent, `connection_host == ip_address` and behavior is identical to today.
- `proxy_host` is **not sensitive** (a host, not a secret) — it is NOT redacted in `to_dict_safe`.
- `proxy_host` is **CC2-only**. It must NOT affect CC1/FDM/resin paths.
- The existing `gcode_proxy_url` (passive g-code capture) and CC1 `proxy_enabled` (HA proxy mode) are **untouched**.
- **Config merge is data-first, options-second** (`{**entry.data, **entry.options}`). A key stored in `entry.data` (like `proxy_host`, which is a `Printer` field) can only be overridden — not removed — by options. So "clearing" `proxy_host` via options must persist `""` (falsy), NOT pop the key.
- **Test layout:** there is NO top-level `tests/` directory. `pyproject.toml` sets `testpaths = ["custom_components"]`, so a new test file must live under `custom_components/elegoo_printer/...` or it will silently never run.

---

### Task 1: Data model + constant (`proxy_host` + derived `connection_host`)

**Context:**
Everything downstream (client routing, `api.py` runtime setup, config flow, camera) depends on the `Printer` model carrying `proxy_host` and exposing a single derived `connection_host`. This task adds the config constant and the model field. It is the foundation — no other task may be done before it. The `Printer` model already carries CC2-specific fields (`cc2_access_code`, `cc2_token_status`) initialized from `config` in `__init__`, persisted in `to_dict`, and restored in `from_dict`; `proxy_host` follows the exact same pattern. `Printer` has NO existing `proxy_host`/`connection_host` member (verified).

**Files:**
- Modify: `custom_components/elegoo_printer/const.py`
- Modify: `custom_components/elegoo_printer/sdcp/models/printer.py`
- Test: `custom_components/elegoo_printer/sdcp/models/tests/test_printer.py` (EXTEND the existing printer-model test file — it already tests `Printer` internals)

**What to implement:**
1. In `const.py`, in the "CC2-specific settings" block (next to `CONF_CC2_ACCESS_CODE`, `CONF_CC2_TOKEN_STATUS`, `CONF_GCODE_PROXY_URL`), add:
   ```python
   CONF_PROXY_HOST = "proxy_host"
   CONF_SERIAL = "serial"
   ```
   Do NOT touch the existing `PROXY_HOST = "127.0.0.1"` constant (that is CC1 HA-proxy-mode). `CONF_PROXY_HOST`/`CONF_SERIAL` collide with nothing (verified).
2. In `printer.py`:
   - In the class attribute list (the block of type annotations near the top of `Printer`, next to `cc2_access_code: str | None` / `cc2_token_status: int`), add: `proxy_host: str | None`.
   - In `__init__`, in the "Initialize config-based attributes for all instances" block (where `self.cc2_access_code = config.get(CONF_CC2_ACCESS_CODE)` is set), add: `self.proxy_host = config.get(CONF_PROXY_HOST)`.
   - Add a derived property:
     ```python
     @property
     def connection_host(self) -> str | None:
         """The host to connect to: the proxy host when set, else the printer IP."""
         return self.proxy_host or self.ip_address
     ```
   - In `to_dict`, add `"proxy_host": self.proxy_host,` (next to the other CC2 fields).
   - In `from_dict`, in the "CC2-specific settings" block (where `printer.cc2_access_code = ...` is set), add: `printer.proxy_host = attrs.get(CONF_PROXY_HOST, attrs.get("proxy_host"))`.
   - Do NOT modify `to_dict_safe` (a host is not a secret).
   - Ensure `CONF_PROXY_HOST` is imported in `printer.py` (check the existing `const` import block and add it if missing).
3. What NOT to change: do not add `proxy_host` to any CC1/FDM/resin code path; do not change `ip_address` semantics.

**Steps:**
- [ ] Write failing tests in `custom_components/elegoo_printer/sdcp/models/tests/test_printer.py`:
  - `connection_host` returns `ip_address` when `proxy_host` is `None`/empty.
  - `connection_host` returns `proxy_host` when set.
  - `to_dict` includes `proxy_host`; `from_dict` round-trips it (construct a `Printer`, `to_dict`, `from_dict`, assert `proxy_host` and `connection_host` survive).
- [ ] Run `make test`
  - Did the new tests fail with `AttributeError`/missing key? If they passed unexpectedly, stop and investigate.
- [ ] Implement the changes above.
- [ ] Run `make test`
  - Did all tests pass? If not, fix and re-run.
- [ ] Run `make format`
  - Did it succeed? If not, fix and re-run.
- [ ] Run `make lint`
  - Did it succeed? If not, fix and re-run.
- [ ] Commit with message: `feat(cc2): add proxy_host + derived connection_host to Printer model (#414)`

**Acceptance criteria:**
- [ ] `CONF_PROXY_HOST == "proxy_host"` and `CONF_SERIAL == "serial"` exist in `const.py`.
- [ ] `Printer` has a `proxy_host` attribute and a `connection_host` property (`proxy_host or ip_address`).
- [ ] `proxy_host` round-trips through `to_dict`/`from_dict`.
- [ ] `make test`, `make format`, `make lint` all pass.

---

### Task 2: Routing — client (MQTT/upload/camera) + `api.py` runtime setup + camera initial URL

**Context:**
Every CC2 connection must target `connection_host`. There are **three** places that build a connection target from `printer.ip_address`, and all three must switch to `connection_host` or a proxy-only printer is unusable:
1. `cc2/client.py` — `connect_printer` sets `self.printer_ip`, which is the MQTT hostname, the `UploadTarget.host` for `upload_gcode`, and the fallback camera URL.
2. `api.py` — **the Critical gap.** Runtime setup (`ElegooPrinterApiClient.async_create`) performs a raw TCP connectivity test against `printer.ip_address` (NOT `connection_host`): `api.py:255` (`self._mqtt_host = printer.ip_address or ""`), `api.py:326`/`api.py:350` (`asyncio.open_connection(self._mqtt_host, ...)`), and on failure returns `None` → `ConfigEntryNotReady`. If this is not fixed, a proxy-only printer passes the config flow (which uses `connect_printer`) but **fails on every HA start** — the feature would appear green in tests while being unusable. `api.py:244` also constructs the client with `printer_ip=printer.ip_address or ""` (harmless since `connect_printer` overrides it, but switch it for consistency).
3. `camera.py` — the initial `mjpeg_url` placeholder is built from `printer.ip_address`.

The camera also needs an explicit **override** in `_handle_video_response`: it currently **prefers** the printer-supplied `video_url` (real IP embedded) and only builds `http://{ip}:8080/?action=stream` as a fallback — when `proxy_host` is set we must instead **always** build from the proxy and ignore the supplied URL.

**Files:**
- Modify: `custom_components/elegoo_printer/cc2/client.py`
- Modify: `custom_components/elegoo_printer/api.py`
- Modify: `custom_components/elegoo_printer/camera.py`
- Test: `custom_components/elegoo_printer/cc2/tests/test_client_factory.py` (client — this is where `_handle_video_response`/connect/video tests live; there is NO `test_client.py`)
- Test: `custom_components/elegoo_printer/tests/test_camera.py` (camera)
- Test: `custom_components/elegoo_printer/tests/test_api_discovery.py` (api — extend the existing api test file, or create `test_api_cc2_proxy.py` in the same dir)

**What to implement:**
1. `cc2/client.py` — `connect_printer`: change
   ```python
   self.printer_ip = printer.ip_address or self.printer_ip
   ```
   to
   ```python
   self.printer_ip = printer.connection_host or self.printer_ip
   ```
   This makes the MQTT hostname, the `UploadTarget.host` (used by `upload_gcode`), and the fallback camera URL all use the proxy when `proxy_host` is set.
2. `cc2/client.py` — `_handle_video_response`: the current logic is
   ```python
   error_code = video_data.get("error_code", 0)
   video_url = video_data.get("video_url", "")
   if error_code == 0 and not video_url:
       video_url = f"http://{self.printer_ip}:8080/?action=stream"
   ```
   Change it so that when the printer is configured with a proxy AND the response is a success, the URL is **always** built from the proxy host and the printer-supplied `video_url` is ignored:
   ```python
   error_code = video_data.get("error_code", 0)
   video_url = video_data.get("video_url", "")
   if error_code == 0 and self.printer.proxy_host:
       # Proxy mode: always build from the proxy host and ignore the
       # printer-supplied URL (it embeds the printer's real IP, which a
       # transparent tunnel does not expose to HA).
       video_url = f"http://{self.printer_ip}:8080/?action=stream"
   elif error_code == 0 and not video_url:
       # No proxy: keep current behavior (prefer supplied, fallback build).
       video_url = f"http://{self.printer_ip}:8080/?action=stream"
   ```
   (Gated on `error_code == 0` so error paths keep the supplied/empty URL. `self.printer_ip` is the proxy host here because `connect_printer` set it to `connection_host`.)
3. `api.py` — **the Critical fix.** In `async_create`:
   - Change `api.py:255` `self._mqtt_host = printer.ip_address or ""` → `self._mqtt_host = printer.connection_host or ""`.
   - Change `api.py:244` `printer_ip=printer.ip_address or ""` → `printer_ip=printer.connection_host or ""`.
   - The reachability checks at `api.py:326`/`api.py:350` already use `self._mqtt_host`, so they now target the proxy automatically. Do NOT change those lines.
   - Do NOT change the non-CC2 branches (`api.py:115`, `api.py:138-206` use `printer.ip_address` for CC1/FDM/resin discovery — those are not CC2 and stay as-is).
4. `camera.py` — the initial `mjpeg_url`, non-`proxy_enabled` branch (currently `mjpeg_url = f"http://{printer.ip_address}:{VIDEO_PORT}/{VIDEO_ENDPOINT}"`): change `printer.ip_address` → `printer.connection_host`. Leave the `proxy_enabled` (CC1) branch unchanged.
5. What NOT to change: do not change the `8080`/`?action=stream` port or query; do not change non-proxy behavior; do not touch `upload_gcode`'s `UploadTarget` construction (it already uses `self.printer_ip`); do not change the runtime `_mjpeg_url` update logic in `camera.py`.

**Steps:**
- [ ] Write failing tests:
  - In `custom_components/elegoo_printer/cc2/tests/test_client_factory.py`:
    - `connect_printer` with a `Printer(proxy_host="10.0.0.5", ip_address="10.0.0.9")` results in `client.printer_ip == "10.0.0.5"` (assert after a mocked connect via the `client_factory` seam).
    - `connect_printer` with `Printer(proxy_host=None, ip_address="10.0.0.9")` results in `client.printer_ip == "10.0.0.9"`.
    - `_handle_video_response` with `client.printer.proxy_host` set + `error_code == 0` returns `http://{proxy_host}:8080/?action=stream` even when `video_data` contains a `video_url` with the real IP (assert the supplied URL is ignored).
    - `_handle_video_response` with `proxy_host` unset keeps current behavior (supplied `video_url` preferred; fallback build when absent).
    - `_handle_video_response` with `proxy_host` set + `error_code != 0` does NOT override (keeps the supplied/empty URL).
  - In `custom_components/elegoo_printer/tests/test_api_discovery.py` (or a new `test_api_cc2_proxy.py` in the same dir):
    - `async_create` with a CC2 `Printer(proxy_host="10.0.0.5", ip_address="10.0.0.9")` opens the connectivity socket to `10.0.0.5` (the proxy), not `10.0.0.9` (assert via a mocked `asyncio.open_connection` / captured `_mqtt_host`).
    - `async_create` with `proxy_host` unset targets `ip_address` (unchanged).
  - In `custom_components/elegoo_printer/tests/test_camera.py`:
    - A camera built from a real `Printer(proxy_host="10.0.0.5", ip_address="10.0.0.9", proxy_enabled=False)` has an initial `mjpeg_url` of `http://10.0.0.5:{VIDEO_PORT}/{VIDEO_ENDPOINT}`.
    - A camera built from `Printer(proxy_host=None, ip_address="10.0.0.9", proxy_enabled=False)` has `http://10.0.0.9:{VIDEO_PORT}/{VIDEO_ENDPOINT}`.
    - IMPORTANT: the `entry` fixture in `conftest.py` supplies a `MagicMock` api whose `printer` is a `MagicMock` (so `printer.proxy_enabled` is truthy and the CC1 branch is taken). To exercise the CC2 branch, attach a **real** `Printer` (with `proxy_enabled=False` and the `proxy_host` under test) to `entry.runtime_data.api.printer` before constructing the camera entity.
- [ ] Run `make test`
  - Did the new tests fail? If they passed unexpectedly, stop and investigate.
- [ ] Implement the changes above.
- [ ] Run `make test`
  - Did all tests pass? If not, fix and re-run.
- [ ] Run `make format` and `make lint`
  - Did both succeed? If not, fix and re-run.
- [ ] Commit with message: `feat(cc2): route MQTT/upload/camera + runtime api.py setup through connection_host (#414)`

**Acceptance criteria:**
- [ ] `connect_printer` sets `self.printer_ip` from `printer.connection_host`; MQTT hostname, `upload_gcode` `UploadTarget.host`, and the fallback camera URL all use the proxy when `proxy_host` is set.
- [ ] `_handle_video_response` builds the camera URL from the proxy and ignores the printer-supplied `video_url` when `proxy_host` is set (success only); behavior is unchanged when unset.
- [ ] `api.py` runtime setup (`_mqtt_host`, client construction, and the `open_connection` reachability checks) targets `connection_host`.
- [ ] The camera initial `mjpeg_url` uses `connection_host`.
- [ ] `make test`, `make format`, `make lint` all pass.

---

### Task 3: Config flow — `proxy_host` field + skip discovery + construct CC2 printer

**Context:**
Adding a printer via a proxy means the user cannot rely on UDP discovery (the proxy doesn't answer it). This task (a) adds `proxy_host` to the `manual_ip` step schema, (b) when `proxy_host` is provided, **skips discovery and constructs a CC2 `Printer` directly** (mirroring `CC2DiscoveredPrinter.to_printer()`), and (c) adds `proxy_host` to the `cc2_options` step so it can be set/edited after setup. When `proxy_host` is empty, the current discovery behavior is preserved exactly.

**Files:**
- Modify: `custom_components/elegoo_printer/config_flow.py`
- Test: `custom_components/elegoo_printer/tests/test_config_flow_cc2_options.py` (extend the existing CC2 flow test file — note it currently covers the **options** flow; there are **no** existing tests for the initial flow, so add new test functions here for `manual_ip`/`cc2_auth_check`/`_attempt_cc2_connection`)

**What to implement:**
1. `manual_ip` schema — the schema is the **module-level `MANUAL_IP_SCHEMA` constant** (config_flow.py ~line 123-138), consumed via `self.add_suggested_values_to_schema(MANUAL_IP_SCHEMA, user_input)`. Add to it:
   ```python
   vol.Optional(CONF_PROXY_HOST): selector.TextSelector(
       selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
   ),
   ```
   (Keep `CONF_IP_ADDRESS` required — the user enters the printer's real IP *or* the proxy host there; `proxy_host` is the explicit "route through this" field.)
2. `async_step_manual_ip` — at the top of the `if user_input is not None:` block, **before** the discovery calls, add the proxy short-circuit. **Every branch must `return`** (the existing code below must NOT run when `proxy_host` is set):
   ```python
   proxy_host = (user_input.get(CONF_PROXY_HOST) or "").strip()
   if proxy_host:
       LOGGER.info("Manual IP entry: proxy_host set (%s) - skipping discovery", proxy_host)
       printer_object = self._build_cc2_printer_from_proxy_input(user_input, proxy_host)
       if printer_object is None:
           _errors["base"] = "manual_ip_no_valid_ip"
           return self.async_show_form(
               step_id="manual_ip",
               data_schema=self.add_suggested_values_to_schema(MANUAL_IP_SCHEMA, user_input),
               errors=_errors,
           )
       self.selected_printer = printer_object
       printer_object.external_ip = user_input.get(CONF_EXTERNAL_IP)
       await self.async_set_unique_id(unique_id=printer_object.id)
       self._abort_if_unique_id_configured()
       LOGGER.info("Routing to CC2 auth flow (proxy) for manual IP entry")
       return await self.async_step_cc2_auth_check()
   ```
   Leave the existing discovery code below it unchanged (it now only runs when `proxy_host` is empty, because the proxy block always returns).
3. Add a helper `_build_cc2_printer_from_proxy_input(self, user_input, proxy_host) -> Printer | None` mirroring `CC2DiscoveredPrinter.to_printer()`:
   ```python
   ip_address = self._cleanup_user_input(user_input.get(CONF_IP_ADDRESS))
   if not ip_address:
       return None
   printer = Printer()
   printer.name = user_input.get(CONF_NAME) or "Elegoo CC2"
   printer.model = "Centauri Carbon 2"  # REQUIRED: from_dict recomputes printer_type from model
   printer.ip_address = ip_address
   printer.connection = ip_address
   printer.protocol_version = ProtocolVersion.CC2
   printer.transport_type = TransportType.CC2_MQTT
   printer.printer_type = PrinterType.from_model("Centauri Carbon 2")
   printer.brand = "ELEGOO"
   printer.mqtt_broker_enabled = False
   printer.proxy_host = proxy_host
   # Serial intentionally NOT set here - acquired in Task 5 (auto-learn/prompt).
   return printer
   ```
   Import `ProtocolVersion` at the **module top** (add it to the existing `from ...sdcp.models.enums import ...` line that already imports `PrinterType, TransportType`). Do NOT use local `# noqa: PLC0415` imports for `Printer`/`TransportType` (they are already module-level).
4. `_cc2_options_schema` (the `@staticmethod` returning the CC2 options dict) — add:
   ```python
   vol.Optional(CONF_PROXY_HOST, default=""): selector.TextSelector(
       selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
   ),
   ```
5. The **nested `_process_cc2_options_input` closure** inside the options-flow `async_step_cc2_options` (NOT a method — it is a local `async def`) — persist `proxy_host` **unconditionally, including `""`** (do NOT pop the key; see the data-first merge invariant at the top):
   ```python
   printer_data[CONF_PROXY_HOST] = (user_input.get(CONF_PROXY_HOST) or "").strip()
   ```
   (Placed near the `ip_address`/`gcode_proxy_url` handling, before `async_create_entry`. Because `connection_host` is `proxy_host or ip_address`, a `""` in options overrides a stale value in `entry.data` and correctly disables the proxy.)
6. Ensure `CONF_PROXY_HOST` is imported in `config_flow.py` (check the existing `const` import and add it if missing).
7. What NOT to change: do not change the non-proxy discovery path; do not touch `gcode_proxy_url` handling; do not add `proxy_host` to CC1/FDM/resin steps.

**Steps:**
- [ ] Write failing tests in `custom_components/elegoo_printer/tests/test_config_flow_cc2_options.py`:
  - `manual_ip` with `proxy_host` set does NOT call `discover_printer`/`CC2Discovery.discover_as_printers` (mock them; assert not called) and routes to `cc2_auth_check` with a `selected_printer` whose `transport_type == CC2_MQTT` and `proxy_host` set.
  - `manual_ip` with `proxy_host` set but a blank `ip_address` shows the `manual_ip_no_valid_ip` error (and does NOT fall through to discovery).
  - `manual_ip` with `proxy_host` empty runs discovery (mock it to return a CC2 printer; assert the existing path is taken).
  - `cc2_options` with `proxy_host` set persists it in the created entry's data; with `proxy_host` blank, persists `""` (so the data-first merge disables the proxy).
  - IMPORTANT: the initial-flow paths call `async_set_unique_id`/`_abort_if_unique_id_configured`, which need a realer `hass`/flow-manager double than the `MagicMock` `hass` in `conftest.py`. **Patch `async_set_unique_id` and `_abort_if_unique_id_configured` on the flow handler** (e.g. `monkeypatch`/`MagicMock`) in these tests.
- [ ] Run `make test`
  - Did the new tests fail? If they passed unexpectedly, stop and investigate.
- [ ] Implement the changes above.
- [ ] Run `make test`
  - Did all tests pass? If not, fix and re-run.
- [ ] Run `make format` and `make lint`
  - Did both succeed? If not, fix and re-run.
- [ ] Commit with message: `feat(cc2): add proxy_host to config flow, skip discovery when set (#414)`

**Acceptance criteria:**
- [ ] `MANUAL_IP_SCHEMA` has an optional `proxy_host` field.
- [ ] When `proxy_host` is set, discovery is skipped (every proxy branch returns) and a CC2 `Printer` is constructed directly (correct `transport_type`/`protocol_version`/`printer_type`/`proxy_host`); the flow routes to `cc2_auth_check`.
- [ ] When `proxy_host` is empty, discovery runs exactly as before.
- [ ] `cc2_options` persists `proxy_host` unconditionally (blank ⇒ `""`, which disables the proxy via the data-first merge).
- [ ] `make test`, `make format`, `make lint` all pass.

---

### Task 4: Serial auto-learn (client method, hand-rolled connection)

**Context:**
The serial is baked into every CC2 MQTT topic (`elegoo/{sn}/...`) and is required for registration (`elegoo/{sn}/api_register`). Normally it is learned via UDP discovery, which is skipped when `proxy_host` is set. This task adds a best-effort auto-learn: connect to the (proxy) MQTT broker, subscribe to the wildcard `elegoo/+/api_status`, and extract the SN from the first status topic. It is setup-only and bounded (5 s) so a non-cooperating firmware (the chicken-and-egg case) fails fast into the user-prompt fallback (Task 5).

**Architecture note (important):** `ElegooCC2Client` has NO callback-style subscribe. Subscriptions go through `_subscribe_to_topics()` (serial-specific topics) and messages flow exclusively through the `_mqtt_listener` task → `_handle_message`, which would dispatch a wildcard status message to `_handle_status_event` WITHOUT extracting the serial. The existing connect path `_try_connect_with_password` **always** subscribes the serial topics and calls `_register()` (publishing `elegoo/{sn}/api_register` — with an empty serial that is `elegoo//api_register`), which would violate the "does NOT register" requirement. So `async_discover_serial` must **hand-roll a minimal connection** and must NOT call `_try_connect_with_password`, `_register`, or start `_mqtt_listener`.

**Files:**
- Modify: `custom_components/elegoo_printer/cc2/client.py`
- Test: `custom_components/elegoo_printer/cc2/tests/test_client_factory.py` (add here; use the `FakeAiomqttClient` seam and its `queue_message` helper)

**What to implement:**
1. Add a constant near the top of `client.py` (or in `cc2/const.py`): `SERIAL_DISCOVERY_TIMEOUT = 5.0` (seconds).
2. Add a method to `ElegooCC2Client`:
   ```python
   async def async_discover_serial(
       self, timeout: float = SERIAL_DISCOVERY_TIMEOUT
   ) -> str | None:
       """
       Best-effort auto-learn the serial via a wildcard subscribe.

       Connects to the (proxy) MQTT broker, subscribes to
       ``elegoo/+/api_status``, and returns the serial from the first status
       topic (``elegoo/{sn}/api_status``). Returns ``None`` on timeout.
       Does NOT register. Always disconnects on exit.
       """
   ```
   Implementation (hand-rolled — do NOT reuse `_try_connect_with_password`/`_register`/`_mqtt_listener`):
   - Build the password list **exactly as `connect_printer` does** (client.py:281-291): if `self.access_code is not None` → `[self.access_code]`; else → `["", CC2_MQTT_DEFAULT_PASSWORD]` (do NOT try all three).
   - For each password:
     - Create the client via `client_cls = self._client_factory or aiomqtt.Client` with the same kwargs as `_try_connect_with_password` (client.py:362-369): `hostname=self.printer_ip`, `port=CC2_MQTT_PORT`, `keepalive`, `username=CC2_MQTT_USERNAME` (`"elegoo"`), `password=password`, `identifier` (a unique client id). There is NO `timeout` kwarg.
     - Use **explicit `__aenter__`/`__aexit__`** (NOT `async with`), exactly like `_try_connect_with_password` (client.py:386-388), so that `disconnect()` is the ONLY closer (it nulls `self.mqtt_client` at client.py:481 → no double-`__aexit__`). Wrap the per-password body in `try: ... except (OSError, aiomqtt.MqttError): continue` so a refused/unreachable proxy does NOT crash the flow.
     - On connect success: `self.mqtt_client = client` → `await client.subscribe("elegoo/+/api_status")` → then:
       ```python
       async def _wait() -> str | None:
           async for msg in client.messages:
               parts = msg.topic.split("/")
               if len(parts) == 3 and parts[0] == "elegoo" and parts[2] == "api_status":
                   return parts[1]
               # ignore non-matching topics, keep waiting
           return None  # client disconnected
       try:
           serial = await asyncio.wait_for(_wait(), timeout=timeout)
       except asyncio.TimeoutError:
           serial = None
       ```
     - In a `finally` **outside** the connect attempt, call `await self.disconnect()` (idempotent — it nulls `self.mqtt_client`, so no double-close).
   - On a discovered serial, log at `debug` and `break` out of the password loop; on timeout, log at `info`.
   - If all passwords fail to connect or time out (no serial found), return `None` (NEVER raise).
3. What NOT to change: do not call the registration publish; do not change `connect_printer`; do not start `_mqtt_listener` or call `_handle_message` from this method.

**Steps:**
- [ ] Write failing tests in `custom_components/elegoo_printer/cc2/tests/test_client_factory.py` (inject a `FakeAiomqttClient` via `client_factory` that, on `subscribe`/`messages`, yields an `api_status` message on `elegoo/{sn}/api_status` via `queue_message`):
  - `async_discover_serial` returns the SN when a status message arrives on `elegoo/{sn}/api_status` within the timeout.
  - `async_discover_serial` returns `None` when no message arrives within the timeout (use a short `timeout`, e.g. `0.1`).
  - `async_discover_serial` does NOT publish to any `api_register` topic (assert the fake client's publish log is empty of `api_register`).
  - `async_discover_serial` disconnects after both success and timeout (assert the fake client's `disconnect`/`__aexit__` was called).
  - `async_discover_serial` ignores non-matching topics (a 2-segment topic, or `elegoo/{sn}/other`) and returns `None`.
  - **Connect-failure:** using a `FailingFakeAiomqttClient` (raises `OSError` from `__aenter__`, per `tests/fakes.py`), all passwords fail ⇒ `async_discover_serial` returns `None` and does NOT raise.
- [ ] Run `make test`
  - Did the new tests fail? If they passed unexpectedly, stop and investigate.
- [ ] Implement the method above.
- [ ] Run `make test`
  - Did all tests pass? If not, fix and re-run.
- [ ] Run `make format` and `make lint`
  - Did both succeed? If not, fix and re-run.
- [ ] Commit with message: `feat(cc2): add bounded serial auto-learn via wildcard subscribe (#414)`

**Acceptance criteria:**
- [ ] `ElegooCC2Client.async_discover_serial(timeout=5.0)` returns the SN from an `elegoo/{sn}/api_status` topic, or `None` on timeout.
- [ ] It does NOT register (no `api_register` publish) and always disconnects on exit.
- [ ] `make test`, `make format`, `make lint` all pass.

---

### Task 5: Serial hybrid in config flow (auto-learn + prompt, access-code threaded) + validation errors

**Context:**
When `proxy_host` is set and the serial is unknown, the CC2 flow must acquire the serial before it can register/validate. This task wires the Task 4 auto-learn into the CC2 flow with a user-prompt fallback, and adds a clear "cannot reach proxy" error.

**Access-code threading (critical):** the access code is currently a **local parameter** of `_attempt_cc2_connection` and is NOT stored on the handler or `selected_printer`. If the serial prompt/auto-learn resumes the flow without it, an access-code-protected printer (the common CC2 case) would retry with only the fallback passwords and fail. So the access code must be **stored on the handler** and threaded into the auto-learn and the serial-prompt resume.

Flow: in `_attempt_cc2_connection`, store the access code on the handler, then if `printer.proxy_host` is set and `printer.id` (serial) is empty, call `_async_auto_learn_serial`; on success set `printer.id` and continue to `_async_test_connection`; on `None`, show a new `cc2_serial_input` step. `cc2_serial_input` prompts for the serial, sets `printer.id`, and resumes `_attempt_cc2_connection` with the stored access code.

**Files:**
- Modify: `custom_components/elegoo_printer/config_flow.py`
- Test: `custom_components/elegoo_printer/tests/test_config_flow_cc2_options.py` (add the serial-hybrid tests here)

**What to implement:**
1. At the top of `_attempt_cc2_connection`, store the access code on the handler:
   ```python
   self._cc2_access_code = access_code
   ```
2. In `_attempt_cc2_connection`, after `printer` is built (after the `printer = Printer.from_dict(...)` block) and **before** `_async_test_connection`, insert the serial hybrid:
   ```python
   if printer.proxy_host and not printer.id:
       serial = await self._async_auto_learn_serial(printer)
       if not serial:
           return await self.async_step_cc2_serial_input()
   ```
3. Add the helper (uses the **stored** access code):
   ```python
   async def _async_auto_learn_serial(self, printer: Printer) -> str | None:
       from .cc2.client import ElegooCC2Client  # noqa: PLC0415

       client = ElegooCC2Client(
           printer_ip=printer.connection_host or "",
           serial_number="",
           access_code=self._cc2_access_code,
           logger=LOGGER,
           printer=printer,
       )
       try:
           serial = await client.async_discover_serial()
           if serial:
               printer.id = serial
               printer.connection = serial
               self.selected_printer.id = serial
               self.selected_printer.connection = serial
               LOGGER.info("Auto-learned CC2 serial %s via proxy", serial)
           else:
               LOGGER.info(
                   "Could not auto-learn CC2 serial via proxy %s - prompting user",
                   printer.proxy_host,
               )
           return serial
       finally:
           await client.disconnect()
   ```
4. Add a new step `async_step_cc2_serial_input`:
   - Schema: a single required `CONF_SERIAL` `TextSelector` (TEXT).
   - On submit: if the serial is blank, show a `cc2_serial_required` error; else set `self.selected_printer.id = serial` and `self.selected_printer.connection = serial`, then `return await self._attempt_cc2_connection(access_code=self._cc2_access_code)`.
   - Loop-safe: `_attempt_cc2_connection` only re-enters the serial prompt when `printer.id` is still empty, so a submitted serial breaks the loop.
5. In `_async_test_connection`, the CC2 branch — (a) make the failed-connect error proxy-aware, and (b) for consistency switch the client construction to `connection_host` (it is harmless today because `connect_printer` overrides it, but keep it consistent with `api.py`):
   ```python
   cc2_client = ElegooCC2Client(
       printer_ip=printer_object.connection_host or "",  # was printer_object.ip_address
       serial_number=printer_object.id or "",
       access_code=access_code,
       logger=LOGGER,
       printer=printer_object,
   )
   ```
   And the failed-connect error:
   ```python
   if not connected:
       if printer_object.proxy_host:
           msg = (
               f"Cannot reach the proxy at {printer_object.connection_host}:1883 "
               f"- is it running?"
           )
       else:
           msg = f"Failed to authenticate with CC2 printer {printer_object.name}"
       raise ElegooConfigFlowConnectionError(msg)
   ```
6. In `_attempt_cc2_connection`'s `except ElegooConfigFlowConnectionError` handler (which currently shows the form with `errors={"base": "cc2_authentication_failed"}`), make the proxy case **user-facing** by selecting a distinct error key:
   ```python
   errors={"base": "cc2_proxy_unreachable" if printer.proxy_host else "cc2_authentication_failed"},
   ```
   (`printer` is the local built earlier in `_attempt_cc2_connection`, so `printer.proxy_host` is in scope.)
7. What NOT to change: do not change the non-proxy CC2 flow; do not change the access-code input step's schema; do not register from the auto-learn path.

**Steps:**
- [ ] Write failing tests in `custom_components/elegoo_printer/tests/test_config_flow_cc2_options.py` (mock `ElegooCC2Client.async_discover_serial`; patch `async_set_unique_id`/`_abort_if_unique_id_configured`):
  - `proxy_host` set + `async_discover_serial` returns an SN ⇒ `printer.id` is set and the flow proceeds to `_async_test_connection`/`cc2_options` (no `cc2_serial_input` shown).
  - `proxy_host` set + `async_discover_serial` returns `None` ⇒ the `cc2_serial_input` step is shown.
  - `cc2_serial_input` with a serial submitted ⇒ `printer.id` is set and the flow proceeds to `_async_test_connection` (no loop).
  - `cc2_serial_input` with a blank serial ⇒ `cc2_serial_required` error.
  - **Access-code threading:** a flow that set an access code (via `cc2_access_code_input`) then hit the serial prompt resumes with that access code (assert `self._cc2_access_code` is passed to `_attempt_cc2_connection` / the auto-learn client).
  - `_async_test_connection` with `proxy_host` set and a failed connect ⇒ the "Cannot reach the proxy at {host}:1883" message is raised, and the `except` in `_attempt_cc2_connection` shows the `cc2_proxy_unreachable` error (user-facing, not `cc2_authentication_failed`).
- [ ] Run `make test`
  - Did the new tests fail? If they passed unexpectedly, stop and investigate.
- [ ] Implement the changes above.
- [ ] Run `make test`
  - Did all tests pass? If not, fix and re-run.
- [ ] Run `make format` and `make lint`
  - Did both succeed? If not, fix and re-run.
- [ ] Commit with message: `feat(cc2): serial hybrid (auto-learn + prompt) + proxy validation errors (#414)`

**Acceptance criteria:**
- [ ] When `proxy_host` is set and the serial is unknown, the flow auto-learns the serial; on timeout it prompts the user; a submitted serial breaks the loop.
- [ ] The access code is stored on the handler and threaded into the auto-learn and the serial-prompt resume.
- [ ] A failed connect with `proxy_host` set raises a "Cannot reach the proxy at {host}:1883" error.
- [ ] The non-proxy CC2 flow is unchanged.
- [ ] `make test`, `make format`, `make lint` all pass.

---

### Task 6: Translations

**Context:**
Tasks 3 and 5 introduce user-facing strings that must exist in `translations/en.json` or they render blank/ugly: a new config step `cc2_serial_input`, a new error key `cc2_serial_required`, and the `proxy_host` field label (in `manual_ip` and `cc2_options`).

**Files:**
- Modify: `custom_components/elegoo_printer/translations/en.json`

**What to implement:**
1. In `config.step`, add `cc2_serial_input` with a `title` (e.g. "Serial number") and `description` (e.g. "Enter the printer's serial number (shown on the label under the printer).").
2. In `config.error`, add `cc2_serial_required` (e.g. "Serial number is required").
3. In `config.step.manual_ip` and `config.step.cc2_options`, add a `data.proxy_host` label (e.g. "Proxy host") and `data_description.proxy_host` (e.g. "Optional: host of a forward proxy to connect through (leave blank to connect directly).").
4. In `config.error`, add `cc2_proxy_unreachable` (e.g. "Cannot reach the proxy — is it running?").
4. Check the existing `en.json` structure for the exact nesting (some steps nest `data`/`data_description` under the step key) and follow it. Other locales fall back to English — no need to edit them.
5. What NOT to change: do not alter existing translations.

**Steps:**
- [ ] Inspect `custom_components/elegoo_printer/translations/en.json` to confirm the exact structure (step/error/data nesting).
- [ ] Add the strings above.
- [ ] Run `make test` (a translation-structure test may exist; if so, ensure it passes).
- [ ] Run `make format` and `make lint`.
- [ ] Commit with message: `feat(cc2): translations for proxy_host field + serial prompt (#414)`

**Acceptance criteria:**
- [ ] `en.json` has `config.step.cc2_serial_input`, `config.error.cc2_serial_required`, `config.error.cc2_proxy_unreachable`, and `data.proxy_host` labels for `manual_ip` and `cc2_options`.
- [ ] `make test`, `make format`, `make lint` all pass.

---

## Follow-ups (NOT in this plan)
- Mux (N→1 connection-count reduction) — a separate, larger project.
- Non-standard port support for `proxy_host`.
- Runtime serial re-learning on reconnect (setup-only here).
- `gcode_proxy_url` — untouched.

## Notes
- Research source: `docs/research/issue-414-cc2-proxy-connection.md`.
- Terminology: `proxy_host` = a **forward** proxy to connect through (HA dials it). Distinct from CC1 `proxy_enabled` (inverted — the printer dials into HA's `ElegooPrinterServer`) and the passive `gcode_proxy_url` (g-code capture API).
