"""Tests for the CC2 HTTP G-code upload (cc2/upload.py)."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Self

import pytest

from custom_components.elegoo_printer.cc2.upload import UploadTarget, upload_gcode
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
)

TARGET = UploadTarget(host="192.0.2.7", token="KP")  # noqa: S106 - test fixture
PUTS_BEFORE_429 = 2


class _Response:
    """Scripted aiohttp-like response usable as an async context manager."""

    def __init__(self, status: int, text: str) -> None:
        """Store the status and body to return."""
        self.status = status
        self._text = text

    async def text(self) -> str:
        """Return the scripted body."""
        return self._text

    async def __aenter__(self) -> Self:
        """Enter the context; yields the response itself."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Nothing to release."""
        return


class FakeSession:
    """Records every PUT and answers from a scripted list of responses."""

    def __init__(self, responses: list[_Response]) -> None:
        """Queue the responses to hand out, in order."""
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def put(self, url: str, *, data: bytes, headers: dict[str, str]) -> _Response:
        """Record the PUT and return the next scripted response."""
        self.calls.append({"url": url, "data": data, "headers": headers})
        return self.responses.pop(0)


def _ok() -> _Response:
    return _Response(200, '{"error_code": 0, "offset": 1}')


def test_chunks_headers_and_token() -> None:  # noqa: D103
    data = b"G28\n" * 700  # 2800 bytes -> 3 chunks of 1000
    session = FakeSession([_ok(), _ok(), _ok()])

    sent = asyncio.run(
        upload_gcode(session, TARGET, "a.gcode", data, chunk_size=1000)  # type: ignore[arg-type]
    )

    assert sent == len(data)
    assert [c["url"] for c in session.calls] == ["http://192.0.2.7:80/upload"] * 3
    assert b"".join(c["data"] for c in session.calls) == data
    ranges = [c["headers"]["Content-Range"] for c in session.calls]
    assert ranges == [
        "bytes 0-999/2800",
        "bytes 1000-1999/2800",
        "bytes 2000-2799/2800",
    ]
    h = session.calls[0]["headers"]
    assert h["X-File-Name"] == "a.gcode"
    assert h["X-Token"] == "KP"
    assert h["X-File-MD5"] == hashlib.md5(data, usedforsecurity=False).hexdigest()
    assert h["Content-Type"] == "application/octet-stream"


def test_429_aborts_without_retry() -> None:  # noqa: D103
    # Measured: retrying after a 429 corrupts the assembled file; the SDK aborts.
    data = b"x" * 2500
    session = FakeSession([_ok(), _Response(429, "<html>429</html>"), _ok(), _ok()])

    with pytest.raises(ElegooPrinterConnectionError, match="429"):
        asyncio.run(
            upload_gcode(session, TARGET, "a.gcode", data, chunk_size=1000)  # type: ignore[arg-type]
        )
    assert len(session.calls) == PUTS_BEFORE_429  # stopped at the 429, no third PUT


def test_printer_error_code_aborts() -> None:  # noqa: D103
    data = b"x" * 10
    session = FakeSession([_Response(200, '{"error_code": 9004, "md5": "deadbeef"}')])

    with pytest.raises(ElegooPrinterConnectionError, match="9004"):
        asyncio.run(upload_gcode(session, TARGET, "a.gcode", data))  # type: ignore[arg-type]


def test_non_json_200_is_a_failure() -> None:  # noqa: D103
    data = b"x" * 10
    session = FakeSession([_Response(200, "OK")])

    with pytest.raises(ElegooPrinterConnectionError):
        asyncio.run(upload_gcode(session, TARGET, "a.gcode", data))  # type: ignore[arg-type]
