from typing import TYPE_CHECKING, Protocol

from .status import LightStatus

if TYPE_CHECKING:
    from .printer import PrinterData


class ElegooClient(Protocol):
    """Protocol for Elegoo clients."""

    printer_data: "PrinterData"

    async def set_light_status(self, light_status: LightStatus) -> None:
        """Set the printer's light status."""
        ...
