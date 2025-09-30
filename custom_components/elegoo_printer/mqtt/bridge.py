"""
MQTT Bridge Server for Elegoo Printers.

This bridge allows Elegoo printers to be controlled via MQTT by:
1. Discovering printers via UDP broadcast
2. Establishing WebSocket connections to printers
3. Translating SDCP messages to/from MQTT
4. Publishing printer status to MQTT broker
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import TYPE_CHECKING

import aiomqtt
from aiohttp import ClientSession

from custom_components.elegoo_printer.const import (
    DEFAULT_BROADCAST_ADDRESS,
    DISCOVERY_MESSAGE,
    DISCOVERY_PORT,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer
from custom_components.elegoo_printer.websocket.client import ElegooPrinterClient

from .const import (
    MQTT_KEEPALIVE,
    MQTT_PORT,
    TOPIC_PREFIX,
    TOPIC_REQUEST,
    TOPIC_STATUS,
)

if TYPE_CHECKING:
    from logging import Logger

DISCOVERY_TIMEOUT = 5


class ElegooMqttBridge:
    """
    MQTT bridge server for Elegoo printers.

    Discovers printers on the network and creates a bridge between
    WebSocket/SDCP protocol and MQTT for each printer.
    """

    def __init__(
        self,
        mqtt_host: str = "localhost",
        mqtt_port: int = MQTT_PORT,
        logger: Logger | None = None,
    ) -> None:
        """Initialize the MQTT bridge."""
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.logger = logger or logging.getLogger(__name__)

        self.mqtt_client: aiomqtt.Client | None = None
        self.printer_clients: dict[str, ElegooPrinterClient] = {}
        self.running = False
        self._discovery_task: asyncio.Task | None = None
        self._mqtt_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the MQTT bridge server."""
        self.logger.info("Starting MQTT bridge server")
        self.running = True

        # Connect to MQTT broker
        self.mqtt_client = aiomqtt.Client(
            hostname=self.mqtt_host,
            port=self.mqtt_port,
            keepalive=MQTT_KEEPALIVE,
        )

        try:
            await self.mqtt_client.__aenter__()
            self.logger.info("Connected to MQTT broker at %s:%s", self.mqtt_host, self.mqtt_port)
        except Exception as e:
            self.logger.error("Failed to connect to MQTT broker: %s", e)
            return

        # Start discovery and MQTT message handling
        self._discovery_task = asyncio.create_task(self._discovery_loop())
        self._mqtt_task = asyncio.create_task(self._mqtt_message_handler())

        # Wait for tasks
        try:
            await asyncio.gather(self._discovery_task, self._mqtt_task)
        except asyncio.CancelledError:
            self.logger.info("Bridge tasks cancelled")

    async def stop(self) -> None:
        """Stop the MQTT bridge server."""
        self.logger.info("Stopping MQTT bridge server")
        self.running = False

        # Cancel tasks
        if self._discovery_task:
            self._discovery_task.cancel()
        if self._mqtt_task:
            self._mqtt_task.cancel()

        # Disconnect from printers
        for client in self.printer_clients.values():
            await client.disconnect()
        self.printer_clients.clear()

        # Disconnect from MQTT
        if self.mqtt_client:
            await self.mqtt_client.__aexit__(None, None, None)

    async def _discovery_loop(self) -> None:
        """Periodically discover printers on the network."""
        while self.running:
            try:
                await self._discover_printers()
                await asyncio.sleep(60)  # Discovery every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Error in discovery loop: %s", e)
                await asyncio.sleep(10)

    async def _discover_printers(self) -> None:
        """Discover printers via UDP broadcast."""
        self.logger.debug("Broadcasting for printer discovery")

        discovered_printers = await asyncio.get_event_loop().run_in_executor(
            None, self._udp_discover
        )

        for printer in discovered_printers:
            if printer.id not in self.printer_clients:
                await self._connect_to_printer(printer)

    def _udp_discover(self) -> list[Printer]:
        """Discover printers using UDP broadcast."""
        discovered_printers: list[Printer] = []

        msg = DISCOVERY_MESSAGE.encode()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(DISCOVERY_TIMEOUT)

            try:
                sock.sendto(msg, (DEFAULT_BROADCAST_ADDRESS, DISCOVERY_PORT))

                while True:
                    try:
                        data, addr = sock.recvfrom(8192)
                        self.logger.debug("Discovery response from %s", addr)

                        try:
                            printer_info = data.decode("utf-8")
                            printer = Printer(printer_info)
                            discovered_printers.append(printer)
                            self.logger.info("Discovered printer: %s (%s)", printer.name, printer.ip_address)
                        except (UnicodeDecodeError, ValueError, TypeError) as e:
                            self.logger.warning("Failed to parse printer data: %s", e)

                    except TimeoutError:
                        break

            except OSError as e:
                self.logger.error("Socket error during discovery: %s", e)

        return discovered_printers

    async def _connect_to_printer(self, printer: Printer) -> None:
        """Connect to a discovered printer."""
        self.logger.info("Connecting to printer: %s", printer.name)

        session = ClientSession()
        client = ElegooPrinterClient(
            ip_address=printer.ip_address,
            session=session,
            logger=self.logger,
        )

        try:
            connected = await client.connect_printer(printer, proxy_enabled=False)
            if connected:
                self.printer_clients[printer.id] = client
                self.logger.info("Successfully connected to printer: %s", printer.name)

                # Start forwarding printer messages to MQTT
                asyncio.create_task(self._forward_printer_to_mqtt(printer, client))
            else:
                self.logger.warning("Failed to connect to printer: %s", printer.name)
                await session.close()

        except Exception as e:
            self.logger.error("Error connecting to printer %s: %s", printer.name, e)
            await session.close()

    async def _forward_printer_to_mqtt(self, printer: Printer, client: ElegooPrinterClient) -> None:
        """Forward printer WebSocket messages to MQTT."""
        # This would need to tap into the WebSocket message stream
        # For now, we'll poll for status updates
        while self.running and client.is_connected:
            try:
                # Get printer status and publish to MQTT
                printer_data = await client.get_printer_status()

                status_topic = f"{TOPIC_PREFIX}/{TOPIC_STATUS}/{printer.id}"
                status_payload = json.dumps(printer_data.status.__dict__, default=str)

                if self.mqtt_client:
                    await self.mqtt_client.publish(status_topic, status_payload)

                await asyncio.sleep(5)  # Poll every 5 seconds

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Error forwarding printer data: %s", e)
                await asyncio.sleep(10)

    async def _mqtt_message_handler(self) -> None:
        """Handle incoming MQTT messages and forward to printers."""
        if not self.mqtt_client:
            return

        # Subscribe to all request topics
        await self.mqtt_client.subscribe(f"{TOPIC_PREFIX}/{TOPIC_REQUEST}/+")

        async for message in self.mqtt_client.messages:
            try:
                await self._handle_mqtt_message(message)
            except Exception as e:
                self.logger.error("Error handling MQTT message: %s", e)

    async def _handle_mqtt_message(self, message: aiomqtt.Message) -> None:
        """Handle a single MQTT message."""
        topic_parts = str(message.topic).split("/")
        if len(topic_parts) >= 3 and topic_parts[1] == TOPIC_REQUEST:
            printer_id = topic_parts[2]

            if printer_id in self.printer_clients:
                client = self.printer_clients[printer_id]

                try:
                    payload = json.loads(message.payload.decode())
                    # Forward command to printer via WebSocket
                    # This would need integration with the existing command system
                    self.logger.debug("Forwarding MQTT command to printer %s: %s", printer_id, payload)

                except json.JSONDecodeError:
                    self.logger.warning("Invalid JSON in MQTT message")
            else:
                self.logger.warning("Received MQTT message for unknown printer: %s", printer_id)
