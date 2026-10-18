"""Payload-free gateway viewer phases, shared with bounded acceptance diagnostics."""
from __future__ import annotations

import json
import logging
import math
import time
from contextlib import AsyncExitStack, asynccontextmanager

logger = logging.getLogger(__name__)
PREFIX = "gateway_client_event="
PHASES = frozenset({
    "runtime_ensure_started", "runtime_ready", "runtime_unavailable", "ticket_started", "ticket_ready",
    "websocket_open_started", "websocket_open_finished", "websocket_open_failed",
    "websocket_close_started", "websocket_close_exited",
    "reader_started", "reader_failed", "reader_stopped", "reader_close_started", "reader_close_finished",
    "message_complete_received", "prompt_receipt_started", "prompt_receipt_finished",
})
_ERROR_TYPES = frozenset({"OSError", "TimeoutError", "ConnectionError", "ConnectionRefusedError",
                          "ConnectionResetError", "ConnectionAbortedError", "BrokenPipeError"})


class _ConnectionExitStack(AsyncExitStack):
    """Track whether a WebSocket context was actually entered before reporting its close."""

    def __init__(self) -> None:
        super().__init__()
        self.connection_opened = False

    async def enter_async_context(self, cm):
        value = await super().enter_async_context(cm)
        self.connection_opened = True
        return value


def safe_event(value: object) -> dict:
    """Rebuild an event from fixed labels and numeric facts; never copy arbitrary log data."""
    if not isinstance(value, dict) or value.get("phase") not in PHASES:
        return {}
    out = {"phase": value["phase"]}
    seconds = value.get("monotonic_s")
    if type(seconds) in (int, float) and 0 <= seconds < 1e12 and math.isfinite(seconds):
        out["monotonic_s"] = round(seconds, 3)
    if value.get("error_type") in _ERROR_TYPES | {"other"}:
        out["error_type"] = value["error_type"]
    for key in ("errno", "winerror"):
        number = value.get(key)
        if type(number) is int and -(2**31) <= number < 2**31:
            out[key] = number
    return out


def record(phase: str, *, error: BaseException | None = None) -> None:
    """Logging is observational: a broken handler must not alter transport or teardown."""
    try:
        value = {"phase": phase, "monotonic_s": time.monotonic()}
        if error is not None:
            name = type(error).__name__
            value.update(error_type=name if name in _ERROR_TYPES else "other",
                         errno=getattr(error, "errno", None), winerror=getattr(error, "winerror", None))
        event = safe_event(value)
        if event:
            logger.info("%s%s", PREFIX, json.dumps(event, sort_keys=True))
    except Exception:  # health: allow BLE001 -- A failed diagnostic handler cannot re-log itself or replace a transport outcome.
        return  # Diagnostic handlers must never replace the original failure or outcome.


@asynccontextmanager
async def connection_stack():
    """Keep the normal exit stack's semantics while locating a blocked socket teardown."""
    stack = _ConnectionExitStack()
    try:
        async with stack:
            try:
                yield stack
            finally:
                if stack.connection_opened:
                    record("websocket_close_started")
    finally:
        if stack.connection_opened:
            record("websocket_close_exited")
