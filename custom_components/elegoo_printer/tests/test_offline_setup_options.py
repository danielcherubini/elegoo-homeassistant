"""
The offline-tolerance option in the UI (#171).

Two things make the option usable for the reporter in #171: it is offered on
every transport's options form, and it can be *enabled while the printer is
switched off*. The WebSocket options step validates a change by connecting to
the printer, and a person who powers their printer on once a month cannot get
through that gate to tick the box that would stop the entry reporting failures.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import PlatformNotReady

from custom_components.elegoo_printer.config_flow import ElegooOptionsFlowHandler
from custom_components.elegoo_printer.const import (
    CONF_ALLOW_PRINTER_OFFLINE,
    CONF_GCODE_PROXY_URL,
    CONF_PROXY_ENABLED,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooConfigFlowConnectionError,
    ElegooConfigFlowGeneralError,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer

_DOC_IP = "192.0.2.10"
_BLANK_ADDRESSES = ["", "   ", "\t"]

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


def _connection_failures() -> list[Exception]:
    """
    Every exception the WebSocket connection test is allowed to raise.

    Each one has its own ``except`` branch in ``_process_websocket_options_input``,
    and all four used to log the failure at ERROR (the ``OSError`` one with a full
    traceback) right before the offline save.
    """
    return [
        ElegooConfigFlowConnectionError("printer is off"),
        ElegooConfigFlowGeneralError("printer is off"),
        PlatformNotReady("printer is off"),
        OSError("printer is off"),
    ]


_CONNECTION_FAILURES = _connection_failures()


def _make_options_flow(data: dict[str, Any]) -> ElegooOptionsFlowHandler:
    entry = MagicMock()
    entry.data = dict(data)
    entry.options = {}
    flow = ElegooOptionsFlowHandler(entry)
    flow.hass = MagicMock()
    # HA >= 2025.12 resolves ``OptionsFlow.config_entry`` through the config-entry
    # registry; the 2025.4 floor short-circuits on the ``_config_entry`` attribute,
    # so this stub is inert there.
    flow.hass.config_entries.async_get_known_entry.return_value = entry
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

    async def test_a_bad_gcode_proxy_url_still_blocks_an_offline_save(self) -> None:
        """
        The offline escape hatch must not bypass the gcode-proxy health check.

        Ticking "allow printer offline" only skips the *printer* connection
        test. A proxy URL the user typed is validated before that, so a proxy
        that cannot be reached re-shows the form with the error on the URL field
        instead of saving a broken proxy address while the printer happens to be
        off. This is the invariant that lets the offline save sit after the
        proxy check in ``_process_websocket_options_input``.
        """
        flow = _make_options_flow(WEBSOCKET_ENTRY_DATA)
        with (
            patch(
                "custom_components.elegoo_printer.config_flow._async_test_connection",
                new=AsyncMock(
                    side_effect=ElegooConfigFlowConnectionError("printer is off")
                ),
            ),
            patch.object(
                ElegooOptionsFlowHandler,
                "_async_validate_gcode_proxy",
                new=AsyncMock(return_value=(None, "gcode_proxy_unreachable")),
            ),
        ):
            result = await flow.async_step_websocket_options(
                user_input={
                    CONF_IP_ADDRESS: _DOC_IP,
                    CONF_PROXY_ENABLED: False,
                    CONF_ALLOW_PRINTER_OFFLINE: True,
                    CONF_GCODE_PROXY_URL: "192.0.2.99:8080",
                }
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "websocket_options"
        assert result["errors"][CONF_GCODE_PROXY_URL] == "gcode_proxy_unreachable"

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

    @pytest.mark.parametrize("failure", _CONNECTION_FAILURES)
    async def test_an_offline_save_logs_nothing_above_info(
        self, caplog: pytest.LogCaptureFixture, failure: Exception
    ) -> None:
        """
        The whole point of #171 is log spam, so the save must be quiet.

        The connection test is expected to fail for this entry, so the failure
        that precedes an offline save is an ordinary event: nothing at WARNING
        or above may be emitted, and no traceback at all (the ``OSError`` branch
        used to call ``LOGGER.exception``). The failure is still recorded, only
        at DEBUG.
        """
        flow = _make_options_flow(WEBSOCKET_ENTRY_DATA)
        with (
            caplog.at_level(logging.DEBUG),
            patch(
                "custom_components.elegoo_printer.config_flow._async_test_connection",
                new=AsyncMock(side_effect=failure),
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
        loud = [r for r in caplog.records if r.levelno > logging.INFO]
        assert not loud, [f"{r.levelname}: {r.getMessage()}" for r in loud]
        assert not [r for r in caplog.records if r.exc_info], "no traceback"
        notes = [
            r for r in caplog.records if str(failure) in r.getMessage()
        ]  # downgraded, not deleted
        assert notes, f"{failure!r} vanished from the log"

    @pytest.mark.parametrize("failure", _CONNECTION_FAILURES)
    async def test_a_blocked_save_still_logs_the_error(
        self, caplog: pytest.LogCaptureFixture, failure: Exception
    ) -> None:
        """
        Diagnostics are not deleted: without the option the failure is an error.

        The offline quieting must be conditional. A failed *normal* save is a
        real problem, so every branch keeps its previous ERROR -- including the
        ``OSError`` traceback.
        """
        flow = _make_options_flow(WEBSOCKET_ENTRY_DATA)
        with (
            caplog.at_level(logging.INFO),
            patch(
                "custom_components.elegoo_printer.config_flow._async_test_connection",
                new=AsyncMock(side_effect=failure),
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
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "quieting the offline save must not remove diagnostics"
        assert str(failure) in "\n".join(r.getMessage() for r in errors)
        if isinstance(failure, OSError):
            assert [r for r in errors if r.exc_info], "traceback kept"

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


class TestBlankAddressIsNeverSaved:
    """
    A blank address is a typo, not a switched-off printer (#171 review).

    Before this option an empty address failed the connection test and the form
    was re-shown, so nothing was saved. The offline save removed that accident,
    and the schema field gained a ``default=""``, so submitting the form with the
    address cleared would persist ``ip_address: ""`` - an entry that can never
    connect and, per the documented trade-off of the option, fails silently.
    """

    @pytest.mark.parametrize("blank", _BLANK_ADDRESSES)
    @pytest.mark.parametrize("allow_offline", [True, False])
    async def test_a_blank_address_reopens_the_form_instead_of_saving(
        self,
        blank: str,
        allow_offline: bool,  # noqa: FBT001
    ) -> None:
        """A cleared address re-shows the form, whichever way the option is set."""
        flow = _make_options_flow(WEBSOCKET_ENTRY_DATA)
        with patch(
            "custom_components.elegoo_printer.config_flow._async_test_connection",
            new=AsyncMock(
                side_effect=ElegooConfigFlowConnectionError("printer is off")
            ),
        ) as test_connection:
            result = await flow.async_step_websocket_options(
                user_input={
                    CONF_IP_ADDRESS: blank,
                    CONF_PROXY_ENABLED: False,
                    CONF_ALLOW_PRINTER_OFFLINE: allow_offline,
                }
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "websocket_options"
        assert result["errors"][CONF_IP_ADDRESS] == "manual_ip_no_valid_ip"
        # A blank address is refused before the pointless connection attempt.
        test_connection.assert_not_awaited()

    async def test_a_typed_address_is_untouched_by_the_guard(self) -> None:
        """The guard only refuses blanks: a real address still saves."""
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
        assert result["data"][CONF_IP_ADDRESS] == _DOC_IP

    @pytest.mark.parametrize(
        ("entry_data", "step"),
        [(MQTT_ENTRY_DATA, "mqtt_options"), (CC2_ENTRY_DATA, "cc2_options")],
    )
    async def test_the_other_transports_refuse_a_blank_address_too(
        self, entry_data: dict[str, Any], step: str
    ) -> None:
        """
        MQTT and CC2 options save without a connection test at all.

        They have no gate to bypass, so a cleared address reaches storage on the
        same key; the refusal is the same guard with the same translation key.
        """
        flow = _make_options_flow(entry_data)
        result = await getattr(flow, f"async_step_{step}")(
            user_input={CONF_IP_ADDRESS: "  ", CONF_ALLOW_PRINTER_OFFLINE: True}
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == step
        assert result["errors"][CONF_IP_ADDRESS] == "manual_ip_no_valid_ip"
