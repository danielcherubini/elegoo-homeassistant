# Elegoo SDCP Printer Integration for Home Assistant

This Home Assistant integration allows you to monitor the status and attributes of your Elegoo 3D printer, bringing valuable information directly into your smart home dashboard. Currently, this integration focuses on exposing sensor data, providing insights into your printer's operation and status. More features and controls may be added in future updates.

## Features

This integration currently exposes the following sensors for your Elegoo Printer:

### Printer Attribute Sensors

These sensors tell you about the printer's capabilities and limits. Think of them as the printer's specifications.

- **Maximum Release Film Usage:** Shows the maximum lifespan or usage limit for the release film.
- **Maximum UV LED Temperature:** The highest temperature the UV LED is allowed to reach. (Unit: °C)
- **Connected Video Streams:** How many video streams are currently connected to the printer.
- **Maximum Video Streams:** The maximum number of video streams you can connect at the same time.

### Printer Status Sensors

These sensors give you live updates on the printer's current operation and the progress of your print job.

- **UV LED Temperature:** The current temperature of the UV LED. (Unit: °C)
- **Total Print Time:** The total amount of time this printer has been printing, ever. (Unit: Milliseconds, displayed in Hours)
- **Current Print Time:** How long the current print job has been running. (Unit: Milliseconds, displayed in Hours)
- **Remaining Print Time:** The estimated time left for the current print job to finish. (Unit: Milliseconds, displayed in Hours)
- **Total Layers:** The total number of layers in the print job.
- **Current Layer:** Which layer is being printed right now.
- **Remaining Layers:** How many layers are left to print.
- **Print Completion Percentage:** How much of the print job is finished. (Unit: %)
- **File Name:** The name of the file you're printing.
- **Print Status:** What the printer is currently doing (e.g., "Printing," "Idle," "Error").
- **Print Error:** If there's a problem, this will show the type of error.
- **Release Film Status:** The current status of the release film.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=danielcherubini&repository=elegoo-homeassistant&category=Integration)

## Configuration

Currently, this integration needs to know your IP Address of your printer, in the future it will automatically find it on your network. So when you add a printer it will ask for your IP Address.

## Future Updates

This is the initial release of the Elegoo Printer integration, focusing on sensor data. Future updates may include:

- **More Sensors:** Exposure of additional printer parameters and status information.
- **Control Features:** Potential for controlling printer functions directly from Home Assistant (e.g., starting/stopping prints, pausing, etc.).
- **Support for more Elegoo Printer Models:** Expanding compatibility to a wider range of Elegoo printers.

Stay tuned for updates and feel free to contribute with feature requests and bug reports!

## Support and Contributions

For any issues or feature requests, please [open an issue]([https://github.com/danielcherubini/elegoo-homeassistant/issues]) on the GitHub repository. Contributions are welcome!

## License

This integration is released under the [MIT License](https://opensource.org/licenses/MIT).
