"""Constants for elegoo_printer."""  # noqa: INP001

import os
import sys
from logging import Logger, getLogger

from loguru import logger

LOGGER: Logger = getLogger(__package__)

debug = False
log_level = "INFO"
if os.environ.get("DEBUG") == "true":
    debug = True
    log_level = "DEBUG"
logger.remove()
logger.add(sys.stdout, colorize=debug, level=log_level)
