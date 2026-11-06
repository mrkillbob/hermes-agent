"""Invariant tests for the mcp-serve readiness artifact builders.

The ``mcp-serve-readiness`` workflow writes two JSON blobs so the durability
check can prove the control plane is live. The payload builders are pure; these
tests lock their schema/pin/allowlist contracts so a refactor cannot silently
change what CI audits.
"""

from __future__ import annotations

import json

from scripts.mcp_serve_readiness_artifacts import (
    IDENTITY_SCHEMA,
    NOTES,
    QUALIFY_PIN,
    QUALIFY_SCHEMA,
    identity_payload,
    qualify_payload,
)


def test_identity_payload_schema_and_profile():
    payload = identity_payload(
        "test",
        task_id="t1",
        session_id=None,
        turn_id="turn-7",
        api_request_id="req-9",
    )
    assert payload["schema"] == IDENTITY_SCHEMA
    assert payload["profile"] == "test"
    assert payload["task_id"] == "t1"
    assert payload["session_id"] is None
    assert payload["turn_id"] == "turn-7"
    assert payload["api_request_id"] == "req-9"


def test_identity_payload_defaults_empty_profile_to_default():
    payload = identity_payload(
        "", task_id=None, session_id=None, turn_id=None, api_request_id=None
    )
    assert payload["profile"] == "default"


def test_qualify_payload_sorts_dedups_and_pins():
    payload = qualify_payload(["web_search", "skills_list", "web_search", "vision_analyze"])
    assert payload["schema"] == QUALIFY_SCHEMA
    assert payload["pin"] == QUALIFY_PIN
    assert payload["readonly_tools"] == ["skills_list", "vision_analyze", "web_search"]
    assert list(payload["notes"]) == list(NOTES)


def test_qualify_payload_persistable_to_json():
    # CI uploads both blobs; they must round-trip through json.dumps/loads.
    blobs = [
        identity_payload("a2a-test", task_id="t", session_id="s", turn_id=None, api_request_id=None),
        qualify_payload(["web_search"]),
    ]
    for blob in blobs:
        round_tripped = json.loads(json.dumps(blob))
        assert round_tripped == blob