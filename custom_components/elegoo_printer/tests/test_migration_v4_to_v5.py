"""Tests for config entry migration v4 -> v5 (has_canvas field)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from custom_components.elegoo_printer import async_migrate_entry


def _make_config_entry(transport_type: str | None, version: int = 4) -> MagicMock:
  entry = MagicMock()
  data = {"name": "Test Printer", "ip_address": "192.0.2.1", "id": "test-id"}
  if transport_type is not None:
    data["transport_type"] = transport_type
  entry.data = data
  entry.options = {}
  entry.version = version
  return entry


class TestMigrationV4ToV5:
  """v4 -> v5 migration sets has_canvas based on transport_type."""

  def test_cc2_mqtt_sets_has_canvas_true(self) -> None:
    async def _run() -> None:
      hass = MagicMock()
      entry = _make_config_entry("cc2_mqtt")
      result = await async_migrate_entry(hass, entry)
      assert result is True
      call_kwargs = hass.config_entries.async_update_entry.call_args
      assert call_kwargs[1]["data"]["has_canvas"] is True
      assert call_kwargs[1]["version"] == 5

    asyncio.run(_run())

  def test_websocket_sets_has_canvas_false(self) -> None:
    async def _run() -> None:
      hass = MagicMock()
      entry = _make_config_entry("websocket")
      result = await async_migrate_entry(hass, entry)
      assert result is True
      call_kwargs = hass.config_entries.async_update_entry.call_args
      assert call_kwargs[1]["data"]["has_canvas"] is False

    asyncio.run(_run())

  def test_missing_transport_type_sets_has_canvas_false(self) -> None:
    async def _run() -> None:
      hass = MagicMock()
      entry = _make_config_entry(None)
      result = await async_migrate_entry(hass, entry)
      assert result is True
      call_kwargs = hass.config_entries.async_update_entry.call_args
      assert call_kwargs[1]["data"]["has_canvas"] is False

    asyncio.run(_run())
