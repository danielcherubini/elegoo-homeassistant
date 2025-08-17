from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.image import Image


@dataclass
class ElegooImage:
    """Returns the cover image"""

    def __init__(
        self,
        image_url: str,
        image_bytes: bytes,
        last_updated_timestamp: int,
        content_type: str,
    ):
        self._image_url = image_url
        self._bytes = image_bytes
        self._content_type = content_type
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

    def get_content_type(self) -> str:
        return self._content_type

    def get_image(self) -> Image:
        return Image(self._content_type, self._bytes)
