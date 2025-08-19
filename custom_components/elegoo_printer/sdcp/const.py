"""Constants for elegoo_printer."""

import os
from logging import Logger, getLogger

DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
LOGGER: Logger = getLogger(__package__)
