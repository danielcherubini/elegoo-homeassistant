"""Custom types for elegoo_printer."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from homeassistant.config_entries import ConfigEntry

"""Data classes for the Elegoo printer integration."""

if TYPE_CHECKING:
    from homeassistant.loader import Integration

    from .api import ElegooPrinterApiClient
    from .coordinator import ElegooDataUpdateCoordinator


class ElegooPrinterConfigEntry(ConfigEntry):
    """Config entry for Elegoo printers."""

    runtime_data: ElegooPrinterData


class ElegooPrinterData(TypedDict):
    """Runtime data for Elegoo printers."""

    api: ElegooPrinterApiClient
    coordinator: ElegooDataUpdateCoordinator
    integration: Integration
