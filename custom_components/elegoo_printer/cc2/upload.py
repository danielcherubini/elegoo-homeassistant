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

import hashlib
import json
import logging
from typing import NamedTuple

import aiohttp

from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
)

_LOGGER = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
UPLOAD_PORT = 80
HTTP_OK = 200
HTTP_TOO_MANY_REQUESTS = 429
USER_AGENT = "elegoo-homeassistant"


class UploadTarget(NamedTuple):
    """Where to upload: printer host and the HTTP token (the access code)."""

    host: str
    token: str


async def upload_gcode(
    session: aiohttp.ClientSession,
    target: UploadTarget,
    filename: str,
    data: bytes,
    *,
    chunk_size: int = CHUNK_SIZE,
) -> int:
    """
    Upload ``data`` as ``filename`` to the printer's local storage.

    Returns the number of bytes sent. Raises ElegooPrinterConnectionError on
    an empty file and on any HTTP or printer-side failure; nothing is retried.
    """
    total = len(data)
    if total == 0:
        msg = f"Refusing to upload {filename}: the file is empty"
        raise ElegooPrinterConnectionError(msg)
    md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
    url = f"http://{target.host}:{UPLOAD_PORT}/upload"
    offset = 0
    while offset < total:
        chunk = data[offset : offset + chunk_size]
        end = offset + len(chunk) - 1
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Range": f"bytes {offset}-{end}/{total}",
            "X-File-Name": filename,
            "X-File-MD5": md5,
            "X-Token": target.token,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        try:
            async with session.put(url, data=chunk, headers=headers) as response:
                status = response.status
                text = await response.text()
        except (OSError, aiohttp.ClientError) as err:
            msg = f"Upload of {filename} failed at byte {offset}: {err!r}"
            raise ElegooPrinterConnectionError(msg) from err
        if status == HTTP_TOO_MANY_REQUESTS:
            msg = (
                f"Printer answered 429 (busy) at byte {offset} of {filename}; "
                "upload aborted, not retried"
            )
            raise ElegooPrinterConnectionError(msg)
        if status != HTTP_OK:
            msg = f"Upload of {filename} failed: HTTP {status} at byte {offset}"
            raise ElegooPrinterConnectionError(msg)
        try:
            body = json.loads(text) if text.strip().startswith("{") else {}
        except json.JSONDecodeError:
            body = {}
        code = body.get("error_code")
        if code != 0:
            msg = (
                f"Printer refused chunk at byte {offset} of {filename}: "
                f"error_code {code}"
            )
            raise ElegooPrinterConnectionError(msg)
        offset += len(chunk)
        _LOGGER.debug("Uploaded %d/%d bytes of %s", offset, total, filename)
    return total
