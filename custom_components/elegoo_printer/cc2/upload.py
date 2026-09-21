"""
HTTP upload of a G-code file to a Centauri Carbon 2.

Mirrors what Elegoo's own elegoo-link SDK does
(src/lan/adapters/elegoo_fdm_cc2/elegoo_fdm_cc2_http_transfer.cpp): PUT /upload
on port 80 in 1 MB chunks with Content-Range, the file name, the MD5 of the
whole file and the access code as headers, all over one kept-alive connection.

Measured on firmware 02.01.00.00: a fresh TCP connection per chunk is answered
with HTTP 429 on the fourth chunk, and retrying a chunk after a 429 corrupts
the assembled file (the last chunk then fails with error_code 9004, MD5
mismatch). So this uses the shared aiohttp session (connection reuse) and
aborts on any non-success without retrying, as the SDK does. The `offset`
field of the printer's response is not used; it is inconsistent (the first
response carries the last written byte, later ones the next offset).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, NamedTuple

import aiohttp

from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_LOGGER = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
UPLOAD_PORT = 80
HTTP_OK = 200
HTTP_TOO_MANY_REQUESTS = 429
USER_AGENT = "elegoo-homeassistant"
# Per chunk. A 1 MB chunk crosses the LAN in well under a second and the printer
# acknowledges each one, so a printer that vanishes mid-upload surfaces here as a
# TimeoutError instead of holding the call, the upload lock and the staged copy
# until TCP gives up on its own. Measured 19.09.2026: without this, a power cut
# during the upload held the service for 2.5 minutes, until the rebooted printer
# answered the replayed chunk with HTTP 408.
CHUNK_TIMEOUT = aiohttp.ClientTimeout(sock_connect=10, sock_read=30)


class UploadTarget(NamedTuple):
    """Where to upload: printer host and the HTTP token (the access code)."""

    host: str
    token: str


class UploadFile(NamedTuple):
    """What is uploaded: name on the printer, total size and MD5 of the whole file."""

    name: str
    size: int
    md5: str


async def upload_gcode(
    session: aiohttp.ClientSession,
    target: UploadTarget,
    file: UploadFile,
    chunks: AsyncIterator[bytes],
) -> int:
    """
    Upload ``chunks`` as ``file.name`` to the printer's local storage.

    ``file.size`` and ``file.md5`` go into every request, so the caller walks
    the file once before sending; nothing here holds more than one chunk.
    Returns the number of bytes sent. Raises ElegooPrinterConnectionError on
    an empty file, on a short stream and on any HTTP or printer-side
    failure; nothing is retried.
    """
    if file.size == 0:
        msg = f"Refusing to upload {file.name}: the file is empty"
        raise ElegooPrinterConnectionError(msg)
    url = f"http://{target.host}:{UPLOAD_PORT}/upload"
    offset = 0
    async for chunk in chunks:
        if not chunk:
            continue
        end = offset + len(chunk) - 1
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Range": f"bytes {offset}-{end}/{file.size}",
            "X-File-Name": file.name,
            "X-File-MD5": file.md5,
            "X-Token": target.token,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        try:
            async with session.put(
                url, data=chunk, headers=headers, timeout=CHUNK_TIMEOUT
            ) as response:
                status = response.status
                text = await response.text()
        except (OSError, aiohttp.ClientError) as err:
            msg = f"Upload of {file.name} failed at byte {offset}: {err!r}"
            raise ElegooPrinterConnectionError(msg) from err
        if status == HTTP_TOO_MANY_REQUESTS:
            msg = (
                f"Printer answered 429 (busy) at byte {offset} of {file.name}; "
                "upload aborted, not retried"
            )
            raise ElegooPrinterConnectionError(msg)
        if status != HTTP_OK:
            msg = f"Upload of {file.name} failed: HTTP {status} at byte {offset}"
            raise ElegooPrinterConnectionError(msg)
        try:
            body = json.loads(text) if text.strip().startswith("{") else {}
        except json.JSONDecodeError:
            body = {}
        code = body.get("error_code")
        if code != 0:
            msg = (
                f"Printer refused chunk at byte {offset} of {file.name}: "
                f"error_code {code}"
            )
            raise ElegooPrinterConnectionError(msg)
        offset += len(chunk)
        _LOGGER.debug("Uploaded %d/%d bytes of %s", offset, file.size, file.name)
    if offset != file.size:
        msg = f"Upload of {file.name} ended after {offset} of {file.size} bytes"
        raise ElegooPrinterConnectionError(msg)
    return offset
