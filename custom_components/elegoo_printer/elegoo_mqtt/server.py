"""MQTT server for Elegoo printers."""

from amqtt.broker import Broker

from .const import DEFAULT_MQTT_PORT, LOGGER


class ElegooMqttServer:
    """MQTT server for interacting with an Elegoo printer."""

    def __init__(self, ip_address: str, port: int = DEFAULT_MQTT_PORT) -> None:
        """Initialize an ElegooMqttServer."""
        self.ip_address = ip_address
        self.port = port
        self.broker = Broker()

    async def start(self) -> None:
        """Start the MQTT server."""
        try:
            await self.broker.start()
            LOGGER.info(f"MQTT server started on {self.ip_address}:{self.port}")
        except Exception as e:
            LOGGER.error(f"Failed to start MQTT server: {e}")

    async def stop(self) -> None:
        """Stop the MQTT server."""
        try:
            await self.broker.shutdown()
            LOGGER.info("MQTT server stopped")
        except Exception as e:
            LOGGER.error(f"Failed to stop MQTT server: {e}")
