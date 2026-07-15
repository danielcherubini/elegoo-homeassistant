"""
Tests for the FDM (Centauri Carbon) print status code mapping.

FDM firmware reports print sub-status from a different code table than
resin SDCP (source: Elegoo's elegoo-link CC adapter). Every wire code
maps 1:1 onto its own state — nothing the firmware reports is folded
into another state — and codes the table does not know surface as
UNRECOGNIZED rather than freezing the sensor. The firmware parks on
lifecycle milestones ("preheating completed") for the duration of a
job, so progress reporting must stay active through all of them.
"""

from custom_components.elegoo_printer.sdcp.models.enums import (
    _FDM_PRINT_STATUS_CODES,
    ElegooPrintStatus,
    PrinterType,
)
from custom_components.elegoo_printer.sdcp.models.status import PrinterStatus

# Elegoo's table, code -> expected state, verbatim from
# elegoo_fdm_cc_message_adapter.cpp. Code 3 is resin-only ("Exposuring")
# and intentionally absent.
FDM_TABLE = {
    0: ElegooPrintStatus.IDLE,
    1: ElegooPrintStatus.HOMING,
    2: ElegooPrintStatus.DROPPING,
    4: ElegooPrintStatus.LIFTING,
    5: ElegooPrintStatus.PAUSING,
    6: ElegooPrintStatus.PAUSED,
    7: ElegooPrintStatus.STOPPING,
    8: ElegooPrintStatus.STOPPED,
    9: ElegooPrintStatus.COMPLETE,
    10: ElegooPrintStatus.FILE_CHECKING,
    11: ElegooPrintStatus.PRINTERS_CHECKING,
    12: ElegooPrintStatus.RESUMING,
    13: ElegooPrintStatus.PRINTING,
    14: ElegooPrintStatus.ERROR,
    15: ElegooPrintStatus.LEVELING,
    16: ElegooPrintStatus.PREHEATING,
    17: ElegooPrintStatus.RESONANCE_TESTING,
    18: ElegooPrintStatus.PRINT_STARTED,
    19: ElegooPrintStatus.AUTO_LEVELING_COMPLETED,
    20: ElegooPrintStatus.PREHEATING_COMPLETED,
    21: ElegooPrintStatus.HOMING_COMPLETED,
    22: ElegooPrintStatus.RESONANCE_TESTING_COMPLETED,
    23: ElegooPrintStatus.AUTO_FEEDING,
    24: ElegooPrintStatus.FILAMENT_UNLOADING,
    25: ElegooPrintStatus.FILAMENT_UNLOAD_ABNORMAL,
    26: ElegooPrintStatus.FILAMENT_UNLOAD_PAUSED,
}


def _cc1_status_payload(print_status: int, machine_status: int = 1) -> dict:
    """Build a minimal CC1 (WebSocket-shaped) status payload."""
    return {
        "Status": {
            "CurrentStatus": [machine_status],
            "PrintInfo": {
                "Status": print_status,
                "CurrentLayer": 90,
                "TotalLayer": 110,
                "Progress": 89,
                "Filename": "/local/test.gcode",
                "TaskId": "task-1",
            },
        }
    }


class TestFdmCodeTable:
    """Direct 1:1 mapping checks against Elegoo's FDM code table."""

    def test_every_code_maps_verbatim(self) -> None:
        """Each firmware code surfaces as its own state, nothing folded."""
        for code, expected in FDM_TABLE.items():
            assert ElegooPrintStatus.from_fdm_int(code) == expected, f"code {code}"

    def test_table_is_exactly_the_elegoo_table(self) -> None:
        """No extra or missing codes versus the authoritative source."""
        assert _FDM_PRINT_STATUS_CODES == FDM_TABLE

    def test_unknown_codes_surface_as_unrecognized(self) -> None:
        """New firmware codes are visible, and the sensor can never freeze."""
        for code in (3, 27, 99):
            assert (
                ElegooPrintStatus.from_fdm_int(code) == ElegooPrintStatus.UNRECOGNIZED
            ), f"code {code}"

    def test_fdm_members_cannot_shadow_resin_codes(self) -> None:
        """
        Keep FDM-only member values outside the wire-code range.

        The resin path's by-value lookup (from_int -> cls(code)) must never
        accidentally resolve to an FDM-only member.
        """
        fdm_only_members = (
            ElegooPrintStatus.PRINTERS_CHECKING,
            ElegooPrintStatus.RESUMING,
            ElegooPrintStatus.ERROR,
            ElegooPrintStatus.RESONANCE_TESTING,
            ElegooPrintStatus.PRINT_STARTED,
            ElegooPrintStatus.AUTO_LEVELING_COMPLETED,
            ElegooPrintStatus.PREHEATING_COMPLETED,
            ElegooPrintStatus.HOMING_COMPLETED,
            ElegooPrintStatus.RESONANCE_TESTING_COMPLETED,
            ElegooPrintStatus.AUTO_FEEDING,
            ElegooPrintStatus.FILAMENT_UNLOADING,
            ElegooPrintStatus.FILAMENT_UNLOAD_ABNORMAL,
            ElegooPrintStatus.FILAMENT_UNLOAD_PAUSED,
            ElegooPrintStatus.UNRECOGNIZED,
        )
        for member in fdm_only_members:
            assert member.value >= 100, (
                f"{member.name}={member.value} shadows a resin wire code"
            )

    def test_resin_mapping_unchanged(self) -> None:
        """The resin table keeps its historical behavior."""
        assert ElegooPrintStatus.from_int(20) == ElegooPrintStatus.LEVELING
        assert ElegooPrintStatus.from_int(15) == ElegooPrintStatus.LOADING
        assert ElegooPrintStatus.from_int(3) == ElegooPrintStatus.PRINTING
        assert ElegooPrintStatus.from_int(9) == ElegooPrintStatus.COMPLETE
        assert ElegooPrintStatus.from_int(18) == ElegooPrintStatus.LOADING
        assert ElegooPrintStatus.from_int(99) is None


class TestPrinterStatusIntegration:
    """End-to-end through PrinterStatus payload parsing."""

    def test_parked_milestone_reports_verbatim_with_progress(self) -> None:
        """
        Parked milestone codes come through verbatim with progress intact.

        CC1 V1.4.46 parks on code 20 for the duration of a job; the state
        must surface as-is while progress keeps flowing.
        """
        status = PrinterStatus(_cc1_status_payload(20), PrinterType.FDM)
        assert status.print_info.status == ElegooPrintStatus.PREHEATING_COMPLETED
        assert status.print_info.percent_complete == 89.0

    def test_all_mid_job_states_keep_progress(self) -> None:
        """Progress must not blank in any state the firmware visits mid-job."""
        mid_job_codes = (12, 13, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26)
        for code in mid_job_codes:
            status = PrinterStatus(_cc1_status_payload(code), PrinterType.FDM)
            assert status.print_info.percent_complete == 89.0, f"code {code}"

    def test_unrecognized_code_keeps_progress(self) -> None:
        """A future firmware code mid-print cannot blank progress."""
        status = PrinterStatus(_cc1_status_payload(42), PrinterType.FDM)
        assert status.print_info.status == ElegooPrintStatus.UNRECOGNIZED
        assert status.print_info.percent_complete == 89.0

    def test_complete_still_reaches_complete(self) -> None:
        """The Spoolman push trigger relies on the complete state."""
        status = PrinterStatus(
            _cc1_status_payload(9, machine_status=0), PrinterType.FDM
        )
        assert status.print_info.status == ElegooPrintStatus.COMPLETE

    def test_resin_payload_keeps_legacy_mapping(self) -> None:
        """A resin printer parsing the same code 20 still reads leveling."""
        status = PrinterStatus(_cc1_status_payload(20), PrinterType.RESIN)
        assert status.print_info.status == ElegooPrintStatus.LEVELING
