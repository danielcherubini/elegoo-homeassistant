---
status: accepted
date: 2026-09-03
supersedes:
superseded-by:
---

# CC2 grafts `SdcpPrinterClient` (shared state/skeleton, own wire)

**Status:** accepted

## Context

`SdcpPrinterClient` (introduced in PR #403) is the shared base for the two SDCP transports: identical state (`_gcode_proxy`, `printer_data`, `_is_connected`, `_listener_task`, `_response_events`, `_response_lock`), a single `disconnect()` skeleton (listener cancel + suppression wait + terminal-exception containment), task accessors, and the SDCP `_send_printer_cmd` framing. CC2 plate-uses the same state names and the same disconnect shape (task 1 bonus), but speaks a different wire: counter-based request IDs, a CC2 envelope, `api_response` routing, registration, a heartbeat, and a delayed disconnect.

Options:

1. Graft CC2 onto the base, sharing state/skeleton, keeping CC2's wire methods.
2. Reconsider ("optional" as designed in plan 001, task 4).
3. Graft CC2 AND unify `_send_command` behind a parameterized ID scheme/envelope.

## Decision

Graft CC2 (option 1): share the `__init__` state block, `is_connected`/`_transport_open` (CC2 keeps its own `_transport_open` override), the base `disconnect()` skeleton via `_disconnect_pre`/`_on_disconnect` hooks (generation increment + heartbeat stop pre-await; delayed-disconnect scheduling and cancel-after-await of the delay task), and only the **verified-identical** task-accessor bodies. CC2's `_send_command`, registration, heartbeat, delayed disconnect, `_handle_message` dispatch, and password fallback remain 100% in CC2. The base keeps the name `SdcpPrinterClient` — a "SDCP-shared machinery + transport-neutral seams" thing — with a docstring note to that effect.

## Consequences

- **Positive:** one disconnect containment path for all three clients; a single location for shared state lifecycle; CC2's pin (task 1 style) adapts from a module patch to inheritance.
- **Negative:** the base README must stay accurate about what is SDCP-specific and what is transport-neutral; inheritance increases the API surface where "unused" stubs live (note `get_printer_task_detail` stub).
- **Neutral:** the ws/mqtt module and the consumer side (api/config flow/coordinator) have zero diff.
