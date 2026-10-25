"""Tests for CC1 Canvas detection via websocket (_async_detect_canvas)."""

from __future__ import annotations

import asyncio
from typing import Self
from unittest.mock import MagicMock, patch

import aiohttp

from custom_components.elegoo_printer.tests.ws_doubles import (
    DOC_IP,
    ams_status_frame,
    closed_msg,
    make_config_flow,
    session_returning,
    text_msg,
)


def _attributes_frame() -> dict:
    """
    Build a non-status frame Canvas detection must skip over.

    Kept local rather than shared: the ``Data -> Data -> Attributes`` nesting here
    is neither of the two attributes shapes the identity probe reads (see
    ``ws_doubles``), and that is fine — this test only needs the frame to be
    something that is not a status frame.
    """
    return {
        "Topic": "sdcp/attributes/test-id",
        "Data": {"Data": {"Attributes": {"FirmwareVersion": "1.0"}}},
    }


def _make_printer() -> MagicMock:
    printer = MagicMock()
    printer.ip_address = DOC_IP
    return printer


class TestCanvasDetectionWs:
    """_async_detect_canvas returns correct result for various frames."""

    def test_status_with_canvas_returns_true(self) -> None:
        async def _run() -> None:
            flow = make_config_flow()
            with patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=session_returning(
                    text_msg(ams_status_frame(ams_connect=1))
                ),
            ):
                result = await flow._async_detect_canvas(_make_printer())
            assert result is True

        asyncio.run(_run())

    def test_status_without_canvas_returns_false(self) -> None:
        async def _run() -> None:
            flow = make_config_flow()
            with patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=session_returning(
                    text_msg(ams_status_frame(ams_connect=0))
                ),
            ):
                result = await flow._async_detect_canvas(_make_printer())
            assert result is False

        asyncio.run(_run())

    def test_skips_non_status_frames(self) -> None:
        """Attributes frame first, then status — detection still succeeds."""

        async def _run() -> None:
            flow = make_config_flow()
            with patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=session_returning(
                    text_msg(_attributes_frame()),
                    text_msg(ams_status_frame(ams_connect=1)),
                ),
            ):
                result = await flow._async_detect_canvas(_make_printer())
            assert result is True

        asyncio.run(_run())

    def test_connection_error_returns_false(self) -> None:
        async def _run() -> None:
            flow = make_config_flow()
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
            flow = make_config_flow()
            with patch(
                "custom_components.elegoo_printer.config_flow.async_get_clientsession",
                return_value=session_returning(closed_msg()),
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
            flow = make_config_flow()
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
