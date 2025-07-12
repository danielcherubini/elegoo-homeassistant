"""Tests for ElegooPrinterClient."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import anyio
from aiohttp import ClientSession

from custom_components.elegoo_printer.elegoo_sdcp.client import ElegooPrinterClient
from custom_components.elegoo_printer.elegoo_sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.elegoo_sdcp.models.status import (
    PrinterStatus,
    PrintInfo,
)


@pytest.fixture
def mock_client_session():
    """Fixture for a mock aiohttp client session."""
    return MagicMock(spec=ClientSession)


@pytest.fixture
def elegoo_client(mock_client_session):
    """Fixture for ElegooPrinterClient."""
    with patch(
        "custom_components.elegoo_printer.elegoo_sdcp.client.ElegooPrinterClient._send_printer_cmd",
        new_callable=AsyncMock,
    ) as mock_send_cmd:
        client = ElegooPrinterClient(
            ip_address="127.0.0.1", session=mock_client_session
        )
        client.printer_data.status = PrinterStatus.from_json(
            '{"Data":{"Status":"Idle"}}'
        )
        client._send_printer_cmd = mock_send_cmd
        yield client


@pytest.mark.anyio
async def test_get_current_task_no_task_id(elegoo_client: ElegooPrinterClient):
    """Test getting current task when no task_id is available."""
    elegoo_client.printer_data.status.print_info = PrintInfo()
    task = await elegoo_client.async_get_printer_current_task()
    assert task is None
    elegoo_client._send_printer_cmd.assert_not_called()


@pytest.mark.anyio
async def test_get_current_task_cached(elegoo_client: ElegooPrinterClient):
    """Test getting current task when it is already cached."""
    task_id = "task123"
    elegoo_client.printer_data.status.print_info = PrintInfo({"TaskId": task_id})
    cached_task = PrintHistoryDetail({"TaskId": task_id, "TaskName": "test.gcode"})
    elegoo_client.printer_data.print_history[task_id] = cached_task

    task = await elegoo_client.async_get_printer_current_task()

    assert task is not None
    assert task.task_id == task_id
    assert task.task_name == "test.gcode"
    elegoo_client._send_printer_cmd.assert_not_called()


@pytest.mark.anyio
async def test_get_current_task_not_cached(elegoo_client: ElegooPrinterClient):
    """Test getting current task when it is not cached."""
    task_id = "task456"
    elegoo_client.printer_data.status.print_info = PrintInfo({"TaskId": task_id})

    # Mock the response by populating the cache after the command is "sent"
    async def mock_send_and_populate(*args, **kwargs):
        await anyio.sleep(0.1)  # simulate network delay
        fetched_task = PrintHistoryDetail(
            {"TaskId": task_id, "TaskName": "new_print.gcode"}
        )
        elegoo_client.printer_data.print_history[task_id] = fetched_task

    elegoo_client._send_printer_cmd.side_effect = mock_send_and_populate

    task = await elegoo_client.async_get_printer_current_task()

    assert task is not None
    assert task.task_id == task_id
    assert task.task_name == "new_print.gcode"
    elegoo_client._send_printer_cmd.assert_called_once_with(321, data={"Id": [task_id]})


@pytest.mark.anyio
async def test_get_last_task(elegoo_client: ElegooPrinterClient):
    """Test getting the last task."""
    task1 = PrintHistoryDetail({"TaskId": "task1", "EndTime": 100})
    task2 = PrintHistoryDetail({"TaskId": "task2", "EndTime": 200})
    elegoo_client.printer_data.print_history = {"task1": task1, "task2": task2}

    last_task = await elegoo_client.async_get_printer_last_task()

    assert last_task is not None
    assert last_task.task_id == "task2"
    elegoo_client._send_printer_cmd.assert_not_called()


@pytest.mark.anyio
async def test_get_last_task_needs_fetch(elegoo_client: ElegooPrinterClient):
    """Test getting the last task when details need to be fetched."""
    task_id = "task_last"
    elegoo_client.printer_data.print_history = {
        task_id: None,
        "task_earlier": PrintHistoryDetail({"TaskId": "task_earlier", "EndTime": 50}),
    }

    async def mock_send_and_populate(*args, **kwargs):
        await anyio.sleep(0.1)
        fetched_task = PrintHistoryDetail(
            {"TaskId": task_id, "TaskName": "last_print.gcode", "EndTime": 300}
        )
        elegoo_client.printer_data.print_history[task_id] = fetched_task

    elegoo_client._send_printer_cmd.side_effect = mock_send_and_populate

    last_task = await elegoo_client.async_get_printer_last_task()

    assert last_task is not None
    assert last_task.task_id == task_id
    assert last_task.task_name == "last_print.gcode"
    elegoo_client._send_printer_cmd.assert_called_once_with(321, data={"Id": [task_id]})

