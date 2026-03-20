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
- [Entities](#-entities)
- [Automation Blueprints](#automation-blueprints)
- [Contributing](#️-contributing)

---

## ✨ Features

- **Broad Printer Support:** Designed for the ever-expanding lineup of Elegoo resin and FDM printers.
- **Comprehensive Sensor Data:** Exposes a wide range of printer attributes and real-time status sensors.
- **Live Camera:** Monitor your print from anywhere.
- **Print Thumbnails:** See an image of what you are currently printing directly in Home Assistant.
- **Direct Printer Control:** Stop and pause prints, control temperatures, and adjust speeds.
- **Local Proxy Server:** An optional built-in proxy to bypass printer connection limits.
- **Automation Blueprints:** Includes a ready-to-use blueprint for print progress notifications.

---

## 🛰️ Local Proxy Server

Modern Elegoo printers often have a built-in limit of 4 simultaneous connections. Since the video stream consumes one of these by itself, users can easily hit this limit. 

The optional proxy server acts as a single gateway, routing all commands and the video stream through one stable connection, effectively bypassing these limits.

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

As of early 2026, Elegoo has not yet added per-slot filament data to the printer's MQTT status response.
For Centauri Carbon 2 printers, you can use the [cc2-gcode-capture-proxy](https://github.com/lantern-eight/cc2-gcode-capture-proxy) which will capture the GCode file and provide the per-slot filament data to the integration.

In integration options you can set the cc2-gcode-capture-proxy URL that you setup on your local network.

With the proxy configured, additional sensors are created: per-slot A1–A4 grams, volume, and length, plus total filament cost and change count when the slicer provides them.

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
4. If no printer is found, select **"Configure manually"** and enter your printer's IP address.

### ⚠️ Firmware v1.1.29 Bug Notice
Elegoo firmware **v1.1.29** contains a bug preventing remote control of lights and temperatures **while a print is in progress**. This is a firmware limitation; if you require these features during prints, consider using v1.1.25 if available for your model.

---

## 📊 Entities
The integration provides a comprehensive set of entities including **Live Camera**, **Print Thumbnails**, **Control Buttons** (Stop/Pause/Resume), and a full suite of **Sensors** (Progress, Temps, Layers, Z-Height, etc.).

## 🤖 Automation Blueprints
Includes a blueprint for mobile notifications. [Import it here.](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/danielcherubini/elegoo-homeassistant/blob/main/blueprints/automation/elegoo_printer/elegoo_printer_progress.yaml)

## 🧵 Spoolman Integration
Compatible with [Spoolman Home Assistant](https://github.com/Disane87/spoolman-homeassistant). See [SPOOLMAN.md](SPOOLMAN.md) for setup.

---

## ❤️ Contributing

If you've tested a new model not mentioned here, or if you've found a way to improve MQTT support, please [open an issue](https://github.com/danielcherubini/elegoo-homeassistant/issues) or a PR!

### Development Setup

Want to contribute code or help debug printer protocols? See the **[Development Guide](DEVELOPMENT.md)** for detailed setup instructions covering:

- Linux/macOS setup
- Windows setup (with troubleshooting for common issues)
- Dev Container setup (VS Code + Docker)
- Running the debug script to capture printer data
