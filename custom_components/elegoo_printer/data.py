"""Custom types for elegoo_printer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.loader import Integration

    from .api import ElegooPrinterApiClient
    from .coordinator import ElegooDataUpdateCoordinator


type ElegooPrinterConfigEntry = ConfigEntry[ElegooPrinterData]


@dataclass
class ElegooPrinterData:
    """Data for the Elegoo integration."""

    client: ElegooPrinterApiClient
    coordinator: ElegooDataUpdateCoordinator
    integration: Integration
