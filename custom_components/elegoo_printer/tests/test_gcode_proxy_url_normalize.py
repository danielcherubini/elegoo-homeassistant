"""Tests for GCode proxy URL normalization in the config flow."""

from __future__ import annotations

import pytest

from custom_components.elegoo_printer.config_flow import (
    ElegooOptionsFlowHandler,
    _normalize_gcode_proxy_base_url,
)
from custom_components.elegoo_printer.const import CONF_GCODE_PROXY_URL

# RFC 5737 TEST-NET-1 (192.0.2.0/24) — documentation-only addresses, not real hosts.
_DOC_HOST = "192.0.2.10"
_DOC_HOST_PORT = "192.0.2.10:8080"


class TestNormalizeGcodeProxyBaseUrl:
    """_normalize_gcode_proxy_base_url strips duplicate schemes and adds http."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (_DOC_HOST, f"http://{_DOC_HOST}"),
            (_DOC_HOST_PORT, f"http://{_DOC_HOST_PORT}"),
            (f"http://{_DOC_HOST}", f"http://{_DOC_HOST}"),
            (f"HTTP://{_DOC_HOST}", f"http://{_DOC_HOST}"),
            (f"https://{_DOC_HOST}", f"https://{_DOC_HOST}"),
            (f"http://http://{_DOC_HOST}", f"http://{_DOC_HOST}"),
            (f"http://https://{_DOC_HOST}", f"https://{_DOC_HOST}"),
            (f"  http://{_DOC_HOST}/  ", f"http://{_DOC_HOST}"),
            (f"//{_DOC_HOST}", f"http://{_DOC_HOST}"),
        ],
    )
    def test_accepted(self, raw: str, expected: str) -> None:
        assert _normalize_gcode_proxy_base_url(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "http://",
            "https://",
            "ftp://192.0.2.99",
            "http://bad://host",
        ],
    )
    def test_rejected(self, raw: str) -> None:
        assert _normalize_gcode_proxy_base_url(raw) is None


class TestCc2OptionsSuggestedGcodeProxy:
    """Options suggested values show a single canonical base URL."""

    def test_canonicalizes_double_scheme(self) -> None:
        suggested = ElegooOptionsFlowHandler._cc2_options_suggested(  # noqa: SLF001
            {CONF_GCODE_PROXY_URL: f"http://http://{_DOC_HOST_PORT}"},
        )
        assert suggested[CONF_GCODE_PROXY_URL] == f"http://{_DOC_HOST_PORT}"

    def test_empty_when_missing(self) -> None:
        suggested = ElegooOptionsFlowHandler._cc2_options_suggested({})  # noqa: SLF001
        assert suggested[CONF_GCODE_PROXY_URL] == ""
