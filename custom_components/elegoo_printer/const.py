"""Constants for elegoo_printer."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

CONF_BRAND = "brand"
CONF_CAMERA_ENABLED = "camera_enabled"
CONF_FIRMWARE = "firmware"
CONF_ID = "id"
CONF_IP = "ip_address"
CONF_MODEL = "model"
CONF_NAME = "name"
CONF_PRINTER_TYPE = "printer_type"
CONF_PROXY_ENABLED = "proxy_enabled"
DOMAIN = "elegoo_printer"
ATTRIBUTION = "Data provided by https://github.com/danielcherubini/elegoo-homeassistant"

# Ports
WEBSOCKET_PORT = 3030
DISCOVERY_PORT = 3000
VIDEO_PORT = 3031

# Endpoints
VIDEO_ENDPOINT = "video"

# Discovery
DISCOVERY_MESSAGE = "M99999"
DEFAULT_BROADCAST_ADDRESS = "<broadcast>"
DEFAULT_FALLBACK_IP = "8.8.8.8"

# Proxy
PROXY_HOST = "127.0.0.1"

# Version
CURRENT_VERSION = 4
CURRENT_MINOR_VERSION = 0
