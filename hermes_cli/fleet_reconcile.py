"""Reconcile a fleet client's exact gateway admission without resubmission."""
from __future__ import annotations

import asyncio
import time

from websockets.exceptions import WebSocketException

from hermes_cli.gateway_client import GatewayClientError
from hermes_cli.stream_json import StreamJsonEmitter

UNKNOWN_EXIT = 75
CONTROL_WAIT = 10.0


def _emit_receipt(emitter, session_id, admission_id, receipt):
    status = receipt.get("status")
    status = status if status in {"queued", "started", "terminal", "unknown"} else "unknown"
    settled = status == "terminal"
    outcome = receipt.get("outcome")
    result = dict(receipt.get("result") or {}) if settled else {}
    result.update(admission_id=admission_id, admission_status=status, settled=settled, outcome=outcome,
                  needs_input=bool(receipt.get("needs_input")))
    if not settled:
        return emitter.emit_result(result, session_id=session_id, exit_code=UNKNOWN_EXIT)
    from hermes_cli.turn_exit import turn_exit_code
    result.update(failed=bool(result.get("failed")) or outcome not in ("completed", "cancelled"),
                  interrupted=bool(result.get("interrupted")) or outcome in ("cancelled", "interrupted"))
    return emitter.emit_result(result, session_id=session_id,
                               exit_code=turn_exit_code(result, kanban_worker=False))


async def _cancel_exact(client, session_id, admission_id, receipt):
    if receipt.get("status") == "queued":
        # The owner atomically cancels queued work or interrupts only the claim
        # that won the race against cancellation.
        await client.rpc("prompt.cancel", session_id=session_id, admission_id=admission_id)
    elif receipt.get("status") == "started":
        snapshot = await client.rpc("session.resume", session_id=session_id)
        generation = receipt.get("execution_generation")
        own = next((row for row in snapshot.get("pending", [])
                    if row.get("admission_id") == admission_id and row.get("status") == "started"), None)
        if (type(generation) is int and own is not None
                and own.get("execution_generation") == generation
                and snapshot.get("execution_generation") == generation):
            # The server rejects a stale generation if this turn settles between
            # the snapshot and interrupt. Never target the successor turn.
            await client.rpc("session.interrupt", session_id=session_id, execution_generation=generation)
            return generation


async def _cancel_and_recheck(client, session_id, admission_id, receipt, control_wait):
    # This budget limits the control exchange, never the task's model/tool work.
    async with asyncio.timeout(max(control_wait, 0.001)):
        deadline = time.monotonic() + control_wait
        interrupted = set()
        cancelled_queue = False
        while True:
            status = receipt.get("status")
            generation = receipt.get("execution_generation")
            if ((status == "queued" and not cancelled_queue)
                    or (status == "started" and generation not in interrupted)):
                stopped = await _cancel_exact(client, session_id, admission_id, receipt)
                cancelled_queue = cancelled_queue or status == "queued"
                if stopped is not None:
                    interrupted.add(stopped)
            receipt = await client.rpc("prompt.receipt", session_id=session_id,
                                       admission_id=admission_id, include_result=True)
            if receipt.get("admission_id") != admission_id:
                raise GatewayClientError("receipt_identity_mismatch")
            if receipt.get("status") in {"terminal", "unknown"} or time.monotonic() >= deadline:
                return receipt
            if status == "queued" and receipt.get("status") == "started":
                continue  # A claim won before the owner's cancel read; stop its exact generation now.
            await asyncio.sleep(min(0.2, max(0, deadline - time.monotonic())))


async def _needs_input(client, session_id, admission_id, receipt):
    snapshot = await client.rpc("session.resume", session_id=session_id)
    generation = receipt.get("execution_generation")
    own = any(row.get("admission_id") == admission_id and row.get("status") == "started"
              and row.get("execution_generation") == generation for row in snapshot.get("pending", []))
    return (type(generation) is int and own and snapshot.get("execution_generation") == generation
            and any(prompt.get("execution_generation") == generation
                    and prompt.get("kind") in {"approval", "clarify"} for prompt in snapshot.get("prompts", [])))


async def reconcile_admission(client, session_id, admission_id, *, cancel=False, control_wait=CONTROL_WAIT,
                              emitter=None):
    """Read only the named receipt; cancellation waits only for control settlement."""
    emitter = emitter or StreamJsonEmitter(session_id=session_id)
    receipt = {}
    try:
        receipt = await client.rpc("prompt.receipt", session_id=session_id,
                                   admission_id=admission_id, include_result=True)
        if receipt.get("admission_id") != admission_id:
            raise GatewayClientError("receipt_identity_mismatch")
        if cancel and receipt.get("status") in {"queued", "started"}:
            receipt = await _cancel_and_recheck(client, session_id, admission_id, receipt, control_wait)
        elif receipt.get("status") == "started":
            receipt = {**receipt, "needs_input": await _needs_input(client, session_id, admission_id, receipt)}
    except (GatewayClientError, OSError, TimeoutError, WebSocketException):
        # A failed control or read is never evidence of task failure or cancellation.
        receipt = {}
    return _emit_receipt(emitter, session_id, admission_id, receipt)


async def run_reconcile(session_id, admission_id, *, cancel=False):
    from hermes_constants import get_hermes_home
    from hermes_cli.gateway_runtime import discover_gateway_endpoint
    from hermes_cli.gateway_client import connect_gateway
    emitter = StreamJsonEmitter(session_id=session_id)
    discovery = await asyncio.to_thread(discover_gateway_endpoint, get_hermes_home())
    if discovery.state != "ready" or discovery.endpoint is None:
        return _emit_receipt(emitter, session_id, admission_id, {})
    try:
        async with connect_gateway(endpoint=discovery.endpoint) as client:
            return await reconcile_admission(client, session_id, admission_id, cancel=cancel, emitter=emitter)
    except (GatewayClientError, OSError, TimeoutError, WebSocketException):
        return _emit_receipt(emitter, session_id, admission_id, {})
