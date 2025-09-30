"""Constants for MQTT implementation."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

# MQTT Connection Settings
MQTT_PORT = 1883
MQTT_KEEPALIVE = 60

# MQTT Topics (based on SDCP protocol structure)
# Topics follow pattern: sdcp/{message_type}/{printer_id}
TOPIC_PREFIX = "sdcp"
TOPIC_REQUEST = "request"
TOPIC_RESPONSE = "response"
TOPIC_STATUS = "status"
TOPIC_ATTRIBUTES = "attributes"
TOPIC_NOTICE = "notice"
TOPIC_ERROR = "error"

# MQTT topic parsing
MQTT_TOPIC_MIN_PARTS = 2
