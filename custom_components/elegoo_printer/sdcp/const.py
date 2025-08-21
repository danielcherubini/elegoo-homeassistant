"""Constants for elegoo_printer."""

import os
from logging import Logger, getLogger

DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
LOGGER: Logger = getLogger(__package__)

# Information Commands
CMD_REQUEST_STATUS_REFRESH = 0
CMD_REQUEST_ATTRIBUTES = 1

# Print Control Commands
CMD_START_PRINT = 128
CMD_PAUSE_PRINT = 129
CMD_STOP_PRINT = 130
CMD_CONTINUE_PRINT = 131
CMD_STOP_MATERIAL_FEEDING = 132
CMD_SKIP_PREHEATING = 133

# Configuration Commands
CMD_CHANGE_PRINTER_NAME = 192

# File Management Commands
CMD_RETRIEVE_FILE_LIST = 258
CMD_BATCH_DELETE_FILES = 259

# History Commands
CMD_RETRIEVE_HISTORICAL_TASKS = 320
CMD_RETRIEVE_TASK_DETAILS = 321

# Video Stream Commands
CMD_SET_VIDEO_STREAM = 386
CMD_SET_TIME_LAPSE_PHOTOGRAPHY = 387

# File Transfer Commands
CMD_TERMINATE_FILE_TRANSFER = 255

# Control Commands
CMD_CONTROL_DEVICE = 403
