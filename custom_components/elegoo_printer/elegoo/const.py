"""Constants for elegoo_printer."""

import os
import sys
from logging import Logger, getLogger

from loguru import logger

LOGGER: Logger = getLogger(__package__)

DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
LOG_LEVEL = "INFO"
logger.remove()
logger.add(sys.stdout, colorize=DEBUG, level=LOG_LEVEL)
