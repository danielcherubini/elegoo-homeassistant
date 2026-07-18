"""Tests for WebSocket printer options."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.data_entry_flow import FlowResultType

from custom_components.elegoo_printer.config_flow import ElegooOptionsFlowHandler
from custom_components.elegoo_printer.const import CONF_PROXY_ENABLED
from custom_components.elegoo_printer.sdcp.models.printer import Printer

_DOC_IP = "192.0.2.10"
_HOSTNAME = "centauri-carbon.local"

WEBSOCKET_ENTRY_DATA = {
    "name": "Centauri Carbon",
    "ip_address": _DOC_IP,
    "transport_type": "websocket",
    "protocol_version": "V3",
    "protocol": "V3",
    "id": "test-board-id",
    "model": "Centauri Carbon",
}


def _make_options_flow() -> ElegooOptionsFlowHandler:
    entry = MagicMock()
    entry.data = dict(WEBSOCKET_ENTRY_DATA)
    entry.options = {}
    flow = ElegooOptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.flow_id = "options-test-flow"
    flow.handler = "elegoo_printer"
    return flow


class TestAsyncStepWebsocketOptions:
    """WebSocket options connection validation."""

    def test_connection_uses_submitted_hostname(self) -> None:
        async def _run() -> None:
            flow = _make_options_flow()
            tested_printer = Printer.from_dict(
                {**WEBSOCKET_ENTRY_DATA, "ip_address": _HOSTNAME}
            )

            with patch(
                "custom_components.elegoo_printer.config_flow._async_test_connection",
                new=AsyncMock(return_value=tested_printer),
            ) as test_connection:
                result = await flow.async_step_websocket_options(
                    user_input={
                        CONF_IP_ADDRESS: _HOSTNAME,
                        CONF_PROXY_ENABLED: False,
                    }
                )

            assert result["type"] == FlowResultType.CREATE_ENTRY
            submitted_printer = test_connection.await_args.args[1]
            assert submitted_printer.ip_address == _HOSTNAME

        asyncio.run(_run())
