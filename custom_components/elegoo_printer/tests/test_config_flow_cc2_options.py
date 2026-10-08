"""Tests for CC2 options flow (gcode proxy URL validation)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.data_entry_flow import FlowResultType

from custom_components.elegoo_printer.config_flow import (
    MANUAL_IP_SCHEMA,
    ElegooFlowHandler,
    ElegooOptionsFlowHandler,
)
from custom_components.elegoo_printer.const import (
    CONF_CC2_ACCESS_CODE,
    CONF_GCODE_PROXY_URL,
    CONF_PROXY_HOST,
    CONF_SERIAL,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooConfigFlowConnectionError,
)
from custom_components.elegoo_printer.sdcp.models.enums import (
    ProtocolVersion,
    TransportType,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer

if TYPE_CHECKING:
    from collections.abc import Iterator

_DOC_IP = "192.0.2.1"
CC2_MODEL = "Centauri Carbon 2"


def _cc2_options_schema_dict() -> dict:
    """Return the raw CC2 options schema as a plain dict."""
    return dict(ElegooOptionsFlowHandler._cc2_options_schema())


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


class TestManualIpWithProxyHost:
    """
    ``async_step_manual_ip`` short-circuits discovery when ``proxy_host`` is set.

    A forward proxy does not answer UDP discovery, so a user who enters the
    proxy host cannot be discovered — the flow must build the CC2 printer
    directly instead.
    """

    PROXY = "192.0.2.50"
    PRINTER_IP = "192.0.2.1"

    @staticmethod
    def _make_flow() -> ElegooFlowHandler:
        flow = ElegooFlowHandler()
        flow.hass = MagicMock()
        flow.flow_id = "config-test-flow"
        flow.handler = "elegoo_printer"
        return flow

    def test_proxy_host_skips_discovery_and_routes_to_cc2_auth(self) -> None:
        """With proxy_host, neither discovery path runs and the CC2 flow starts."""

        async def _run() -> None:
            flow = self._make_flow()
            with (
                patch.object(
                    ElegooFlowHandler,
                    "async_set_unique_id",
                    new=AsyncMock(),
                ),
                patch.object(
                    ElegooFlowHandler, "_abort_if_unique_id_configured"
                ) as abort_mock,
                patch(
                    "custom_components.elegoo_printer.config_flow.ElegooPrinterClient"
                ) as ws_client_mock,
                patch(
                    "custom_components.elegoo_printer.config_flow.CC2Discovery"
                ) as cc2_discovery_mock,
            ):
                result = await flow.async_step_manual_ip(
                    {
                        CONF_IP_ADDRESS: self.PRINTER_IP,
                        CONF_PROXY_HOST: self.PROXY,
                    }
                )

            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "cc2_auth_check"
            ws_client_mock.assert_not_called()
            cc2_discovery_mock.discover_as_printers.assert_not_called()
            abort_mock.assert_called_once()

            printer = flow.selected_printer
            assert printer is not None
            assert printer.proxy_host == self.PROXY
            assert printer.connection_host == self.PROXY
            assert printer.ip_address == self.PRINTER_IP
            assert printer.transport_type == TransportType.CC2_MQTT
            assert printer.protocol_version == ProtocolVersion.CC2
            assert printer.model == CC2_MODEL
            assert printer.mqtt_broker_enabled is False

        asyncio.run(_run())

    def test_proxy_host_survives_the_connection_round_trip(self) -> None:
        """
        The proxy-built printer keeps proxy_host through ``to_dict``/``from_dict``.

        ``_attempt_cc2_connection`` rebuilds the printer this way, so the proxy
        must not be lost between the manual-IP step and the connection test.
        """

        async def _run() -> None:
            flow = self._make_flow()
            with (
                patch.object(ElegooFlowHandler, "async_set_unique_id", new=AsyncMock()),
                patch.object(ElegooFlowHandler, "_abort_if_unique_id_configured"),
                patch(
                    "custom_components.elegoo_printer.config_flow.ElegooPrinterClient"
                ),
                patch("custom_components.elegoo_printer.config_flow.CC2Discovery"),
            ):
                await flow.async_step_manual_ip(
                    {
                        CONF_IP_ADDRESS: self.PRINTER_IP,
                        CONF_PROXY_HOST: self.PROXY,
                    }
                )

            rebuilt = Printer.from_dict(flow.selected_printer.to_dict())

            assert rebuilt.proxy_host == self.PROXY
            assert rebuilt.connection_host == self.PROXY
            assert rebuilt.transport_type == TransportType.CC2_MQTT

        asyncio.run(_run())

    def test_proxy_host_with_blank_ip_shows_error(self) -> None:
        """A blank printer IP still errors instead of falling through to discovery."""

        async def _run() -> None:
            flow = self._make_flow()
            with (
                patch(
                    "custom_components.elegoo_printer.config_flow.ElegooPrinterClient"
                ) as ws_client_mock,
                patch(
                    "custom_components.elegoo_printer.config_flow.CC2Discovery"
                ) as cc2_discovery_mock,
            ):
                result = await flow.async_step_manual_ip(
                    {CONF_IP_ADDRESS: "   ", CONF_PROXY_HOST: self.PROXY}
                )

            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "manual_ip"
            assert result["errors"]["base"] == "manual_ip_no_valid_ip"
            ws_client_mock.assert_not_called()
            cc2_discovery_mock.discover_as_printers.assert_not_called()

        asyncio.run(_run())

    def test_blank_proxy_host_runs_discovery_as_before(self) -> None:
        """An empty proxy_host keeps the existing discovery path untouched."""

        async def _run() -> None:
            flow = self._make_flow()
            discovered = Printer.from_dict(
                {
                    "name": "Discovered CC2",
                    "ip_address": self.PRINTER_IP,
                    "id": "discovered-sn",
                    "model": CC2_MODEL,
                    "transport_type": "cc2_mqtt",
                    "protocol_version": "CC2",
                }
            )
            flow.hass.async_add_executor_job = AsyncMock(return_value=[discovered])
            with (
                patch.object(ElegooFlowHandler, "async_set_unique_id", new=AsyncMock()),
                patch.object(ElegooFlowHandler, "_abort_if_unique_id_configured"),
                patch(
                    "custom_components.elegoo_printer.config_flow.ElegooPrinterClient"
                ),
            ):
                result = await flow.async_step_manual_ip(
                    {CONF_IP_ADDRESS: self.PRINTER_IP, CONF_PROXY_HOST: ""}
                )

            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "cc2_auth_check"
            assert flow.selected_printer is discovered
            assert flow.selected_printer.proxy_host is None
            flow.hass.async_add_executor_job.assert_awaited()

        asyncio.run(_run())

    def test_manual_ip_schema_offers_proxy_host(self) -> None:
        """The manual-IP form exposes an optional ``proxy_host`` field."""
        assert CONF_PROXY_HOST in MANUAL_IP_SCHEMA.schema


class TestCc2OptionsProxyHost:
    """``cc2_options`` persists ``proxy_host``, including the cleared value."""

    PROXY = "192.0.2.50"

    def test_proxy_host_persisted_when_set(self) -> None:
        """A filled proxy_host lands in the created entry data."""

        async def _run() -> None:
            flow = _make_options_flow()
            result = await flow.async_step_cc2_options(
                user_input={
                    CONF_IP_ADDRESS: _DOC_IP,
                    CONF_PROXY_HOST: self.PROXY,
                },
            )

            assert result["type"] == FlowResultType.CREATE_ENTRY
            assert result["data"][CONF_PROXY_HOST] == self.PROXY

        asyncio.run(_run())

    def test_cleared_proxy_host_persisted_as_empty_string(self) -> None:
        """
        Clearing the field stores ``""`` — popping it would not disable the proxy.

        The options flow merges data-first (``{**entry.data, **entry.options}``),
        so a key that only exists in ``entry.data`` cannot be removed by
        options; only an empty-string override disables the proxy.
        """

        async def _run() -> None:
            flow = _make_options_flow()
            flow.config_entry.data = {**CC2_ENTRY_DATA, CONF_PROXY_HOST: self.PROXY}
            result = await flow.async_step_cc2_options(
                user_input={CONF_IP_ADDRESS: _DOC_IP, CONF_PROXY_HOST: ""},
            )

            assert result["type"] == FlowResultType.CREATE_ENTRY
            assert result["data"][CONF_PROXY_HOST] == ""
            merged = {**flow.config_entry.data, **result["data"]}
            assert Printer.from_dict(merged).connection_host == _DOC_IP

        asyncio.run(_run())

    def test_cc2_options_schema_offers_proxy_host(self) -> None:
        """The CC2 options form exposes an optional ``proxy_host`` field."""
        assert CONF_PROXY_HOST in _cc2_options_schema_dict()


class TestProxySerialHybrid:
    """
    A proxy entry must acquire the serial before it can register (#414).

    Discovery is what normally supplies the serial, and the proxy path skips
    it. The flow auto-learns it over MQTT and falls back to prompting.
    """

    PROXY = "192.0.2.50"
    PRINTER_IP = "192.0.2.1"
    SERIAL = "SERIAL12345"

    @staticmethod
    def _make_flow(*, proxy_host: str | None = PROXY) -> ElegooFlowHandler:
        flow = ElegooFlowHandler()
        flow.hass = MagicMock()
        flow.flow_id = "config-test-flow"
        flow.handler = "elegoo_printer"
        flow.selected_printer = Printer.from_dict(
            {
                "name": "CC2 Proxy Unit Test",
                "ip_address": TestProxySerialHybrid.PRINTER_IP,
                "model": CC2_MODEL,
                "transport_type": "cc2_mqtt",
                "protocol_version": "CC2",
                CONF_PROXY_HOST: proxy_host,
            }
        )
        flow._requires_access_code = False
        return flow

    @staticmethod
    @contextlib.contextmanager
    def _flow_context(test_connection: object) -> Iterator[None]:
        """Patch away the unique-id bookkeeping and the live connection test."""
        with (
            patch.object(ElegooFlowHandler, "async_set_unique_id", new=AsyncMock()),
            patch.object(ElegooFlowHandler, "_abort_if_unique_id_configured"),
            patch(
                "custom_components.elegoo_printer.config_flow._async_test_connection",
                test_connection,
            ),
        ):
            yield

    def test_auto_learned_serial_skips_the_prompt(self) -> None:
        """A learned serial is stored and the connection test runs immediately."""

        async def _run() -> None:
            flow = self._make_flow()
            seen: dict[str, Any] = {}

            async def fake_test(_hass: Any, printer: Printer, _input: dict) -> Printer:
                seen["id"] = printer.id
                seen["connection"] = printer.connection
                return printer

            with (
                self._flow_context(fake_test),
                patch(
                    "custom_components.elegoo_printer.cc2.client.ElegooCC2Client."
                    "async_discover_serial",
                    new=AsyncMock(return_value=self.SERIAL),
                ) as learn_mock,
            ):
                result = await flow._attempt_cc2_connection(access_code=None)

            assert result["type"] == FlowResultType.CREATE_ENTRY
            learn_mock.assert_awaited_once()
            assert seen == {"id": self.SERIAL, "connection": self.SERIAL}
            assert flow.selected_printer.id == self.SERIAL

        asyncio.run(_run())

    def test_failed_auto_learn_prompts_for_the_serial(self) -> None:
        """When nothing can be learned, the user is asked instead of failing."""

        async def _run() -> None:
            flow = self._make_flow()

            async def fake_test(_hass: Any, printer: Printer, _input: dict) -> Printer:
                return printer

            with (
                self._flow_context(fake_test),
                patch(
                    "custom_components.elegoo_printer.cc2.client.ElegooCC2Client."
                    "async_discover_serial",
                    new=AsyncMock(return_value=None),
                ),
            ):
                result = await flow._attempt_cc2_connection(access_code=None)

            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "cc2_serial_input"

        asyncio.run(_run())

    def test_serial_prompt_sets_the_serial_and_continues(self) -> None:
        """A typed serial breaks the prompt loop by resuming the connection test."""

        async def _run() -> None:
            flow = self._make_flow()
            seen: dict[str, Any] = {}

            async def fake_test(_hass: Any, printer: Printer, _input: dict) -> Printer:
                seen["id"] = printer.id
                return printer

            with self._flow_context(fake_test):
                result = await flow.async_step_cc2_serial_input(
                    {CONF_SERIAL: self.SERIAL}
                )

            assert result["type"] == FlowResultType.CREATE_ENTRY
            assert seen["id"] == self.SERIAL
            assert flow.selected_printer.connection == self.SERIAL

        asyncio.run(_run())

    def test_blank_serial_prompt_shows_an_error(self) -> None:
        """A blank serial re-shows the form rather than retrying the connection."""

        async def _run() -> None:
            flow = self._make_flow()

            async def fail_test(*_args: Any) -> Printer:
                msg = "connection test must not run"
                raise AssertionError(msg)

            with self._flow_context(fail_test):
                result = await flow.async_step_cc2_serial_input({CONF_SERIAL: "  "})

            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "cc2_serial_input"
            assert result["errors"]["base"] == "cc2_serial_required"
            assert flow.selected_printer.id is None

        asyncio.run(_run())

    def test_prompt_resumes_with_the_stored_access_code(self) -> None:
        """
        The access code survives the serial prompt.

        It is a local parameter of ``_attempt_cc2_connection``, so without
        threading it onto the handler a prompt would resume with ``None`` and an
        access-code-protected printer would be retried with fallback passwords
        only — and fail.
        """

        async def _run() -> None:
            flow = self._make_flow()
            flow._requires_access_code = True
            seen: dict[str, Any] = {}

            async def fake_test(
                _hass: Any, printer: Printer, user_input: dict
            ) -> Printer:
                seen["user_input"] = dict(user_input)
                return printer

            with (
                self._flow_context(fake_test),
                patch(
                    "custom_components.elegoo_printer.cc2.client.ElegooCC2Client."
                    "async_discover_serial",
                    new=AsyncMock(return_value=self.SERIAL),
                ) as learn_mock,
            ):
                # First pass stores the code, then hits the serial prompt.
                learn_mock.return_value = None
                prompt = await flow._attempt_cc2_connection(access_code="secret")
                assert prompt["step_id"] == "cc2_serial_input"

                # The prompt resumes the connection test with the same code.
                result = await flow.async_step_cc2_serial_input(
                    {CONF_SERIAL: self.SERIAL}
                )

            assert result["type"] == FlowResultType.CREATE_ENTRY
            assert seen["user_input"][CONF_CC2_ACCESS_CODE] == "secret"

        asyncio.run(_run())

    def test_auto_learn_client_reaches_the_proxy(self) -> None:
        """The probe dials the proxy with an empty serial, not the printer IP."""

        async def _run() -> None:
            flow = self._make_flow()

            async def fake_test(_hass: Any, printer: Printer, _input: dict) -> Printer:
                return printer

            cc2_client_cls = MagicMock(name="ElegooCC2Client")
            cc2_client_cls.return_value.async_discover_serial = AsyncMock(
                return_value=self.SERIAL
            )
            cc2_client_cls.return_value.disconnect = AsyncMock()

            with (
                self._flow_context(fake_test),
                patch(
                    "custom_components.elegoo_printer.cc2.client.ElegooCC2Client",
                    cc2_client_cls,
                ),
            ):
                await flow._attempt_cc2_connection(access_code="secret")

            learn_kwargs = cc2_client_cls.call_args_list[0].kwargs
            assert learn_kwargs["printer_ip"] == self.PROXY
            assert learn_kwargs["serial_number"] == ""
            assert learn_kwargs["access_code"] == "secret"
            # The probe is transient: it must always be closed again.
            cc2_client_cls.return_value.disconnect.assert_awaited()

        asyncio.run(_run())

    def test_unreachable_proxy_reports_a_distinct_error(self) -> None:
        """A proxy that cannot be reached is not reported as a bad access code."""

        async def _run() -> None:
            flow = self._make_flow()

            async def refuse(_hass: Any, _printer: Printer, _input: dict) -> Printer:
                msg = "Cannot reach the proxy"
                raise ElegooConfigFlowConnectionError(msg)

            with (
                self._flow_context(refuse),
                patch(
                    "custom_components.elegoo_printer.cc2.client.ElegooCC2Client."
                    "async_discover_serial",
                    new=AsyncMock(return_value=self.SERIAL),
                ),
            ):
                result = await flow._attempt_cc2_connection(access_code=None)

            assert result["type"] == FlowResultType.FORM
            assert result["errors"]["base"] == "cc2_proxy_unreachable"

        asyncio.run(_run())

    def test_direct_printer_still_reports_authentication_failed(self) -> None:
        """Without a proxy the failure stays ``cc2_authentication_failed``."""

        async def _run() -> None:
            flow = self._make_flow(proxy_host=None)

            async def refuse(_hass: Any, _printer: Printer, _input: dict) -> Printer:
                msg = "Failed to authenticate"
                raise ElegooConfigFlowConnectionError(msg)

            with (
                self._flow_context(refuse),
                patch(
                    "custom_components.elegoo_printer.cc2.client.ElegooCC2Client."
                    "async_discover_serial",
                    new=AsyncMock(),
                ) as learn_mock,
            ):
                result = await flow._attempt_cc2_connection(access_code=None)

            assert result["errors"]["base"] == "cc2_authentication_failed"
            learn_mock.assert_not_awaited()

        asyncio.run(_run())

    def test_connection_test_targets_the_proxy_host(self) -> None:
        """
        The live connection test client is built against ``connection_host``.

        Runs the real ``_async_test_connection`` (only the CC2 client class is
        doubled) so the constructor arguments are genuinely pinned.
        """

        async def _run() -> None:
            flow = self._make_flow()

            cc2_client_cls = MagicMock(name="ElegooCC2Client")
            instance = cc2_client_cls.return_value
            instance.async_discover_serial = AsyncMock(return_value=self.SERIAL)
            instance.connect_printer = AsyncMock(return_value=True)
            instance.disconnect = AsyncMock()
            instance.access_code = ""
            instance.is_connected = False

            with (
                patch.object(ElegooFlowHandler, "async_set_unique_id", new=AsyncMock()),
                patch.object(ElegooFlowHandler, "_abort_if_unique_id_configured"),
                patch(
                    "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                    return_value=MagicMock(),
                ),
                patch(
                    "custom_components.elegoo_printer.cc2.client.ElegooCC2Client",
                    cc2_client_cls,
                ),
            ):
                result = await flow._attempt_cc2_connection(access_code=None)

            assert result["type"] == FlowResultType.CREATE_ENTRY
            test_kwargs = cc2_client_cls.call_args_list[-1].kwargs
            assert test_kwargs["printer_ip"] == self.PROXY
            # The learned serial is what the real connection registers with.
            assert test_kwargs["serial_number"] == self.SERIAL

        asyncio.run(_run())
