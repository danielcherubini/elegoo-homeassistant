"""Tests for CC1 Canvas detection via websocket (_async_detect_canvas)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Self
from unittest.mock import MagicMock, patch

import aiohttp

from custom_components.elegoo_printer.config_flow import ElegooFlowHandler

_DOC_IP = "192.0.2.1"


class _FakeWS:
    """Async-iterable mock websocket that yields pre-built messages."""

    def __init__(self, messages: list) -> None:
        self._messages = iter(messages)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration from None


def _text_msg(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(payload))


def _status_frame(ams_connect: int = 1) -> dict:
    return {
        "Topic": "sdcp/status/test-id",
        "Data": {"Data": {"Status": {"AmsConnectStatus": ams_connect}}},
    }


def _attributes_frame() -> dict:
    return {
        "Topic": "sdcp/attributes/test-id",
        "Data": {"Data": {"Attributes": {"FirmwareVersion": "1.0"}}},
    }


def _make_flow() -> ElegooFlowHandler:
    flow = ElegooFlowHandler()
    flow.hass = MagicMock()
    return flow


def _make_printer() -> MagicMock:
    printer = MagicMock()
    printer.ip_address = _DOC_IP
    return printer


class TestCanvasDetectionWs:
    """_async_detect_canvas returns correct result for various frames."""

    def test_status_with_canvas_returns_true(self) -> None:
        async def _run() -> None:
            flow = _make_flow()
            ws = _FakeWS([_text_msg(_status_frame(ams_connect=1))])
            with patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=MagicMock(ws_connect=MagicMock(return_value=ws)),
            ):
                result = await flow._async_detect_canvas(_make_printer())
            assert result is True

        asyncio.run(_run())

    def test_status_without_canvas_returns_false(self) -> None:
        async def _run() -> None:
            flow = _make_flow()
            ws = _FakeWS([_text_msg(_status_frame(ams_connect=0))])
            with patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=MagicMock(ws_connect=MagicMock(return_value=ws)),
            ):
                result = await flow._async_detect_canvas(_make_printer())
            assert result is False

        asyncio.run(_run())

    def test_skips_non_status_frames(self) -> None:
        """Attributes frame first, then status — detection still succeeds."""

        async def _run() -> None:
            flow = _make_flow()
            ws = _FakeWS(
                [
                    _text_msg(_attributes_frame()),
                    _text_msg(_status_frame(ams_connect=1)),
                ]
            )
            with patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=MagicMock(ws_connect=MagicMock(return_value=ws)),
            ):
                result = await flow._async_detect_canvas(_make_printer())
            assert result is True

        asyncio.run(_run())

    def test_connection_error_returns_false(self) -> None:
        async def _run() -> None:
            flow = _make_flow()
            mock_session = MagicMock()
            mock_session.ws_connect = MagicMock(
                side_effect=aiohttp.ClientError("refused"),
            )
            with patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=mock_session,
            ):
                result = await flow._async_detect_canvas(_make_printer())
            assert result is False

        asyncio.run(_run())

    def test_closed_message_returns_false(self) -> None:
        async def _run() -> None:
            flow = _make_flow()
            closed_msg = SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)
            ws = _FakeWS([closed_msg])
            with patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=MagicMock(ws_connect=MagicMock(return_value=ws)),
            ):
                result = await flow._async_detect_canvas(_make_printer())
            assert result is False

        asyncio.run(_run())

    def test_hanging_handshake_returns_false(self) -> None:
        """A ws_connect that never completes is bounded by the outer timeout."""

        class _HangingWS:
            async def __aenter__(self) -> Self:
                await asyncio.Event().wait()  # never set — hangs forever
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

        async def _run() -> None:
            flow = _make_flow()
            with (
                patch(
                    "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                    return_value=MagicMock(
                        ws_connect=MagicMock(return_value=_HangingWS())
                    ),
                ),
                patch(
                    "custom_components.elegoo_printer.config_flow._CANVAS_DETECT_TIMEOUT",
                    0.05,
                ),
            ):
                result = await flow._async_detect_canvas(_make_printer())
            assert result is False

        asyncio.run(_run())
