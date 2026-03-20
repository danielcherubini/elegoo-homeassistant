"""Tests for CC2 options flow (gcode proxy URL validation)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.data_entry_flow import FlowResultType

from custom_components.elegoo_printer.config_flow import ElegooOptionsFlowHandler
from custom_components.elegoo_printer.const import CONF_GCODE_PROXY_URL

_DOC_IP = "192.0.2.1"

CC2_ENTRY_DATA = {
    "name": "CC2 Unit Test",
    "ip_address": _DOC_IP,
    "transport_type": "cc2_mqtt",
    "protocol_version": "CC2",
    "protocol": "CC2",
    "id": "test-board-id",
    "model": "Elegoo Centauri Carbon 2",
}


def _make_options_flow() -> ElegooOptionsFlowHandler:
    entry = MagicMock()
    entry.data = dict(CC2_ENTRY_DATA)
    entry.options = {}
    flow = ElegooOptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.flow_id = "options-test-flow"
    flow.handler = "elegoo_printer"
    return flow


class TestAsyncStepCc2OptionsProxyUrl:
    """``ElegooOptionsFlowHandler.async_step_cc2_options`` proxy URL errors."""

    def test_invalid_proxy_url_returns_form_error(self) -> None:
        """Normalization failure surfaces ``gcode_proxy_invalid`` on the field."""

        async def _run() -> None:
            flow = _make_options_flow()
            result = await flow.async_step_cc2_options(
                user_input={
                    CONF_IP_ADDRESS: _DOC_IP,
                    CONF_GCODE_PROXY_URL: "http://",
                },
            )
            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "cc2_options"
            assert result["errors"][CONF_GCODE_PROXY_URL] == "gcode_proxy_invalid"

        asyncio.run(_run())

    def test_unreachable_proxy_returns_form_error(self) -> None:
        """Health check failure surfaces ``gcode_proxy_unreachable``."""

        async def _run() -> None:
            flow = _make_options_flow()
            with (
                patch(
                    "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                    return_value=MagicMock(),
                ),
                patch(
                    "custom_components.elegoo_printer.config_flow.GCodeProxyClient",
                ) as mock_cls,
            ):
                mock_cls.return_value.check_health = AsyncMock(return_value=False)
                result = await flow.async_step_cc2_options(
                    user_input={
                        CONF_IP_ADDRESS: _DOC_IP,
                        CONF_GCODE_PROXY_URL: "192.0.2.99",
                    },
                )
            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "cc2_options"
            assert result["errors"][CONF_GCODE_PROXY_URL] == "gcode_proxy_unreachable"
            mock_cls.assert_called_once()
            assert mock_cls.call_args[0][0] == "http://192.0.2.99"

        asyncio.run(_run())
