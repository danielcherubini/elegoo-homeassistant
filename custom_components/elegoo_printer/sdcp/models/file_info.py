"""File information models for Elegoo SDCP."""

from typing import Any


class FileInfo:
    """Represents information about a file stored on the printer."""

    def __init__(self, data: dict[str, Any]) -> None:
        """
        Initialize FileInfo from response data.

        Expected data format (based on SDCP patterns):
        {
            "name": str,  # Full path like "/local/filename.gcode"
            "FileName": str,  # Alternative field name
            "FileSize": int,  # in bytes
            "CreateTime": int,  # timestamp
            "type": int,  # 1 = file, other = directory
        }
        """
        # Handle both "name" (web interface) and "FileName" (legacy)
        filename_raw = data.get("name", data.get("FileName", ""))

        # Extract just the filename from full path
        # (e.g., "/local/file.gcode" -> "file.gcode")
        if "/" in filename_raw:
            self.filename = filename_raw.split("/")[-1]
        else:
            self.filename = filename_raw

        self.file_size: int | None = data.get("FileSize")
        self.create_time: int | None = data.get("CreateTime")
        self.file_type: int | None = data.get("type")  # 1 = file

    def __repr__(self) -> str:
        """Return string representation."""
        return str(self.__dict__)
