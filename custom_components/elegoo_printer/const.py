"""Constants for elegoo_printer."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

CONF_PROXY_ENABLED = "proxy_enabled"
DOMAIN = "elegoo_printer"
ATTRIBUTION = "Data provided by https://github.com/danielcherubini/elegoo-homeassistant"

# Ports
WEBSOCKET_PORT = 8888
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
