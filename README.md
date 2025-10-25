# Elegoo Printers for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)
![GitHub stars](https://img.shields.io/github/stars/danielcherubini/elegoo-homeassistant)
![GitHub issues](https://img.shields.io/github/issues/danielcherubini/elegoo-homeassistant)

Bring your Elegoo 3D printers into Home Assistant! This integration allows you to monitor status, view live print thumbnails, and control your printers directly from your smart home dashboard.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=danielcherubini&repository=elegoo-homeassistant&category=Integration)

<img width="1000" height="auto" alt="image" src="https://github.com/user-attachments/assets/d2010a5d-d9f2-473c-8c6c-60e64bb43f97" />

## Index

* [Features](#-features)
* [Local Proxy Server](#️-local-proxy-server)
* [Supported Printers](#️-supported-printers)
* [Installation](#️-installation)
* [Configuration](#-configuration)
* [Entities](#-entities)
* [Automation Blueprints](#automation-blueprints)
* [Contributing](#️-contributing)

---

## ✨ Features

* **Broad Printer Support:** Compatible with a growing list of Elegoo resin and FDM printers.
* **Comprehensive Sensor Data:** Exposes a wide range of printer attributes and real-time status sensors.
* **Live Camera:** Monitor your print from anywhere.
* **Print Thumbnails:** See an image of what you are currently printing directly in Home Assistant.
* **Direct Printer Control:** Stop and pause prints, control temperatures, and adjust speeds.
* **Local Proxy Server:** An optional built-in proxy to bypass printer connection limits, allowing multiple clients (slicers, monitoring tools) to connect simultaneously.
* **Automation Blueprints:** Includes a ready-to-use blueprint for print progress notifications.

---

## 🛰️ Local Proxy Server

Printers like the Elegoo Centauri Carbon have a built-in limit of 4 simultaneous connections. Since the video stream consumes one of these connections by itself, users can easily hit this limit, resulting in "connection limit reached" errors.

The optional proxy server solves this by acting as a single gateway. It routes all commands, file uploads, and **even the video stream** through just one stable connection to the printer, effectively bypassing the limit and preventing connection conflicts.

You can enable the proxy server during the initial setup of the integration or at any time afterward by clicking **"Configure"** on the integration card.

➡️ **[Read more and join the discussion here](https://github.com/danielcherubini/elegoo-homeassistant/discussions/95)**

---

## 🖨️ Supported Printers

This integration is designed to work with Elegoo printers that use the `SDCP` protocol. The following models have been tested and are known to work:

### ✅ Fully Supported Printers (WebSocket/SDCP)

#### Resin Printers

* Mars 5
* Mars 5 Ultra
* Saturn 3
* Saturn 3 Ultra
* Saturn 4
* Saturn 4 Ultra

#### FDM Printers

* Centauri Carbon

### 🧪 Beta Support - Legacy MQTT Printers

The following older models use the MQTT protocol and have **experimental/beta support**. Most features work, but some data may be missing or incomplete due to protocol differences:

#### Resin Printers

* Saturn 2
* Saturn 2 8K
* Mars 3
* Mars 3 Pro
* Mars 4
* Mars 4 DLP
* Mars 4 Max
* Mars 4 Ultra

**Note:** MQTT printer support is in early stages. If you experience any issues or notice missing/incorrect data, please [open a GitHub issue](https://github.com/danielcherubini/elegoo-homeassistant/issues) with details about your printer model and the problem you're encountering. Community contributions to improve MQTT support are greatly appreciated!

**Known Limitations for MQTT Printers:**
- Begin Time, End Time, and Cover Image sensors will show "Unknown" (this data is not available via MQTT protocol)
- All other sensors (status, layers, temperatures, progress, etc.) work normally

---

If your printer isn't listed but uses the `SDCP` or `MQTT` protocol, it may still work. Please [open an issue](https://github.com/danielcherubini/elegoo-homeassistant/issues) to let us know!

---

## ⚙️ Installation

The recommended way to install this integration is through the [Home Assistant Community Store (HACS)](https://hacs.xyz/).

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=danielcherubini&repository=elegoo-homeassistant&category=Integration)

1.  In HACS, go to **Integrations** and click the **"+" (Explore & Download Repositories)** button.
2.  Search for **"Elegoo Printers"** and select it.
3.  Click **"Download"** and follow the prompts.
4.  After downloading, **restart Home Assistant**.

---

## 🔧 Configuration

Once installed, you can add your printer to Home Assistant:

1.  Go to **Settings** > **Devices & Services**.
2.  Click **"Add Integration"** and search for **"Elegoo Printers"**.
3.  The integration will attempt to auto-discover printers on your network. If your printer is found, select it from the list.
4.  If no printer is discovered, you can select **"Configure manually"** and enter your printer's IP address.
5.  Follow the on-screen prompts to complete the setup. You will be able to enable the **Local Proxy Server** during this step.

### ⚠️ Firmware v1.1.29 Bug Notice

Please be aware that the Elegoo firmware **version 1.1.29** has a bug that prevents remote control of lights, temperatures, and fans **while a print is in progress**. This is a limitation within the printer's firmware itself. Previous versions (like v1.1.25) do not have this bug.

---

## 📊 Entities

The integration creates the following entities for your printer.

### Camera

| Entity | Description |
| --- | --- |
| `Camera` | Displays a live video of the current print. |

### Image

| Entity | Description |
| --- | --- |
| `Thumbnail` | Displays a thumbnail of the current print. |

### Buttons

| Button | Description |
| --- | --- |
| `Stop Print` | Stops the current print job. |
| `Pause Print` | Pauses the current print job. |
| `Resume Print` | Resumes a paused print job. |

### Select

| Select | Description |
| --- | --- |
| `Print Speed` | Allows you to change the print speed (e.g., Standard, Silent). |

### Number

| Number | Description | Unit |
| --- | --- | --- |
| `Target Bed Temp` | Sets the target temperature for the heated bed. | `°C` |
| `Target Nozzle Temp`| Sets the target temperature for the nozzle. | `°C` |

### Sensors

| Sensor | Description | Unit |
| --- | --- | --- |
| `Status` | The current status of the printer. | |
| `File Name` | The name of the file currently being printed. | |
| `Print Progress` | The completion percentage of the current print.| `%` |
| `Time Remaining` | Estimated time remaining for the current print.| `minutes`|
| `Time Elapsed` | Time elapsed for the current print. | `minutes`|
| `Layer` | The current layer being printed. | |
| `Total Layers` | The total number of layers in the print job. | |
| `Nozzle Temp` | The current temperature of the nozzle. | `°C` |
| `Bed Temp` | The current temperature of the heated bed. | `°C` |
| `Fan Speed` | The current speed of the cooling fan. | `%` |
| `Print Speed` | The current print speed multiplier. | `%` |
| `Print Error Reason`| The reason for the last print error. | |
| `Firmware Version` | The firmware version of the printer. | |
| `Z-Height` | The current height of the Z-axis. | `mm` |

---

## Automation Blueprints

This integration includes a blueprint to send notifications to your mobile device with the progress of your prints.

<img width="400" height="auto" alt="image" src="https://github.com/user-attachments/assets/f131475a-6d12-44c6-8572-852f159d0045" />

[Read more about it here](https://github.com/danielcherubini/elegoo-homeassistant/discussions/180)

[![Open your Home Assistant instance and import the blueprint.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/danielcherubini/elegoo-homeassistant/blob/main/blueprints/automation/elegoo_printer/elegoo_printer_progress.yaml)

---

## ❤️ Contributing

Contributions are welcome! If you'd like to help, please feel free to submit a pull request or open an issue to discuss a new feature or bug.
