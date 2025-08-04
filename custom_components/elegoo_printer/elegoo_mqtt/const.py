"""Constants for the MQTT client."""

import os
from logging import Logger, getLogger

DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
LOGGER: Logger = getLogger(__package__)

DEFAULT_MQTT_PORT = 1883
