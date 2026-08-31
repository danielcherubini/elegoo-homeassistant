"""
Conformance pins for the three SDCP client transports.

Each transport is constructed with no network and pins the pre-connect
``is_connected is False`` flag. The ws and mqtt transports additionally
pin the shared push routes (print history / history detail / video —
shared handler bodies on ``SdcpPrinterClient``) and the status /
attributes push handling, going through each transport's OWN wire
shape (ws: in-frame ``Topic`` via ``_parse_response``; mqtt:
leading-slash broker topic via ``_parse_response``).

CC2 does not inherit the shared base (its wire — registration,
heartbeat, delayed disconnect, the ``elegoo/...`` topics — is
``_handle_message``-based, outside the base's contract), so cc2 gets
construction/flag pins only and inherits nothing.

The AMS pin is ws-only via the Ack-gated ``_canvas_handler`` — never
via ``_handle_push_frame``.
"""

from __future__ import annotations

import json
from types import MappingProxyType
from unittest.mock import MagicMock

import pytest

from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.conftest import FakeClientSession
from custom_components.elegoo_printer.mqtt.client import ElegooMqttClient
from custom_components.elegoo_printer.sdcp.models.attributes import (
    PrinterAttributes,
)
from custom_components.elegoo_printer.sdcp.models.print_history_detail import (
    PrintHistoryDetail,
)
from custom_components.elegoo_printer.sdcp.models.status import PrinterStatus
from custom_components.elegoo_printer.sdcp.models.video import ElegooVideo
from custom_components.elegoo_printer.sdcp.transport.base import (
    SdcpPrinterClient,
)
from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient

_SAMPLE_PRINTER_JSON = json.dumps(
    {
        "Id": "test_connection",
        "Data": {
            "Name": "Test Printer",
            "MachineName": "Saturn 4 Ultra",
            "BrandName": "Elegoo",
            "MainboardIP": "192.168.1.100",
            "ProtocolVersion": "V3.0.0",
            "FirmwareVersion": "V1.0.0",
            "MainboardID": "test_mainboard_id_12345",
        },
    }
)


def _make_ws_client() -> ElegooPrinterClient:
    """Build a ws client wired to a fake session (no network)."""
    return ElegooPrinterClient(
        ip_address="192.168.1.150",
        session=FakeClientSession(),
        logger=MagicMock(),
        config=MappingProxyType(json.loads(_SAMPLE_PRINTER_JSON)),
    )


def _make_mqtt_client() -> ElegooMqttClient:
    """Build an mqtt client with the factory seam (no network)."""
    return ElegooMqttClient(
        mqtt_host="127.0.0.1", logger=MagicMock(), client_factory=None
    )


def _make_cc2_client() -> ElegooCC2Client:
    """Construct (only — never connect) a cc2 client."""
    return ElegooCC2Client(printer_ip="192.168.1.150", serial_number="sn1")


@pytest.fixture
def ws_client() -> ElegooPrinterClient:
    """Construct a ws client (no network)."""
    return _make_ws_client()


@pytest.fixture
def mqtt_client() -> ElegooMqttClient:
    """Construct an mqtt client (no network)."""
    return _make_mqtt_client()


@pytest.fixture
def cc2_client() -> ElegooCC2Client:
    """Construct-only cc2 client."""
    return _make_cc2_client()


def test_ws_client_constructs_disconnected(ws_client: ElegooPrinterClient) -> None:
    """ws: construction (no network) + is_connected False pre-connect."""
    assert isinstance(ws_client, SdcpPrinterClient)
    assert ws_client.is_connected is False
    assert ws_client._is_connected is False


def test_mqtt_client_constructs_disconnected(
    mqtt_client: ElegooMqttClient,
) -> None:
    """mqtt: construction (no network) + is_connected False pre-connect."""
    assert isinstance(mqtt_client, SdcpPrinterClient)
    assert mqtt_client.is_connected is False
    assert mqtt_client._is_connected is False


def test_cc2_client_constructs_disconnected(
    cc2_client: ElegooCC2Client,
) -> None:
    """cc2: construct-only + is_connected False; cc2 does NOT inherit the base."""
    assert not isinstance(cc2_client, SdcpPrinterClient)
    assert cc2_client.is_connected is False
    assert cc2_client._is_connected is False


async def test_ws_push_handlers_route_to_printer_data(
    ws_client: ElegooPrinterClient,
) -> None:
    """The 3 shared push handler pins, via ws's own in-frame Topic routing."""
    ws_client._parse_response(
        json.dumps(
            {
                "Id": "conn",
                "Data": {
                    "Cmd": 320,
                    "Data": {"HistoryData": ["m-1", "m-2"]},
                    "RequestID": "r1",
                },
                "Topic": "sdcp/response/m1",
            }
        )
    )
    assert set(ws_client.printer_data.print_history) == {"m-1", "m-2"}

    ws_client._parse_response(
        json.dumps(
            {
                "Id": "conn",
                "Data": {
                    "Cmd": 321,
                    "Data": {
                        "HistoryDetailList": [
                            {
                                "TaskId": "m-1",
                                "BeginTime": 1.0,
                                "EndTime": 2.0,
                            }
                        ]
                    },
                    "RequestID": "r2",
                },
                "Topic": "sdcp/response/m2",
            }
        )
    )
    detail = ws_client.printer_data.print_history["m-1"]
    assert isinstance(detail, PrintHistoryDetail)
    assert detail.task_id == "m-1"

    ws_client._parse_response(
        json.dumps(
            {
                "Id": "conn",
                "Data": {
                    "Cmd": 386,
                    "Data": {
                        "Ack": 0,
                        "VideoUrl": "http://printer:8080/?action=stream",
                    },
                    "RequestID": "r3",
                },
                "Topic": "sdcp/response/m3",
            }
        )
    )
    assert isinstance(ws_client.printer_data.video, ElegooVideo)
    assert (
        ws_client.printer_data.video.video_url == "http://printer:8080/?action=stream"
    )


async def test_ws_status_and_attribute_pushes_via_own_routing(
    ws_client: ElegooPrinterClient,
) -> None:
    """ws status/attribute pushes hit the ws whole-frame shape handlers."""
    ws_client._parse_response(
        json.dumps(
            {
                "Id": 2,
                "Topic": "sdcp/status/x",
                "Status": {"TempOfNozzle": 60.5},
            }
        )
    )
    assert isinstance(ws_client.printer_data.status, PrinterStatus)
    assert ws_client.printer_data.status.temp_of_nozzle == 60.5

    default_attributes = ws_client.printer_data.attributes
    ws_client._parse_response(
        json.dumps(
            {
                "Id": 2,
                "Topic": "sdcp/attributes/x",
                "Attributes": {
                    "MachineName": "Saturn 4",
                    "FirmwareVersion": "V1.2.3",
                },
            }
        )
    )
    assert isinstance(ws_client.printer_data.attributes, PrinterAttributes)
    assert ws_client.printer_data.attributes is not default_attributes
    assert ws_client.printer_data.attributes.firmware_version == "V1.2.3"


async def test_ws_ams_canvas_pin_is_ack_gated_and_stays_in_ws(
    ws_client: ElegooPrinterClient,
) -> None:
    """AMS pin: ws-only via Ack-gated _canvas_handler (never the base router)."""
    # Ack=1 must be skipped — no ams_status update.
    ws_client._parse_response(
        json.dumps(
            {
                "Id": 1,
                "Data": {
                    "Cmd": 324,
                    "Data": {"Ack": 1},
                    "RequestID": "r4",
                },
                "Topic": "sdcp/response/x",
            }
        )
    )
    assert ws_client.printer_data.ams_status is None

    # Ack=0 stores the parsed AMS status.
    ws_client._parse_response(
        json.dumps(
            {
                "Id": 1,
                "Data": {
                    "Cmd": 324,
                    "Data": {"Ack": 0, "ActiveTrayId": 1},
                    "RequestID": "r5",
                },
                "Topic": "sdcp/response/x",
            }
        )
    )
    assert ws_client.printer_data.ams_status is not None

    # The base router has no ams channel — never via _handle_push_frame.
    ws_client._handle_push_frame("ams_status", {"Ack": 0})
    # (No exception; the router is silent for unknown channels.)


async def test_mqtt_push_handlers_route_to_printer_data(
    mqtt_client: ElegooMqttClient,
) -> None:
    """The 3 shared push handler pins, via mqtt leading-slash topics."""
    mqtt_client._parse_response(
        json.dumps(
            {
                "Id": "conn",
                "Data": {
                    "Cmd": 320,
                    "Data": {"HistoryData": ["q-1"]},
                    "RequestID": "q1",
                },
            }
        ),
        "/sdcp/response/q1",
    )
    assert set(mqtt_client.printer_data.print_history) == {"q-1"}

    mqtt_client._parse_response(
        json.dumps(
            {
                "Id": "conn",
                "Data": {
                    "Cmd": 321,
                    "Data": {
                        "HistoryDetailList": [
                            {
                                "TaskId": "q-1",
                                "BeginTime": 1.0,
                                "EndTime": 2.0,
                            }
                        ]
                    },
                    "RequestID": "q2",
                },
            }
        ),
        "/sdcp/response/q2",
    )
    detail = mqtt_client.printer_data.print_history["q-1"]
    assert isinstance(detail, PrintHistoryDetail)
    assert detail.task_id == "q-1"

    mqtt_client._parse_response(
        json.dumps(
            {
                "Id": "conn",
                "Data": {
                    "Cmd": 386,
                    "Data": {
                        "Ack": 0,
                        "VideoUrl": "http://printer:8080/?action=stream",
                    },
                    "RequestID": "q3",
                },
            }
        ),
        "/sdcp/response/q3",
    )
    assert isinstance(mqtt_client.printer_data.video, ElegooVideo)
    assert (
        mqtt_client.printer_data.video.video_url == "http://printer:8080/?action=stream"
    )


async def test_mqtt_status_and_attribute_pushes_via_own_routing(
    mqtt_client: ElegooMqttClient,
) -> None:
    """mqtt status pushes hit the 4-case chain's happy case (a)."""
    mqtt_client._parse_response(
        json.dumps({"Data": {"Status": {"TempOfNozzle": 40.5}}}),
        "/sdcp/status/q1",
    )
    assert isinstance(mqtt_client.printer_data.status, PrinterStatus)
    assert mqtt_client.printer_data.status.temp_of_nozzle == 40.5

    default_attributes = mqtt_client.printer_data.attributes
    mqtt_client._parse_response(
        json.dumps(
            {
                "Data": {
                    "Attributes": {
                        "MachineName": "Saturn 4",
                        "FirmwareVersion": "V1.2.3",
                    }
                }
            }
        ),
        "/sdcp/attributes/q1",
    )
    assert isinstance(mqtt_client.printer_data.attributes, PrinterAttributes)
    assert mqtt_client.printer_data.attributes is not default_attributes
