# Elegoo Printer Integration for Home Assistant

[![HACS Default](about:sanitized)](https://hacs.xyz/docs/default_repository)

This Home Assistant integration allows you to monitor the status and attributes of your Elegoo 3D printer, bringing valuable information directly into your smart home dashboard. Currently, this integration focuses on exposing sensor data, providing insights into your printer's operation and status. More features and controls may be added in future updates.

## Features

This integration currently exposes the following sensors for your Elegoo Printer:

### Printer Attributes Sensors

These sensors provide information about the printer's capabilities and settings.

- **Release Film Max:** Indicates the maximum lifespan or usage limit of the release film.
  - Icon: `mdi:film`
- **UV LED Temp Max:** Maximum allowed temperature of the UV LED module.
  - Icon: `mdi:thermometer`
  - Unit: °C (Celsius)
- **Video Stream Connected:** Indicates the number of video streams currently connected to the printer.
  - Icon: `mdi:camera`
- **Video Stream Max:** Maximum number of video streams allowed to connect to the printer simultaneously.
  - Icon: `mdi:camera`

### Printer Status Sensors

These sensors provide real-time information about the printer's current operational status and print job.

- **UV LED Temp:** Current temperature of the UV LED module.
  - Icon: `mdi:thermometer`
  - Unit: °C (Celsius)
- **Total Print Time:** The total accumulated print time of the printer.
  - Icon: `mdi:timer-sand-complete`
  - Unit: Milliseconds (displayed in Hours for convenience)
- **Current Print Time:** The elapsed time of the current print job.
  - Icon: `mdi:progress-clock`
  - Unit: Milliseconds (displayed in Hours for convenience)
- **Remaining Print Time:** Estimated time remaining for the current print job.
  - Icon: `mdi:timer-sand`
  - Unit: Milliseconds (displayed in Hours for convenience)
- **Total Layers:** The total number of layers in the current print job.
  - Icon: `mdi:eye`
- **Current Layer:** The current layer being printed.
  - Icon: `mdi:eye`
- **Remaining Layers:** The number of layers remaining to be printed.
  - Icon: `mdi:eye`
- **Percent Complete:** The percentage of the current print job that is complete.
  - Icon: `mdi:percent`
  - Unit: % (Percentage)
- **File Name:** The name of the file currently being printed.
  - Icon: `mdi:file`
- **Print Status:** The current status of the printer (e.g., "Printing", "Idle", "Error").
  - Icon: `mdi:file`
- **Print Error:** If a print error occurs, this sensor will display the error type.
  - Icon: `mdi:file`
- **Release Film:** Current status or reading related to the release film.
  - Icon: `mdi:film`

## Installation

This integration is installed manually through the Home Assistant Community Store (HACS) as it is not yet in the default HACS repository. Follow these steps to install:

1.  **Ensure HACS is Installed:** If you don't have HACS installed, follow the [HACS installation instructions](https://hacs.xyz/docs/setup/download) first.

2.  **Add Custom Repository:**

    - Go to your Home Assistant instance.
    - Navigate to **HACS** \> **Integrations**.
    - Click the **three dots** in the top right corner and select **"Custom repositories"**.
    - In the "Add custom repository" dialog:
      - **Repository URL:** `[YOUR_REPOSITORY_URL_HERE]` _(Replace `[YOUR_REPOSITORY_URL_HERE]` with the actual repository URL once it is available.)_
      - **Category:** Select **"Integration"**.
    - Click **"ADD"**.

3.  **Install the Integration:**

    - Navigate to **HACS** \> **Integrations**.
    - Click the **"+" Explore & download repositories** button in the bottom right corner.
    - Search for **"Elegoo Printer"** (or the name of your repository if different).
    - Click on the **Elegoo Printer Integration** card.
    - Click **"Download"** in the bottom right corner.
    - Select the desired version (usually the latest) and click **"Download"**.

4.  **Restart Home Assistant:** After the installation is complete, restart your Home Assistant instance for the integration to be loaded.

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
