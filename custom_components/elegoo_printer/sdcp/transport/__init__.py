"""
Shared SDCP transport layer for the websocket and mqtt printer clients.

``base.SdcpPrinterClient`` is the plain (non-ABC) base that owns the
shared SDCP request/response plumbing; ``discovery`` is the shared UDP
discovery skeleton. The original per-transport classes in
``mqtt.client`` and ``websocket.client`` inherit the base and keep only
what differs (wire framing, topic shape, local filtering).
"""
