"""
Shared UDP discovery skeleton for SDCP printers.

The per-transport ``discover_printer`` methods (websocket / mqtt
clients) are synchronous (Home Assistant invokes them via
``async_add_executor_job``) and delegate here to the blocking-socket
broadcast below. Per-transport behaviour stays in the class: the
websocket printer/"Proxy" local-IP filter and the mqtt transport's
absence of a local filter.

Stdlib only — this module must not import aiohttp / aiomqtt / paho
(enforced by ``tests/test_import_sdcppack.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import Any

from custom_components.elegoo_printer.const import (
    DEFAULT_BROADCAST_ADDRESS,
    DISCOVERY_MESSAGE,
    DISCOVERY_PORT,
    DISCOVERY_TIMEOUT,
    TCP_PROBE_TIMEOUT,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer


async def async_tcp_port_is_open(
    host: str | None,
    port: int,
    *,
    wait_timeout: float = TCP_PROBE_TIMEOUT,
) -> bool:
    """
    Report whether a TCP port answers, without speaking the protocol.

    SDCP discovery (UDP 3000) answers a different question from reachability:
    a CC1 that cannot ping its default gateway decides the network is down and
    stops answering ``M99999`` while its control socket (TCP 3030) stays open
    for the whole time (#431). Callers that must decide "is the printer there"
    therefore need this second, transport-level question asked.

    True only on a *completed* connect — an unanswered SYN, a refused port and
    a host that cannot be resolved all return False, and the function never
    raises for them. The socket is closed on success; a peer that vanished
    between the handshake and the close is not a reason to report the port
    closed, so the close is suppressed rather than observed.

    Arguments:
        host: The host to dial; None or blank returns False.
        port: The TCP port to dial.
        wait_timeout: Seconds to wait for the connect to complete. Named
            ``wait_timeout`` rather than ``timeout`` for the same reason
            ``ElegooCC2Client.async_discover_serial`` names its bound that:
            ruff ASYNC109 rejects an async function with a ``timeout``
            parameter.

    Returns:
        True when the TCP connect completed within ``wait_timeout``.

    """
    if not host:
        return False
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=wait_timeout
        )
    except (TimeoutError, OSError):
        # TimeoutError: silent. OSError covers ConnectionRefusedError (closed
        # port), the gaierror of an unresolvable host and the socket errors a
        # network with no route raises. All of them mean "not open".
        return False
    with contextlib.suppress(OSError):
        # The connect already completed; how the teardown goes says nothing
        # about whether the port answered.
        writer.close()
        await writer.wait_closed()
    return True


def parse_printer_discovery_response(data: bytes) -> Printer | None:
    """
    Parse a discovery response frame into a Printer.

    Arguments:
        data: The raw UDP datagram bytes.

    Returns:
        The Printer if the frame decodes and instantiates; None when the
        frame is malformed (undecodable bytes, or a printer that fails
        to parse).

    """
    try:
        printer_info = data.decode("utf-8")
        return Printer(printer_info)
    except (UnicodeDecodeError, ValueError, TypeError):
        return None


def perform_printer_discovery(
    broadcast_address: str = DEFAULT_BROADCAST_ADDRESS,
    *,
    logger: Any | None = None,
) -> list[Printer]:
    """
    Broadcast the SDCP discovery message and collect printer responses.

    Sends ``DISCOVERY_MESSAGE`` to ``broadcast_address`` over a
    broadcast UDP socket and reads responses until the socket times out
    or a socket error occurs.

    Arguments:
        broadcast_address: The network address to send the discovery
            message to.
        logger: Optional logger for received-responses and socket errors.

    Returns:
        The discovered printers; an empty list when a socket error occurs
        (matching the pre-extraction per-transport behaviour).

    """
    discovered_printers: list[Printer] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(DISCOVERY_TIMEOUT)
        try:
            sock.sendto(DISCOVERY_MESSAGE.encode(), (broadcast_address, DISCOVERY_PORT))
            while True:
                try:
                    data, addr = sock.recvfrom(8192)
                    if logger is not None:
                        msg = f"Discovery response received from {addr}"
                        logger.info(msg)
                    printer = parse_printer_discovery_response(data)
                    if printer is not None:
                        discovered_printers.append(printer)
                except TimeoutError:
                    break  # Timeout, no more responses
        except OSError as e:
            msg = f"Socket error during discovery: {e}"
            if logger is not None:
                logger.exception(msg)
            return []
    return discovered_printers
