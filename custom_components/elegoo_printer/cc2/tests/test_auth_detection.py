"""Tests for CC2 auth failure detection."""

from __future__ import annotations

import aiomqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client


def test_auth_failure_rc_4() -> None:  # noqa: D103
    exc = aiomqtt.MqttCodeError(4, "Connection refused")
    assert ElegooCC2Client._is_auth_failure(exc) is True


def test_auth_failure_rc_5() -> None:  # noqa: D103
    exc = aiomqtt.MqttCodeError(5, "Connection refused")
    assert ElegooCC2Client._is_auth_failure(exc) is True


def test_not_auth_failure_rc_1() -> None:  # noqa: D103
    exc = aiomqtt.MqttCodeError(1, "Connection refused")
    assert ElegooCC2Client._is_auth_failure(exc) is False


def test_not_auth_failure_timeout() -> None:  # noqa: D103
    assert ElegooCC2Client._is_auth_failure(TimeoutError()) is False


def test_not_auth_failure_os_error() -> None:  # noqa: D103
    assert ElegooCC2Client._is_auth_failure(OSError("Connection refused")) is False


def test_auth_failure_mqtt5_reason_code() -> None:  # noqa: D103
    rc = ReasonCode(PacketTypes.CONNACK, "Bad user name or password")
    assert rc.value == 134  # noqa: PLR2004
    exc = aiomqtt.MqttCodeError(rc, "Connection refused")
    assert ElegooCC2Client._is_auth_failure(exc) is True


def test_auth_failure_mqtt5_not_authorized() -> None:  # noqa: D103
    rc = ReasonCode(PacketTypes.CONNACK, "Not authorized")
    assert rc.value == 135  # noqa: PLR2004
    exc = aiomqtt.MqttCodeError(rc, "Connection refused")
    assert ElegooCC2Client._is_auth_failure(exc) is True
