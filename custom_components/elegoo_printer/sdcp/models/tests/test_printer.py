"""Tests for the Printer model."""

import pytest

from custom_components.elegoo_printer.sdcp.models.printer import Printer


class TestOpenCentauriDetection:
    """Test Open Centauri firmware detection."""

    @pytest.mark.parametrize(
        ("model", "firmware", "expected"),
        [
            # Valid Open Centauri firmware versions
            ("Centauri Carbon", "V0.1.0 O", True),
            ("Centauri Carbon", "V0.1.0 o", True),
            ("Centauri Carbon", "V0.1.0O", True),
            ("Centauri Carbon", "V0.1.0o", True),
            ("Centauri Carbon", "V0.2.0OC", True),
            ("Centauri Carbon", "V0.2.0oc", True),
            ("Centauri Carbon", "V0.2.0 OC", True),
            ("Centauri Carbon", "V0.2.0 oc", True),
            ("centauri carbon", "V0.1.0 O", True),  # Case-insensitive model
            ("CENTAURI CARBON", "v0.1.0 o", True),  # Mixed case
            # Invalid - not Open Centauri firmware (no OC or standalone O)
            ("Centauri Carbon", "V0.1.0", False),
            ("Centauri Carbon", "V0.1.0 OCEAN", False),  # O not standalone
            ("Centauri Carbon", "V0.1.0 OFFICIAL", False),  # O not standalone
            ("Centauri Carbon", "V1.0.0", False),
            # Invalid - not Centauri printer
            ("Neptune 4", "V0.1.0 O", False),
            ("Neptune 4 Pro", "V0.2.0OC", False),
            ("Saturn 3", "V0.1.0 O", False),
            # Edge cases
            (None, "V0.1.0 O", False),  # No model
            ("Centauri Carbon", None, False),  # No firmware
            (None, None, False),  # Neither
            ("", "V0.1.0 O", False),  # Empty model
            ("Centauri Carbon", "", False),  # Empty firmware
        ],
    )
    def test_is_open_centauri(
        self, model: str | None, firmware: str | None, expected: bool
    ) -> None:
        """Test Open Centauri detection with various firmware patterns."""
        result = Printer._is_open_centauri(model, firmware)
        assert result == expected, (
            f"Expected {expected} for model='{model}' firmware='{firmware}', "
            f"got {result}"
        )
