# Elegoo Printer Integration for Home Assistant

This Home Assistant integration allows you to monitor the status and attributes of your Elegoo resin and FDM 3D printers, bringing valuable information directly into your smart home dashboard.

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

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=danielcherubini&repository=elegoo-homeassistant&category=Integration)

## Configuration

Currently, this integration needs the IP Address of your printer. In the future, it will automatically discover it on your network. When you add the integration, it will ask for your printer's IP Address.

## Future Updates

This integration is actively being developed. Future updates may include:

- **Live Video Support:** Direct integration of the printer's video feed.
- **Control Features:** The ability to start, stop, and pause prints directly from Home Assistant.
- **Expanded Model Support:** Compatibility with more Elegoo printer models as they are released.

Stay tuned for updates and feel free to contribute with feature requests and bug reports!

## Support and Contributions

For any issues or feature requests, please [open an issue](https://github.com/danielcherubini/elegoo-homeassistant/issues) on the GitHub repository. Contributions are welcome!

## Development
To develop locally please clone the repo, then run the following

```bash
make
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
