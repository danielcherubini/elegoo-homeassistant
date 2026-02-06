"""
CC2 (Centauri Carbon 2) protocol implementation.

This package implements support for Centauri Carbon 2 printers which use
a fundamentally different communication architecture from other Elegoo printers:

- The printer runs its own MQTT broker (inverted from CC1/MQTT architecture)
- Discovery uses port 52700 with JSON messages
- Different command IDs (1001, 1002, 1020, etc.)
- Requires client registration before sending commands
- Uses heartbeat/ping-pong mechanism
- Sends delta status updates instead of full status
"""

from .client import ElegooCC2Client
from .discovery import CC2Discovery

__all__ = ["CC2Discovery", "ElegooCC2Client"]
