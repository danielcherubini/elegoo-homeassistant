"""Elegoo Video Model"""

from typing import Any

from custom_components.elegoo_printer.elegoo_sdcp.models.enums import ElegooVideoStatus


class ElegooVideo:
    """Represents video information from an Elegoo device."""

    def __init__(self, data: dict[str, Any] | None = None):
        if data is None:
            data = {}

        self.status: ElegooVideoStatus | None = ElegooVideoStatus.from_int(
            data.get("Ack", 0)
        )
        self.video_url: str = data.get("VideoUrl", "")

    def to_dict(self) -> dict[str, Any]:
        """Converts the ElegooVideo object to a dictionary."""
        return {
            "status": self.status,
            "video_url": self.video_url,
        }
