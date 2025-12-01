"""File information models for Elegoo SDCP."""

from typing import Any


class FileInfo:
    """Represents information about a file stored on the printer."""

    def __init__(self, data: dict[str, Any]) -> None:
        """
        Initialize FileInfo from response data.

        Expected data format (based on SDCP patterns):
        {
            "FileName": str,
            "FileSize": int,  # in bytes
            "CreateTime": int,  # timestamp
        }
        """
        self.filename: str = data.get("FileName", "")
        self.file_size: int | None = data.get("FileSize")
        self.create_time: int | None = data.get("CreateTime")

    def __repr__(self) -> str:
        """Return string representation."""
        return str(self.__dict__)
