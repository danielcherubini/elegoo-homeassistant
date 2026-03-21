"""Tests for URL redaction used in api logging."""

from __future__ import annotations

import pytest

from custom_components.elegoo_printer.api import _sanitize_url_for_log


class TestSanitizeUrlForLog:
    """_sanitize_url_for_log strips authority userinfo for safe logging."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (
                "http://user:secret@192.0.2.1:8080/proxy",
                "http://192.0.2.1:8080/proxy",
            ),
            (
                "https://onlyuser@example.com/",
                "https://example.com/",
            ),
            (
                "http://user:p%40ssword@host/path?q=1",
                "http://host/path?q=1",
            ),
            (
                "http://user:pass@[::1]:9000/",
                "http://[::1]:9000/",
            ),
            ("http://192.0.2.1/", "http://192.0.2.1/"),
            ("192.0.2.1", "192.0.2.1"),
        ],
    )
    def test_cases(self, raw: str, expected: str) -> None:
        assert _sanitize_url_for_log(raw) == expected
