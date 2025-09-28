"""ElegooEntity class."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTRIBUTION,
    CONF_BRAND,
    CONF_FIRMWARE,
    CONF_ID,
    CONF_IP,
    CONF_MODEL,
    CONF_NAME,
    CONF_PROXY_ENABLED,
    DOMAIN,
    LOGGER,
    WEBSOCKET_PORT,
)
from .coordinator import ElegooDataUpdateCoordinator
from .sdcp.models.printer import PrinterData


class ElegooPrinterEntity(CoordinatorEntity[ElegooDataUpdateCoordinator]):
    """ElegooEntity class."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: ElegooDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info with dynamically updated configuration URL."""
        config_data = self.coordinator.config_entry.data

        # Determine if we have printer data available
        has_printer_data = self.coordinator.data and self.coordinator.data.printer

        LOGGER.debug(
            "Building device_info for %s: using %s data",
            config_data.get(CONF_NAME, "unknown"),
            "printer" if has_printer_data else "config",
        )

        # Set variables based on data availability
        if has_printer_data:
            printer = self.coordinator.data.printer
            device_id = printer.id or config_data[CONF_ID]
            device_name = printer.name or config_data[CONF_NAME]
            device_model = printer.model or config_data[CONF_MODEL]
            device_manufacturer = printer.brand or config_data[CONF_BRAND]
            device_firmware = printer.firmware or config_data[CONF_FIRMWARE]
            device_ip = printer.ip_address
            proxy_enabled = printer.proxy_enabled

            LOGGER.debug(
                "Using printer data: name=%s, model=%s, firmware=%s, ip=%s, proxy=%s",
                device_name,
                device_model,
                device_firmware,
                device_ip,
                proxy_enabled,
            )
        else:
            # Use config fallbacks
            device_id = config_data[CONF_ID]
            device_name = config_data[CONF_NAME]
            device_model = config_data[CONF_MODEL]
            device_manufacturer = config_data[CONF_BRAND]
            device_firmware = config_data[CONF_FIRMWARE]
            device_ip = config_data.get(CONF_IP)
            proxy_enabled = config_data.get(CONF_PROXY_ENABLED, False)

            LOGGER.debug(
                "Config fallback: name=%s, model=%s, firmware=%s, ip=%s, proxy=%s",
                device_name,
                device_model,
                device_firmware,
                device_ip,
                proxy_enabled,
            )

        # Build configuration URL
        configuration_url = None
        if device_ip:
            if proxy_enabled:
                # Use centralized proxy with MainboardID query parameter
                proxy_ip = PrinterData.get_local_ip(device_ip)
                configuration_url = f"http://{proxy_ip}:{WEBSOCKET_PORT}?id={device_id}"
                LOGGER.debug("Built proxy configuration URL: %s", configuration_url)
            else:
                configuration_url = f"http://{device_ip}:{WEBSOCKET_PORT}"
                LOGGER.debug("Built direct configuration URL: %s", configuration_url)
        else:
            LOGGER.debug("No IP address available, configuration URL will be None")

        LOGGER.debug(
            "Final device_info: id=%s, name=%s, model=%s, manufacturer=%s",
            device_id,
            device_name,
            device_model,
            device_manufacturer,
        )

        # Construct and return DeviceInfo
        return DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=device_name,
            model=device_model,
            manufacturer=device_manufacturer,
            sw_version=device_firmware,
            serial_number=device_id,
            configuration_url=configuration_url,
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available
