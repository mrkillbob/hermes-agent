"""Focused contract tests for the source-bound LLM egress firewall."""

from __future__ import annotations

import base64
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.llm_egress_firewall import (
    AuthorizedEgress,
    DestinationClass,
    EgressBlocked,
    EgressDecision,
    LLMEgressFirewall,
    LiteralSegment,
    OutboundText,
    SanitizedSegment,
    SourceGrant,
    SourceBoundSegment,
    TypedOutboundRequest,
    classify_destination,
    source_grant_digest,
    static_literal_sha256,
)


def _route(**overrides):
    values = {"provider": "custom", "model": "test-model", "base_url": "https://llm.example.test/v1", "api_mode": None}
    values.update(overrides)
    return SimpleNamespace(**values)


def _source_grant(path: Path, *, request_id: str = "req-1", start: int = 1, end: int = 1):
    content = path.read_bytes().splitlines(keepends=True)[start - 1 : end]
    payload = b"".join(content)
    return SourceGrant(
        canonical_path=path.resolve(),
        display_path="src/private.py",
        line_start=start,
        line_end=end,
        content_sha256=sha256(payload).hexdigest(),
        byte_count=len(payload),
        session_id="session-1",
        turn_id="turn-1",
        request_id=request_id,
        policy_digest="policy-1",
    )


def _request(payload: str = "ordinary prompt"):
    return {
        "messages": [{"role": "user", "content": payload}],
        "session_id": "session-1",
        "turn_id": "turn-1",
        "request_id": "req-1",
        "policy_digest": "policy-1",
    }


def _typed_request(request, *, source_grant: SourceGrant | None = None):
    def typed(value):
        if isinstance(value, str):
            return LiteralSegment(value)
        if isinstance(value, list):
            return [typed(item) for item in value]
        if isinstance(value, dict):
            return {key: typed(item) for key, item in value.items()}
        return value

    # Request identity is control-plane metadata for grant binding and receipts,
    # not provider payload. Keep only the logical provider request here.
    payload = typed({"messages": request["messages"]})
    if source_grant is not None:
        payload["messages"][0]["content"] = OutboundText(
            (SourceBoundSegment(source_grant_digest(source_grant)),)
        )
    return TypedOutboundRequest(
        payload=payload,
        session_id=request["session_id"],
        turn_id=request["turn_id"],
        request_id=request["request_id"],
        policy_digest=request["policy_digest"],
    )


_COMMON_STATIC_LITERALS = {
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


def firewall(tmp_path: Path, *, static_literals=(), **kwargs) -> LLMEgressFirewall:
    allowed = _COMMON_STATIC_LITERALS | set(static_literals)
    return LLMEgressFirewall(
        tmp_path,
        static_literal_hashes_by_policy={
            "policy-1": frozenset(static_literal_sha256(value) for value in allowed)
        },
        **kwargs,
    )


def test_lan_and_unknown_are_remote_while_numeric_loopback_is_loopback():
    assert classify_destination("ollama", "http://127.0.0.1:11434", None).value == "loopback"
    assert classify_destination("custom", "http://192.168.1.9:8000", None).value == "remote"
    assert classify_destination("custom", None, None).value == "unknown"


def test_destination_classification_does_not_trust_dns_or_provider_name():
    assert classify_destination("ollama", "http://localhost:11434", None) in {
        DestinationClass.REMOTE,
        DestinationClass.UNKNOWN,
    }
    assert classify_destination("ollama", "http://ollama:11434", None) == DestinationClass.REMOTE
    assert classify_destination("custom", "not-a-url", None) == DestinationClass.UNKNOWN
    assert classify_destination("custom", "http://[::1]:8000", None) == DestinationClass.LOOPBACK
    assert classify_destination("custom", "http://100.64.0.1:8000", None) == DestinationClass.REMOTE


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:99999",
        "http://127.0.0.1:not-a-port",
        "http://[::1]:99999",
    ],
)
def test_malformed_loopback_ports_are_unknown(url):
    assert classify_destination("custom", url, None) == DestinationClass.UNKNOWN


def test_explicit_in_process_api_mode_is_local_process():
    assert classify_destination("custom", None, "local_process") == DestinationClass.LOCAL_PROCESS
    assert classify_destination("custom", None, "in_process") == DestinationClass.LOCAL_PROCESS


def test_source_grant_is_immutable_and_has_the_required_binding_fields(tmp_path):
    path = tmp_path / "private.py"
    path.write_text("private source\n", encoding="utf-8")
    grant = _source_grant(path)
    with pytest.raises(FrozenInstanceError):
        grant.request_id = "forged"  # type: ignore[misc]
    assert grant.canonical_path == path.resolve()
    assert grant.line_start == 1 and grant.line_end == 1
    assert grant.byte_count == len(b"private source\n")


def test_loopback_request_is_allowed_without_source_grant(tmp_path):
    decision = firewall(tmp_path).preflight(_request("local prompt"), _route(base_url="http://127.0.0.1:11434"))
    assert decision.allowed is True
    assert decision.destination_class == DestinationClass.LOOPBACK


def test_remote_request_requires_an_exact_source_grant(tmp_path):
    path = tmp_path / "private.py"
    path.write_text("x=1\npublic source\n", encoding="utf-8")
    grant = _source_grant(path)
    request = _request("private source\n")
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(_typed_request(request), _route(), grants=())
    assert "untrusted_provenance" in exc_info.value.decision.reason_codes

    decision = firewall(tmp_path).preflight(
        _typed_request(request, source_grant=grant),
        _route(),
        grants=(grant,),
    )
    assert isinstance(decision, EgressDecision)
    assert decision.allowed is True
    assert decision.payload_sha256


def test_unrelated_grant_and_incomplete_typed_source_request_are_denied(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("first source\n", encoding="utf-8")
    second.write_text("second source\n", encoding="utf-8")
    first_grant = _source_grant(first)
    second_grant = _source_grant(second)
    request = _request("first source\n")
    with pytest.raises(EgressBlocked) as unrelated_exc:
        firewall(tmp_path).preflight(
            _typed_request(request, source_grant=second_grant),
            _route(),
            grants=(first_grant,),
        )
    assert "source_segment_grant_mismatch" in unrelated_exc.value.decision.reason_codes

    with pytest.raises(EgressBlocked) as incomplete_exc:
        firewall(tmp_path).preflight(
            _typed_request(request, source_grant=first_grant),
            _route(),
            grants=(first_grant, second_grant),
        )
    assert "source_grant_unbound" in incomplete_exc.value.decision.reason_codes


def test_remote_raw_request_is_never_authorized_even_with_a_valid_grant(tmp_path):
    path = tmp_path / "private.py"
    path.write_text("private source\n", encoding="utf-8")
    grant = _source_grant(path)
    raw_request = _request("private source\n")
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(raw_request, _route(), grants=(grant,))
    assert "typed_request_required" in exc_info.value.decision.reason_codes


def test_source_bytes_cannot_be_smuggled_as_a_sanitized_literal(tmp_path):
    path = tmp_path / "private.py"
    path.write_text("private source\n", encoding="utf-8")
    grant = _source_grant(path)
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(
            _typed_request(_request("private source\n")),
            _route(),
            grants=(grant,),
        )
    assert "source_bytes_in_literal" in exc_info.value.decision.reason_codes
    assert "source_grant_unbound" in exc_info.value.decision.reason_codes


def test_only_exact_policy_allowlisted_static_wrappers_accompany_source(tmp_path):
    path = tmp_path / "private.py"
    path.write_text("x=1\n", encoding="utf-8")
    grant = _source_grant(path)
    typed_request = _typed_request(_request("ignored"), source_grant=grant)
    typed_request.payload["messages"][0]["content"] = OutboundText(
        (
            LiteralSegment("<source>\n"),
            SourceBoundSegment(source_grant_digest(grant)),
            LiteralSegment("</source>"),
        )
    )
    gate = firewall(tmp_path, static_literals={"<source>\n", "</source>"})
    authorization = gate.authorize(typed_request, _route(), grants=(grant,))
    assert json.loads(authorization.payload_bytes)["messages"][0]["content"] == (
        "<source>\nx=1\n</source>"
    )

    typed_request.payload["messages"][0]["content"] = OutboundText(
        (
            LiteralSegment("<changed>\n"),
            SourceBoundSegment(source_grant_digest(grant)),
            LiteralSegment("</source>"),
        )
    )
    with pytest.raises(EgressBlocked) as changed_exc:
        gate.preflight(typed_request, _route(), grants=(grant,))
    assert "static_literal_not_allowed" in changed_exc.value.decision.reason_codes

    typed_request.payload["messages"][0]["content"] = OutboundText(
        (
            SanitizedSegment("<source>\n"),
            SourceBoundSegment(source_grant_digest(grant)),
            LiteralSegment("</source>"),
        )
    )
    with pytest.raises(EgressBlocked) as sanitized_exc:
        gate.preflight(typed_request, _route(), grants=(grant,))
    assert "sanitized_segment_forbidden" in sanitized_exc.value.decision.reason_codes


@pytest.mark.parametrize(
    ("wrapper", "reason"),
    [
        ("token=super-secret-value\n", "secret_detected"),
        ("AQID\n", "base64_payload"),
    ],
)
def test_allowlisted_static_wrapper_still_passes_final_send_scan(tmp_path, wrapper, reason):
    path = tmp_path / "private.py"
    path.write_text("x=1\n", encoding="utf-8")
    grant = _source_grant(path)
    typed_request = _typed_request(_request("ignored"), source_grant=grant)
    typed_request.payload["messages"][0]["content"] = OutboundText(
        (
            LiteralSegment(wrapper),
            SourceBoundSegment(source_grant_digest(grant)),
        )
    )
    gate = firewall(tmp_path, static_literals={wrapper})
    with pytest.raises(EgressBlocked) as exc_info:
        gate.authorize(typed_request, _route(), grants=(grant,))
    assert reason in exc_info.value.decision.reason_codes
    assert not exc_info.value.decision.allowed


def test_second_source_cannot_be_mislabeled_as_allowlisted_literal(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("x=1\n", encoding="utf-8")
    second.write_text("y=2\n", encoding="utf-8")
    first_grant = _source_grant(first)
    second_grant = _source_grant(second)
    typed_request = _typed_request(_request("ignored"), source_grant=first_grant)
    typed_request.payload["messages"][0]["content"] = OutboundText(
        (
            SourceBoundSegment(source_grant_digest(first_grant)),
            LiteralSegment("y=2\n"),
        )
    )
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(
            typed_request,
            _route(),
            grants=(first_grant, second_grant),
        )
    assert "static_literal_not_allowed" in exc_info.value.decision.reason_codes
    assert "source_bytes_in_literal" in exc_info.value.decision.reason_codes
    assert "source_grant_unbound" in exc_info.value.decision.reason_codes


def test_source_bytes_are_constructed_from_grant_and_authorized_bytes_reject_tampering(tmp_path):
    path = tmp_path / "private.py"
    path.write_text("x=1\n", encoding="utf-8")
    grant = _source_grant(path)
    raw_request = _request("ignored raw bytes")
    typed_request = _typed_request(raw_request, source_grant=grant)
    authorization = firewall(tmp_path).authorize(typed_request, _route(), grants=(grant,))
    authorized_payload = json.loads(authorization.payload_bytes)
    assert authorized_payload["messages"][0]["content"] == "x=1\n"
    assert "ignored raw bytes" not in authorization.payload_bytes.decode("utf-8")

    typed_request.payload["messages"][0]["content"] = LiteralSegment("tampered")
    assert authorization.payload_bytes == authorization.verify_payload(authorization.payload_bytes)
    with pytest.raises(EgressBlocked) as exc_info:
        authorization.verify_payload(authorization.payload_bytes.replace(b"x=1", b"x=2"))
    assert "payload_digest_mismatch" in exc_info.value.decision.reason_codes


def test_typed_payload_identity_cannot_diverge_from_grant_binding_identity(tmp_path):
    path = tmp_path / "private.py"
    path.write_text("verified source\n", encoding="utf-8")
    grant = _source_grant(path)
    typed_request = _typed_request(_request("ignored"), source_grant=grant)
    typed_request.payload["request_id"] = LiteralSegment("different-request")
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(typed_request, _route(), grants=(grant,))
    assert "request_identity_mismatch" in exc_info.value.decision.reason_codes


def test_modified_source_and_adjacent_slice_are_denied(tmp_path):
    path = tmp_path / "private.py"
    path.write_text("private source\nneighbor\n", encoding="utf-8")
    grant = _source_grant(path)
    request = _request("private source\n")
    path.write_text("changed source\nneighbor\n", encoding="utf-8")
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(
            _typed_request(request, source_grant=grant),
            _route(),
            grants=(grant,),
        )
    assert "source_hash_mismatch" in exc_info.value.decision.reason_codes


def test_sensitive_path_and_forged_grant_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("API_KEY=sk-test-secret\n", encoding="utf-8")
    grant = _source_grant(path)
    sensitive_request = _request("API_KEY=sk-test-secret\n")
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(
            _typed_request(sensitive_request, source_grant=grant),
            _route(),
            grants=(grant,),
        )
    assert "sensitive_path" in exc_info.value.decision.reason_codes

    missing = tmp_path / "missing.py"
    forged = SourceGrant(
        canonical_path=missing,
        display_path="src/private.py",
        line_start=1,
        line_end=1,
        content_sha256=sha256(b"private source\n").hexdigest(),
        byte_count=len(b"private source\n"),
        session_id="session-1",
        turn_id="turn-1",
        request_id="req-1",
        policy_digest="policy-1",
    )
    missing_request = _request("private source\n")
    with pytest.raises(EgressBlocked) as missing_exc:
        firewall(tmp_path).preflight(
            _typed_request(missing_request, source_grant=forged),
            _route(),
            grants=(forged,),
        )
    assert "source_unavailable" in missing_exc.value.decision.reason_codes


def test_forced_secret_redaction_rejects_instead_of_rewriting(tmp_path):
    path = tmp_path / "secret.txt"
    path.write_text("token=super-secret-value\n", encoding="utf-8")
    grant = _source_grant(path)
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(
            _typed_request(_request("ignored"), source_grant=grant),
            _route(),
            grants=(grant,),
        )
    assert "secret_detected" in exc_info.value.decision.reason_codes


@pytest.mark.parametrize(
    "payload",
    [
        base64.b64encode(b"sk-proj-abc123def456ghi789jkl012").decode("ascii"),
        base64.urlsafe_b64encode(b"harmless text that is still encoded").decode("ascii"),
        base64.b64encode(b"sk-x").decode("ascii"),
        base64.b64encode(b"sk-short").decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(b"\xfb\xffshort-secret").decode("ascii").rstrip("="),
        base64.b64encode(b"\x01\x02\x03").decode("ascii"),
        base64.urlsafe_b64encode(b"\xfb\xff\x00").decode("ascii").rstrip("="),
    ],
)
def test_canonical_base64_payloads_are_rejected_even_when_decoded_content_is_benign(
    tmp_path,
    payload,
):
    path = tmp_path / "encoded.txt"
    path.write_text(payload + "\n", encoding="utf-8")
    grant = _source_grant(path)
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(
            _typed_request(_request("ignored"), source_grant=grant),
            _route(),
            grants=(grant,),
        )
    assert "base64_payload" in exc_info.value.decision.reason_codes


def test_ordinary_short_base64_alphabet_words_are_not_false_positives(tmp_path):
    decision = firewall(tmp_path).preflight(
        _request("ordinary short words remain usable"),
        _route(base_url="http://127.0.0.1:11434"),
    )
    assert "base64_payload" not in decision.reason_codes


def test_scanner_error_fails_closed(monkeypatch, tmp_path):
    def explode(*args, **kwargs):
        raise RuntimeError("scanner unavailable")

    monkeypatch.setattr("agent.llm_egress_firewall.redact_sensitive_text", explode)
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(
            _typed_request(_request()),
            _route(),
            grants=(object(),),
        )
    assert "redaction_failed" in exc_info.value.decision.reason_codes


def test_byte_and_token_caps_fail_closed(tmp_path):
    with pytest.raises(EgressBlocked) as bytes_exc:
        firewall(tmp_path, max_serialized_bytes=32).preflight(
            _typed_request(_request()),
            _route(),
            grants=(object(),),
        )
    assert "serialized_bytes_exceeded" in bytes_exc.value.decision.reason_codes

    with pytest.raises(EgressBlocked) as token_exc:
        firewall(tmp_path, max_serialized_bytes=100_000, max_conservative_tokens=2).preflight(
            _typed_request(_request("ordinary prompt")),
            _route(),
            grants=(object(),),
        )
    assert "token_cap_exceeded" in token_exc.value.decision.reason_codes


def test_byte_and_token_caps_allow_the_exact_boundary(tmp_path):
    request = _request("boundary prompt")
    serialized = json.dumps(
        request,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    token_count = (len(serialized) + 2) // 3
    decision = firewall(
        tmp_path,
        max_serialized_bytes=len(serialized),
        max_conservative_tokens=token_count,
    ).preflight(request, _route(base_url="http://127.0.0.1:11434"))
    assert decision.allowed is True


def test_allow_receipt_contains_hashes_and_counts_but_no_payload(tmp_path):
    path = tmp_path / "private.py"
    path.write_text("x=1\n", encoding="utf-8")
    grant = _source_grant(path)
    request = _request("private source\n")
    decision = firewall(tmp_path).preflight(
        _typed_request(request, source_grant=grant),
        _route(),
        grants=(grant,),
    )
    receipt = json.loads((tmp_path / "llm-egress-receipts.jsonl").read_text().splitlines()[0])
    assert receipt["payload_sha256"] == decision.payload_sha256
    assert receipt["serialized_bytes"] == decision.serialized_bytes
    assert receipt["estimated_tokens"] == decision.estimated_tokens
    assert receipt["source_grants"][0]["content_sha256"] == grant.content_sha256
    assert "private source" not in json.dumps(receipt)
    assert "canonical_path" not in receipt["source_grants"][0]


def test_receipt_hashes_unsafe_identity_and_route_labels(tmp_path):
    secret_request_id = "sk-proj-abc123def456ghi789jkl012"
    private_model_label = str(Path.home() / "private-model")
    with pytest.raises(EgressBlocked):
        firewall(tmp_path).preflight(
            _typed_request({**_request(), "request_id": secret_request_id}),
            _route(model=private_model_label),
            grants=(object(),),
        )
    receipt_text = (tmp_path / "llm-egress-receipts.jsonl").read_text()
    assert secret_request_id not in receipt_text
    assert str(Path.home()) not in receipt_text


def test_receipt_is_owner_only_and_rejects_symlink_ledger(tmp_path):
    path = tmp_path / "private.py"
    path.write_text("x=1\n", encoding="utf-8")
    grant = _source_grant(path)
    request = _request("private source\n")
    ledger = tmp_path / "llm-egress-receipts.jsonl"
    firewall(tmp_path).preflight(
        _typed_request(request, source_grant=grant),
        _route(),
        grants=(grant,),
    )
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600

    ledger.unlink()
    target = tmp_path / "outside.jsonl"
    target.write_text("", encoding="utf-8")
    ledger.symlink_to(target)
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(
            _typed_request(request, source_grant=grant),
            _route(),
            grants=(grant,),
        )
    assert "receipt_unavailable" in exc_info.value.decision.reason_codes


def test_concurrent_receipt_appends_remain_complete_json_lines(tmp_path):
    gate = firewall(tmp_path)

    def append(index: int) -> None:
        gate.preflight(
            {**_request(f"local prompt {index}"), "request_id": f"req-{index}"},
            _route(base_url="http://127.0.0.1:11434"),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        tuple(executor.map(append, range(32)))

    lines = (tmp_path / "llm-egress-receipts.jsonl").read_text().splitlines()
    receipts = [json.loads(line) for line in lines]
    assert len(receipts) == 32
    assert {receipt["request_id"] for receipt in receipts} == {
        f"req-{index}" for index in range(32)
    }


def test_unknown_destination_fails_closed_even_with_source_grant(tmp_path):
    path = tmp_path / "private.py"
    path.write_text("private source\n", encoding="utf-8")
    grant = _source_grant(path)
    with pytest.raises(EgressBlocked) as exc_info:
        firewall(tmp_path).preflight(_request("private source"), _route(base_url=None), grants=(grant,))
    assert exc_info.value.decision.destination_class == DestinationClass.UNKNOWN
    assert "unknown_destination" in exc_info.value.decision.reason_codes
