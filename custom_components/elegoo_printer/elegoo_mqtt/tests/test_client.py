"""Tests for the MQTT client."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.elegoo_printer.elegoo_mqtt.client import ElegooMqttClient


@pytest.mark.anyio
async def test_connect_success():
    """Test successful connection to the MQTT broker."""
    with patch("paho.mqtt.client.Client") as mock_client_class:
        mock_client_instance = MagicMock()
        mock_client_instance.connect = MagicMock()
        mock_client_class.return_value = mock_client_instance

        client = ElegooMqttClient(
            "localhost",
            config={"ip_address": "localhost"},
            logger=MagicMock(),
        )

        # The connect method should now return True
        await client.connect()
        client.on_connect(None, None, None, 0)
        assert client.is_connected

        mock_client_instance.connect.assert_called_once_with("localhost", 1883)
        mock_client_instance.loop_start.assert_called_once()


@pytest.mark.anyio
async def test_connect_failure():
    """Test failed connection to the MQTT broker."""
    with patch("paho.mqtt.client.Client") as mock_client_class:
        mock_client_instance = MagicMock()
        mock_client_instance.connect.side_effect = Exception("Connection failed")
        mock_client_class.return_value = mock_client_instance

        client = ElegooMqttClient(
            "localhost",
            config={"ip_address": "localhost"},
            logger=MagicMock(),
        )
        # The connect method should now return False
        assert not await client.connect()
