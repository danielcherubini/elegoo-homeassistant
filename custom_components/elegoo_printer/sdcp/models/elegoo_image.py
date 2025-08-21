"""Image model for Elegoo printers."""

from dataclasses import dataclass
from datetime import UTC, datetime

from homeassistant.components.image import Image


@dataclass
class ElegooImage:
    """Represents the cover image of a print job."""

    def __init__(
        self,
        image_url: str,
        image_bytes: bytes,
        last_updated_timestamp: int,
        content_type: str,
    ) -> None:
        """Initialize an ElegooImage object."""
        self._image_url = image_url
        self._bytes = image_bytes
        self._content_type = content_type
        try:
            self._image_last_updated = datetime.fromtimestamp(
                float(last_updated_timestamp), UTC
            )
        except (ValueError, TypeError, OSError) as e:
            msg = f"Invalid timestamp: {last_updated_timestamp}"
            raise ValueError(msg) from e

    def get_bytes(self) -> bytes:
        """Return the image as bytes."""
        return self._bytes

    def get_last_update_time(self) -> datetime:
        """Return the last update time of the image."""
        return self._image_last_updated

    def get_content_type(self) -> str:
        """Return the content type of the image."""
        return self._content_type

    def get_image(self) -> Image:
        """Return the image as a Home Assistant Image object."""
        return Image(self._content_type, self._bytes)
