"""
The offline-tolerance option in the UI (#171).

Two things make the option usable for the reporter in #171: it is offered on
every transport's options form, and it can be *enabled while the printer is
switched off*. The WebSocket options step validates a change by connecting to
the printer, and a person who powers their printer on once a month cannot get
through that gate to tick the box that would stop the entry reporting failures.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.data_entry_flow import FlowResultType

from custom_components.elegoo_printer.config_flow import ElegooOptionsFlowHandler
from custom_components.elegoo_printer.const import (
    CONF_ALLOW_PRINTER_OFFLINE,
    CONF_PROXY_ENABLED,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooConfigFlowConnectionError,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer

_DOC_IP = "192.0.2.10"

WEBSOCKET_ENTRY_DATA = {
    "name": "Centauri Carbon",
    "ip_address": _DOC_IP,
    "transport_type": "websocket",
    "protocol_version": "V3",
    "protocol": "V3",
    "id": "test-board-id",
    "model": "Centauri Carbon",
}

MQTT_ENTRY_DATA = {
    **WEBSOCKET_ENTRY_DATA,
    "name": "Saturn 4",
    "model": "Saturn 4",
    "transport_type": "mqtt",
    "protocol_version": "V1",
    "protocol": "V1.0.0",
}

CC2_ENTRY_DATA = {
    **WEBSOCKET_ENTRY_DATA,
    "name": "Centauri Carbon 2",
    "model": "Centauri Carbon 2",
    "transport_type": "cc2_mqtt",
    "protocol_version": "CC2",
    "protocol": "CC2",
}


def _make_options_flow(data: dict[str, Any]) -> ElegooOptionsFlowHandler:
    entry = MagicMock()
    entry.data = dict(data)
    entry.options = {}
    flow = ElegooOptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.flow_id = "options-test-flow"
    flow.handler = "elegoo_printer"
    return flow


async def _form_fields(flow: ElegooOptionsFlowHandler, step: str) -> set[str]:
    """Names of the fields the given options step renders."""
    result = await getattr(flow, f"async_step_{step}")()
    assert result["type"] == FlowResultType.FORM
    return {str(key) for key in result["data_schema"].schema}


class TestOptionIsOfferedOnEveryTransport:
    """All three transports share the failure mode, so all three offer it."""

    async def test_websocket_options_offer_the_option(self) -> None:
        flow = _make_options_flow(WEBSOCKET_ENTRY_DATA)
        assert CONF_ALLOW_PRINTER_OFFLINE in await _form_fields(
            flow, "websocket_options"
        )

    async def test_mqtt_options_offer_the_option(self) -> None:
        flow = _make_options_flow(MQTT_ENTRY_DATA)
        assert CONF_ALLOW_PRINTER_OFFLINE in await _form_fields(flow, "mqtt_options")

    async def test_cc2_options_offer_the_option(self) -> None:
        flow = _make_options_flow(CC2_ENTRY_DATA)
        assert CONF_ALLOW_PRINTER_OFFLINE in await _form_fields(flow, "cc2_options")

    async def test_option_defaults_to_off_when_never_set(self) -> None:
        """Existing entries keep "failed setup" until the user opts in."""
        flow = _make_options_flow(WEBSOCKET_ENTRY_DATA)
        result = await flow.async_step_websocket_options()
        suggested = flow.add_suggested_values_to_schema(
            result["data_schema"], {**WEBSOCKET_ENTRY_DATA}
        )
        rendered = suggested({CONF_ALLOW_PRINTER_OFFLINE: None})
        assert rendered[CONF_ALLOW_PRINTER_OFFLINE] is False


class TestSaveWhilePrinterIsOff:
    """Ticking the box must not require the printer to be reachable."""

    async def test_connection_failure_still_blocks_a_normal_save(self) -> None:
        """Default behaviour unchanged: a bad address re-shows the form."""
        flow = _make_options_flow(WEBSOCKET_ENTRY_DATA)
        with patch(
            "custom_components.elegoo_printer.config_flow._async_test_connection",
            new=AsyncMock(
                side_effect=ElegooConfigFlowConnectionError("no route to host")
            ),
        ):
            result = await flow.async_step_websocket_options(
                user_input={
                    CONF_IP_ADDRESS: _DOC_IP,
                    CONF_PROXY_ENABLED: False,
                    CONF_ALLOW_PRINTER_OFFLINE: False,
                }
            )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "connection"}

    async def test_option_saves_even_when_the_connection_test_fails(self) -> None:
        """The whole point: enabling it while the printer is switched off."""
        flow = _make_options_flow(WEBSOCKET_ENTRY_DATA)
        with patch(
            "custom_components.elegoo_printer.config_flow._async_test_connection",
            new=AsyncMock(
                side_effect=ElegooConfigFlowConnectionError("printer is off")
            ),
        ):
            result = await flow.async_step_websocket_options(
                user_input={
                    CONF_IP_ADDRESS: _DOC_IP,
                    CONF_PROXY_ENABLED: False,
                    CONF_ALLOW_PRINTER_OFFLINE: True,
                }
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_ALLOW_PRINTER_OFFLINE] is True

    async def test_saving_offline_keeps_the_stored_printer_identity(self) -> None:
        """The printer cannot be re-queried, so the stored values must survive."""
        flow = _make_options_flow(WEBSOCKET_ENTRY_DATA)
        with patch(
            "custom_components.elegoo_printer.config_flow._async_test_connection",
            new=AsyncMock(
                side_effect=ElegooConfigFlowConnectionError("printer is off")
            ),
        ):
            result = await flow.async_step_websocket_options(
                user_input={
                    CONF_IP_ADDRESS: "192.0.2.99",
                    CONF_PROXY_ENABLED: False,
                    CONF_ALLOW_PRINTER_OFFLINE: True,
                }
            )

        saved = result["data"]
        assert saved[CONF_IP_ADDRESS] == "192.0.2.99"
        assert saved["id"] == "test-board-id"
        assert saved["model"] == "Centauri Carbon"
        assert saved["name"] == "Centauri Carbon"

    async def test_option_can_be_turned_off_while_the_printer_is_off(self) -> None:
        """Unticking it is an ordinary save and must still be blocked offline."""
        flow = _make_options_flow(
            {**WEBSOCKET_ENTRY_DATA, CONF_ALLOW_PRINTER_OFFLINE: True}
        )
        with patch(
            "custom_components.elegoo_printer.config_flow._async_test_connection",
            new=AsyncMock(
                side_effect=ElegooConfigFlowConnectionError("printer is off")
            ),
        ):
            result = await flow.async_step_websocket_options(
                user_input={
                    CONF_IP_ADDRESS: _DOC_IP,
                    CONF_PROXY_ENABLED: False,
                    CONF_ALLOW_PRINTER_OFFLINE: False,
                }
            )

        assert result["type"] == FlowResultType.FORM

    async def test_a_successful_connection_still_persists_the_option(self) -> None:
        """With the printer on, the option rides along on the normal path."""
        flow = _make_options_flow(WEBSOCKET_ENTRY_DATA)
        tested = Printer.from_dict({**WEBSOCKET_ENTRY_DATA, "ip_address": _DOC_IP})
        with patch(
            "custom_components.elegoo_printer.config_flow._async_test_connection",
            new=AsyncMock(return_value=tested),
        ):
            result = await flow.async_step_websocket_options(
                user_input={
                    CONF_IP_ADDRESS: _DOC_IP,
                    CONF_PROXY_ENABLED: False,
                    CONF_ALLOW_PRINTER_OFFLINE: True,
                }
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_ALLOW_PRINTER_OFFLINE] is True
