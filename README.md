# Elegoo Printer Integration for Home Assistant

This Home Assistant integration allows you to monitor the status and attributes of your Elegoo resin and FDM 3D printers, bringing valuable information directly into your smart home dashboard.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=danielcherubini&repository=elegoo-homeassistant&category=Integration)

## Features

- **Broad Printer Support:** Compatible with a growing list of Elegoo resin and FDM printers.
- **Comprehensive Sensor Data:** Exposes a wide range of printer attributes and real-time status sensors.
- **Live Print Thumbnails:** See an image of what you are currently printing directly in Home Assistant.
- **Live Video Stream (Coming Soon):** Keep an eye on your prints with a live video feed.

## Supported Printers

This integration supports the following Elegoo printer models:

### Resin Printers
- Mars 5
- Mars 5 Ultra
- Saturn 4
- Saturn 4 Ultra
- Saturn 4 Ultra 16k

### FDM Printers
- Centauri
- Centauri Carbon

## Sensors

The integration exposes the following sensors, which may vary depending on your printer model.

### General Printer Sensors
These sensors provide live updates on the printer's current operation and are available for both resin and FDM models.

- **File Name:** The name of the file you're printing.
- **Print Status:** What the printer is currently doing (e.g., "Printing," "Idle," "Error").
- **Print Error:** If there's a problem, this will show the type of error.
- **Total Print Time:** The total amount of time this printer has been printing, ever. (Unit: Milliseconds, displayed in Hours)
- **Current Print Time:** How long the current print job has been running. (Unit: Milliseconds, displayed in Hours)
- **Remaining Print Time:** The estimated time left for the current print job to finish. (Unit: Milliseconds, displayed in Hours)
- **Total Layers:** The total number of layers in the print job.
- **Current Layer:** Which layer is being printed right now.
- **Remaining Layers:** How many layers are left to print.
- **Print Completion Percentage:** How much of the print job is finished. (Unit: %)
- **Connected Video Streams:** How many video streams are currently connected to the printer.
- **Maximum Video Streams:** The maximum number of video streams you can connect at the same time.

### Resin Printer Specific Sensors
These sensors are specific to resin printers.

- **UV LED Temperature:** The current temperature of the UV LED. (Unit: °C)
- **Maximum UV LED Temperature:** The highest temperature the UV LED is allowed to reach. (Unit: °C)
- **Release Film Status:** The current status of the release film.
- **Maximum Release Film Usage:** Shows the maximum lifespan or usage limit for the release film.

## Configuration

Currently, this integration needs the IP Address of your printer. In the future, it will automatically discover it on your network. When you add the integration, it will ask for your printer's IP Address.

## 🛰️ Local Proxy Server

This integration includes an optional local proxy server that allows multiple clients (e.g., slicers, monitoring tools, other devices) to communicate with your Elegoo printer simultaneously.

### The Problem It Solves

Printers like the Elegoo Centauri Carbon have a built-in limit of 4 simultaneous connections. Since the video stream consumes one of these connections by itself, users can easily hit this limit, resulting in "connection limit reached" errors from other applications.

The proxy server solves this by acting as a single gateway. It routes all commands, file uploads, and the video stream through just one stable connection to the printer, effectively bypassing the limit and preventing connection conflicts.

### How to Enable It

You can enable the proxy server during the initial setup of the integration or at any time afterward by clicking **"Configure"** on the integration card in Home Assistant.

For more details, use cases, and to join the community conversation about this feature, please see our post on GitHub Discussions.

➡️ **[Read more and join the discussion here](https://github.com/danielcherubini/elegoo-homeassistant/discussions/185)**

## Future Updates

This integration is actively being developed. Future updates may include:
- **Expanded Model Support:** Compatibility with more older Elegoo printer models.

Stay tuned for updates and feel free to contribute with feature requests and bug reports!

## Automation Blueprints

This integration includes a blueprint to send notifications to your mobile device with the progress of your prints.

[![Open your Home Assistant instance and import the blueprint.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/danielcherubini/elegoo-homeassistant/blob/main/blueprints/automation/elegoo_printer/elegoo_printer_progress.yaml)

### Blueprint: Elegoo Printer Progress Notification

This blueprint sends notifications to your mobile device with the progress of your prints. You can configure the following:

- **Percent Complete Sensor:** The sensor that tracks the print progress.
- **Notification Device:** The mobile device to send notifications to.
- **Notification Frequency:** How often to send notifications (e.g., every 5% of progress).
- **Printer Camera:** The camera entity for your printer.
- **Dashboard URL (Optional):** A URL to open when the notification is clicked.
- **Notification Group (Optional):** A unique name for the notification group, channel, and tag.

## Support and Contributions

For any issues or feature requests, please [open an issue](https://github.com/danielcherubini/elegoo-homeassistant/issues) on the GitHub repository. Contributions are welcome!

## Development
To develop locally please clone the repo, then run the following

```bash
make setup
```
```bash
source .venv/bin/activate
```

That will set you up and activate the venv. This project uses UV.

## Running locally
You can either run it through the devcontainer, which is explained below, or in a debug mode, or you can run it directly.

## Debug Mode
There is a debug mode where it runs only the API against the printer. To run it:
```bash
make debug
```
## Devcontainer
This project includes a devcontainer, which should be automatically set up if you are using VSCode. For any issues, check the development section above.

Once in the devcontainer you can start it with:
```bash
make start
```
## Directly
To run it directly, first initialize your environment as explained in the development section, then run:
```bash
make start
```
## Credits
This integration is based on the amazing work of SDCP and Elegoo.

## License
This integration is released under the MIT License.
