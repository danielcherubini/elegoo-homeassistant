"""
Utility functions and constants for the Elegoo Printer Proxy Server.

This module contains shared utilities, constants, and helper functions
used across the proxy server components.
"""

from __future__ import annotations

import re
import socket
from typing import TYPE_CHECKING

from custom_components.elegoo_printer.const import (
    DEFAULT_FALLBACK_IP,
    PROXY_HOST,
)

if TYPE_CHECKING:
    from multidict import CIMultiDictProxy

# Network and Protocol Constants
INADDR_ANY = "0.0.0.0"  # noqa: S104
DISCOVERY_TIMEOUT = 5
DISCOVERY_RATE_LIMIT_SECONDS = 30
MIN_MAINBOARD_ID_LENGTH = 8
TOPIC_PARTS_COUNT = 3  # Expected parts in SDCP topic: sdcp/{type}/{MainboardID}
MIN_PATH_PARTS_FOR_FALLBACK = 2  # Minimum path parts needed for MainboardID fallback
MIN_API_PATH_PARTS = 3  # Minimum parts for /api/{MainboardID}/... pattern
MIN_VIDEO_PATH_PARTS = 2  # Minimum parts for /video/{MainboardID} pattern
MAX_LOG_LENGTH = 50  # Maximum length for log message truncation

# HTTP Header Configuration
ALLOWED_REQUEST_HEADERS = {
    "GET": [
        "accept",
        "accept-language",
        "accept-encoding",
        "priority",
        "user-agent",
        "range",
        "if-none-match",
        "if-modified-since",
        "cache-control",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
        "sec-fetch-user",
        "upgrade-insecure-requests",
        "referer",
    ],
    "HEAD": [
        "accept",
        "accept-language",
        "accept-encoding",
        "priority",
        "user-agent",
        "range",
        "if-none-match",
        "if-modified-since",
        "cache-control",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
        "sec-fetch-user",
        "upgrade-insecure-requests",
        "referer",
    ],
    "POST": [
        "content-type",
        "content-length",
        "accept",
        "accept-language",
        "accept-encoding",
        "user-agent",
        "origin",
        "referer",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
    ],
    "WS": [
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-protocol",
        "sec-websocket-extensions",
        "upgrade",
        "connection",
        "origin",
        "user-agent",
    ],
}

ALLOWED_RESPONSE_HEADERS = {
    "GET": [
        "content-length",
        "content-type",
        "content-encoding",
        "etag",
        "cache-control",
        "last-modified",
        "accept-ranges",
        "access-control-allow-origin",
    ],
    "HEAD": [
        "content-length",
        "content-type",
        "content-encoding",
        "etag",
        "cache-control",
        "last-modified",
        "accept-ranges",
        "access-control-allow-origin",
    ],
    "OPTIONS": [
        "access-control-allow-origin",
        "access-control-allow-methods",
        "access-control-allow-headers",
        "access-control-max-age",
        "content-length",
    ],
    "POST": ["content-length", "content-type", "content-encoding"],
}

TRANSFORMABLE_MIME_TYPES = [
    "text/javascript",
    "application/json",
]

CACHEABLE_MIME_TYPES = [
    "text/css",
    "text/javascript",
    "application/javascript",
    "image/apng",
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/svg+xml",
    "image/webp",
]


def extract_mainboard_id_from_topic(topic: str) -> str | None:
    """
    Extract MainboardID from SDCP topic string.

    Args:
        topic: SDCP topic in format "sdcp/{type}/{MainboardID}"

    Returns:
        MainboardID if found, None otherwise

    """
    if not topic:
        return None

    parts = topic.split("/")
    if (
        len(parts) >= TOPIC_PARTS_COUNT
        and parts[0].lower() == "sdcp"
        and len(parts[2]) >= MIN_MAINBOARD_ID_LENGTH
    ):
        return parts[2]
    return None


def extract_mainboard_id_from_header(referer: str) -> str | None:
    """
    Extract MainboardID from referer header using query parameter pattern.

    Args:
        referer: Referer header value (e.g., "http://proxy:3030/?id=mainboardid")

    Returns:
        MainboardID if found, None otherwise

    """
    if not referer:
        return None

    # Look for id= query parameter in referer
    match = re.search(r"[?&]id=([^&]+)", referer)
    if match and len(match.group(1)) >= MIN_MAINBOARD_ID_LENGTH:
        return match.group(1)
    return None


def get_local_ip() -> str:
    """Determine the local IP address for outbound communication."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((DEFAULT_FALLBACK_IP, 1))
            return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return PROXY_HOST


def get_request_headers(method: str, headers: CIMultiDictProxy[str]) -> dict[str, str]:
    """Filter and return allowed request headers for proxying."""
    allowed = ALLOWED_REQUEST_HEADERS.get(method, [])
    filtered_headers = {}
    for h in allowed:
        if h in headers:
            filtered_headers[h] = headers[h]
    return filtered_headers


def get_response_headers(method: str, headers: CIMultiDictProxy[str]) -> dict[str, str]:
    """Filter and return allowed response headers for proxying."""
    allowed = ALLOWED_RESPONSE_HEADERS.get(method, [])
    filtered_headers = {}
    for h in allowed:
        if h in headers:
            filtered_headers[h] = headers[h]
    return filtered_headers


def set_caching_headers(headers: dict[str, str]) -> dict[str, str]:
    """Set appropriate caching headers for static content."""
    headers["Cache-Control"] = "public, max-age=31536000"  # 1 year
    return headers


def get_filtered_headers(
    headers: CIMultiDictProxy[str], allowed_headers: list[str]
) -> dict[str, str]:
    """Filter headers to only include allowed ones."""
    filtered_headers = {}
    for h in allowed_headers:
        if h in headers:
            filtered_headers[h] = headers[h]
    return filtered_headers
