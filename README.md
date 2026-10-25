# Elegoo Printers for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)
![GitHub stars](https://img.shields.io/github/stars/danielcherubini/elegoo-homeassistant)
![GitHub issues](https://img.shields.io/github/issues/danielcherubini/elegoo-homeassistant)

Bring your Elegoo 3D printers into Home Assistant! This integration allows you to monitor status, view live print thumbnails, and control your printers directly from your smart home dashboard.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=danielcherubini&repository=elegoo-homeassistant&category=Integration)

<img width="1000" height="auto" alt="image" src="https://github.com/user-attachments/assets/d2010a5d-d9f2-473c-8c6c-60e64bb43f97" />

## Index

- [Features](#-features)
- [Local Proxy Server](#️-local-proxy-server)
- [Supported Printers](#️-supported-printers)
- [Installation](#️-installation)
- [Configuration](#-configuration)
- [Services](#️-services)
- [Entities](#-entities)
- [Automation Blueprints](#-automation-blueprints)
- [Contributing](#️-contributing)

---

## ✨ Features

- **Broad Printer Support:** Designed for the ever-expanding lineup of Elegoo resin and FDM printers.
- **Comprehensive Sensor Data:** Exposes a wide range of printer attributes and real-time status sensors.
- **Live Camera:** Monitor your print from anywhere.
- **Print Thumbnails:** See an image of what you are currently printing directly in Home Assistant.
- **Direct Printer Control:** Stop and pause prints, control temperatures, and adjust speeds.
- **Local Proxy Server:** An optional built-in proxy that serves several printers from one address.
- **Automation Blueprints:** Includes a ready-to-use blueprint for print progress notifications.

---

## 🛰️ Local Proxy Server

Modern Elegoo printers often have a built-in limit of 4 simultaneous connections. Since the video stream consumes one of these by itself, users can easily hit this limit.

The optional proxy server lets several printers be served from one address: it runs as a single server with all configured printers registered against it, so a client makes one connection to the proxy and reaches any of them by id, instead of needing a separate address and port per printer. It also rewrites the video URL the printer advertises, so streams can be pulled through the proxy rather than fetched from each printer directly.

**It does not reduce how many connections a printer sees.** Each client connection is passed through to the printer as its own upstream connection, so the printer counts the same clients as before — they simply arrive from the proxy's address. If the connection limit is what forces you to power-cycle the printer, the proxy is not the fix; a camera-connection multiplexer would be, and that is tracked in [#414](https://github.com/danielcherubini/elegoo-homeassistant/issues/414).

➡️ **[Read more and join the discussion here](https://github.com/danielcherubini/elegoo-homeassistant/discussions/95)**

---

## 🖨️ Supported Printers

Elegoo releases new models frequently, and this integration is designed to be as "future-proof" as possible. Instead of worrying about specific version numbers, look at the **Protocol** your printer uses.

> **Don't see your specific model? Try it anyway!**
> If your printer uses the SDCP protocol (which almost all modern networked Elegoo printers do), there is a very high chance it will work perfectly. This list is **non-exhaustive** and grows with the community.

### ✅ Modern Printers (SDCP over WebSocket)
Most newer models utilize WebSockets for communication. This integration offers full support for:

* **Mars Range** (e.g., Mars 5, 5 Ultra)
* **Saturn Range** (e.g., Saturn 4, 4 Ultra)
* **Centauri Range** (e.g., Centauri Carbon)

### 🧪 Legacy Printers (SDCP over MQTT)
Older networked models typically use MQTT. These are supported in **Beta**, meaning most features work, though some metadata (like start/end times or cover images) may be missing due to the limitations of the older protocol.

* **Saturn Range** (e.g., Saturn 2, 3 Ultra)
* **Mars Range** (e.g., Mars 3, 4 Ultra)
* **Jupiter Range**

**Known Limitations for MQTT:**
* `Begin Time`, `End Time`, and `Cover Image` sensors will show "Unknown."
* Standard sensors (status, layers, temps, progress) function normally.

### 🆕 CC2 FDM Printers (LAN-Only Connection)
CC2 (Centauri Carbon 2) printers use an inverted MQTT architecture where the printer runs its own broker. **This integration supports LOCAL network connections only.**

**Supported Models:**
* Centauri Carbon 2
* Elegoo Cura (some models)

**⚠️ CRITICAL: LAN-Only Mode Required**

CC2 printers **MUST** be configured for LAN-Only mode:

1. On your printer: **Settings → Network → LAN Only Mode**
2. **Enable** LAN Only Mode
3. Save and restart if prompted
4. Ensure printer and Home Assistant are on the **same network/subnet**

**Cloud mode is NOT supported.** Cloud connectivity requires Elegoo's OAuth2 authentication and cloud relay services, which are not currently implemented. This integration connects directly to your printer over your local network only.

**Network Requirements:**
- Printer and Home Assistant must be on the same network/VLAN/subnet
- For containerized Home Assistant (Docker/Kubernetes): Use host networking or proper network bridging
- Port 1883 (MQTT) must be accessible between HA and printer

**Optional GCode capture proxy:** 

The printers report only a total filament usage value over their normal
telemetry (MQTT for CC2, SDCP for CC1) — the per-slot breakdown (how
much each Canvas spool contributed) exists only inside the G-code file,
and there is no way to retrieve a file from the printer after it has
been sent.

The [elegoo-printer-proxy](https://github.com/lantern-eight/elegoo-printer-proxy)
sits between ElegooSlicer and the printer, transparently capturing
every G-code file at upload time and parsing out per-slot filament
data. It supports both the CC2 and the CC1 (Centauri Carbon). Configure
the proxy URL in the integration options
(Settings → Integrations → Elegoo → Configure).

With the proxy configured, additional sensors are created: per-slot
A1–A4 grams, volume, length, and color, plus total filament cost
and change count when the slicer provides them. See
[SPOOLMAN.md](SPOOLMAN.md) for automations that push this data to
Spoolman for spool weight tracking.

**Optional: connect a Centauri Carbon 2 *through* the proxy:** the capture
option above only borrows the proxy's parsed G-code data — Home Assistant
still talks to the printer directly. If you would rather the integration use
the proxy for **everything**, enter its address in the **Proxy Host** field
instead (when adding the printer, or later in the CC2 options).

Then MQTT control (1883), G-code uploads (80) and the camera (8080) all go to
the proxy host rather than to the printer. Use this when Home Assistant and the
printer are on different networks or VLANs and only the proxy can reach the
printer, or when you would rather the camera came from the proxy too instead of
needing a separate MJPEG entity alongside the integration's own.

It is worth being clear about what this does *not* do. The proxy forwards each
port as a plain TCP pass-through, one printer connection per client connection,
so it does not reduce how many connections or MQTT sessions the printer sees —
Home Assistant is still a separate client, arriving from the proxy's address
instead of yours. If the printer's connection limit is what forces you to
power-cycle it, routing Home Assistant through the proxy will not fix that; a
camera-connection multiplexer in the proxy would, and that is being looked at
upstream. See
[#414](https://github.com/danielcherubini/elegoo-homeassistant/issues/414) for
the current state of that.

A few things to expect:

- The proxy does not answer printer discovery, so discovery is skipped and the
  printer is set up as a Centauri Carbon 2 directly. Its name and model are
  read from the printer itself once connected.
- The serial number is normally found by discovery. Without it, the
  integration listens on the proxy's MQTT for the printer's own status
  messages and learns it; if the printer pushes nothing within a few seconds,
  it asks you to type it (label under the printer, or Settings → Network).
- Leave **Proxy Host** blank to connect directly, exactly as before. It is
  separate from the **GCode proxy URL** field above, and from the CC1 "local
  proxy server".
- This is a *forward* proxy — Home Assistant dials it, and it must forward the
  printer's own ports. A field on a printer that is not a CC2 has no effect.

See [CC2 Protocol Documentation](docs/CC2_PROTOCOL.md) for technical details.

---

## ⚙️ Installation

The recommended way to install this integration is through the [Home Assistant Community Store (HACS)](https://hacs.xyz/).

1. In HACS, go to **Integrations** and click the **"+"** button.
2. Search for **"Elegoo Printers"** and select it.
3. Click **"Download"** and **restart Home Assistant**.

---

## 🔧 Configuration

1. Go to **Settings** > **Devices & Services**.
2. Click **"Add Integration"** and search for **"Elegoo Printers"**.
3. The integration will attempt to **auto-discover** printers on your network.
4. If no printer is found, select **"Configure manually"** and enter your printer's IP address or hostname.

**Note:** If **auto-discovery** didn't work, you may need some [advanced network setup](https://github.com/danielcherubini/elegoo-homeassistant/wiki/Connecting-Elegoo-Printers-to-Home-Assistant-Across-Different-Networks).

### Printer is often switched off

A printer that is normally powered down — off at the wall, or switched by a **smart plug** when it isn't printing — used to leave the integration stuck on **"Failed setup, will retry"** until it came back. Every printer's **options** now carry a **"Printer is often switched off"** checkbox for exactly that case. Tick it and setup finishes even when the printer can't be reached: the entities stay **"Unavailable"** rather than reporting a failed setup, and they return on their own **within a minute or so** (a couple of the 30-second polls) once the printer is powered on. It applies to **every supported printer type** (WebSocket, MQTT and Centauri Carbon 2), and you can tick it **while the printer is switched off** — no need to power it on just to change the setting. Saving the options reloads the entry straight away.

**The trade-off:** with this option on, a **wrong IP address or an unreachable printer looks exactly like a switched-off one**. If a printer never comes back after you power it on, search the Home Assistant log for **`without a connection`** — the integration logs that line once, at setup, and it is your only clue that the address may simply be wrong.

### ⚠️ Firmware v1.1.29 Bug Notice
Elegoo firmware **v1.1.29** contains a bug preventing remote control of lights and temperatures **while a print is in progress**. This is a firmware limitation; if you require these features during prints, consider using v1.1.25 if available for your model.

---

## 🛠️ Services

### `update_ip`
When a printer's IP changes (e.g. via DHCP), there is no need to delete and re-add the integration. The `update_ip` service updates the printer's IP address stored in a config entry and reloads that entry, so the fix takes **seconds** instead of a full re-setup.

- The `entry_id` field is a **dropdown**: pick which printer, then type the new address in the `ip_address` field.
- Two ways to invoke it:
  - **UI:** **Developer Tools** → **Actions** → `elegoo_printer.update_ip` — the config-entry dropdown and IP field appear there, and a "return response" option is available through the service response.
  - **Automation / script:** call the service with the config entry's UUID and the new address in the data:
    ```yaml
    action: elegoo_printer.update_ip
    data:
      entry_id: <config entry UUID>
      ip_address: "192.168.1.3"
    ```
- **Watcher scripts:** an external IP-detection script (or a router webhook relay) can call the service the moment the printer's new address is known — **no user intervention** required.

**Known behavior:**
- On a **still-connected (LOADED) entry** the reload effectively runs twice — once triggered by the data change and once explicitly. Home Assistant serializes both, but that means a **second full teardown/reconnect** of the printer connection. On an entry whose printer is unreachable at the old IP (the common DHCP-move case) it is exactly **one reload**.
- If the printer is **unreachable at the new address**, the entry ends in `SETUP_ERROR` / `SETUP_RETRY` and the service reports failure in its response. **Check the response (or the logs) before assuming success.**

### `start_print` (Centauri Carbon 2 only)
Starts a G-code file that is **already in the printer's local storage** - the "print this again" the Elegoo app offers, which is lost in LAN Only mode. Uploading a file is not part of this service; the slicer does that.

```yaml
action: elegoo_printer.start_print
data:
  entry_id: <config entry UUID>
  filename: "benchy.gcode"   # exact name from the printer's file list
  tray: 2                    # optional, Canvas tray 0-3 (A1-A4), mapped to every G-code tool the file uses
  bed_leveling: true         # optional, default true: auto bed leveling first (~3 min), as the slicer does
```

- The response says whether the printer accepted the job: `error_code` 1009 means it is busy.
- `tray` is validated by Home Assistant, **not by the printer** - an out-of-range tray is acknowledged with `error_code` 0 and silently printed from tray 0. The tray is mapped to **every** G-code tool the file's listing reports (its `color_map`); a file whose tool list the printer never reported maps its first tool. Whether the printer honours a complete multi-tool map is **not yet confirmed on hardware** - see #435, which stays open until someone can watch which tray a multi-tool print actually consumes.
- `bed_leveling` maps to the protocol's `printer_check`, which ElegooSlicer sends on every job - so it defaults to on. `false` is sent as `false`, which is how the slicer's own unchecked box does it and the only way to stop the printer levelling: leaving the key out lets it decide, and it has been observed levelling for 196 s even when the previous print used the same bed temperature.
- Not available for the first-generation Centauri Carbon or resin printers, where the equivalent SDCP command crashed the printer (#297).

### `upload_gcode` (Centauri Carbon 2 only)
Uploads a G-code file **from the device you are using Home Assistant on** to the printer's local storage, and optionally starts it. In **Developer Tools → Actions** the `file` field opens a file picker (filtered to `.gcode`); the file goes to Home Assistant first and from there to the printer over the LAN, the way ElegooSlicer sends it (`PUT /upload` in 1 MB chunks). It then appears in the printer's file list and can be started with `start_print` or from the printer's screen.

```yaml
action: elegoo_printer.upload_gcode
data:
  entry_id: <config entry UUID>
  file: <file_id from the file picker>
  start: true               # optional, default false
  tray: 2                   # optional, with start
  bed_leveling: true        # optional, with start, default true
```

- Up to 100 MB (Home Assistant's upload limit). The file keeps its name; an existing file of the same name is replaced.
- The upload is not retried: if the printer answers "busy" (HTTP 429) or rejects a chunk, the service reports it and stops - the printer does not list a failed upload.
- In an automation the `file` field needs a file id from Home Assistant's upload API; the field is meant for the UI.

### `cleanup_files` (Centauri Carbon 2 only)
Deletes old files from the printer's local storage, selected by **age** and by **name pattern**, so a daily automation can keep the printer's storage from filling up (#432). The `include` / `exclude` vocabulary and the `all` / `nothing` starting defaults are the reporter's from that issue.

```yaml
action: elegoo_printer.cleanup_files
data:
  entry_id: <config entry UUID>
  older_than_days: 60      # optional, default 60, whole days only
  include: "ECC*"          # optional, default every file
  exclude: "Keep*"         # optional, default nothing
  dry_run: true            # optional, default TRUE: report, delete nothing
response_variable: cleanup
```

- **Run the dry run first.** `dry_run` defaults to **on**, so the first call only answers `would_delete` and touches nothing. It is also the only mode that currently does anything: measured on firmware 02.01.01.07 the printer **refuses every delete** — a real run reports each selected file in `failed` as `printer refused the delete (error_code 1003)` and answers `success: false`, and the file stays on the printer. Nothing was deleted, and nothing was destroyed either: the service checks the file list afterwards rather than trusting the printer, so it reports the refusal instead of claiming a deletion. Switch the dry run off only after reading the list - the **Print file** select on the device page is the same listing, refreshed every ten minutes, so you can eyeball it too.
- **`exclude` wins over `include`.** A file matching both is kept. Patterns are case-sensitive, and the same on every machine - a pattern that keeps `Keep_this.gcode` keeps it whether Home Assistant runs on Linux or Windows.
- **A file with no creation date is never deleted, and the file being printed is never deleted.** The second half matters when a nightly job runs while something is printing. The first half has a case you will hit: **a printer whose clock has never been set reports a creation date in 1970**, and this service treats any creation date before 2000 as "no date" and leaves the file alone. That is the same rule #426 had to apply to the time sensors, applied here where getting it wrong would delete a file instead of misreading a sensor - so a printer that stops reporting dates degrades to *deleting nothing*, never to *deleting everything*.
- **Leaving `include` blank matches nothing, so nothing is deleted** - omit the field to mean "every file". A run in that state still reports `success: true` (a nightly job that finds nothing to do is not an error) but says in its message that no listed file matched the pattern, so an automation that quietly stops deleting is visible in the notification instead of invisible. The blank field is deliberately **not** read as "everything": a template that renders to empty would otherwise delete every old file on the printer on a call that named nothing.
- **Deletion is confirmed by re-reading the file list, not by trusting the printer's reply.** A reply alone is not proof: a printer can answer a delete and still list the file. So a file the printer accepted and still lists comes back in `failed` as `still listed after delete`, and `success` is false unless *every* selected file was confirmed gone. `success: false` therefore means "the printer is not in the state you asked for" - which is what an automation should alert on. A run that selected nothing is a **successful** run.
- **Two response shapes.** A run that reached the printer carries `success`, `dry_run`, `deleted`, `failed` (name → why it stayed), `skipped` (name → why it was not a candidate: `younger_than_limit`, `no_creation_date`, `excluded`, `not_included`, `currently_printing`), `would_delete` and a `message` — or an `error` instead of a message when it reached the printer but could not act. A call that never got there — unknown or unloaded printer, or one that is not a Centauri Carbon 2 — carries only `success: false` and an `error`, with no lists at all.
- **Videos are not on the file list, so this service cannot reach them.** #432 asked for "both gcode and video files", but that list is the only inventory the printer offers, and on firmware 02.01.01.07 the list does not contain the timelapse videos the display shows under its camera tab - measured for `local`, `sdcard` and `udisk` alike (the storage-media vocabulary is itself unsettled - the protocol's SDK-derived table spells them `sd-card` and `u-disk`, and both spellings are being probed), so those videos are display-only (or USB export) and no pattern can select them. The camera itself is a live MJPEG stream rather than a file. There is deliberately **no extension filter**, so a list that *does* contain video files matches and deletes them like any other file; that stays the design for firmware that ever reports them.
- Deletes are sent one at a time, each waiting up to 10 s for the printer, so a printer full of old files can take a while. Fine behind a 03:30 trigger; not something to put behind a UI button.

A daily cleanup that tells you what it removed:

```yaml
alias: Nightly printer file cleanup
triggers:
  - trigger: time
    at: "03:30"
actions:
  - action: elegoo_printer.cleanup_files
    data:
      entry_id: <config entry UUID>
      older_than_days: 60
      dry_run: false
    response_variable: cleanup
  - action: notify.mobile_app_your_phone
    data:
      title: Printer cleanup
      message: >-
        Deleted {{ cleanup.deleted | default([]) | length }} file(s),
        refused {{ cleanup.failed | default([]) | length }}.
        {{ cleanup.message | default(cleanup.error, true) }}
```

The response dict lands in `response_variable` **itself**: `cleanup.deleted`, not `cleanup.response.deleted` — reading a `.response` segment raises a template error, so the notification step fails instead of arriving.

---

## 📊 Entities
The integration provides a comprehensive set of entities including **Live Camera**, **Print Thumbnails**, **Control Buttons** (Stop/Pause/Resume), and a full suite of **Sensors** (Progress, Temps, Layers, Z-Height, etc.).

**Firmware update (SDCP V3 printers):** a **Firmware** update entity shows new printer firmware in **Settings → Updates**, with Elegoo's changelog as release notes. It is read-only — SDCP has no command to install firmware, so install it from the printer's screen.

**Filament / Canvas A1–A4 sensors (CC1 and CC2):** Gcode file-detail and optional proxy sensors are created at setup time (proxy extras are only added when a proxy URL is configured). They stay **available** between prints; when there is no current job data they report **unknown** rather than becoming **unavailable**, so automations and history are not disrupted each time a print ends.

**Print file (CC2):** a select listing the G-code files in the printer's local storage, read over MQTT (method 1044) every ten minutes and on the **Refresh File List** button. Choosing a file does not start it: press **Print Selected File**, which prints from the tray chosen in **Print Tray** (Automatic or A1-A4, Canvas printers only) with bed leveling on, as in the slicer - or pass the select's state to `start_print` as `filename` to skip leveling.

## 🤖 Automation Blueprints
Includes a blueprint for mobile notifications. [Import it here.](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/danielcherubini/elegoo-homeassistant/blob/main/blueprints/automation/elegoo_printer/elegoo_printer_progress.yaml)

## 🧵 Spoolman Integration
Compatible with
[Spoolman Home Assistant](https://github.com/Disane87/spoolman-homeassistant).
Some approaches are available depending on your printer and
firmware: automated per-slot tracking for CC2 via the gcode capture
proxy, live extrusion tracking for CC1 with OpenCentauri firmware,
or a filename-template workaround for CC1 on stock firmware. See
[SPOOLMAN.md](SPOOLMAN.md) for setup and example automations.

---

## ❤️ Contributing

If you've tested a new model not mentioned here, or if you've found a way to improve MQTT support, please [open an issue](https://github.com/danielcherubini/elegoo-homeassistant/issues) or a PR!

### Development Setup

Want to contribute code or help debug printer protocols? See the **[Development Guide](DEVELOPMENT.md)** for detailed setup instructions covering:

- Linux/macOS setup
- Windows setup (with troubleshooting for common issues)
- Dev Container setup (VS Code + Docker)
- Running the debug script to capture printer data
