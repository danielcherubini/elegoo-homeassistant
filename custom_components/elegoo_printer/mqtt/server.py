"""
Embedded MQTT Broker for Elegoo Printers.

Based on SimpleMQTTServer from Cassini (MIT License).
Copyright (C) 2023 Vladimir Vukicevic
Adapted for Home Assistant Elegoo Printer Integration.
"""

import asyncio
import logging
import struct
from typing import Any

from .const import MQTT_BROKER_HOST, MQTT_BROKER_PORT

_LOGGER = logging.getLogger(__name__)

# MQTT Message Types
MQTT_CONNECT = 1
MQTT_CONNACK = 2
MQTT_PUBLISH = 3
MQTT_PUBACK = 4
MQTT_SUBSCRIBE = 8
MQTT_SUBACK = 9
MQTT_DISCONNECT = 14


class ElegooMQTTBroker:
    """
    Minimal embedded MQTT broker for Elegoo printers.

    This broker allows Elegoo printers to connect via MQTT without requiring
    an external Mosquitto broker. It implements a minimal subset of the MQTT
    protocol needed for printer communication.

    This is a singleton - only one broker instance runs, shared by all MQTT printers.
    """

    _instance: "ElegooMQTTBroker | None" = None
    _reference_count: int = 0
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(
        self, host: str = MQTT_BROKER_HOST, port: int = MQTT_BROKER_PORT
    ) -> None:
        """
        Initialize the MQTT broker.

        Args:
            host: Host address to bind to (default: 0.0.0.0 for all interfaces)
            port: Port to listen on (default: 18830)

        """
        self.host = host
        self.port = port
        self.server = None
        self.incoming_messages: asyncio.Queue = asyncio.Queue()
        self.outgoing_messages: asyncio.Queue = asyncio.Queue()
        self.connected_clients: dict[str, Any] = {}
        # Global subscription registry: {topic: {writer: qos}}
        self.subscriptions: dict[str, dict[Any, int]] = {}
        self.subscriptions_lock: asyncio.Lock = asyncio.Lock()
        self.next_pack_id_value = 1
        self._running = False

    @classmethod
    async def get_instance(cls) -> "ElegooMQTTBroker":
        """
        Get or create the singleton broker instance.

        Returns:
            The shared broker instance

        """
        async with cls._lock:
            if cls._instance is None:
                _LOGGER.debug("Creating new MQTT broker instance")
                cls._reference_count = 0  # Reset count when creating new instance
                cls._instance = cls()
                await cls._instance.start()
            cls._reference_count += 1
            _LOGGER.info(
                "MQTT broker reference count increased to %s", cls._reference_count
            )
            return cls._instance

    @classmethod
    async def release_instance(cls) -> None:
        """
        Release a reference to the broker instance.

        Stops the broker when reference count reaches zero.
        """
        async with cls._lock:
            if cls._reference_count > 0:
                cls._reference_count -= 1
                _LOGGER.debug(
                    "MQTT broker reference count decreased to %s", cls._reference_count
                )

            if cls._reference_count == 0 and cls._instance is not None:
                _LOGGER.info("Stopping MQTT broker (ref count reached 0)")
                await cls._instance.stop()
                cls._instance = None

    async def start(self) -> None:
        """Start the MQTT broker server."""
        try:
            self.server = await asyncio.start_server(
                self.handle_client, self.host, self.port
            )
            self.port = self.server.sockets[0].getsockname()[1]
            self._running = True
            _LOGGER.info(
                "MQTT Broker listening on %s",
                self.server.sockets[0].getsockname(),
            )
        except OSError:
            _LOGGER.exception("Failed to start MQTT broker on port %s", self.port)
            raise

    async def stop(self) -> None:
        """Stop the MQTT broker server."""
        self._running = False
        if self.server:
            self.server.close()
            try:
                # Use timeout to prevent hanging during shutdown
                await asyncio.wait_for(self.server.wait_closed(), timeout=5.0)
                _LOGGER.info("MQTT Broker stopped")
            except TimeoutError:
                _LOGGER.warning("MQTT Broker stop timed out, forcing shutdown")
            finally:
                self.server = None

    async def serve_forever(self) -> None:
        """Run the broker server forever."""
        if self.server:
            await self.server.serve_forever()

    def publish(self, topic: str, payload: str) -> None:
        """
        Queue a message to be published to subscribed clients.

        Args:
            topic: MQTT topic to publish to
            payload: Message payload

        """
        self.outgoing_messages.put_nowait({"topic": topic, "payload": payload})

    async def next_published_message(self) -> dict[str, str]:
        """
        Wait for and return the next published message from a client.

        Returns:
            Dictionary with 'topic' and 'payload' keys

        """
        return await self.incoming_messages.get()

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """
        Handle a connected MQTT client.

        Args:
            reader: Asyncio stream reader
            writer: Asyncio stream writer

        """
        try:
            await self._handle_client_inner(reader, writer)
        except Exception:
            _LOGGER.exception("Exception handling MQTT client")

    async def _handle_client_inner(  # noqa: C901, PLR0912, PLR0915
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """
        Inner client handler with protocol implementation.

        Args:
            reader: Asyncio stream reader
            writer: Asyncio stream writer

        """
        addr = writer.get_extra_info("peername")
        _LOGGER.debug("MQTT client connected from %s", addr)
        data = b""

        subscribed_topics: dict[str, int] = {}
        client_id: str | None = None

        read_future = asyncio.ensure_future(reader.read(1024))
        outgoing_messages_future = asyncio.ensure_future(self.outgoing_messages.get())

        while self._running:
            completed, _pending = await asyncio.wait(
                [read_future, outgoing_messages_future],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Handle outgoing messages to this client
            if outgoing_messages_future in completed:
                outmsg = outgoing_messages_future.result()
                topic = outmsg["topic"]
                payload = outmsg["payload"]

                if topic in subscribed_topics:
                    qos = subscribed_topics[topic]
                    await self._send_msg(
                        writer,
                        MQTT_PUBLISH,
                        payload=self._encode_publish(
                            topic, payload, self._next_pack_id()
                        ),
                    )
                else:
                    _LOGGER.debug("MQTT SEND: Client not subscribed to %s", topic)
                outgoing_messages_future = asyncio.ensure_future(
                    self.outgoing_messages.get()
                )

            # Handle incoming data from client
            if read_future in completed:
                d = read_future.result()
                if not d:  # Connection closed
                    break
                data += d
                read_future = asyncio.ensure_future(reader.read(1024))
            else:
                continue

            # Process MQTT packets
            while True:
                if len(data) < 2:  # noqa: PLR2004
                    break

                msg_type = data[0] >> 4
                msg_flags = data[0] & 0xF
                msg_length, len_bytes_consumed = self._decode_length(data[1:])

                # Check if we have the full packet
                head_len = len_bytes_consumed + 1
                if msg_length + head_len > len(data):
                    break

                # Extract message payload and advance buffer
                message = data[head_len : head_len + msg_length]
                data = data[head_len + msg_length :]

                # Process MQTT packet types
                if msg_type == MQTT_CONNECT:
                    if message[0:6] != b"\x00\x04MQTT":
                        _LOGGER.error("MQTT client %s: bad CONNECT", addr)
                        writer.close()
                        return

                    client_id_len = struct.unpack("!H", message[10:12])[0]
                    client_id = message[12 : 12 + client_id_len].decode("utf-8")

                    _LOGGER.info("MQTT client %s at %s connected", client_id, addr)
                    self.connected_clients[client_id] = addr
                    await self._send_msg(writer, MQTT_CONNACK, payload=b"\x00\x00")

                elif msg_type == MQTT_PUBLISH:
                    qos = (msg_flags >> 1) & 0x3
                    topic, packid, content = self._parse_publish(message, qos)

                    _LOGGER.debug("MQTT received message on topic: %s", topic)
                    max_log_len = 500
                    payload_preview = (
                        content[:max_log_len] if len(content) > max_log_len else content
                    )
                    _LOGGER.debug("MQTT message payload: %s", payload_preview)
                    self.incoming_messages.put_nowait(
                        {"topic": topic, "payload": content}
                    )

                    # Forward message to all subscribed clients
                    async with self.subscriptions_lock:
                        if topic in self.subscriptions:
                            for client_writer in self.subscriptions[topic]:
                                # Don't send back to the publishing client
                                if client_writer != writer:
                                    try:
                                        # Forward with QoS 0 (no packet ID needed)
                                        await self._send_msg(
                                            client_writer,
                                            MQTT_PUBLISH,
                                            flags=0,
                                            payload=self._encode_publish(
                                                topic, content, packid=0
                                            ),
                                        )
                                    except Exception as e:  # noqa: BLE001
                                        _LOGGER.debug(
                                            "Failed to forward message to client: %s", e
                                        )

                    if qos > 0:
                        await self._send_msg(writer, MQTT_PUBACK, packet_ident=packid)

                elif msg_type == MQTT_SUBSCRIBE:
                    qos = (msg_flags >> 1) & 0x3
                    packid = message[0] << 8 | message[1]
                    message = message[2:]
                    topic = self._parse_subscribe(message)
                    _LOGGER.info(
                        "MQTT client %s subscribed to '%s' (QoS %s)", addr, topic, qos
                    )
                    subscribed_topics[topic] = qos

                    # Add to global subscription registry
                    async with self.subscriptions_lock:
                        if topic not in self.subscriptions:
                            self.subscriptions[topic] = {}
                        self.subscriptions[topic][writer] = qos

                    await self._send_msg(
                        writer, MQTT_SUBACK, packet_ident=packid, payload=bytes([qos])
                    )

                elif msg_type == MQTT_DISCONNECT:
                    _LOGGER.info("MQTT client %s disconnected", addr)

                    # Cancel pending futures to avoid "Task was destroyed" warnings
                    if not read_future.done():
                        read_future.cancel()
                    if not outgoing_messages_future.done():
                        outgoing_messages_future.cancel()

                    writer.close()
                    await writer.wait_closed()

                    if client_id is not None:
                        del self.connected_clients[client_id]

                    # Remove from subscription registry
                    async with self.subscriptions_lock:
                        for topic in list(self.subscriptions.keys()):
                            if writer in self.subscriptions[topic]:
                                del self.subscriptions[topic][writer]
                            if not self.subscriptions[topic]:
                                del self.subscriptions[topic]
                    return

        # Cleanup on exit
        # Cancel pending futures to avoid "Task was destroyed" warnings
        if not read_future.done():
            read_future.cancel()
        if not outgoing_messages_future.done():
            outgoing_messages_future.cancel()

        writer.close()
        await writer.wait_closed()
        if client_id and client_id in self.connected_clients:
            del self.connected_clients[client_id]

        # Remove from subscription registry on unexpected disconnect
        async with self.subscriptions_lock:
            for topic in list(self.subscriptions.keys()):
                if writer in self.subscriptions[topic]:
                    del self.subscriptions[topic][writer]
                if not self.subscriptions[topic]:
                    del self.subscriptions[topic]

    async def _send_msg(
        self,
        writer: asyncio.StreamWriter,
        msg_type: int,
        flags: int = 0,
        packet_ident: int = 0,
        payload: bytes = b"",
    ) -> None:
        """
        Send an MQTT message to a client.

        Args:
            writer: Stream writer to send to
            msg_type: MQTT message type
            flags: Message flags
            packet_ident: Packet identifier
            payload: Message payload

        """
        head = bytes([msg_type << 4 | flags])
        payload_length = len(payload)
        if packet_ident > 0:
            payload_length += 2
        head += self._encode_length(payload_length)
        if packet_ident > 0:
            head += bytes([packet_ident >> 8, packet_ident & 0xFF])
        data = head + payload
        writer.write(data)
        await writer.drain()

    def _encode_length(self, length: int) -> bytearray:
        """
        Encode message length per MQTT spec.

        Args:
            length: Length to encode

        Returns:
            Encoded length bytes

        """
        encoded = bytearray()
        while True:
            digit = length % 128
            length //= 128
            if length > 0:
                digit |= 0x80
            encoded.append(digit)
            if length == 0:
                break
        return encoded

    def _decode_length(self, data: bytes) -> tuple[int, int]:
        """
        Decode message length per MQTT spec.

        Args:
            data: Bytes containing encoded length

        Returns:
            Tuple of (decoded_length, bytes_consumed)

        """
        multiplier = 1
        value = 0
        bytes_read = 0

        for byte in data:
            bytes_read += 1
            value += (byte & 0x7F) * multiplier
            if byte & 0x80 == 0:
                break
            multiplier *= 128
            if multiplier > 2097152:  # noqa: PLR2004
                msg = "Malformed MQTT Remaining Length"
                raise ValueError(msg)

        return value, bytes_read

    def _parse_publish(self, data: bytes, qos: int = 0) -> tuple[str, int, str]:
        """
        Parse MQTT PUBLISH message.

        Args:
            data: Message data
            qos: Quality of Service level (0, 1, or 2)

        Returns:
            Tuple of (topic, packet_id, message_content)

        """
        topic_len = struct.unpack("!H", data[0:2])[0]
        topic = data[2 : 2 + topic_len].decode("utf-8")

        # QoS 0 messages don't have a packet ID
        if qos == 0:
            message_start = 2 + topic_len
            message = data[message_start:].decode("utf-8")
            return topic, 0, message

        # QoS > 0 messages have a packet ID
        packid = struct.unpack("!H", data[2 + topic_len : 4 + topic_len])[0]
        message_start = 4 + topic_len
        message = data[message_start:].decode("utf-8")
        return topic, packid, message

    def _parse_subscribe(self, data: bytes) -> str:
        """
        Parse MQTT SUBSCRIBE message.

        Args:
            data: Message data

        Returns:
            Topic string

        """
        topic_len = struct.unpack("!H", data[0:2])[0]
        return data[2 : 2 + topic_len].decode("utf-8")

    def _encode_publish(self, topic: str, message: str, packid: int = 0) -> bytes:
        """
        Encode MQTT PUBLISH message.

        Args:
            topic: Topic name
            message: Message content
            packid: Packet identifier (0 for QoS 0)

        Returns:
            Encoded PUBLISH message bytes

        """
        topic_len = len(topic)
        topic_bytes = topic.encode("utf-8")
        message_bytes = message.encode("utf-8")

        # For QoS 0, don't include packet ID
        if packid == 0:
            return struct.pack("!H", topic_len) + topic_bytes + message_bytes

        # For QoS > 0, include packet ID
        packid_bytes = struct.pack("!H", packid)
        return struct.pack("!H", topic_len) + topic_bytes + packid_bytes + message_bytes

    def _next_pack_id(self) -> int:
        """
        Get next packet identifier.

        Returns:
            Packet ID

        """
        pack_id = self.next_pack_id_value
        self.next_pack_id_value += 1
        return pack_id
