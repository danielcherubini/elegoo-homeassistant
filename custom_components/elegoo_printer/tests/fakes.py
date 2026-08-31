"""
Shared test doubles for the Elegoo Printer test suites.

This module holds fake objects used across multiple test modules (the
SDCP and cc2 client-test suites), so that renaming or moving a single
test file cannot break the others. It contains NO test functions — only
importable doubles.
"""

from __future__ import annotations

import asyncio
import json
import types
from types import SimpleNamespace
from typing import Any, Self


class FakeAiomqttClient:
    """
    Test double standing in for ``aiomqtt.Client`` (async context manager).

    Records constructor kwargs, subscriptions, and published frames; queued
    messages are delivered to the client's ``async for message in
    client.messages`` loop via an :class:`asyncio.Queue`.

    Shared by ``cc2/tests/test_client_factory.py`` — one implementation for
    both transports.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Record the constructor kwargs and start unconnected."""
        self.kwargs: dict[str, Any] = dict(kwargs)
        self.connected: bool = False
        self.subscribed: list[str] = []
        self.published: list[tuple[str, str]] = []
        self._queue: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self) -> Self:
        """Mark the transport as connected and return self."""
        self.connected = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        """Mark the transport as disconnected."""
        del exc_type, exc, traceback
        self.connected = False

    async def subscribe(self, topic: Any) -> None:
        """Record a subscription."""
        self.subscribed.append(str(topic))

    async def publish(self, topic: Any, payload: Any) -> None:
        """Record a published frame."""
        self.published.append((str(topic), str(payload)))

    async def disconnect(self) -> None:
        """No-op disconnect."""

    def queue_message(self, topic: str, payload: dict[str, Any] | bytes | str) -> None:
        """Queue an inbound message for the client's message loop."""
        if isinstance(payload, bytes):
            data = payload
        elif isinstance(payload, str):
            data = payload.encode("utf-8")
        else:
            data = json.dumps(payload).encode("utf-8")
        self._queue.put_nowait(SimpleNamespace(topic=topic, payload=data))

    @property
    def messages(self) -> Any:
        """Provide the async-iterable message stream the client consumes."""
        return self._iter_messages()

    async def _iter_messages(self) -> Any:
        """Yield queued messages until the consuming task is cancelled."""
        while True:
            yield await self._queue.get()


class FailingFakeAiomqttClient(FakeAiomqttClient):
    """Fake whose :meth:`__aenter__` raises :class:`OSError`."""

    async def __aenter__(self) -> Self:
        """Raise OSError every time (simulates a refused connection)."""
        msg = "simulated connection refused"
        raise OSError(msg)
