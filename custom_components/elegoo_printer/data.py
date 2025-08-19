"""Custom types for elegoo_printer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from homeassistant.loader import Integration

    from .api import ElegooPrinterApiClient
    from .coordinator import ElegooDataUpdateCoordinator


class ElegooPrinterConfigEntry(ConfigEntry):
    """Config entry for Elegoo printers."""

    runtime_data: ElegooPrinterData


@dataclass
class ElegooPrinterData:
    """Runtime data for Elegoo printers."""

    api: ElegooPrinterApiClient
    coordinator: ElegooDataUpdateCoordinator
    integration: Integration
