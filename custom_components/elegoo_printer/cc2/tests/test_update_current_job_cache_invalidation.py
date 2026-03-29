"""Tests for _update_current_job invalidating stale file details on new task_id."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer


def _client_with_proxy() -> ElegooCC2Client:
    proxy = MagicMock()
    printer = Printer()
    return ElegooCC2Client(
        printer_ip="192.0.2.1",
        serial_number="TESTSN",
        printer=printer,
        gcode_proxy=proxy,
    )


OLD_PROXY = {
    "filename": "CC2_Model.gcode",
    "filament": {"per_slot_grams": [0.0, 15.55, 0.0, 0.0]},
}


class TestUpdateCurrentJobCacheInvalidation:
    """Re-sliced same filename must drop cached proxy/MQTT enrichment for new uuid."""

    def test_new_task_clears_stale_proxy_and_mqtt_fields(self) -> None:
        """New task_id drops cached file_info; proxy refetch is scheduled."""
        client = _client_with_proxy()
        fn = "CC2_Model.gcode"
        client._integration_data["_file_details"] = {
            fn: {
                "proxy_filament": OLD_PROXY,
                "proxy_filament_status": "success",
                "total_filament_used": 24.8,
                "color_map": [{"name": "PLA", "t": 0}],
                "print_time": 100,
                "TotalLayers": 500,
            },
        }
        client._cached_status = {
            "print_status": {
                "uuid": "task-new-001",
                "filename": fn,
                "total_layer": 100,
            },
        }
        client._request_proxy_filament_background = MagicMock()
        client._request_file_detail_background = MagicMock()
        client._request_file_thumbnail_background = MagicMock()

        client._update_current_job()

        details = client._integration_data["_file_details"][fn]
        assert "proxy_filament" not in details
        assert "proxy_filament_status" not in details
        assert "total_filament_used" not in details
        assert "color_map" not in details
        assert "print_time" not in details
        assert "TotalLayers" not in details
        client._request_proxy_filament_background.assert_called_once_with(fn)
        client._request_file_detail_background.assert_not_called()

    def test_same_task_keeps_cached_proxy(self) -> None:
        """Same task_id must not clear successful proxy cache between MQTT updates."""
        client = _client_with_proxy()
        fn = "CC2_Model.gcode"
        tid = "task-same-001"
        client.printer_data.print_history[tid] = PrintHistoryDetail(
            {"TaskId": tid, "TaskName": fn},
        )
        client._integration_data["_file_details"] = {
            fn: {
                "proxy_filament": OLD_PROXY,
                "proxy_filament_status": "success",
            },
        }
        client._cached_status = {
            "print_status": {
                "uuid": tid,
                "filename": fn,
                "total_layer": 50,
            },
        }
        client._request_proxy_filament_background = MagicMock()
        client._request_file_detail_background = MagicMock()
        client._request_file_thumbnail_background = MagicMock()

        client._update_current_job()

        details = client._integration_data["_file_details"][fn]
        assert details["proxy_filament"] == OLD_PROXY
        assert details["proxy_filament_status"] == "success"
        client._request_proxy_filament_background.assert_not_called()

    def test_new_task_empty_file_info_still_schedules_proxy_fetch(self) -> None:
        """New task with no prior file_info should request proxy data once."""
        client = _client_with_proxy()
        fn = "CC2_Other.gcode"
        client._cached_status = {
            "print_status": {
                "uuid": "task-fresh-002",
                "filename": fn,
                "total_layer": 10,
            },
        }
        client._request_proxy_filament_background = MagicMock()
        client._request_file_detail_background = MagicMock()
        client._request_file_thumbnail_background = MagicMock()

        client._update_current_job()

        client._request_proxy_filament_background.assert_called_once_with(fn)
