"""
Tests for the CC2 delete-file command (method 1047).

1047 is marked "Untested" in the protocol doc and documents no response at
all, so these run against a reply shape this repo inferred from the shared
envelope. That is exactly why the assertions here are about what the client
refuses to promise: a reply it never got is never reported as a delete.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.elegoo_printer.cc2 import client as client_module
from custom_components.elegoo_printer.cc2.client import ElegooCC2Client
from custom_components.elegoo_printer.cc2.const import (
    CC2_CMD_DELETE_FILE,
    CC2_CMD_GET_STATUS,
    CC2_ERROR_FILE_DELETE_FAILED,
    CC2_STATUS_IDLE,
    CC2_STATUS_PRINTING,
    CC2_SUBSTATUS_PRINTING,
)
from custom_components.elegoo_printer.sdcp.exceptions import (
    ElegooPrinterConnectionError,
    ElegooPrinterTimeoutError,
)
from custom_components.elegoo_printer.sdcp.models.enums import PrinterType
from custom_components.elegoo_printer.sdcp.models.printer import Printer


def _client() -> ElegooCC2Client:
    printer = Printer()
    printer.printer_type = PrinterType.FDM
    return ElegooCC2Client("192.0.2.1", "TESTSN", printer=printer)


def _response(result: dict | None) -> dict | None:
    body: dict = {"error_code": 0} if result is None else result
    return {"id": 1, "method": CC2_CMD_DELETE_FILE, "result": body}


def test_delete_sends_the_documented_request_shape() -> None:  # noqa: D103
    # The request is what the protocol documents; the reply is not.
    client = _client()
    with patch.object(
        client, "_send_command", new_callable=AsyncMock, return_value=_response(None)
    ) as mock_cmd:
        code = asyncio.run(client.delete_file("old_model.gcode"))

        mock_cmd.assert_called_once_with(
            CC2_CMD_DELETE_FILE,
            {"storage_media": "local", "filename": "old_model.gcode"},
        )
    assert code == 0


def test_delete_returns_a_refused_error_code_instead_of_raising() -> None:  # noqa: D103
    # One locked or busy file must not abort a forty-file cleanup: the code is
    # data the service reports per file, so it is returned, not raised.
    client = _client()
    with patch.object(
        client,
        "_send_command",
        new_callable=AsyncMock,
        return_value=_response({"error_code": CC2_ERROR_FILE_DELETE_FAILED}),
    ):
        assert asyncio.run(client.delete_file("locked.gcode")) == (
            CC2_ERROR_FILE_DELETE_FAILED
        )


def test_delete_without_answer_raises() -> None:  # noqa: D103
    # No reply at all - a disconnect mid-command, or firmware that does not
    # implement 1047 and stays silent - is never a delete.
    client = _client()
    with (
        patch.object(
            client, "_send_command", new_callable=AsyncMock, return_value=None
        ),
        pytest.raises(ElegooPrinterConnectionError),
    ):
        asyncio.run(client.delete_file("ghost.gcode"))


def test_delete_without_error_code_raises() -> None:  # noqa: D103
    # A reply that carries no error code says nothing about the file, so it is
    # treated as unanswered rather than as the error_code 0 a .get() default
    # would invent.
    client = _client()
    with (
        patch.object(
            client,
            "_send_command",
            new_callable=AsyncMock,
            return_value=_response({"filename": "maybe.gcode"}),
        ),
        pytest.raises(ElegooPrinterConnectionError),
    ):
        asyncio.run(client.delete_file("maybe.gcode"))


# --- refresh_status freshness (finding F6) --------------------------------
#
# cleanup_files asks the printer what it is doing BEFORE it deletes anything, and
# compares the answer against the file it is about to destroy. That guard is only
# worth having if the status it reads is the one refresh_status() fetched, and it
# is not obviously so: `_handle_response` signals the waiting `_send_command`
# BEFORE it maps the reply into `printer_data.status`, so the caller is woken a
# step ahead of the status being rebuilt. The guarantee rests entirely on nothing
# awaiting at that seam. Insert `await asyncio.sleep(0)` there and every caller
# silently reads the PREVIOUS status — a job that started after the last push
# stays invisible, and the file being printed becomes a delete candidate.
#
# Nothing else in the suite notices: every other test mocks refresh_status away.
# These drive the real path. The fake below is only the wire — json round-trip,
# topic routing, request-id matching, the response lock, the event,
# `_handle_response`, `_handle_full_status` and the status mapper are all
# production code, so the ordering under test is the production ordering. No live
# broker is needed for it.

PRINTING_FILE = "just_started_now.gcode"


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
    # Read the way the guard reads it, so a test that only checked the raw cache
    # could not pass while the delete guard still saw a stale status.
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
