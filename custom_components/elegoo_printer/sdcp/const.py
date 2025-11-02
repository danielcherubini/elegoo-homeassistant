"""Constants for elegoo_printer."""

import os
from logging import Logger, getLogger

DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
LOGGER: Logger = getLogger(__package__)

# Connection Commands
CMD_DISCONNECT = 64

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
CMD_RENAME_FILE = 257  # Centauri Carbon 2
CMD_GET_FILE_INFO = 260  # Centauri Carbon 2

# History Commands
CMD_RETRIEVE_HISTORICAL_TASKS = 320
CMD_RETRIEVE_TASK_DETAILS = 321
CMD_DELETE_HISTORY = 322  # Centauri Carbon 2
CMD_EXPORT_TIME_LAPSE = 323  # Centauri Carbon 2

# Video Stream Commands
CMD_SET_VIDEO_STREAM = 386
CMD_SET_TIME_LAPSE_PHOTOGRAPHY = 387

# File Transfer Commands
CMD_TERMINATE_FILE_TRANSFER = 255

# Manual Control Commands (Centauri Carbon 2)
CMD_XYZ_MOVE_CONTROL = 401
CMD_XYZ_HOME_CONTROL = 402

# Control Commands
CMD_CONTROL_DEVICE = 403

# AMS (Automatic Material System) Commands (Centauri Carbon 2)
CMD_AMS_GET_SLOT_LIST = 500
CMD_AMS_SET_FILAMENT_INFO = 501
CMD_AMS_PRINT_WITH_MAPPING = 502
CMD_AMS_GET_MAPPING_INFO = 503
CMD_AMS_LOADING = 504
CMD_AMS_UNLOADING = 505

# MQTT Auto-Push Commands
CMD_SET_STATUS_UPDATE_PERIOD = 512  # Tell printer to auto-push status updates
