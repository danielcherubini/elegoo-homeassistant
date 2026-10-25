"""
Shared doubles for the config-flow tests that talk to a printer over TCP 3030.

Importable doubles only, NO test functions — the same shape as ``fakes.py`` next
door, which holds the MQTT transport doubles. This module holds the other stack:
the aiohttp WebSocket scaffolding the config-flow probes are driven with, the SDCP
frame builders those probes are fed, and the flow-builder those tests each needed
a private copy of.

Why one place: four config-flow test modules hand-rolled this scaffolding and the
copies had already drifted — one ``_text_msg`` serialised unconditionally while
another passed strings through untouched, two same-named ``_status_frame``
builders put different keys in the frame (``AmsConnectStatus`` vs
``CurrentStatus``), and the frame topic and the mainboard id differed between
files. When the SDCP frame contract changes, a contributor fixes one file and the
others silently stop exercising the real shape. The rule here is therefore
"genuinely shared": anything with a single owning test stays in that file, named
for what it means there.

What deliberately did NOT move here:

* ``_poisoned_attributes`` and the routing-key list stay in
  ``test_probe_frame_trust.py`` — they ARE the trust boundary that file pins, and
  a shared module would invite a second consumer to lean on attack payloads it is
  not testing. Same for that file's frames that must not raise
  (``_deeply_nested_json``) and ``_CancellingWS``.
* The ``CurrentStatus`` status frame stays in ``test_manual_ip_no_discovery.py``.
  It is a different payload from the Canvas status frame, not a variant of it;
  merging the two behind a parameter would hide which test asserts on which key.
* ``_HangingWS`` stays in ``test_canvas_detection.py``, the only probe-timeout
  test, and the attributes frame that file builds (``Data -> Data -> Attributes``)
  stays there too — that shape is neither of the two the identity probe reads, and
  its only job is to be "a frame that is not a status frame".

Arguments named ``payload``/``attributes`` below are, as far as the integration is
concerned, device-controlled text; the builders take them as given on purpose, so
a test can hand the probes a frame no real printer would send.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Self
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from custom_components.elegoo_printer.config_flow import ElegooFlowHandler

if TYPE_CHECKING:
    from collections.abc import Callable

    from custom_components.elegoo_printer.sdcp.models.printer import Printer

# Documentation address (RFC 5737), so a test log can never read as a real host.
DOC_IP = "192.0.2.1"
# The mainboard id the CC1 in #431 reports, used verbatim so these tests read like
# the report they came from. The frame builders below use it in their topics too,
# so every frame a test feeds the probes looks like it came from that printer.
MAINBOARD_ID = "000000000001d354"


class FakePushWebSocket:
    """
    Async-iterable WebSocket double yielding pre-queued push messages.

    Stands in for what ``session.ws_connect`` returns inside
    ``_async_scan_websocket_frames``: the config-flow probes only iterate it, so
    it carries no send/close behaviour. Deliberately distinct from the package
    ``conftest`` ``FakeWebSocket``, which is wired into ``ElegooPrinterClient`` and
    does record sends — merging them would give one double two unrelated
    contracts.
    """

    def __init__(self, messages: list[Any]) -> None:
        """Queue ``messages`` for ``__anext__`` to hand out one at a time."""
        self._messages = iter(messages)

    async def __aenter__(self) -> Self:
        """Enter the connection context (always succeeds)."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Leave the connection context; there is nothing to release."""
        del args

    def __aiter__(self) -> Self:
        """Return the asynchronous iterator."""
        return self

    async def __anext__(self) -> Any:
        """Yield the next queued message, ending the stream when they run out."""
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration from None


def text_msg(payload: Any) -> SimpleNamespace:
    """
    Build a TEXT WebSocket message carrying ``payload``.

    Arguments:
        payload: A dict or list to serialise, or a raw ``str`` sent exactly as
            given — the raw form is how a test feeds the frame loops something
            ``json.loads`` cannot decode. Passing strings through untouched is the
            behaviour the shared copy kept; the variant that always called
            ``json.dumps`` could not express that case at all.

    Returns:
        A message double with ``type`` TEXT and ``data`` the text to read.

    """
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=data)


def closed_msg() -> SimpleNamespace:
    """
    Build a CLOSED WebSocket message: the stream ends without saying anything.

    Returns:
        A message double whose ``type`` is not TEXT, which the frame loops read as
        "the printer hung up".

    """
    return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)


def session_returning(*messages: Any) -> MagicMock:
    """
    Build a hass-session double whose ``ws_connect`` yields ``messages``.

    Arguments:
        messages: Messages the one fake WebSocket hands out, in order.

    Returns:
        A session double. It stays a ``MagicMock`` so a caller can assert where
        the probe dialed via ``ws_connect.call_args``.

    """
    return MagicMock(
        ws_connect=MagicMock(return_value=FakePushWebSocket(list(messages)))
    )


def session_refusing(error: Exception | None = None) -> MagicMock:
    """
    Build a session double whose ``ws_connect`` raises (nothing is listening).

    Arguments:
        error: Exception to raise, defaulting to the ``ClientConnectorError``
            aiohttp raises for a refused port — the case the probes must answer
            with a refusal rather than a crash.

    Returns:
        A session double that never yields a connection.

    """

    def _refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise error or aiohttp.ClientConnectorError(
            MagicMock(host=DOC_IP, port=3030, req=None), ConnectionRefusedError()
        )

    return MagicMock(ws_connect=MagicMock(side_effect=_refuse))


def make_config_flow() -> ElegooFlowHandler:
    """
    Build a bare ``ElegooFlowHandler`` for direct step calls.

    ``async_add_executor_job`` runs the callable inline rather than returning a
    coroutine: the manual-IP step awaits it for both UDP discovery protocols, and
    a plain ``MagicMock`` would hand the step an unawaited mock instead of the
    empty list the test is simulating.

    Returns:
        A handler with a mocked ``hass`` and the identity a flow needs.

    """
    flow = ElegooFlowHandler()
    flow.hass = MagicMock()
    flow.flow_id = "config-test-flow"
    flow.handler = "elegoo_printer"

    async def _run_in_executor(func: Callable[..., Any], *args: Any) -> Any:
        return func(*args)

    flow.hass.async_add_executor_job = AsyncMock(side_effect=_run_in_executor)
    return flow


def cc1_self_reported_attributes() -> dict[str, Any]:
    """
    Return the attributes a CC1 reports about itself (the printer in #431).

    The baseline self-description: everything the probe is allowed to believe and
    nothing it is not. Attack payloads are built on top of this in
    ``test_probe_frame_trust.py`` so the identity half stays real.

    Returns:
        A fresh dict (callers mutate it) of root-level SDCP attributes.

    """
    return {
        "Name": "CC1",
        "MachineName": "Centauri Carbon",
        "BrandName": "ELEGOO",
        "ProtocolVersion": "V3.0.0",
        "FirmwareVersion": "V1.1.29",
        "MainboardIP": "10.30.20.44",
        "MainboardID": MAINBOARD_ID,
        "NetworkStatus": "wlan",
    }


def flat_attributes_frame(attributes: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Build the root-level ``{Attributes: ...}`` attributes frame.

    The shape the conformance test and the MQTT client's "WebSocket fallback
    format" comment use.

    Arguments:
        attributes: Attributes to carry, defaulting to
            :func:`cc1_self_reported_attributes`.

    Returns:
        A frame dict with the attributes at the frame root.

    """
    return {
        "Id": "push-1",
        "Topic": f"sdcp/attributes/{MAINBOARD_ID}",
        "Attributes": (
            cc1_self_reported_attributes() if attributes is None else attributes
        ),
    }


def nested_attributes_frame(
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the nested ``{Data: {Attributes: ...}}`` attributes frame.

    The shape ``scripts/test_server.py`` pushes via ``create_push_message``. Both
    shapes are read because this repo's own evidence disagrees about which one a
    real CC1 sends and no real frame has ever been captured here.

    Arguments:
        attributes: Attributes to carry, defaulting to
            :func:`cc1_self_reported_attributes`.

    Returns:
        A frame dict with the attributes under ``Data``.

    """
    return {
        "Id": "push-1",
        "Topic": f"sdcp/attributes/{MAINBOARD_ID}",
        "Data": {
            "Attributes": (
                cc1_self_reported_attributes() if attributes is None else attributes
            )
        },
    }


def ams_status_frame(ams_connect: int = 1) -> dict[str, Any]:
    """
    Build a status frame answering the Canvas question.

    ``AmsConnectStatus`` is the only key ``_async_detect_canvas`` reads, so this is
    the frame that means "Canvas connected" (1) or "not connected" (0). A status
    frame carrying some *other* status key is a different payload, not a variant of
    this one, and lives with the test that needs it.

    Arguments:
        ams_connect: Value for ``AmsConnectStatus``.

    Returns:
        A status frame dict.

    """
    return {
        "Topic": f"sdcp/status/{MAINBOARD_ID}",
        "Data": {"Data": {"Status": {"AmsConnectStatus": ams_connect}}},
    }


def pass_printer_through(
    hass: Any,  # noqa: ARG001
    printer: Printer,
    user_input: Any,  # noqa: ARG001
) -> Printer:
    """
    Stand in for ``_async_test_connection``: the printer is already valid.

    The real helper opens a transport, which these flow-routing tests are not
    testing; they need the flow to get past the connection test and reach
    ``async_create_entry``.

    Arguments:
        hass: Ignored.
        printer: The printer under test.
        user_input: Ignored.

    Returns:
        The same printer, unchanged.

    """
    return printer
