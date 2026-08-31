"""
Characterization tests for the embedded MQTT broker (singleton server).

Pins the singleton/refcount contract (exercising the ``_reset_for_tests``
seam), the in-process ``publish``/``next_published_message`` API, and one
real network round-trip: a genuine aiomqtt client (the same library the
production ``ElegooMQTTClient`` uses) connects to a per-test ephemeral
port, subscribes, and receives a published message forwarded by the
broker — then verifies broker stop clean-up.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import aiomqtt
import pytest

from custom_components.elegoo_printer.mqtt.server import ElegooMQTTBroker


@pytest.fixture(autouse=True)
async def reset_broker() -> Any:
    """Reset the singleton before and after every test (and stop any leak)."""
    ElegooMQTTBroker._reset_for_tests()
    yield
    if ElegooMQTTBroker._instance is not None:
        await ElegooMQTTBroker._instance.stop()
    ElegooMQTTBroker._reset_for_tests()


async def test_get_instance_creates_shared_singleton_and_increments_refs() -> None:
    """Two get_instance() calls yield the same object; refs increment."""
    first = await ElegooMQTTBroker.get_instance()
    second = await ElegooMQTTBroker.get_instance()

    assert first is second
    assert ElegooMQTTBroker._reference_count == 2
    assert first.port == 18830  # Singleton binds the documented default port.
    assert first._running is True

    await ElegooMQTTBroker.release_instance()
    assert ElegooMQTTBroker._reference_count == 1
    assert ElegooMQTTBroker._instance is not None

    await ElegooMQTTBroker.release_instance()
    assert ElegooMQTTBroker._reference_count == 0
    assert ElegooMQTTBroker._instance is None
    assert first.server is None


async def test_get_instance_recreates_after_all_refs_released() -> None:
    """After the last release, the next get_instance() is a fresh object."""
    first = await ElegooMQTTBroker.get_instance()
    await ElegooMQTTBroker.release_instance()
    assert ElegooMQTTBroker._instance is None

    second = await ElegooMQTTBroker.get_instance()
    assert second is not first
    await ElegooMQTTBroker.release_instance()


async def test_publish_api_queues_message_and_stop_cleans_up() -> None:
    """The in-process publish() API queues; never-started stop() is a no-op."""
    broker = ElegooMQTTBroker(host="127.0.0.1", port=0)
    broker.publish("test/topic", "hello")

    message = await asyncio.wait_for(broker.outgoing_messages.get(), 5)
    assert message == {"topic": "test/topic", "payload": "hello"}

    broker._running = False
    await broker.stop()
    assert broker.server is None


async def test_round_trip_subscribe_publish_with_real_mqtt_client() -> None:
    """A genuine aiomqtt client round-trip against the embedded broker."""
    broker = ElegooMQTTBroker(host="127.0.0.1", port=0)
    await broker.start()
    assert broker.port != 0  # start() re-records the real bound port.

    try:
        async with aiomqtt.Client(
            "127.0.0.1", broker.port, identifier="subclient"
        ) as subscriber:
            await subscriber.subscribe("test/topic")
            received: asyncio.Future = asyncio.Future()

            async def _watch() -> None:
                """Forward the first message the subscriber receives."""
                async for message in subscriber.messages:
                    if not received.done():
                        received.set_result(message)

            watcher = asyncio.create_task(_watch())
            try:
                async with aiomqtt.Client(
                    "127.0.0.1", broker.port, identifier="pubclient"
                ) as publisher:
                    await publisher.publish("test/topic", "hello-broker")
                message = await asyncio.wait_for(received, 10)
                # The broker also records published messages in-process.
                assert await asyncio.wait_for(broker.next_published_message(), 5) == {
                    "topic": "test/topic",
                    "payload": "hello-broker",
                }
            finally:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher

            assert str(message.topic) == "test/topic"
            assert message.payload == b"hello-broker"
    finally:
        await broker.stop()

    assert broker._running is False
    assert broker.server is None
