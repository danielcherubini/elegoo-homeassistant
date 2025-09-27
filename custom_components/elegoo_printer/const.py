"""Constants for the Elegoo Printer integration."""

from logging import getLogger

# Integration domain
DOMAIN = "elegoo_printer"
LOGGER = getLogger(__package__)

# Attributes
ATTRIBUTION = "Data provided by Elegoo"

# Configuration keys
CONF_BRAND = "brand"
CONF_CAMERA_ENABLED = "camera_enabled"
CONF_FIRMWARE = "firmware"
CONF_ID = "id"
CONF_IP = "ip_address"
CONF_MODEL = "model"
CONF_NAME = "name"
CONF_PRINTER_TYPE = "printer_type"
CONF_PROXY_ENABLED = "proxy_enabled"
CONF_PROXY_WEBSOCKET_PORT = "proxy_websocket_port"
CONF_PROXY_VIDEO_PORT = "proxy_video_port"

# Websocket and proxy settings
DEFAULT_BROADCAST_ADDRESS = "255.255.255.255"
DEFAULT_FALLBACK_IP = "8.8.8.8"
DISCOVERY_MESSAGE = "M99999"
DISCOVERY_PORT = 3000
PROXY_HOST = "127.0.0.1"
VIDEO_ENDPOINT = "video"
VIDEO_PORT = 3031
WEBSOCKET_PORT = 3030

# Firmware service settings
FIRMWARE_SERVICE_BASE_URL = "https://mms.chituiot.com"
FIRMWARE_UPDATE_ENDPOINT = "/mainboardVersionUpdate/getInfo.do7"

# Error messages
MIGRATE_V4_ERROR = (
    "Migration v3->v4 failed: 'name' or 'id' field is missing from config."
)
SETUP_ERROR = "Failed to connect to the printer"

# Migration versions
CONFIG_VERSION_1 = 1
CONFIG_VERSION_2 = 2
CONFIG_VERSION_3 = 3
CONFIG_VERSION_4 = 4
