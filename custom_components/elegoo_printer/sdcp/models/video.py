"""Elegoo Video Model"""

from typing import Any

from custom_components.elegoo_printer.sdcp.models.enums import ElegooVideoStatus


class ElegooVideo:
    """Represents video information from an Elegoo device."""

    def __init__(self, data: dict[str, Any] | None = None):
        """
        Initialize an ElegooVideo instance with status and video URL extracted from the provided data dictionary.

        Parameters:
            data (dict[str, Any] | None): Optional dictionary containing video information. If not provided, defaults are used for all attributes.
        """
        if data is None:
            data = {}

        self.status: ElegooVideoStatus | None = ElegooVideoStatus.from_int(
            data.get("Ack", 0)
        )
        self.video_url: str = data.get("VideoUrl", "")

    def to_dict(self) -> dict[str, Any]:
        """
        Return a dictionary representation of the ElegooVideo instance, including its status and video URL.

        Returns:
            dict: A dictionary with keys "status" and "video_url" reflecting the current attributes of the object.
        """
        return {
            "status": self.status,
            "video_url": self.video_url,
        }
