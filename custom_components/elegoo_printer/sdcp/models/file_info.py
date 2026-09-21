"""A file in the Centauri Carbon 2's local storage, as method 1044 lists it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


class PrinterFile:
    """
    One entry of a 1044 file listing.

    Field names are the printer's own, measured on firmware 02.01.00.00:
    ``filename``, ``size`` (bytes), ``create_time`` (unix seconds), ``layer``,
    ``print_time`` (estimated seconds), ``total_filament_used`` (grams) and
    ``color_map`` (one entry per G-code tool, with ``color`` and ``name``).
    Directory entries carry ``type`` other than ``"file"`` and are skipped by
    the caller.
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """Read one listing entry; missing fields become empty values, not errors."""
        if data is None:
            data = {}
        self.name: str = str(data.get("filename") or "")
        self.size: int = int(data.get("size") or 0)
        self.created: datetime | None = None
        created = data.get("create_time")
        if created:
            try:
                self.created = datetime.fromtimestamp(int(created), tz=UTC)
            except (OverflowError, OSError, ValueError, TypeError):
                # a corrupt or out-of-range timestamp is not worth losing the file for
                self.created = None
        self.layers: int = int(data.get("layer") or 0)
        self.print_time: int = int(data.get("print_time") or 0)
        self.filament_used: float = float(data.get("total_filament_used") or 0.0)
        self.colors: list[str] = [
            str(entry.get("color") or "")
            for entry in data.get("color_map") or []
            if isinstance(entry, dict)
        ]

    def __repr__(self) -> str:
        """Return a compact representation for logs."""
        return f"PrinterFile({self.name!r}, {self.size} bytes, {self.print_time} s)"
