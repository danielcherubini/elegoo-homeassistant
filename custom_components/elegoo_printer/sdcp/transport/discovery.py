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

import socket
from typing import Any

from custom_components.elegoo_printer.const import (
    DEFAULT_BROADCAST_ADDRESS,
    DISCOVERY_MESSAGE,
    DISCOVERY_PORT,
    DISCOVERY_TIMEOUT,
)
from custom_components.elegoo_printer.sdcp.models.printer import Printer


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
