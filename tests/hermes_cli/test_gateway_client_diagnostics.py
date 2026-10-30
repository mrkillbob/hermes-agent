"""Transport evidence preserves phases and numeric failures without peer data."""
import asyncio
from contextlib import asynccontextmanager
import json
import logging

import pytest


@pytest.mark.asyncio
async def test_websocket_failure_keeps_numeric_cause_and_existing_refusal(monkeypatch, caplog):
    from websockets.asyncio import client as transport
    from hermes_cli.gateway_client import connect_gateway, GatewayClientError
    from hermes_cli.gateway_client_diagnostics import PREFIX

    secret = "private-ticket-and-response"
    monkeypatch.setenv("HERMES_TUI_GATEWAY_URL", f"ws://fixture.invalid/api/ws?token={secret}")
    failure = ConnectionRefusedError(61, secret)
    calls = []

    @asynccontextmanager
    async def dial(*args, **kwargs):
        calls.append(kwargs)
        raise failure
        yield

    monkeypatch.setattr(transport, "connect", dial)
    with caplog.at_level(logging.INFO), pytest.raises(GatewayClientError) as caught:
        async with connect_gateway():
            pytest.fail("refused connection entered body")
    assert str(caught.value) == "Gateway connection failed; no local fallback"
    assert caught.value.__cause__ is failure
    assert len(calls) == 1 and calls[0]["open_timeout"] == 10 and calls[0]["proxy"] is None
    rows = [json.loads(r.message.split(PREFIX, 1)[1]) for r in caplog.records if PREFIX in r.message]
    failure_row = next(r for r in rows if r["phase"] == "websocket_open_failed")
    assert failure_row["error_type"] == "ConnectionRefusedError" and failure_row["errno"] == 61
    phases = [row["phase"] for row in rows]
    assert "websocket_close_started" not in phases
    assert "websocket_close_exited" not in phases
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_terminal_receipt_precedes_reader_teardown_without_payload(caplog):
    from hermes_cli.gateway_client import GatewayClient
    from hermes_cli.gateway_client_diagnostics import PREFIX

    frame = {"method": "session.event", "params": {"type": "message.complete", "payload": {
        "text": "private-answer", "session_id": "private-session", "api_key": "private-key"}}}

    class Socket:
        async def __aiter__(self):
            yield json.dumps(frame)
            await asyncio.Event().wait()

    with caplog.at_level(logging.INFO):
        async with GatewayClient(Socket()) as client:
            assert await client.events.get() == frame
    phases = [json.loads(r.message.split(PREFIX, 1)[1])["phase"]
              for r in caplog.records if PREFIX in r.message]
    assert phases.index("message_complete_received") < phases.index("reader_close_started")
    assert phases.index("reader_close_started") < phases.index("reader_close_finished")
    assert "private" not in caplog.text


def test_diagnostics_cannot_replace_transport_result(monkeypatch):
    from hermes_cli import gateway_client_diagnostics as diagnostics

    def unavailable(*args, **kwargs):
        raise OSError("private-log-path")

    monkeypatch.setattr(diagnostics.logger, "info", unavailable)
    diagnostics.record("websocket_open_failed", error=TimeoutError("private-error"))


@pytest.mark.asyncio
async def test_completed_body_exposes_waiting_websocket_teardown(monkeypatch, caplog):
    from websockets.asyncio import client as transport
    from hermes_cli.gateway_client import connect_gateway
    from hermes_cli.gateway_client_diagnostics import PREFIX

    monkeypatch.setenv("HERMES_TUI_GATEWAY_URL", "ws://fixture.invalid/api/ws")
    closing, release = asyncio.Event(), asyncio.Event()

    class Socket:
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield "{}"

    @asynccontextmanager
    async def dial(*args, **kwargs):
        try:
            yield Socket()
        finally:
            closing.set()
            await release.wait()

    monkeypatch.setattr(transport, "connect", dial)

    async def turn():
        async with connect_gateway():
            return "completed fixture answer"

    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(turn())
        try:
            await closing.wait()
            phases = [json.loads(r.message.split(PREFIX, 1)[1])["phase"]
                      for r in caplog.records if PREFIX in r.message]
            assert "reader_close_finished" in phases and "websocket_close_started" in phases
            assert "websocket_close_exited" not in phases and not task.done()
        finally:
            release.set()
            answer = await task
    assert answer == "completed fixture answer"
    assert "websocket_close_exited" in caplog.text
