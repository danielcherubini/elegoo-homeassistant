"""Tests for _handle_full_status preserving integration-injected data."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client


def _make_client(initial_cached_status: dict | None = None) -> MagicMock:
    """Create a minimal mock CC2 client with the real status handler methods."""
    client = MagicMock(spec=ElegooCC2Client)
    client._cached_status = initial_cached_status or {}
    client._status_sequence = 0
    client._non_continuous_count = 0
    client.logger = MagicMock()

    client._handle_full_status = (
        ElegooCC2Client._handle_full_status.__get__(client, ElegooCC2Client)
    )
    client._update_printer_status = MagicMock()
    return client


SAMPLE_FILE_DETAILS = {
    "test.gcode": {
        "TotalLayers": 111,
        "total_filament_used": 11.1,
        "color_map": [{"color": "#111111", "name": "PLA", "t": 1}],
        "proxy_filament": {
            "filename": "test.gcode",
            "filament": {
                "per_slot_grams": [1.1, 1.1],
                "total_filament_changes": 11,
            },
        },
    },
}

SAMPLE_FILE_THUMBNAILS = {
    "test.gcode": "data:image/png;base64,abc123",
}

PRINTER_STATUS = {
    "machine_status": {"status": 2, "sub_status": 4},
    "extruder": {"temperature": 210.0, "target": 215.0},
    "heater_bed": {"temperature": 60.0, "target": 60.0},
    "sequence": 42,
}


class TestHandleFullStatusPreservesInternalKeys:
    """Full status resets must not wipe _file_details or _file_thumbnails."""

    def test_preserves_file_details(self) -> None:
        """_file_details survives a full status replacement."""
        client = _make_client({
            "_file_details": SAMPLE_FILE_DETAILS,
            "machine_status": {"status": 0},
            "sequence": 10,
        })

        client._handle_full_status(PRINTER_STATUS)

        assert "_file_details" in client._cached_status
        details = client._cached_status["_file_details"]["test.gcode"]
        assert details["TotalLayers"] == 111
        assert details["total_filament_used"] == 11.1
        assert details["proxy_filament"]["filament"]["total_filament_changes"] == 11

    def test_preserves_file_thumbnails(self) -> None:
        """_file_thumbnails survives a full status replacement."""
        client = _make_client({
            "_file_thumbnails": SAMPLE_FILE_THUMBNAILS,
            "sequence": 10,
        })

        client._handle_full_status(PRINTER_STATUS)

        assert client._cached_status["_file_thumbnails"] == SAMPLE_FILE_THUMBNAILS

    def test_preserves_both_internal_keys(self) -> None:
        """Both _file_details and _file_thumbnails survive together."""
        client = _make_client({
            "_file_details": SAMPLE_FILE_DETAILS,
            "_file_thumbnails": SAMPLE_FILE_THUMBNAILS,
            "machine_status": {"status": 0},
            "sequence": 5,
        })

        client._handle_full_status(PRINTER_STATUS)

        assert "_file_details" in client._cached_status
        assert "_file_thumbnails" in client._cached_status
        assert client._cached_status["_file_details"] is SAMPLE_FILE_DETAILS
        assert client._cached_status["_file_thumbnails"] is SAMPLE_FILE_THUMBNAILS

    def test_printer_status_fields_are_replaced(self) -> None:
        """Printer-reported fields are replaced by the new full status."""
        client = _make_client({
            "_file_details": SAMPLE_FILE_DETAILS,
            "machine_status": {"status": 0},
            "extruder": {"temperature": 25.0},
            "sequence": 5,
        })

        client._handle_full_status(PRINTER_STATUS)

        assert client._cached_status["extruder"]["temperature"] == 210.0
        assert client._cached_status["machine_status"]["status"] == 2

    def test_sequence_number_updated(self) -> None:
        """Sequence number is updated from the new status data."""
        client = _make_client({"sequence": 10})

        client._handle_full_status(PRINTER_STATUS)

        assert client._status_sequence == 42

    def test_non_continuous_count_reset(self) -> None:
        """Non-continuous count is reset to 0."""
        client = _make_client()
        client._non_continuous_count = 4

        client._handle_full_status(PRINTER_STATUS)

        assert client._non_continuous_count == 0

    def test_no_internal_keys_still_works(self) -> None:
        """Full status with no prior internal keys works normally."""
        client = _make_client({
            "machine_status": {"status": 0},
            "sequence": 1,
        })

        client._handle_full_status(PRINTER_STATUS)

        assert "_file_details" not in client._cached_status
        assert client._cached_status["extruder"]["temperature"] == 210.0

    def test_calls_update_printer_status(self) -> None:
        """_update_printer_status is called after handling full status."""
        client = _make_client()

        client._handle_full_status(PRINTER_STATUS)

        client._update_printer_status.assert_called_once()

    def test_new_status_does_not_contain_stale_printer_fields(self) -> None:
        """Old printer fields not present in new status are removed."""
        client = _make_client({
            "_file_details": SAMPLE_FILE_DETAILS,
            "fans": {"fan": {"speed": 128}},
            "sequence": 5,
        })

        status_without_fans = {
            "machine_status": {"status": 0},
            "extruder": {"temperature": 200.0},
            "sequence": 50,
        }

        client._handle_full_status(status_without_fans)

        assert "fans" not in client._cached_status
        assert "_file_details" in client._cached_status

    def test_repeated_full_status_preserves_data(self) -> None:
        """Multiple consecutive full status updates preserve internal keys."""
        client = _make_client({
            "_file_details": SAMPLE_FILE_DETAILS,
            "_file_thumbnails": SAMPLE_FILE_THUMBNAILS,
            "sequence": 1,
        })

        for seq in range(10, 60, 10):
            status = {**PRINTER_STATUS, "sequence": seq}
            client._handle_full_status(status)

        assert client._cached_status["_file_details"] is SAMPLE_FILE_DETAILS
        assert client._cached_status["_file_thumbnails"] is SAMPLE_FILE_THUMBNAILS
        assert client._status_sequence == 50
