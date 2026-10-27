"""
Tests for the CC2 full-status gate (method 1002): the read a destructive caller trusts.

These were written against the `cleanup_files` service, which was removed for
#432 (1047 is not served on stock firmware). The service is gone; the gate is
not. `refresh_status`, `CC2UnusableStatusError` and `_read_status_result` all
survived the removal, and `scripts/cc2_probe_delete.py` still drives
`refresh_status` through the same idle gate before it will send anything. A
surviving method that fails closed is only safe for as long as something proves
it fails closed, so these pins moved here rather than being deleted along with
the service.

The framing is now the caller's, not the service's: what is being protected is
the promise that a caller which asked "is the printer idle?" is never handed a
refusal dressed up as idle.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.elegoo_printer.cc2 import client as client_module
from custom_components.elegoo_printer.cc2.client import (
    CC2UnusableStatusError,
    ElegooCC2Client,
)
from custom_components.elegoo_printer.cc2.const import (
    CC2_CMD_GET_STATUS,
    CC2_ERROR_INVALID_PARAMETER,
    CC2_ERROR_PRINTER_BUSY,
    CC2_STATUS_IDLE,
    CC2_STATUS_PRINTING,
    CC2_SUBSTATUS_PRINTING,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterTimeoutError,
)
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer

PRINTING_FILE = "just_started_now.gcode"


def _client() -> ElegooCC2Client:
    printer = Printer()
    printer.printer_type = PrinterType.FDM
    return ElegooCC2Client("192.0.2.1", "TESTSN", printer=printer)


def _status_reply(filename: str | None) -> dict:
    """Build a 1002 reply naming `filename` as the live job, as sent."""
    print_status: dict = {"current_layer": 3}
    if filename is not None:
        print_status["filename"] = filename
    return {
        "method": CC2_CMD_GET_STATUS,
        "result": {
            "error_code": 0,
            "sequence": 42,
            "machine_status": {
                "status": CC2_STATUS_PRINTING,
                "sub_status": CC2_SUBSTATUS_PRINTING,
            },
            "print_status": print_status,
        },
    }


def _idle_client() -> ElegooCC2Client:
    """Return a client whose cached status is an idle printer naming no file."""
    client = _client()
    client._handle_full_status(
        {
            "machine_status": {"status": CC2_STATUS_IDLE, "sub_status": 0},
            "print_status": {},
            "sequence": 1,
        }
    )
    assert client.printer_data.status.print_info.filename is None
    return client


def _printing_client(filename: str) -> ElegooCC2Client:
    """Return a client whose cache says it is printing `filename` right now."""
    client = _client()
    client._handle_full_status(_status_reply(filename)["result"])
    assert client.printer_data.status.print_info.filename == filename
    return client


def _connected(client: ElegooCC2Client) -> ElegooCC2Client:
    """Mark a client as connected+registered, which is all `_send_command` checks."""
    client._is_connected = True
    client._is_registered = True
    return client


def _answer_by_method(
    client: ElegooCC2Client,
    pending: list[asyncio.Task],
    replies: dict[int, dict],
) -> list[dict]:
    """
    Answer each command the client sends with `replies[method]`, as delivered.

    Returns the list the fake transport records outgoing requests in, so a test
    can assert what the printer was ASKED for.

    Replies arrive the way a real one does: on the listener's task, through
    `_handle_message`, with the id taken from the request that prompted it. So
    the listener's cache write happens for every reply, exactly as it does on a
    printer — which is the half of the bug a test that only patched
    `_send_command` could never see.
    """
    published: list[dict] = []
    topic = f"elegoo/{client.serial_number}/{client._client_id}/api_response"

    async def _publish(_request_topic: str, payload: str) -> None:
        request = json.loads(payload)
        published.append(request)
        result = replies.get(request["method"])
        if result is None:
            return
        pending.append(
            asyncio.create_task(
                client._handle_message(
                    topic,
                    json.dumps(
                        {
                            "id": request["id"],
                            "method": request["method"],
                            "result": result,
                        }
                    ),
                )
            )
        )

    client.mqtt_client = MagicMock()  # type: ignore[assignment]
    client.mqtt_client.publish = AsyncMock(side_effect=_publish)
    return published


# --- refresh_status freshness (finding F6) --------------------------------
#
# Anything that acts on the printer has to ask what it is doing FIRST, and
# compare the answer against what it is about to destroy. That guard is only
# worth having if the status it reads is the one refresh_status() fetched, and it
# is not obviously so: `_handle_response` signals the waiting `_send_command`
# BEFORE it maps the reply into `printer_data.status`, so the caller is woken a
# step ahead of the status being rebuilt. The guarantee rests entirely on nothing
# awaiting at that seam. Insert `await asyncio.sleep(0)` there and every caller
# silently reads the PREVIOUS status.
#
# Nothing else in the suite notices: every other test mocks refresh_status away.
# These drive the real path. The fake below is only the wire — json round-trip,
# topic routing, request-id matching, the response lock, the event,
# `_handle_response`, `_handle_full_status` and the status mapper are all
# production code, so the ordering under test is the production ordering. No live
# broker is needed for it.


def test_a_woken_waiter_sees_the_status_mapped_by_the_same_reply() -> None:  # noqa: D103
    # THE pin for the freshness guarantee. A waiter is parked on the request's
    # event, with the same shape _send_command uses (`wait_for(event.wait())`),
    # then the real listener path handles a real 1002 reply naming a new file.
    # The waiter resumes when the event is set — and what it reads at that exact
    # instant is what every caller of refresh_status() reads. It has to be the
    # file from THIS reply.
    #
    # This is written against the seam rather than against refresh_status()
    # end-to-end on purpose: end-to-end, whether the woken caller or the
    # listener's next step runs first is decided by scheduler bookkeeping, and an
    # assertion that depends on it passes or fails by accident. Observing what
    # the waiter itself sees is deterministic, and it fails — with the previous
    # status, not an error — the moment anything awaits between `event.set()` and
    # `_handle_full_status`.
    client = _idle_client()
    reply = _status_reply(PRINTING_FILE)
    request_id = 7

    async def scenario() -> list[str | None]:
        event = asyncio.Event()
        client._response_events[request_id] = event
        client._response_data[request_id] = reply
        seen: list[str | None] = []

        async def waiter() -> None:
            # Mirrors _send_command's wait, which is what refresh_status returns
            # through: the first thing it does after resuming is read the status.
            await asyncio.wait_for(event.wait(), timeout=5)
            seen.append(client.printer_data.status.print_info.filename)

        waiting = asyncio.create_task(waiter())
        await asyncio.sleep(0)  # let the waiter park on the event
        assert not waiting.done()

        # The reply arriving, through the real routing and the real handler.
        await client._handle_message(
            f"elegoo/{client.serial_number}/{client._client_id}/api_response",
            json.dumps({**reply, "id": request_id}),
        )
        await waiting
        return seen

    assert asyncio.run(scenario()) == [PRINTING_FILE]


def test_refresh_status_leaves_the_reply_it_fetched_readable() -> None:  # noqa: D103
    # The same guarantee from the caller's side, end to end: after
    # refresh_status() returns — no extra yield by the caller — the status names
    # the file the printer named in ITS answer to this call, not the idle
    # nothing the cache held. Unlike the test above this one does not pin the
    # seam (which task resumes first is scheduler bookkeeping); it pins that the
    # whole path is wired, so the seam test cannot pass on a client whose
    # refresh_status never actually reaches the status mapper.
    pending: list[asyncio.Task] = []

    async def publish(_topic: str, payload: str) -> None:
        # The printer answers on its own schedule, on the listener's task. Held
        # in `pending` so the task cannot be garbage-collected mid-flight.
        envelope = {**_status_reply(PRINTING_FILE), "id": json.loads(payload)["id"]}
        pending.append(
            asyncio.create_task(
                client._handle_message(
                    f"elegoo/{client.serial_number}/{client._client_id}/api_response",
                    json.dumps(envelope),
                )
            )
        )

    client = _idle_client()
    client._is_connected = True
    client._is_registered = True
    client.mqtt_client = MagicMock()  # type: ignore[assignment]
    client.mqtt_client.publish = AsyncMock(side_effect=publish)

    asyncio.run(client.refresh_status())
    for task in pending:
        task.cancel()

    assert client.printer_data.status.print_info.filename == PRINTING_FILE
    # Read the way a destructive caller reads it, so a test that only checked the
    # raw cache could not pass while the caller still saw a stale status.
    assert client.printer_data.status.current_status is not None
    assert client.printer_data.status.print_info.status is not None


def test_refresh_status_raises_when_the_printer_stays_silent() -> None:  # noqa: D103
    # Freshness is not the only promise: a call that gets no answer must not
    # leave the caller holding the previous status and believing it is current.
    # The real wait is used, with the 10 s budget shortened, so the timeout path
    # is production code rather than a mocked stand-in.
    client = _idle_client()
    client._is_connected = True
    client._is_registered = True
    client.mqtt_client = MagicMock()  # type: ignore[assignment]
    client.mqtt_client.publish = AsyncMock()
    with (
        patch.object(client_module, "CC2_COMMAND_TIMEOUT", 0.01),
        pytest.raises(ElegooPrinterTimeoutError),
    ):
        asyncio.run(client.refresh_status())
    assert client.printer_data.status.print_info.filename is None


# --- a 1002 reply that refuses (Greptile P1, finding 1) --------------------
#
# `refresh_status` used to DISCARD the reply it waited for: the only failure it
# could report was "no answer at all". So a printer that answered the status
# request by REFUSING it left the caller holding the cache — and the cache was
# worse than stale, because `_handle_full_status` wrote the refusal envelope into
# it unconditionally. `CC2StatusMapper.map_status` reads a missing
# `machine_status` as `.get("status", CC2_STATUS_IDLE)` and a missing
# `print_status` as `filename=None`, so `{"error_code": 1003}` did not read as a
# failed status read: it read as an IDLE printer with nothing loaded.
#
# That single reply disarms both questions a destructive caller asks at once —
# "idle, so acting is safe" and "no file is being printed, so nothing needs
# protecting" — on the strength of a refusal. The tests below drive the refusal
# down the real wire (the fake is only the transport; topic routing, request-id
# matching, `_handle_response`, `_handle_full_status` and the mapper are all
# production code) and assert the two halves of the outcome: the caller is told,
# and the status the client could read before is still readable afterwards.


@pytest.mark.parametrize(
    "refusal",
    [
        # The shape the finding named: a plain firmware refusal inside `result`.
        {"error_code": CC2_ERROR_INVALID_PARAMETER},
        # Busy — a printer that will not answer a status request while printing.
        {"error_code": CC2_ERROR_PRINTER_BUSY},
        # A code that is not a number at all. `_error_code` reads it as None, and
        # None must be a refusal: a `.get("error_code", 0)` default would call it
        # "the printer did not refuse".
        {"error_code": "unavailable"},
        {"error_code": True},
        {"error_code": 0.5},
        # A refusal that also carries an empty status-shaped dict, so nothing
        # about the reply looks empty.
        {"error_code": CC2_ERROR_INVALID_PARAMETER, "machine_status": {}},
    ],
)
def test_a_refused_status_reply_raises_instead_of_reading_as_idle(  # noqa: D103
    refusal: dict,
) -> None:
    # Both halves, and they are different halves: the caller must be told, AND the
    # status the client could read before must survive. The second is what every
    # OTHER consumer of the cache needs — the sensors, the camera, the
    # coordinator — and it is the half the listener's own write used to destroy
    # before any caller got a chance to object.
    client = _connected(_printing_client(PRINTING_FILE))
    pending: list[asyncio.Task] = []
    _answer_by_method(client, pending, {CC2_CMD_GET_STATUS: refusal})

    with pytest.raises(CC2UnusableStatusError):
        asyncio.run(client.refresh_status())

    # Readable, and still saying what it said: printing, naming the live job.
    status = client.printer_data.status
    assert status.print_info.filename == PRINTING_FILE, refusal
    assert status.print_info.filename is not None
    assert client._cached_status.get("machine_status", {}).get("status") == (
        CC2_STATUS_PRINTING
    ), refusal
    for task in pending:
        task.cancel()


def test_a_status_reply_naming_no_activity_raises_rather_than_claiming_idle() -> None:  # noqa: D103
    # `{"error_code": 0}` is the printer saying "I did not refuse" and NOTHING
    # else. Accepted, it maps to IDLE with no filename — an assertion that the
    # printer is idle, made by a reply that never mentioned the printer's state.
    # Refusing it costs a caller one retried read; accepting it costs whatever the
    # caller was about to do to a printer it believed was idle.
    client = _connected(_printing_client(PRINTING_FILE))
    pending: list[asyncio.Task] = []
    _answer_by_method(client, pending, {CC2_CMD_GET_STATUS: {"error_code": 0}})

    with pytest.raises(CC2UnusableStatusError):
        asyncio.run(client.refresh_status())

    assert client.printer_data.status.print_info.filename == PRINTING_FILE
    for task in pending:
        task.cancel()


def test_a_frame_naming_a_job_but_no_machine_status_is_still_readable() -> None:  # noqa: D103
    # The other direction, so the fix is not "demand a perfect frame": a reply
    # that names the live job answers the question the gate actually asks ("which
    # file is under the nozzle?") and must not be thrown away. Refusing partial
    # frames would make the sensors blind for a printer whose firmware sends them.
    client = _connected(_idle_client())
    pending: list[asyncio.Task] = []
    _answer_by_method(
        client,
        pending,
        {
            CC2_CMD_GET_STATUS: {
                "error_code": 0,
                "print_status": {"filename": "x.gcode"},
            }
        },
    )

    asyncio.run(client.refresh_status())

    assert client.printer_data.status.print_info.filename == "x.gcode"
    for task in pending:
        task.cancel()
