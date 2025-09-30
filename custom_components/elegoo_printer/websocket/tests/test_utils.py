"""Tests for the server utils module."""

from unittest.mock import Mock, patch

from custom_components.elegoo_printer.const import DEFAULT_FALLBACK_IP, PROXY_HOST
from custom_components.elegoo_printer.websocket.server.utils import (
    MIN_API_PATH_PARTS,
    MIN_MAINBOARD_ID_LENGTH,
    MIN_PATH_PARTS_FOR_FALLBACK,
    MIN_VIDEO_PATH_PARTS,
    extract_mainboard_id_from_header,
    extract_mainboard_id_from_topic,
    get_local_ip,
)


class TestExtractMainboardIdFromHeader:
    """Test cases for extract_mainboard_id_from_header function."""

    def test_extract_from_query_parameter(self) -> None:
        """Test extracting MainboardID from query parameter."""
        referer = "http://10.0.0.114:3030/?id=3c4c1a910147017000002c0000000000"
        result = extract_mainboard_id_from_header(referer)
        assert result == "3c4c1a910147017000002c0000000000"

    def test_extract_from_mixed_query_parameters(self) -> None:
        """Test extracting MainboardID when multiple query parameters exist."""
        referer = "http://10.0.0.114:3030/?other=value&id=test_mainboard_id_12345&another=param"
        result = extract_mainboard_id_from_header(referer)
        assert result == "test_mainboard_id_12345"

    def test_extract_too_short_id(self) -> None:
        """Test that too short IDs are rejected."""
        short_id = "a" * (MIN_MAINBOARD_ID_LENGTH - 1)
        referer = f"http://10.0.0.114:3030/?id={short_id}"
        result = extract_mainboard_id_from_header(referer)
        assert result is None

    def test_extract_minimum_length_id(self) -> None:
        """Test that minimum length IDs are accepted."""
        min_id = "a" * MIN_MAINBOARD_ID_LENGTH
        referer = f"http://10.0.0.114:3030/?id={min_id}"
        result = extract_mainboard_id_from_header(referer)
        assert result == min_id

    def test_extract_no_referer(self) -> None:
        """Test with empty or None referer."""
        assert extract_mainboard_id_from_header("") is None
        assert extract_mainboard_id_from_header(None) is None

    def test_extract_no_id_parameter(self) -> None:
        """Test with referer that has no id parameter."""
        referer = "http://10.0.0.114:3030/?other=value&another=param"
        result = extract_mainboard_id_from_header(referer)
        assert result is None


class TestExtractMainboardIdFromTopic:
    """Test cases for extract_mainboard_id_from_topic function."""

    def test_extract_from_valid_topic(self) -> None:
        """Test extracting MainboardID from valid topic."""
        topic = "sdcp/request/3c4c1a910147017000002c0000000000"
        result = extract_mainboard_id_from_topic(topic)
        assert result == "3c4c1a910147017000002c0000000000"

    def test_extract_from_topic_with_different_suffix(self) -> None:
        """Test extracting MainboardID from topic with different suffix."""
        topic = "sdcp/response/test_mainboard_id_12345"
        result = extract_mainboard_id_from_topic(topic)
        assert result == "test_mainboard_id_12345"

    def test_extract_from_topic_too_few_parts(self) -> None:
        """Test with topic that has too few parts."""
        topic = "device/short"
        result = extract_mainboard_id_from_topic(topic)
        assert result is None

    def test_extract_from_topic_wrong_prefix(self) -> None:
        """Test with topic that doesn't start with 'sdcp/'."""
        topic = "device/request/3c4c1a910147017000002c0000000000"
        result = extract_mainboard_id_from_topic(topic)
        assert result is None

    def test_extract_from_topic_too_short_id(self) -> None:
        """Test with topic containing too short MainboardID."""
        short_id = "a" * (MIN_MAINBOARD_ID_LENGTH - 1)
        topic = f"sdcp/request/{short_id}"
        result = extract_mainboard_id_from_topic(topic)
        assert result is None

    def test_extract_from_empty_topic(self) -> None:
        """Test with empty topic."""
        result = extract_mainboard_id_from_topic("")
        assert result is None

    def test_extract_from_none_topic(self) -> None:
        """Test with None topic."""
        result = extract_mainboard_id_from_topic(None)
        assert result is None


class TestGetLocalIp:
    """Test cases for get_local_ip function."""

    @patch("socket.socket")
    def test_get_local_ip_success(self, mock_socket_class: Mock) -> None:
        """Test successful local IP retrieval."""
        mock_socket = Mock()
        mock_socket.getsockname.return_value = ("192.168.1.100", 12345)
        mock_socket_class.return_value.__enter__.return_value = mock_socket

        result = get_local_ip()
        assert result == "192.168.1.100"

        # Verify socket was used correctly (DEFAULT_FALLBACK_IP, port 1)
        mock_socket.connect.assert_called_once_with((DEFAULT_FALLBACK_IP, 1))

    @patch("socket.socket")
    def test_get_local_ip_socket_error(self, mock_socket_class: Mock) -> None:
        """Test local IP retrieval with socket error."""
        mock_socket = Mock()
        mock_socket.connect.side_effect = OSError("Connection failed")
        mock_socket_class.return_value.__enter__.return_value = mock_socket

        result = get_local_ip()
        assert result == PROXY_HOST

    @patch("socket.socket")
    def test_get_local_ip_os_error(self, mock_socket_class: Mock) -> None:
        """Test local IP retrieval with OS error."""
        mock_socket = Mock()
        mock_socket.connect.side_effect = OSError("Network unreachable")
        mock_socket_class.return_value.__enter__.return_value = mock_socket

        result = get_local_ip()
        assert result == PROXY_HOST


class TestConstants:
    """Test cases for constants."""

    def test_min_constants_are_positive(self) -> None:
        """Test that minimum length constants are positive integers."""
        assert isinstance(MIN_MAINBOARD_ID_LENGTH, int)
        assert MIN_MAINBOARD_ID_LENGTH > 0

        assert isinstance(MIN_API_PATH_PARTS, int)
        assert MIN_API_PATH_PARTS > 0

        assert isinstance(MIN_VIDEO_PATH_PARTS, int)
        assert MIN_VIDEO_PATH_PARTS > 0

        assert isinstance(MIN_PATH_PARTS_FOR_FALLBACK, int)
        assert MIN_PATH_PARTS_FOR_FALLBACK > 0
