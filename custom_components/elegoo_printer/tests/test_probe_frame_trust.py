"""
What a probe frame may and may not decide (#429/#431 follow-up review).

``_async_probe_websocket_printer`` hands ``Printer.from_dict`` the only
dictionary in this integration that a device wrote. That matters because
``from_dict`` reads transport selection and credentials out of the same dict it
reads the printer's name from, and the config flow then routes on
``transport_type`` and persists ``to_dict()`` into ``entry.data``. Before the
frame was filtered, a frame carrying ``transport_type``/``protocol_version``/
``proxy_host`` produced a printer whose ``connection_host`` was the attacker's
host and whose entry said so permanently — Home Assistant dialling a host the
LAN device picked, with the CC2 registration and fallback passwords in hand.

So two invariants are pinned here:

* **identity, not routing** — the frame may say who it is and nothing else, and
* **totality** — no frame, however shaped, may raise out of either config-flow
  probe, because ``data_entry_flow`` catches only ``AbortFlow`` and anything
  else surfaces as HA's generic "Config flow could not be loaded".

The second one used to hold for the identity probe only; ``_async_detect_canvas``
kept the older frame loop and is reachable with a device-controlled first frame,
since the probe guarantees no more than "something at this IP accepts TCP".
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Self
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.data_entry_flow import FlowResultType

from custom_components.elegoo_printer.config_flow import (
    _PRINTER_IDENTITY_KEYS,
    ElegooFlowHandler,
    _identity_only,
)
from custom_components.elegoo_printer.const import CONF_PROXY_ENABLED
from custom_components.elegoo_printer.sdcp.models.enums import (
    PrinterType,
    TransportType,
)
from custom_components.elegoo_printer.tests.ws_doubles import (
    DOC_IP,
    MAINBOARD_ID,
    ams_status_frame,
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
    from custom_components.elegoo_printer.sdcp.models.printer import Printer

# Every key from_dict reads that decides where HA connects, what it
# authenticates with, or which proxy the entry trusts. None of them may survive
# a frame, and the guard test below fails if the allowlist ever grows one.
ROUTING_KEYS: tuple[str, ...] = (
    "transport_type",
    "protocol_version",
    "proxy_host",
    "proxy_enabled",
    "proxy_websocket_port",
    "proxy_video_port",
    "mqtt_external_host",
    "mqtt_external_port",
    "mqtt_broker_enabled",
    "cc2_access_code",
    "cc2_token_status",
    "camera_enabled",
    "has_canvas",
    "external_ip",
    "is_proxy",
    "Proxy",
)


class _CancellingWS:
    """A connection that is cancelled during the handshake."""

    async def __aenter__(self) -> Self:
        raise asyncio.CancelledError

    async def __aexit__(self, *args: object) -> None:
        pass


def _poisoned_attributes() -> dict[str, Any]:
    """
    Build a real CC1 attributes payload plus every routing key `from_dict` reads.

    The identity half is what the printer in #431 actually sends; the rest is the
    attack: the device trying to pick the host Home Assistant dials, the
    credential it takes there, and the transport the flow routes on. The
    lowercase aliases are included because ``from_dict`` falls back to them
    (``attrs.get("MainboardIP", attrs.get("ip_address"))`` and friends), so an
    allowlist that only blocked the wire names would still be leaky.
    """
    return {
        **cc1_self_reported_attributes(),
        "transport_type": "cc2_mqtt",
        "protocol_version": "CC2",
        "proxy_host": "attacker.example",
        "proxy_enabled": True,
        "proxy_websocket_port": 9999,
        "proxy_video_port": 9998,
        "mqtt_external_host": "attacker.example",
        "mqtt_external_port": 1883,
        "mqtt_broker_enabled": True,
        "cc2_access_code": "stolen-or-guessed-access-code",
        "cc2_token_status": 3,
        "camera_enabled": True,
        "has_canvas": True,
        "external_ip": "attacker.example",
        "is_proxy": True,
        "Proxy": True,
        # The lowercase aliases from_dict falls back to:
        "ip_address": "attacker.example",
        "id": "hijacked-id",
        "connection": "attacker.example",
        "name": "attacker",
        "model": "AttackerBox",
    }


def _deeply_nested_json(depth: int = 30_000) -> str:
    """
    JSON ``json.loads`` cannot decode.

    Not a ``JSONDecodeError`` and not an ``aiohttp.ClientError``: the decoder
    recurses per nesting level and raises ``RecursionError`` first, which is why
    the frame handlers name it explicitly.
    """
    return "[" * depth + "]" * depth


async def _probe_with(*messages: Any) -> Printer | None:
    flow = make_config_flow()
    with patch(
        "custom_components.elegoo_printer.config_flow.async_get_clientsession",
        return_value=session_returning(*messages),
    ):
        return await flow._async_probe_websocket_printer(DOC_IP)


async def _canvas_with(*messages: Any) -> bool:
    flow = make_config_flow()
    printer = MagicMock()
    printer.ip_address = DOC_IP
    with patch(
        "custom_components.elegoo_printer.config_flow.async_get_clientsession",
        return_value=session_returning(*messages),
    ):
        return await flow._async_detect_canvas(printer)


class TestAllowlistIsIdentityOnly:
    """The trust boundary itself, pinned as a set rather than as a behaviour."""

    def test_no_routing_key_is_in_the_allowlist(self) -> None:
        """
        Guard against widening the allowlist into the hole again.

        Every key ``Printer.from_dict`` reads for transport, proxy, broker,
        credential or feature-flag purposes must stay out. A future contributor
        adding one "because the printer reports it" reopens config-time SSRF, and
        this test is the cheapest place to notice.
        """
        leaked = sorted(set(ROUTING_KEYS) & _PRINTER_IDENTITY_KEYS)
        assert leaked == []

    def test_identity_keys_are_the_discovery_constructor_keys(self) -> None:
        """
        The allowlist is what UDP discovery has always been willing to believe.

        ``Printer(json_string)`` — the discovery path, never exploited — reads
        Name/MachineName/BrandName/MainboardIP/ProtocolVersion/FirmwareVersion/
        MainboardID/Proxy. The allowlist is that set minus ``Proxy``: the probe is
        pointed at an address the user typed to reach a printer, so a device
        claiming to be a proxy buys nothing and adds a key identity does not need.
        """
        assert (
            frozenset(
                {
                    "Name",
                    "MachineName",
                    "BrandName",
                    "MainboardIP",
                    "MainboardID",
                    "ProtocolVersion",
                    "FirmwareVersion",
                }
            )
            == _PRINTER_IDENTITY_KEYS
        )

    def test_identity_only_drops_everything_not_listed(self) -> None:
        """Filtering, not sanitising: an unknown key is dropped, not inspected."""
        kept = _identity_only(_poisoned_attributes())
        assert kept == _identity_only(cc1_self_reported_attributes())
        assert set(kept) <= _PRINTER_IDENTITY_KEYS


class TestProbeFrameCannotDecideRouting:
    """B1: the frame answers "who are you", never "where does HA connect"."""

    @pytest.mark.parametrize(
        ("shape", "frame_builder"),
        [("flat", flat_attributes_frame), ("nested", nested_attributes_frame)],
    )
    async def test_poisoned_frame_describes_identity_but_not_routing(
        self, shape: str, frame_builder: Any
    ) -> None:
        """
        Both attributes shapes are accepted, and neither carries routing.

        The identity half must survive — that is the whole point of the probe and
        the reason it is not simply a reachability test — while every routing and
        credential field ends at its neutral default.
        """
        printer = await _probe_with(text_msg(frame_builder(_poisoned_attributes())))

        assert printer is not None, shape
        # Identity, from the frame:
        assert printer.id == MAINBOARD_ID
        assert printer.name == "CC1"
        assert printer.model == "Centauri Carbon"
        assert printer.firmware == "V1.1.29"
        assert printer.printer_type is PrinterType.FDM
        # Routing, NOT from the frame: the typed address wins, and transport is
        # derived from ProtocolVersion the way from_dict derives it when the
        # routing keys are absent.
        assert printer.ip_address == DOC_IP
        assert printer.connection == DOC_IP
        assert printer.connection_host == DOC_IP
        assert printer.transport_type is TransportType.WEBSOCKET

        data = printer.to_dict()
        assert data["proxy_host"] is None
        assert data["mqtt_external_host"] is None
        assert data["mqtt_external_port"] is None
        assert data["cc2_access_code"] is None
        assert data["cc2_token_status"] == 0
        assert data["proxy_enabled"] is False
        assert data["camera_enabled"] is False
        assert data["has_canvas"] is False
        assert data["external_ip"] is None
        assert data["mqtt_broker_enabled"] is False
        # `is_proxy` is the one key here the probe never needed to reset: the code
        # that reads it filters *discovered* printers, which this one never joins.
        # Asserted for the same reason as the lines above — it pins the outcome,
        # so it fails if the allowlist ever lets `Proxy` through or `from_dict`
        # grows a non-neutral default for it — not because a line in the probe
        # depends on it today.
        assert data["is_proxy"] is False
        assert data["transport_type"] == TransportType.WEBSOCKET.value

    async def test_poisoned_frame_cannot_route_the_flow_to_the_cc2_auth_step(
        self,
    ) -> None:
        """
        Flow-level half of B1: a frame cannot pick the transport branch.

        ``async_step_manual_ip`` routes on ``transport_type``. Left steerable,
        ``cc2_mqtt`` sent the flow down the CC2 auth path — which sends the
        access code and fallback passwords to ``connection_host`` — and ``mqtt``
        created an entry with no connection test at all. With the frame filtered,
        a V3 printer lands on the FDM options step like any other WebSocket
        printer, and the created entry carries no attacker host.
        """
        flow = make_config_flow()
        with (
            patch(
                "custom_components.elegoo_printer.config_flow.ElegooPrinterClient"
            ) as ws_client,
            patch(
                "custom_components.elegoo_printer.config_flow.CC2Discovery"
            ) as cc2_discovery,
            patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=session_returning(
                    text_msg(flat_attributes_frame(_poisoned_attributes()))
                ),
            ),
            patch.object(
                ElegooFlowHandler, "async_set_unique_id", new=AsyncMock()
            ) as unique_id,
            patch.object(ElegooFlowHandler, "_abort_if_unique_id_configured"),
            patch.object(
                ElegooFlowHandler,
                "_async_detect_canvas",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "custom_components.elegoo_printer.config_flow._async_test_connection",
                new=AsyncMock(side_effect=pass_printer_through),
            ),
        ):
            ws_client.return_value.discover_printer.return_value = []
            cc2_discovery.discover_as_printers.return_value = []

            routed = await flow.async_step_manual_ip({CONF_IP_ADDRESS: DOC_IP})
            assert routed["type"] == FlowResultType.FORM
            assert routed["step_id"] == "fdm_options"

            created = await flow.async_step_fdm_options(
                {CONF_PROXY_ENABLED: False, "has_canvas": False}
            )

        assert created["type"] == FlowResultType.CREATE_ENTRY
        assert created["data"]["id"] == MAINBOARD_ID
        assert created["data"]["ip_address"] == DOC_IP
        assert created["data"]["transport_type"] == TransportType.WEBSOCKET.value
        assert created["data"]["proxy_host"] is None
        assert created["data"]["mqtt_external_host"] is None
        assert created["data"]["cc2_access_code"] is None
        # Duplicate protection still ran, on the id the printer really reported.
        call = unique_id.await_args
        assert call is not None
        assert call.kwargs["unique_id"] == MAINBOARD_ID

    async def test_frame_cannot_make_the_probe_dial_something_it_should_not(
        self,
    ) -> None:
        """
        The probe dials the typed address and nothing else.

        The attack is not only what lands in ``entry.data``: the probe itself must
        not be talked into a second connection. The session is recorded, so a
        frame that redirected the probe would show up as a second ``ws_connect``
        to a different URL.
        """
        flow = make_config_flow()
        session = session_returning(
            text_msg(flat_attributes_frame(_poisoned_attributes()))
        )
        with patch(
            "custom_components.elegoo_printer.config_flow.async_get_clientsession",
            return_value=session,
        ):
            await flow._async_probe_websocket_printer(DOC_IP)

        assert session.ws_connect.call_count == 1
        url = session.ws_connect.call_args.args[0]
        assert url == f"ws://{DOC_IP}:3030/websocket"


class TestFrameLoopsAreTotal:
    """C5: neither probe may raise for anything a device controls."""

    @pytest.mark.parametrize(
        "frame",
        [
            [1, 2],
            "hello",
            None,
            7,
            {"Topic": 7},
            {"Topic": None},
            {"Topic": "sdcp/status/x", "Data": "not-a-dict"},
            {"Topic": "status", "Data": {"Data": [1, 2]}},
            {"Topic": "status", "Data": {"Data": {"Status": "nope"}}},
            {"Topic": "status", "Data": {"Data": {"Status": [1, 2]}}},
            {"Topic": [{"nested": "topic"}]},
        ],
        ids=[
            "list",
            "string",
            "null",
            "int",
            "int-topic",
            "null-topic",
            "data-string",
            "data-list",
            "status-string",
            "status-list",
            "list-topic",
        ],
    )
    async def test_canvas_detection_refuses_instead_of_raising(
        self, frame: Any
    ) -> None:
        """
        Every shape that used to escape ``_async_detect_canvas`` now answers False.

        Measured against the pre-fix loop: ``[1, 2]`` raised ``AttributeError`` on
        ``parsed.get``, ``{"Topic": 7}`` raised ``TypeError`` on ``"status" in
        topic``, and a non-dict ``Data``/``Status`` level raised ``AttributeError``
        mid-chain. ``data_entry_flow`` catches only ``AbortFlow``, so each of those
        was the generic "Config flow could not be loaded" — and this method is
        reachable with a device-controlled first frame, because the identity probe
        only guarantees that TCP 3030 accepts.
        """
        assert await _canvas_with(text_msg(frame)) is False

    async def test_canvas_detection_survives_an_undecodable_frame(self) -> None:
        """
        A frame nested past the decoder's limit ends in False, not RecursionError.

        ``json.loads`` recurses per level, so ~30k levels raise ``RecursionError``
        rather than ``JSONDecodeError`` — a class the old handler did not name, in
        a method whose handler named only ``TimeoutError``/``ClientError``/
        ``JSONDecodeError``.
        """
        assert await _canvas_with(text_msg(_deeply_nested_json())) is False

    async def test_probe_survives_an_undecodable_frame(self) -> None:
        """The same frame refused by the probe, which already had the loop."""
        assert await _probe_with(text_msg(_deeply_nested_json())) is None

    async def test_canvas_detection_still_reads_a_real_status_frame(self) -> None:
        """
        Totality did not cost the answer.

        Garbage is skipped and reading continues, so the frames a hostile device
        pushes first cannot hide the real status frame behind them; and a status
        frame without ``AmsConnectStatus`` still means no Canvas.
        """
        assert (
            await _canvas_with(
                text_msg([1, 2]),
                text_msg("this is not json"),
                text_msg(_deeply_nested_json()),
                text_msg(ams_status_frame(ams_connect=1)),
            )
            is True
        )
        assert await _canvas_with(text_msg(ams_status_frame(ams_connect=0))) is False
        assert (
            await _canvas_with(
                text_msg({"Topic": "sdcp/status/x", "Data": {"Data": {"Status": {}}}})
            )
            is False
        )

    async def test_probe_still_reads_a_real_attributes_frame_after_garbage(
        self,
    ) -> None:
        """Same for the probe: unusable frames first, real identity after."""
        printer = await _probe_with(
            text_msg("not json at all"),
            text_msg([1, 2]),
            text_msg(flat_attributes_frame(cc1_self_reported_attributes())),
        )
        assert printer is not None
        assert printer.id == MAINBOARD_ID

    @pytest.mark.parametrize(
        ("shape", "frame_builder"),
        [("flat", flat_attributes_frame), ("nested", nested_attributes_frame)],
    )
    async def test_empty_attributes_object_does_not_end_the_scan(
        self, shape: str, frame_builder: Any
    ) -> None:
        """
        An empty attributes object is read as "no answer yet", in both shapes.

        Matching on it would stop the scan on the first frame, which lets a device
        hide a usable attributes frame behind an empty one — and the outcome would
        be the same refusal the user sees for a dead address, so nothing would
        hint that a second frame was never looked at.
        """
        printer = await _probe_with(
            text_msg(frame_builder({})),
            text_msg(frame_builder(cc1_self_reported_attributes())),
        )
        assert printer is not None, shape
        assert printer.id == MAINBOARD_ID, shape

    async def test_refused_connection_is_still_a_refusal(self) -> None:
        """Nothing listening is still "no printer", not a crash and not a guess."""
        flow = make_config_flow()
        with patch(
            "custom_components.elegoo_printer.config_flow.async_get_clientsession",
            return_value=session_refusing(),
        ):
            assert await flow._async_probe_websocket_printer(DOC_IP) is None

        printer = MagicMock()
        printer.ip_address = DOC_IP
        with patch(
            "custom_components.elegoo_printer.config_flow.async_get_clientsession",
            return_value=session_refusing(),
        ):
            assert await flow._async_detect_canvas(printer) is False

    async def test_cancellation_still_propagates(self) -> None:
        """
        ``asyncio.CancelledError`` is not swallowed by the total loop.

        The handlers catch ``Exception`` subclasses on purpose; ``CancelledError``
        derives from ``BaseException``, and Home Assistant must be able to cancel a
        flow. Pinned because "make it total" is exactly the phrasing that tempts a
        ``BaseException`` in here.
        """
        flow = make_config_flow()
        with (
            patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=MagicMock(
                    ws_connect=MagicMock(return_value=_CancellingWS())
                ),
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await flow._async_probe_websocket_printer(DOC_IP)


class TestUntrustedFrameLogging:
    """The frame is device text: not default-visible, not unbounded, not a line."""

    async def test_unparseable_frame_is_not_dumped_at_warning(self, caplog) -> None:
        """
        The operator still gets the warning; the device does not get the log.

        The frame is a dict the printer chose, and WARNING is a level HA shows by
        default. The frame is now logged at DEBUG, through ``repr`` (so a newline
        in a value cannot forge extra log lines) and truncated. The refusal itself
        stays loud — an operator must still learn that a printer answered with
        something unreadable — but it names the event, not the payload.
        """
        hostile = _poisoned_attributes()
        hostile["MachineName"] = 7  # makes from_dict raise AttributeError
        injected = "line one\nFAKE WARNING forged by the device"
        hostile["Name"] = injected

        with caplog.at_level(logging.DEBUG):
            assert await _probe_with(text_msg(flat_attributes_frame(hostile))) is None

        loud = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("cannot parse" in m for m in loud)
        for message in loud:
            assert "attacker.example" not in message
            assert "forged by the device" not in message
            assert "\n" not in message

        # Present, but only at DEBUG and only escaped: the device text cannot
        # break out of the record it is logged into.
        dumped = next(
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.DEBUG
            and "Unparseable attributes frame" in r.getMessage()
        )
        assert "forged by the device" in dumped
        assert "\n" not in dumped
        assert dumped.count("Unparseable attributes frame") == 1

    async def test_frame_dump_is_truncated(self, caplog) -> None:
        """A huge frame cannot be logged in full at any level."""
        huge = cc1_self_reported_attributes()
        huge["MachineName"] = 7
        huge["Name"] = "x" * 20_000
        with caplog.at_level(logging.DEBUG):
            assert await _probe_with(text_msg(flat_attributes_frame(huge))) is None

        dumped = next(
            m
            for m in (r.getMessage() for r in caplog.records)
            if "Unparseable attributes frame" in m
        )
        assert "x" * 20_000 not in dumped
        assert "chars total" in dumped

    async def test_device_reported_name_and_id_are_bounded_at_info(
        self, caplog
    ) -> None:
        """
        The INFO success line stays useful without trusting the strings.

        The name and mainboard id are device text too, and this level is visible
        by default. Two things are required of them: the record cannot be
        extended into a forged second line — the device's newline reaches the log
        escaped, so the record stays one physical line — and neither string is
        echoed at a length the device chooses. What survives is a bounded prefix,
        which is still what an operator greps for. The frame itself never reaches
        this level at all (see the test above).
        """
        attributes = cc1_self_reported_attributes()
        attributes["Name"] = f"pwn\nFAKE WARNING {'a' * 500}"
        attributes["MainboardID"] = f"{MAINBOARD_ID}{'b' * 500}"
        with caplog.at_level(logging.INFO):
            printer = await _probe_with(text_msg(flat_attributes_frame(attributes)))

        assert printer is not None
        assert printer.id == f"{MAINBOARD_ID}{'b' * 500}", "only the log is scrubbed"
        line = next(
            r.getMessage()
            for r in caplog.records
            if "without discovery" in r.getMessage()
        )
        # One record, one physical line: the device's newline is escaped text
        # inside it, not a line break that could start a forged one.
        assert "\n" not in line
        assert line.startswith("Probed 192.0.2.1 over TCP 3030")
        # Bounded: neither device string is echoed at the length the device chose.
        assert "a" * 500 not in line
        assert "b" * 500 not in line
        assert len(line) < 400

    async def test_a_benign_name_and_id_are_logged_unchanged(self, caplog) -> None:
        """
        Ordinary printers are unaffected by the scrubbing.

        The name and id are what an operator matches against the printer they are
        looking for, so a short printable value is echoed exactly as before: the
        escaping is reserved for values that are long, not a string, or carry a
        control character.
        """
        with caplog.at_level(logging.INFO):
            printer = await _probe_with(
                text_msg(flat_attributes_frame(cc1_self_reported_attributes()))
            )

        assert printer is not None
        line = next(
            r.getMessage()
            for r in caplog.records
            if "without discovery" in r.getMessage()
        )
        assert f"found CC1 (PrinterType.FDM, id={MAINBOARD_ID})" in line
