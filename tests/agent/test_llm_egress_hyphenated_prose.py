"""Contract: hyphenated prose is grammar, not a base64 encoding channel."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.llm_egress_firewall import (
    LLMEgressFirewall,
    LiteralSegment,
    SanitizedSegment,
    TypedOutboundRequest,
    static_literal_sha256,
    _canonical_base64_candidate,
)


def _route():
    return SimpleNamespace(
        provider="custom",
        model="test-model",
        base_url="https://llm.example.test/v1",
        api_mode=None,
    )


def _sanitized_request(text: str) -> TypedOutboundRequest:
    return TypedOutboundRequest(
        payload={
            "messages": [
                {"role": LiteralSegment("user"), "content": SanitizedSegment(text)}
            ]
        },
        session_id="session-1",
        turn_id="turn-1",
        request_id="req-1",
        policy_digest="policy-1",
    )


def _firewall(tmp_path: Path) -> LLMEgressFirewall:
    allowed = {
        "messages",
        "role",
        "content",
        "user",
        "session_id",
        "session-1",
        "turn_id",
        "turn-1",
        "request_id",
        "req-1",
        "policy_digest",
        "policy-1",
    }
    return LLMEgressFirewall(
        tmp_path,
        static_literal_hashes_by_policy={
            "policy-1": frozenset(static_literal_sha256(value) for value in allowed)
        },
    )


@pytest.mark.parametrize(
    "text",
    [
        "A no-op or unavailable-evidence outcome still closes the card.",
        "claim->spawn->execute- then report the repository-changing result.",
    ],
)
def test_hyphenated_prose_compounds_are_not_base64_payloads(tmp_path, text):
    """live incident, 2026-08-31: the kanban dispatcher's own worker prompt
    template contains hyphenated lowercase compounds ("unavailable-evidence",
    "repository-changing") and an arrow fragment ("execute-") that sit in the
    base64url alphabet and round-trip as unpadded encodings by coincidence, so
    every kanban worker was eligible for a false-positive egress block."""
    decision = _firewall(tmp_path).preflight(_sanitized_request(text), _route())
    assert "base64_payload" not in decision.reason_codes


def test_hyphenated_word_exemption_stays_fail_closed_for_payload_shapes():
    for prose in ("execute-", "unavailable-evidence", "repository-changing"):
        assert not _canonical_base64_candidate(prose)
    # Mixed case, digits, or underscores keep encoding-shaped candidates
    # eligible, and a genuine base64 of secret text is still flagged.
    real = base64.b64encode(b"secret source code payload").decode().rstrip("=")
    assert _canonical_base64_candidate(real)
    assert _canonical_base64_candidate("c2VjcmV0")
