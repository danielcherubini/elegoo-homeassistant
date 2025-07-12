from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.image import Image


@dataclass
class ElegooImage:
    """Returns the cover image"""

    def __init__(self, url: str, bytes: bytes, last_updated_timestamp: int):
        self._image_url = url
        self._bytes = bytes
        try:
            self._image_last_updated = datetime.fromtimestamp(
                float(last_updated_timestamp)
            )
        except (ValueError, TypeError, OSError) as e:
            raise ValueError(f"Invalid timestamp: {last_updated_timestamp}") from e

    def get_bytes(self) -> bytes:
        return self._bytes

    def get_last_update_time(self) -> datetime:
        return self._image_last_updated

    def get_image(self) -> Image:
        return Image("image/png", self._bytes)
