#!/usr/bin/env python3
"""Emit the mcp-serve readiness artifacts (identity handshake + qualification).

The ``mcp-serve-readiness`` workflow dumps two small JSON blobs into
``artifacts/`` so the durability check can prove the A2A/MCP control plane is
present before tests run:

* ``artifacts/identity.json``   -- schema ``hermes.identity.v1``
* ``artifacts/qualify.json``    -- schema ``hermes.mcp.serve.qualify.v1``

The payload builders are pure so the invariant tests can lock them without
importing the MCP server or the profile store.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

IDENTITY_SCHEMA = "hermes.identity.v1"
QUALIFY_SCHEMA = "hermes.mcp.serve.qualify.v1"
QUALIFY_PIN = "1.0.0"
NOTES = (
    "Approvals disabled; ESTOP preserved",
    "Profile-scoped allowlist active when profile in {test, a2a-test} or HERMES_MCP_READONLY=1",
)


def identity_payload(profile: str, *, task_id: str | None, session_id: str | None,
                     turn_id: str | None, api_request_id: str | None) -> dict[str, object]:
    """Pure builder for the identity handshake blob."""
    return {
        "schema": IDENTITY_SCHEMA,
        "profile": profile or "default",
        "task_id": task_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "api_request_id": api_request_id,
    }


def qualify_payload(tools: Iterable[str], *, notes: Iterable[str] = NOTES) -> dict[str, object]:
    """Pure builder for the qualification blob."""
    return {
        "schema": QUALIFY_SCHEMA,
        "pin": QUALIFY_PIN,
        "readonly_tools": sorted(set(tools)),
        "notes": list(notes),
    }


def _active_profile() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name
        return str(get_active_profile_name() or "default")
    except Exception:  # health: allow BLE001 -- readiness blob must never fail on an unreadable profile; default the id and continue
        return "default"


def _exposed_tools() -> tuple[str, ...]:
    from agent.transports.hermes_tools_mcp_server import _profile_scoped_exposed_tools
    return _profile_scoped_exposed_tools()


def main() -> int:
    os.environ.setdefault("HERMES_MCP_READONLY", "1")
    profile = _active_profile()
    identity = identity_payload(
        profile,
        task_id=os.getenv("HERMES_TASK_ID"),
        session_id=os.getenv("HERMES_SESSION_ID"),
        turn_id=os.getenv("HERMES_TURN_ID"),
        api_request_id=os.getenv("HERMES_API_REQUEST_ID"),
    )
    qual = qualify_payload(_exposed_tools())

    artifacts = Path("artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
    (artifacts / "qualify.json").write_text(json.dumps(qual, indent=2), encoding="utf-8")
    print("Wrote artifacts/identity.json and artifacts/qualify.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())