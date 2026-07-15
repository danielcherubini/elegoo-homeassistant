"""Tests for MQTT options flow (external port validation)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.data_entry_flow import FlowResultType

from custom_components.elegoo_printer.config_flow import ElegooOptionsFlowHandler
from custom_components.elegoo_printer.const import CONF_MQTT_EXTERNAL_PORT

_DOC_IP = "192.0.2.1"

MQTT_ENTRY_DATA = {
    "name": "MQTT Unit Test",
    "ip_address": _DOC_IP,
    "transport_type": "mqtt",
    "protocol_version": "V1",
    "protocol": "V1.0.0",
    "id": "test-board-id",
    "model": "ELEGOO Test Printer",
}


def _make_options_flow() -> ElegooOptionsFlowHandler:
    entry = MagicMock()
    entry.data = dict(MQTT_ENTRY_DATA)
    entry.options = {}
    flow = ElegooOptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.flow_id = "options-test-flow"
    flow.handler = "elegoo_printer"
    return flow


class TestAsyncStepMqttOptionsPortValidation:
    """``ElegooOptionsFlowHandler.async_step_mqtt_options`` port validation."""

    def test_non_numeric_port_returns_form_error(self) -> None:
        """A non-numeric port surfaces ``mqtt_external_port_invalid``."""

        async def _run() -> None:
            flow = _make_options_flow()
            result = await flow.async_step_mqtt_options(
                user_input={
                    CONF_IP_ADDRESS: _DOC_IP,
                    CONF_MQTT_EXTERNAL_PORT: "abc",
                },
            )
            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "mqtt_options"
            assert (
                result["errors"][CONF_MQTT_EXTERNAL_PORT]
                == "mqtt_external_port_invalid"
            )

        asyncio.run(_run())

    def test_out_of_range_port_returns_form_error(self) -> None:
        """A port outside 1-65535 surfaces the same error."""

        async def _run() -> None:
            flow = _make_options_flow()
            result = await flow.async_step_mqtt_options(
                user_input={
                    CONF_IP_ADDRESS: _DOC_IP,
                    CONF_MQTT_EXTERNAL_PORT: "70000",
                },
            )
            assert result["type"] == FlowResultType.FORM
            assert (
                result["errors"][CONF_MQTT_EXTERNAL_PORT]
                == "mqtt_external_port_invalid"
            )

        asyncio.run(_run())

    def test_valid_port_creates_entry(self) -> None:
        """A valid numeric port saves without error."""

        async def _run() -> None:
            flow = _make_options_flow()
            result = await flow.async_step_mqtt_options(
                user_input={
                    CONF_IP_ADDRESS: _DOC_IP,
                    CONF_MQTT_EXTERNAL_PORT: "1883",
                },
            )
            assert result["type"] == FlowResultType.CREATE_ENTRY

        asyncio.run(_run())
