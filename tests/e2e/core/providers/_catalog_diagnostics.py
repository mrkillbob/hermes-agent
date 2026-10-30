"""Bounded lifecycle facts for synthetic catalog homes; raw logs never leave the fixture."""
from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.gateway_client_diagnostics import PREFIX, safe_event

_STATES = frozenset({"starting", "running", "draining", "stopped", "failed", "blocked"})


def _tail(path: Path, limit: int) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - limit))
            return stream.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def snapshot(home: Path) -> dict:
    events = []
    for name in ("agent.log", "gateway.log"):
        for line in _tail(home / ".hermes" / "logs" / name, 65536).splitlines():
            _before, marker, raw = line.partition(PREFIX)
            if not marker or len(raw) > 1024:
                continue
            try:
                event = safe_event(json.loads(raw))
            except (ValueError, TypeError):
                continue
            if event:
                events.append(event)
    state = "missing"
    raw = _tail(home / ".hermes" / "gateway_state.json", 8192)
    if raw:
        state = "invalid"
        try:
            value = json.loads(raw)
            if isinstance(value, dict) and value.get("gateway_state") in _STATES:
                state = value["gateway_state"]
        except (ValueError, TypeError):
            pass
    return {"gateway_state": state, "events": events[-12:]}
