"""Tests for CC1 per-slot filament fetching from the gcode capture proxy."""

import asyncio
from typing import Any
from unittest.mock import MagicMock

from custom_components.elegoo_printer.sdcp.models.printer import FileFilamentData
from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient

PROXY_PAYLOAD: dict[str, Any] = {
    "filename": "CC1_benchy.gcode",
    "slicer_version": "ElegooSlicer 1.3.2.9",
    "filament": {
        "per_slot_mm": [0.0, 0.0, 0.0, 500.5],
        "per_slot_cm3": [0.0, 0.0, 0.0, 1.1],
        "per_slot_grams": [0.0, 0.0, 0.0, 1.4],
        "per_slot_cost": [0.0, 0.0, 0.0, 0.05],
        "per_slot_density": [0.0, 0.0, 0.0, 1.24],
        "per_slot_diameter": [0.0, 0.0, 0.0, 1.75],
        "filament_names": ["", "", "", "ElegooPLA-Basic-White"],
        "total_grams": 1.4,
        "total_cost": 0.05,
        "total_filament_changes": 0,
        "estimated_time": "1h 18m 10s",
    },
}


class _FakeGcodeProxy:
    """Stub proxy client returning a canned payload."""

    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def fetch_filament_data(self, filename: str) -> dict[str, Any] | None:
        self.calls.append(filename)
        return self.payload


def _make_client(proxy: _FakeGcodeProxy | None) -> ElegooPrinterClient:
    return ElegooPrinterClient(
        "192.168.1.50",
        session=MagicMock(),
        gcode_proxy=proxy,  # type: ignore[arg-type]
    )


def test_from_proxy_payload_maps_slots() -> None:
    """Proxy payload maps onto FileFilamentData per-slot fields."""
    data = FileFilamentData.from_proxy_payload(PROXY_PAYLOAD)
    assert data is not None
    assert data.per_slot_grams == [0.0, 0.0, 0.0, 1.4]
    assert data.filament_names[3] == "ElegooPLA-Basic-White"
    assert data.total_filament_used == 1.4
    assert data.filename == "CC1_benchy.gcode"
    assert data.slicer_version == "ElegooSlicer 1.3.2.9"


def test_from_proxy_payload_empty_returns_none() -> None:
    """A payload with no filament block yields None."""
    assert FileFilamentData.from_proxy_payload({"filename": "x.gcode"}) is None


def test_fetch_populates_printer_data() -> None:
    """A new print job triggers one proxy fetch and caches the result."""

    async def run() -> None:
        proxy = _FakeGcodeProxy(PROXY_PAYLOAD)
        client = _make_client(proxy)

        client._maybe_fetch_gcode_filament("CC1_benchy.gcode", "task-1")
        await asyncio.gather(*client._background_tasks)

        data = client.printer_data.gcode_filament_data
        assert data is not None
        assert data.per_slot_grams == [0.0, 0.0, 0.0, 1.4]
        assert proxy.calls == ["CC1_benchy.gcode"]

        # Repeated status pushes for the same job don't refetch
        client._maybe_fetch_gcode_filament("CC1_benchy.gcode", "task-1")
        await asyncio.gather(*client._background_tasks)
        assert proxy.calls == ["CC1_benchy.gcode"]

    asyncio.run(run())


def test_same_filename_new_task_refetches() -> None:
    """
    Re-printing the same filename as a new job refetches proxy data.

    Covers the multi-plate workflow: the same project file is re-sliced per
    plate and uploaded under an identical filename, so the job id is the
    only signal that the content changed.
    """

    async def run() -> None:
        proxy = _FakeGcodeProxy(PROXY_PAYLOAD)
        client = _make_client(proxy)

        client._maybe_fetch_gcode_filament("project.gcode", "task-1")
        await asyncio.gather(*client._background_tasks)
        assert proxy.calls == ["project.gcode"]

        # Same filename, new task — plate 2 of the same project
        client._maybe_fetch_gcode_filament("project.gcode", "task-2")
        # Previous job's data is dropped before the refetch lands
        await asyncio.gather(*client._background_tasks)
        assert proxy.calls == ["project.gcode", "project.gcode"]
        assert client.printer_data.gcode_filament_data is not None

    asyncio.run(run())


def test_new_filename_clears_previous_data() -> None:
    """A new print drops the previous print's filament data before fetching."""

    async def run() -> None:
        proxy = _FakeGcodeProxy(PROXY_PAYLOAD)
        client = _make_client(proxy)

        client._maybe_fetch_gcode_filament("first.gcode", "task-1")
        await asyncio.gather(*client._background_tasks)
        assert client.printer_data.gcode_filament_data is not None

        proxy.payload = None  # second file unknown to the proxy
        client._maybe_fetch_gcode_filament("second.gcode", "task-2")
        assert client.printer_data.gcode_filament_data is None
        await asyncio.gather(*client._background_tasks)
        assert client.printer_data.gcode_filament_data is None
        assert proxy.calls == ["first.gcode", "second.gcode"]

    asyncio.run(run())


def test_failed_fetch_does_not_mark_fetched() -> None:
    """A proxy miss leaves the job eligible for retry after the window."""

    async def run() -> None:
        proxy = _FakeGcodeProxy(None)
        client = _make_client(proxy)

        client._maybe_fetch_gcode_filament("missing.gcode", "task-1")
        await asyncio.gather(*client._background_tasks)

        assert client.printer_data.gcode_filament_data is None
        assert client._gcode_filament_fetched is None
        # Within the retry window the same job is not re-queried
        client._maybe_fetch_gcode_filament("missing.gcode", "task-1")
        assert proxy.calls == ["missing.gcode"]
        # After the window expires it retries
        client._gcode_filament_attempt_at = 0.0
        client._maybe_fetch_gcode_filament("missing.gcode", "task-1")
        await asyncio.gather(*client._background_tasks)
        assert proxy.calls == ["missing.gcode", "missing.gcode"]

    asyncio.run(run())


def test_pathed_filename_queries_basename() -> None:
    """A storage-path filename from SDCP status queries the bare name."""

    async def run() -> None:
        proxy = _FakeGcodeProxy(PROXY_PAYLOAD)
        client = _make_client(proxy)

        client._maybe_fetch_gcode_filament("/local/deep_part.gcode", "task-1")
        await asyncio.gather(*client._background_tasks)

        assert proxy.calls == ["deep_part.gcode"]
        assert client.printer_data.gcode_filament_data is not None

    asyncio.run(run())


def test_no_proxy_or_no_task_is_noop() -> None:
    """Without a proxy client or an active job the hook does nothing."""
    client = _make_client(None)
    client._maybe_fetch_gcode_filament("anything.gcode", "task-1")
    assert not client._background_tasks

    proxy = _FakeGcodeProxy(PROXY_PAYLOAD)
    client = _make_client(proxy)
    client._maybe_fetch_gcode_filament("anything.gcode", None)
    client._maybe_fetch_gcode_filament(None, "task-1")
    assert not client._background_tasks
    assert client.printer_data.gcode_filament_data is None
