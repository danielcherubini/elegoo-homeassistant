"""Tests for the CC2 print-file select: a choice, never an action."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.elegoo_printer.definitions import (
    PRINT_TRAY_OPTIONS,
    PRINTER_FDM_BUTTONS_CC2_ONLY,
    PRINTER_FILE_SELECT_CC2,
    PRINTER_TRAY_SELECT_CC2,
    _print_selected_file_action,
    _print_selected_file_available,
)
from custom_components.elegoo_printer.sdcp.models.ams import AMSStatus
from custom_components.elegoo_printer.sdcp.models.enums import ElegooMachineStatus
from custom_components.elegoo_printer.sdcp.models.file_info import PrinterFile
from custom_components.elegoo_printer.sdcp.models.printer import PrinterData
from custom_components.elegoo_printer.select import (
    ElegooPrintFileSelect,
    ElegooPrintTraySelect,
)

BENCHY = "benchy.gcode"
CLIP = "clip.gcode"


def _select(files: list[str]) -> ElegooPrintFileSelect:
    coordinator = MagicMock()
    coordinator.generate_unique_id = lambda key: f"testsn_{key}"
    coordinator.data = PrinterData()
    coordinator.data.file_list = {
        name: PrinterFile({"filename": name, "size": 1}) for name in files
    }
    return ElegooPrintFileSelect(coordinator, PRINTER_FILE_SELECT_CC2[0])


def test_options_are_the_printers_files_sorted() -> None:
    select = _select([CLIP, BENCHY])
    assert select.options == [BENCHY, CLIP]
    assert select.current_option is None


def test_selecting_keeps_the_choice_and_sends_nothing() -> None:
    select = _select([BENCHY, CLIP])
    select.async_write_ha_state = MagicMock()
    asyncio.run(select.async_select_option(CLIP))
    assert select.current_option == CLIP
    assert select.coordinator.data.selected_file == CLIP
    select.async_write_ha_state.assert_called_once()
    # nothing on the coordinator or its API was touched
    assert not select.coordinator.method_calls


def test_selecting_a_file_not_on_the_printer_is_refused() -> None:
    select = _select([BENCHY])
    with pytest.raises(ServiceValidationError, match="not on the printer"):
        asyncio.run(select.async_select_option(CLIP))
    assert select.current_option is None


def test_choice_disappears_with_the_file_and_returns_with_it() -> None:
    select = _select([BENCHY, CLIP])
    select.async_write_ha_state = MagicMock()
    asyncio.run(select.async_select_option(CLIP))
    del select.coordinator.data.file_list[CLIP]
    assert select.current_option is None
    select.coordinator.data.file_list[CLIP] = PrinterFile({"filename": CLIP})
    assert select.current_option == CLIP


def test_unavailable_until_the_printer_reported_a_list() -> None:
    select = _select([])
    with patch(
        "custom_components.elegoo_printer.entity.ElegooPrinterEntity.available",
        new=True,
    ):
        assert select.available is False
        select.coordinator.data.file_list[BENCHY] = PrinterFile({"filename": BENCHY})
        assert select.available is True


def test_restores_the_last_choice() -> None:
    select = _select([BENCHY, CLIP])
    last = MagicMock()
    last.state = CLIP
    with (
        patch(
            "custom_components.elegoo_printer.select.RestoreEntity.async_added_to_hass",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.elegoo_printer.entity.ElegooPrinterEntity.async_added_to_hass",
            new=AsyncMock(),
        ),
        patch.object(select, "async_get_last_state", new=AsyncMock(return_value=last)),
    ):
        asyncio.run(select.async_added_to_hass())
    assert select.current_option == CLIP


def _client(
    files: list[str], chosen: str | None, status: ElegooMachineStatus
) -> MagicMock:
    client = MagicMock()
    client.printer_data = PrinterData()
    client.printer_data.file_list = {n: PrinterFile({"filename": n}) for n in files}
    client.printer_data.selected_file = chosen
    client.printer_data.status.current_status = status
    client.print_start = AsyncMock(return_value=0)
    return client


def test_print_button_starts_the_chosen_file_with_defaults() -> None:
    client = _client([BENCHY], BENCHY, ElegooMachineStatus.IDLE)
    asyncio.run(_print_selected_file_action(client))
    client.print_start.assert_awaited_once_with(BENCHY, tray_id=None)


def test_print_button_passes_the_chosen_tray() -> None:
    client = _client([BENCHY], BENCHY, ElegooMachineStatus.IDLE)
    client.printer_data.selected_tray = 2
    asyncio.run(_print_selected_file_action(client))
    client.print_start.assert_awaited_once_with(BENCHY, tray_id=2)


def test_print_button_does_nothing_without_a_choice() -> None:
    client = _client([BENCHY], None, ElegooMachineStatus.IDLE)
    asyncio.run(_print_selected_file_action(client))
    client.print_start.assert_not_awaited()


def test_print_button_availability() -> None:
    assert _print_selected_file_available(
        _client([BENCHY], BENCHY, ElegooMachineStatus.IDLE)
    )
    assert not _print_selected_file_available(
        _client([BENCHY], None, ElegooMachineStatus.IDLE)
    )
    assert not _print_selected_file_available(
        _client([BENCHY], BENCHY, ElegooMachineStatus.PRINTING)
    )
    # chosen earlier, deleted on the printer since
    assert not _print_selected_file_available(
        _client([], BENCHY, ElegooMachineStatus.IDLE)
    )


def test_print_button_is_the_first_cc2_button() -> None:
    assert [d.key for d in PRINTER_FDM_BUTTONS_CC2_ONLY] == [
        "print_selected_file",
        "refresh_file_list",
    ]


CANVAS = {
    "canvas_list": [
        {
            "canvas_id": 0,
            "connected": 1,
            "tray_list": [
                {
                    "tray_id": 0,
                    "filament_name": "RAPID PLA+",
                    "filament_color": "#D2C5A3",
                },
                {
                    "tray_id": 1,
                    "filament_name": "RAPID PLA+",
                    "filament_color": "#F72221",
                },
                {
                    "tray_id": 2,
                    "filament_name": "PLA Silk",
                    "filament_color": "#000000",
                },
                {
                    "tray_id": 3,
                    "filament_name": "PLA Matte",
                    "filament_color": "#077747",
                },
            ],
        }
    ]
}


def _tray_select(*, canvas: bool = True) -> ElegooPrintTraySelect:
    coordinator = MagicMock()
    coordinator.generate_unique_id = lambda key: f"testsn_{key}"
    coordinator.data = PrinterData()
    if canvas:
        coordinator.data.ams_status = AMSStatus(CANVAS)
    return ElegooPrintTraySelect(coordinator, PRINTER_TRAY_SELECT_CC2[0])


def test_tray_options_and_default() -> None:
    select = _tray_select()
    assert select.options == ["Automatic", "A1", "A2", "A3", "A4"]
    assert select.current_option == "Automatic"
    assert select.coordinator.data.selected_tray is None


def test_tray_choice_maps_to_tray_id() -> None:
    select = _tray_select()
    select.async_write_ha_state = MagicMock()
    asyncio.run(select.async_select_option("A3"))
    assert select.coordinator.data.selected_tray == PRINT_TRAY_OPTIONS["A3"] == 2
    assert select.current_option == "A3"
    asyncio.run(select.async_select_option("Automatic"))
    assert select.coordinator.data.selected_tray is None


def test_tray_attributes_show_current_filaments() -> None:
    select = _tray_select()
    assert select.extra_state_attributes == {
        "A1": "RAPID PLA+ #D2C5A3",
        "A2": "RAPID PLA+ #F72221",
        "A3": "PLA Silk #000000",
        "A4": "PLA Matte #077747",
    }


def test_tray_select_unavailable_without_canvas_status() -> None:
    select = _tray_select(canvas=False)
    with patch(
        "custom_components.elegoo_printer.entity.ElegooPrinterEntity.available",
        new=True,
    ):
        assert select.available is False
        assert select.extra_state_attributes == {}
