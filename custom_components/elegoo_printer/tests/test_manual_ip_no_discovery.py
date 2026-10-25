"""
Manual IP when UDP 3000 discovery is unanswered (#429 / #431).

A Centauri Carbon CC1 on an IoT VLAN stops answering SDCP discovery (UDP 3000)
as soon as it cannot ping its default gateway — it decides the network is down —
while its control socket, TCP 3030, stays open the whole time (#431, with a
router capture behind it). Discovery and reachability are different questions,
and this integration conflated them, so a user who typed the printer's IP by
hand was told "no printer found" about a printer they could telnet to.

Two walls are pinned here:

* setup — ``async_step_manual_ip`` required a discovery reply before it would
  describe the printer at all, and
* runtime — ``api.py`` decided WebSocket reachability with
  ``_discover_printer_with_fallback``, which asks UDP only, so an entry that
  survived the first wall would have sat in "Failed setup, will retry" forever.

The probe reads the printer's identity from the live connection and refuses
anything it cannot use: ``printer.id`` is the config entry's unique id, the
device identifier and the entity unique-id prefix all at once, and nothing in
the integration repairs it afterwards, so a frame that yields no usable id ends
in ``no_printer_found`` rather than an entry carrying a made-up one.
"""

from __future__ import annotations

import contextlib
import socket
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.data_entry_flow import FlowResultType

import custom_components.elegoo_printer.api as api_module
from custom_components.elegoo_printer.api import ElegooPrinterApiClient
from custom_components.elegoo_printer.config_flow import ElegooFlowHandler
from custom_components.elegoo_printer.const import CONF_PROXY_ENABLED
from custom_components.elegoo_printer.sdcp.models.enums import (
    PrinterType,
    TransportType,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer
from custom_components.elegoo_printer.sdcp.transport.discovery import (
    async_tcp_port_is_open,
)
from custom_components.elegoo_printer.tests.ws_doubles import (
    DOC_IP,
    MAINBOARD_ID,
    cc1_self_reported_attributes,
    flat_attributes_frame,
    make_config_flow,
    nested_attributes_frame,
    pass_printer_through,
    session_refusing,
    session_returning,
    text_msg,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _current_status_frame() -> dict[str, Any]:
    """
    Build a real status frame carrying ``CurrentStatus`` and nothing else.

    A printer pushes this while printing. It is deliberately NOT the Canvas
    status frame from ``ws_doubles.ams_status_frame``: that one answers the
    Canvas question with ``AmsConnectStatus``, while this one says something
    ordinary that both probes must decline to read as an answer. Two payloads,
    two names.
    """
    return {
        "Topic": f"sdcp/status/{MAINBOARD_ID}",
        "Data": {"Data": {"Status": {"CurrentStatus": [0]}}},
    }


@contextlib.contextmanager
def _silent_discovery(session: MagicMock | None = None) -> Any:
    """
    Both UDP protocols answer nothing; the live socket yields ``session``'s frames.

    Mirrors the #431 printer exactly: ``discover_printer`` (SDCP) and
    ``CC2Discovery.discover_as_printers`` (CC2) both come back empty, so only
    the TCP 3030 connection can say anything about the printer.

    Yields the doubles the caller asserts on: ``unique_id`` / ``abort`` are the
    duplicate-protection calls, ``ws_client`` / ``cc2_discovery`` the two UDP
    protocols.
    """
    with (
        patch(
            "custom_components.elegoo_printer.config_flow.ElegooPrinterClient"
        ) as ws_client,
        patch(
            "custom_components.elegoo_printer.config_flow.CC2Discovery"
        ) as cc2_discovery,
        patch(
            "custom_components.elegoo_printer.config_flow.async_get_clientsession",
            return_value=session
            if session is not None
            else session_returning(text_msg(nested_attributes_frame())),
        ),
        patch.object(
            ElegooFlowHandler, "async_set_unique_id", new=AsyncMock()
        ) as unique_id,
        patch.object(ElegooFlowHandler, "_abort_if_unique_id_configured") as abort,
        patch.object(
            ElegooFlowHandler, "_async_detect_canvas", new=AsyncMock(return_value=False)
        ),
    ):
        ws_client.return_value.discover_printer.return_value = []
        cc2_discovery.discover_as_printers.return_value = []
        yield SimpleNamespace(
            unique_id=unique_id,
            abort=abort,
            ws_client=ws_client,
            cc2_discovery=cc2_discovery,
        )


class TestProbeReadsIdentityFromTheLiveConnection:
    """``_async_probe_websocket_printer`` describes, or refuses, what answered."""

    async def test_nested_attributes_frame_yields_a_printer_with_the_id(self) -> None:
        """
        The nested shape describes the printer, identity included.

        ``Printer.sync_from_attributes`` copies firmware/name/model/brand but
        never ``id``, so the probe has to read ``MainboardID`` itself — that
        field is the whole reason the probe exists. The typed IP also wins over
        ``MainboardIP``, and ``connection`` is set the way discovery sets it.
        """
        flow = make_config_flow()
        with patch(
            "custom_components.elegoo_printer.config_flow.async_get_clientsession",
            return_value=session_returning(text_msg(nested_attributes_frame())),
        ):
            printer = await flow._async_probe_websocket_printer(DOC_IP)

        assert printer is not None
        assert printer.id == MAINBOARD_ID
        assert printer.name == "CC1"
        assert printer.model == "Centauri Carbon"
        assert printer.firmware == "V1.1.29"
        assert printer.ip_address == DOC_IP
        assert printer.connection == DOC_IP
        assert printer.transport_type is TransportType.WEBSOCKET
        assert printer.printer_type is PrinterType.FDM
        assert printer.mqtt_broker_enabled is False

    async def test_flat_attributes_frame_still_parses(self) -> None:
        """
        The root-level ``Attributes`` shape is accepted too.

        The repo's own evidence disagrees about which shape a CC1 sends
        (``scripts/test_server.py`` nests it; the conformance test and the MQTT
        client's "WebSocket fallback format" comment do not), and no real frame
        has ever been captured here, so both are read rather than guessed at.
        """
        flow = make_config_flow()
        with patch(
            "custom_components.elegoo_printer.config_flow.async_get_clientsession",
            return_value=session_returning(text_msg(flat_attributes_frame())),
        ):
            printer = await flow._async_probe_websocket_printer(DOC_IP)

        assert printer is not None
        assert printer.id == MAINBOARD_ID
        assert printer.model == "Centauri Carbon"

    async def test_no_attributes_frame_refuses_the_printer(self) -> None:
        """A socket that pushes no attributes says nothing usable: refused."""
        flow = make_config_flow()
        with patch(
            "custom_components.elegoo_printer.config_flow.async_get_clientsession",
            return_value=session_returning(text_msg(_current_status_frame())),
        ):
            assert await flow._async_probe_websocket_printer(DOC_IP) is None

    async def test_attributes_without_mainboard_id_refuses_the_printer(self) -> None:
        """
        A printer that identifies itself by name only is still refused.

        An entry with no id is not merely ugly: ``_abort_if_unique_id_configured``
        early-returns on a ``None`` unique id, so duplicate protection is
        silently switched off, and ``coordinator.generate_unique_id`` raises
        ``TypeError`` for every entity. Nothing in the integration writes the id
        in later — not ``_update_config_entry_if_needed`` (which does not compare
        ``id``) and not the options flow — so refusing is the only safe answer.
        """
        attributes = cc1_self_reported_attributes()
        del attributes["MainboardID"]
        flow = make_config_flow()
        with patch(
            "custom_components.elegoo_printer.config_flow.async_get_clientsession",
            return_value=session_returning(text_msg(flat_attributes_frame(attributes))),
        ):
            assert await flow._async_probe_websocket_printer(DOC_IP) is None

    async def test_hostile_attributes_frame_refuses_instead_of_raising(self) -> None:
        """
        Seven device-controlled frames are refused, and not one raises.

        ``data_entry_flow._async_handle_step`` catches only ``AbortFlow``, so an
        exception escaping the probe reaches the user as "Config flow could not
        be loaded" — worse than the error this task replaces. Four of these
        frames genuinely raise out of ``Printer.from_dict`` (a non-string
        ``MachineName`` hits ``.lower()``, a non-string ``ProtocolVersion`` and
        ``FirmwareVersion`` hit ``.upper()``, an unknown ``transport_type`` hits
        the enum — and note ``transport_type`` only raises when
        ``protocol_version`` is present alongside it, and ``FirmwareVersion``
        only when a model is present too). The other three are rejected by the
        probe's own shape check before the parse is reached; they are kept in the
        same test so both halves of the contract are pinned end to end.
        """
        hostile_frames = [
            # Raises out of Printer.from_dict:
            flat_attributes_frame({"MachineName": 7, "MainboardID": MAINBOARD_ID}),
            flat_attributes_frame(
                {"ProtocolVersion": 999, "MainboardID": MAINBOARD_ID}
            ),
            flat_attributes_frame(
                {
                    "MachineName": "Centauri Carbon",
                    "FirmwareVersion": 11,
                    "MainboardID": MAINBOARD_ID,
                }
            ),
            flat_attributes_frame(
                {"transport_type": "nope", "protocol_version": "V3.0.0"}
            ),
            # Rejected by the probe's shape check:
            {"Topic": "sdcp/attributes/x", "Attributes": []},
            {"Topic": "sdcp/attributes/x", "Attributes": "not-a-dict"},
            {"Topic": "sdcp/attributes/x", "Data": []},
        ]
        flow = make_config_flow()
        for frame in hostile_frames:
            with patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=session_returning(text_msg(frame)),
            ):
                # An unexpected exception type fails the test loudly; a refusal
                # is the only acceptable outcome.
                assert await flow._async_probe_websocket_printer(DOC_IP) is None, frame

    async def test_non_dict_frame_refuses_the_printer(self) -> None:
        """
        Hostile *top-level* frame shapes are refused too.

        The frame loop touches the frame before the parse does, so it has its own
        device-controlled surface: ``json.loads`` happily produces ``[1, 2]``,
        ``"hello"``, ``null`` or ``7``, on which ``frame.get(...)`` is an
        ``AttributeError``; and ``{"Topic": 7}`` makes ``"attributes" in topic``
        a ``TypeError``. Both are type-checked away rather than caught, which is
        why the loop needs no broad handler.
        """
        hostile_frames: list[Any] = [
            [1, 2],
            "hello",
            None,
            7,
            {"Topic": 7, "Attributes": cc1_self_reported_attributes()},
            {"Topic": None, "Attributes": cc1_self_reported_attributes()},
        ]
        flow = make_config_flow()
        for frame in hostile_frames:
            with patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=session_returning(text_msg(frame)),
            ):
                assert await flow._async_probe_websocket_printer(DOC_IP) is None, frame

    async def test_refused_connection_returns_none(self) -> None:
        """
        A dead address stays a dead address: the probe does not invent a printer.

        Nothing is listening, so ``ws_connect`` raises what aiohttp raises for a
        refused port. The probe returns None and the caller reports the existing
        ``no_printer_found`` — the error the user already understood.
        """
        flow = make_config_flow()
        with patch(
            "custom_components.elegoo_printer.config_flow.async_get_clientsession",
            return_value=session_refusing(),
        ):
            assert await flow._async_probe_websocket_printer(DOC_IP) is None


class TestManualIpWithoutDiscovery:
    """``async_step_manual_ip`` reaches a discovery-silent printer by IP."""

    async def test_manual_ip_succeeds_when_discovery_is_silent(self) -> None:
        """
        The #429 report: discovery silent, IP typed, printer set up.

        The step routes on to the FDM options step (``manual_ip`` itself never
        creates an entry) and, crucially, sets the unique id from the id the
        printer reported over the live socket — duplicate protection is intact.
        """
        flow = make_config_flow()
        with _silent_discovery() as doubles:
            result = await flow.async_step_manual_ip({CONF_IP_ADDRESS: DOC_IP})

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "fdm_options"
        unique_id = doubles.unique_id.await_args
        assert unique_id is not None
        assert unique_id.kwargs["unique_id"] == MAINBOARD_ID
        doubles.abort.assert_called_once()
        assert flow.selected_printer is not None
        assert flow.selected_printer.id == MAINBOARD_ID
        assert flow.selected_printer.ip_address == DOC_IP

    async def test_the_live_probed_identity_reaches_the_created_entry(self) -> None:
        """
        The id the printer reported survives the options rebuild into ``data``.

        ``fdm_options`` rebuilds the printer through ``to_dict``/``from_dict``
        before creating the entry, and that dict is what becomes the entry's
        unique id, its device identifier and every entity's unique-id prefix. If
        the id were lost in the round trip the entry would be created broken, so
        the assertion is on the created data, not on the in-memory printer.
        """
        flow = make_config_flow()
        with _silent_discovery():
            await flow.async_step_manual_ip({CONF_IP_ADDRESS: DOC_IP})
            with patch(
                "custom_components.elegoo_printer.config_flow._async_test_connection",
                new=AsyncMock(side_effect=pass_printer_through),
            ):
                result = await flow.async_step_fdm_options(
                    {CONF_PROXY_ENABLED: False, "has_canvas": False}
                )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["id"] == MAINBOARD_ID
        assert result["data"]["ip_address"] == DOC_IP
        assert result["data"]["name"] == "CC1"
        assert result["data"]["transport_type"] == TransportType.WEBSOCKET.value

    async def test_dead_address_still_reports_no_printer_found(self) -> None:
        """An IP that answers nothing keeps today's error, and sets no id."""
        flow = make_config_flow()
        with _silent_discovery(session=session_refusing()) as doubles:
            result = await flow.async_step_manual_ip({CONF_IP_ADDRESS: DOC_IP})

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "manual_ip"
        assert result["errors"]["base"] == "no_printer_found"
        doubles.unique_id.assert_not_awaited()
        doubles.abort.assert_not_called()

    async def test_hostile_frame_ends_in_an_error_form_not_a_crash(self) -> None:
        """
        A printer that answers with an unparseable frame gets an error form.

        This is the flow-level half of the probe's exception contract: what the
        user must see is the manual-IP form with ``no_printer_found``, never the
        generic "Config flow could not be loaded" that any exception escaping
        ``async_step_manual_ip`` produces.
        """
        hostile = flat_attributes_frame({"MachineName": 7, "MainboardID": MAINBOARD_ID})
        flow = make_config_flow()
        with _silent_discovery(session=session_returning(text_msg(hostile))) as doubles:
            result = await flow.async_step_manual_ip({CONF_IP_ADDRESS: DOC_IP})

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "manual_ip"
        assert result["errors"]["base"] == "no_printer_found"
        doubles.unique_id.assert_not_awaited()
        doubles.abort.assert_not_called()

    @pytest.mark.parametrize(
        "mainboard_id",
        [123, True, ["a"], 1.5, "   ", ""],
        ids=["int", "bool", "list", "float", "whitespace", "empty"],
    )
    async def test_non_string_mainboard_id_never_creates_an_entry(
        self, mainboard_id: Any
    ) -> None:
        """
        An id that exists but cannot be used as a unique id is refused.

        A truthy non-string is the dangerous case, because a bare
        ``if not mainboard_id:`` lets it through: ``async_set_unique_id(123)``
        succeeds (only ``None`` is special-cased), duplicate protection then runs
        against an integer, and ``coordinator.generate_unique_id`` evaluates
        ``machine_id + "_" + key`` with ``machine_id = 123`` — a ``TypeError`` in
        every entity platform, on an entry nothing can repair. So the requirement
        is a non-empty *string*, not merely a present id, and an all-whitespace
        string is refused with it.
        """
        attributes = cc1_self_reported_attributes()
        attributes["MainboardID"] = mainboard_id
        flow = make_config_flow()
        with _silent_discovery(
            session=session_returning(text_msg(flat_attributes_frame(attributes)))
        ) as doubles:
            result = await flow.async_step_manual_ip({CONF_IP_ADDRESS: DOC_IP})

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "manual_ip"
        assert result["errors"]["base"] == "no_printer_found"
        doubles.unique_id.assert_not_awaited()
        doubles.abort.assert_not_called()

    async def test_probe_is_skipped_when_discovery_answers(self) -> None:
        """A printer that answers discovery never pays the live probe."""
        discovered = Printer.from_dict(
            {
                "name": "Discovered CC1",
                "ip_address": DOC_IP,
                "id": MAINBOARD_ID,
                "model": "Centauri Carbon",
                "protocol": "V3.0.0",
            }
        )
        flow = make_config_flow()
        with (
            patch(
                "custom_components.elegoo_printer.config_flow.ElegooPrinterClient"
            ) as ws_client,
            # The step builds its discovery client from the shared session
            # before either protocol runs, so it has to be a double here too.
            patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=MagicMock(),
            ),
            patch.object(ElegooFlowHandler, "async_set_unique_id", new=AsyncMock()),
            patch.object(ElegooFlowHandler, "_abort_if_unique_id_configured"),
            patch.object(
                ElegooFlowHandler, "_async_probe_websocket_printer", new=AsyncMock()
            ) as probe,
            patch.object(
                ElegooFlowHandler,
                "_async_detect_canvas",
                new=AsyncMock(return_value=False),
            ),
        ):
            ws_client.return_value.discover_printer.return_value = [discovered]
            result = await flow.async_step_manual_ip({CONF_IP_ADDRESS: DOC_IP})

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "fdm_options"
        probe.assert_not_awaited()

    async def test_proxy_host_path_is_untouched(self) -> None:
        """The proxy short-circuit still runs before any of this and skips it."""
        flow = make_config_flow()
        with (
            patch(
                "custom_components.elegoo_printer.config_flow.ElegooPrinterClient"
            ) as ws_client,
            patch(
                "custom_components.elegoo_printer.config_flow.CC2Discovery"
            ) as cc2_discovery,
            patch.object(ElegooFlowHandler, "async_set_unique_id", new=AsyncMock()),
            patch.object(ElegooFlowHandler, "_abort_if_unique_id_configured"),
            patch.object(
                ElegooFlowHandler, "_async_probe_websocket_printer", new=AsyncMock()
            ) as probe,
        ):
            result = await flow.async_step_manual_ip(
                {CONF_IP_ADDRESS: DOC_IP, "proxy_host": "192.0.2.50"}
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "cc2_auth_check"
        ws_client.return_value.discover_printer.assert_not_called()
        cc2_discovery.discover_as_printers.assert_not_called()
        probe.assert_not_awaited()


class TestTcpProbeHelper:
    """
    ``async_tcp_port_is_open`` against real sockets.

    The runtime probe is the guard against declaring a printer unreachable, so it
    has to be driven for real: patched out everywhere else, a helper that answered
    "open" to every address would pass the whole suite.
    """

    async def test_listening_port_is_open(self) -> None:
        """A socket accepting the connect is reported open."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            assert await async_tcp_port_is_open("127.0.0.1", port) is True
        finally:
            listener.close()

    async def test_closed_port_is_not_reachable(self) -> None:
        """A port nothing listens on is refused, not open."""
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        assert await async_tcp_port_is_open("127.0.0.1", port) is False

    async def test_blank_host_is_not_reachable(self) -> None:
        """No address to dial is not an open port — and nothing is dialed."""
        assert await async_tcp_port_is_open(None, 3030) is False
        assert await async_tcp_port_is_open("", 3030) is False


def _api_client(discovered_printers: list[Printer]) -> ElegooPrinterApiClient:
    """Build an API client whose UDP discovery returns ``discovered_printers``."""
    api_client = ElegooPrinterApiClient.__new__(ElegooPrinterApiClient)
    api_client.client = MagicMock()
    api_client.client.discover_printer.return_value = discovered_printers
    api_client._logger = MagicMock()
    api_client.hass = MagicMock()

    async def _run_in_executor(func: Callable[..., Any], *args: Any) -> Any:
        return func(*args)

    api_client.hass.async_add_executor_job = AsyncMock(side_effect=_run_in_executor)
    return api_client


class TestRuntimeReachabilityProbe:
    """
    Wall 2: ``_discover_printer_with_fallback`` no longer asks UDP alone.

    Both ``async_create`` and ``reconnect()`` gate the WebSocket transport on
    this one method, so without the probe an entry created by wall 1's fix would
    fail setup with ``ConfigEntryNotReady`` forever and never recover on the
    coordinator's retry poll either.
    """

    async def test_tcp_open_counts_as_reachable_when_discovery_is_silent(self) -> None:
        """Silent on UDP 3000, open on TCP 3030: reachable."""
        api_client = _api_client([])
        with patch.object(
            api_module,
            "async_tcp_port_is_open",
            new=AsyncMock(return_value=True),
        ) as probe:
            assert (
                await api_client._discover_printer_with_fallback(
                    Printer.from_dict({"ip_address": DOC_IP, "protocol": "V3.0.0"})
                )
                is True
            )

        assert probe.await_args is not None
        assert probe.await_args.args[0] == DOC_IP

    async def test_tcp_closed_leaves_the_printer_unreachable(self) -> None:
        """Neither protocol answers: genuinely unreachable, exactly as before."""
        api_client = _api_client([])
        with patch.object(
            api_module, "async_tcp_port_is_open", new=AsyncMock(return_value=False)
        ):
            assert (
                await api_client._discover_printer_with_fallback(
                    Printer.from_dict({"ip_address": DOC_IP, "protocol": "V3.0.0"})
                )
                is False
            )

    async def test_discovery_answered_skips_the_probe_entirely(self) -> None:
        """A printer that answers discovery never pays the extra connect."""
        api_client = _api_client(
            [Printer.from_dict({"ip_address": DOC_IP, "protocol": "V3.0.0"})]
        )
        with patch.object(
            api_module, "async_tcp_port_is_open", new=AsyncMock()
        ) as probe:
            assert (
                await api_client._discover_printer_with_fallback(
                    Printer.from_dict({"ip_address": DOC_IP, "protocol": "V3.0.0"})
                )
                is True
            )

        probe.assert_not_awaited()

    async def test_fallback_calls_the_real_helper_end_to_end(self) -> None:
        """
        The real helper, through the real fallback, against a real socket.

        Every other runtime test patches the helper out to keep the suite fast,
        and a patched mock accepts any keyword — which is exactly how a call-site
        signature bug (passing ``timeout=`` to a parameter named
        ``wait_timeout``) passed 800-odd green tests once already. This test does
        not patch the helper, so the keyword mismatch raises here instead of on
        every unreachable printer at setup and reconnect.
        """
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        printer = Printer.from_dict({"ip_address": "127.0.0.1", "protocol": "V3.0.0"})
        try:
            with patch.object(api_module, "WEBSOCKET_PORT", port):
                api_client = _api_client([])
                assert await api_client._discover_printer_with_fallback(printer) is True
        finally:
            listener.close()

        # And the same call path against a port nobody listens on: unreachable.
        probe_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe_socket.bind(("127.0.0.1", 0))
        closed_port = probe_socket.getsockname()[1]
        probe_socket.close()
        with patch.object(api_module, "WEBSOCKET_PORT", closed_port):
            api_client = _api_client([])
            assert await api_client._discover_printer_with_fallback(printer) is False
