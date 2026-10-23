"""Private refusal diagnostics bind a block to one live admitted request."""

import json
import threading
from contextvars import copy_context
from contextlib import nullcontext
from types import SimpleNamespace

from agent import llm_egress_request_diagnostics as diagnostics


def _scope(**kwargs):
    authority = SimpleNamespace(profile_id="profile")
    source = object()
    logical_session_id = kwargs.setdefault("logical_session_id", "session")
    generation = kwargs.setdefault("execution_generation", 1)
    scope = diagnostics.DiagnosticScope(
        authority=authority, profile_id="profile", source=source, **kwargs
    )
    scope.verify_claimed_turn(
        authority, logical_session_id, generation, source, logical_session_id
    )
    return scope


def test_receipt_uses_only_validated_admission_and_fixed_request_fields():
    scope = _scope(
        admission_id="a" * 32, authority_epoch=4, execution_generation=7,
        admission_sequence=9,
    )
    request = object()
    agent = object()
    scope.bind_turn_agent(agent, "session", scope.source)
    attempt = scope.bind_request(
        request, agent=agent, session_id="session", turn_id="turn",
        request_id="request", policy_digest="digest",
    )

    receipt = diagnostics.receipt_fields(scope, attempt, request=request)

    assert receipt == {
        "version": 1,
        "admission_ref": "a" * 32,
        "authority_epoch": 4,
        "execution_generation": 7,
        "admission_sequence": 9,
        "authorization_ordinal": 1,
        "admission_binding": "bound",
        "request_binding": "exact",
        "lineage_status": "unavailable",
        "findings": [],
        "findings_overflow": False,
        "lineage_overflow": False,
    }
    assert "secret" not in json.dumps(receipt)


def test_invalid_admission_metadata_is_omitted_and_mismatch_is_not_exact():
    scope = _scope(
        admission_id="not-an-id", authority_epoch=True, execution_generation=-1,
        admission_sequence=2**40,
    )
    request = object()
    agent = object()
    scope.bind_turn_agent(agent, "session", scope.source)
    attempt = scope.bind_request(
        request, agent=agent, session_id="session", turn_id="turn",
        request_id="request", policy_digest="digest",
    )

    receipt = diagnostics.receipt_fields(scope, attempt, request=object())

    assert "admission_ref" not in receipt
    assert "authority_epoch" not in receipt
    assert "execution_generation" not in receipt
    assert "admission_sequence" not in receipt
    assert receipt["request_binding"] == "mismatch"


def test_closed_scope_rejects_binding_and_receipt_snapshot():
    scope = _scope(admission_id="b" * 32)
    request = object()
    agent = object()
    scope.bind_turn_agent(agent, "session", scope.source)
    attempt = scope.bind_request(
        request, agent=agent, session_id="session", turn_id="turn",
        request_id="request", policy_digest="digest",
    )
    scope.close()

    assert scope.bind_request(
        object(), agent=object(), session_id="session", turn_id="turn",
        request_id="request", policy_digest="digest",
    ) is None
    assert diagnostics.receipt_fields(scope, attempt, request=request) is None


def test_unbound_scope_cannot_bind_a_request():
    scope = diagnostics.DiagnosticScope(admission_id="b" * 32)
    assert scope.bind_request(
        object(), agent=object(), session_id="session", turn_id="turn",
        request_id="request", policy_digest="digest",
    ) is None


def test_copied_attempt_cannot_publish_after_synchronized_scope_close():
    scope = _scope(admission_id="e" * 32)
    request = object()
    agent = object()
    scope.bind_turn_agent(agent, "session", scope.source)
    bound = threading.Event()
    checked = threading.Event()
    published = []

    def detached_observer():
        attempt = scope.bind_request(
            request, agent=agent, session_id="session", turn_id="turn",
            request_id="request", policy_digest="digest",
        )
        bound.set()
        assert checked.wait(timeout=2)
        published.append(diagnostics.receipt_fields(scope, attempt, request=request))

    thread = threading.Thread(target=detached_observer)
    thread.start()
    assert bound.wait(timeout=2)
    scope.close()
    checked.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert published == [None]


def test_scoped_request_serializes_only_matching_blocked_attempt():
    scope = _scope(admission_id="d" * 32, authority_epoch=2)
    token = diagnostics.activate(scope)
    agent = object()
    scope.bind_turn_agent(agent, "session", scope.source)
    request = SimpleNamespace(
        payload={"messages": [{"content": "SYNTHETIC PRIVATE CONTENT"}]},
        session_id="session", turn_id="turn", request_id="request", policy_digest="policy",
    )
    decision = SimpleNamespace(
        allowed=False, session_id="session", turn_id="turn", request_id="request",
        policy_digest="policy", reason_codes=("base64_payload", "arbitrary_private_code"),
    )
    receipt = {}
    try:
        with diagnostics.scoped_request(agent, request, nullcontext()):
            diagnostics.append_receipt_binding(receipt, decision, request=request)
        assert receipt["refusal_request_v1"]["request_binding"] == "exact"
        assert receipt["refusal_request_v1"]["findings"] == [{
            "reason": "base64_payload", "scan_stage": "aggregate",
            "basis": "final_scan", "carrier": "aggregate", "temporal": "unknown",
        }, {
            "reason": "other", "scan_stage": "aggregate",
            "basis": "final_scan", "carrier": "aggregate", "temporal": "unknown",
        }]
        encoded = json.dumps(receipt, sort_keys=True)
        assert "SYNTHETIC PRIVATE CONTENT" not in encoded
        assert "arbitrary_private_code" not in encoded
        assert request.payload["messages"][0]["content"] == "SYNTHETIC PRIVATE CONTENT"

        other_decision = SimpleNamespace(
            allowed=False, session_id="other", turn_id="turn", request_id="request",
            policy_digest="policy",
        )
        with diagnostics.scoped_request(agent, request, nullcontext()):
            diagnostics.append_receipt_binding({}, other_decision, request=request)
        other_request = SimpleNamespace(
            payload={}, session_id="session", turn_id="turn", request_id="request",
            policy_digest="policy",
        )
        wrong_request_receipt = {}
        with diagnostics.scoped_request(agent, request, nullcontext()):
            diagnostics.append_receipt_binding(
                wrong_request_receipt, decision, request=other_request
            )
        assert "refusal_request_v1" not in wrong_request_receipt
    finally:
        scope.close()
        diagnostics.reset(token)


def test_failed_nested_binding_masks_outer_attempt_and_exited_attempt_is_revoked():
    scope = _scope(admission_id="f" * 32)
    token = diagnostics.activate(scope)
    agent = object()
    scope.bind_turn_agent(agent, "session", scope.source)
    request = SimpleNamespace(
        payload={}, session_id="session", turn_id="turn", request_id="request",
        policy_digest="policy",
    )
    decision = SimpleNamespace(
        allowed=False, session_id="session", turn_id="turn", request_id="request",
        policy_digest="policy",
    )
    detached = None
    inner_receipt = {}
    outer_receipt = {}
    try:
        with diagnostics.scoped_request(agent, request, nullcontext()):
            detached = copy_context()
            mismatched = SimpleNamespace(
                payload={}, session_id="other", turn_id="turn", request_id="request",
                policy_digest="policy",
            )
            with diagnostics.scoped_request(agent, mismatched, nullcontext()):
                diagnostics.append_receipt_binding(inner_receipt, decision, request=mismatched)
            diagnostics.append_receipt_binding(outer_receipt, decision, request=request)
        assert "refusal_request_v1" not in inner_receipt
        assert outer_receipt["refusal_request_v1"]["request_binding"] == "exact"
        detached_receipt = {}
        detached.run(
            lambda: diagnostics.append_receipt_binding(
                detached_receipt, decision, request=request
            )
        )
        assert "refusal_request_v1" not in detached_receipt
    finally:
        scope.close()
        diagnostics.reset(token)


def test_receipt_attachment_linearizes_before_concurrent_scope_close(monkeypatch):
    scope = _scope(admission_id="g" * 32)
    agent = object()
    scope.bind_turn_agent(agent, "session", scope.source)
    request = SimpleNamespace(
        payload={}, session_id="session", turn_id="turn", request_id="request",
        policy_digest="policy",
    )
    decision = SimpleNamespace(
        allowed=False, session_id="session", turn_id="turn", request_id="request",
        policy_digest="policy", reason_codes=("base64_payload",),
    )
    receipt = {}
    entered = threading.Event()
    release = threading.Event()
    close_started = threading.Event()
    close_finished = threading.Event()
    token = diagnostics.activate(scope)
    original_findings = diagnostics._final_scan_findings

    def pause_findings(codes):
        entered.set()
        assert release.wait(timeout=2)
        return original_findings(codes)

    monkeypatch.setattr(diagnostics, "_final_scan_findings", pause_findings)

    def append():
        inner_token = diagnostics.activate(scope)
        try:
            with diagnostics.scoped_request(agent, request, nullcontext()):
                diagnostics.append_receipt_binding(receipt, decision, request=request)
        finally:
            diagnostics.reset(inner_token)

    def close():
        close_started.set()
        scope.close()
        close_finished.set()

    append_thread = threading.Thread(target=append)
    append_thread.start()
    assert entered.wait(timeout=2)
    close_thread = threading.Thread(target=close)
    close_thread.start()
    assert close_started.wait(timeout=2)
    assert not close_finished.is_set()
    release.set()
    append_thread.join(timeout=2)
    close_thread.join(timeout=2)
    diagnostics.reset(token)

    assert not append_thread.is_alive()
    assert not close_thread.is_alive()
    assert close_finished.is_set()
    assert receipt["refusal_request_v1"]["request_binding"] == "exact"


def test_authorization_ordinals_are_unique_under_concurrent_binding():
    scope = _scope(admission_id="c" * 32)
    barrier = threading.Barrier(8)
    ordinals = []
    lock = threading.Lock()
    agent = object()
    scope.bind_turn_agent(agent, "session", scope.source)

    def bind(index):
        barrier.wait()
        attempt = scope.bind_request(
            object(), agent=agent, session_id="session", turn_id="turn",
            request_id="request", policy_digest=str(index),
        )
        with lock:
            ordinals.append(attempt.ordinal)

    threads = [threading.Thread(target=bind, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(ordinals) == list(range(1, 9))


def test_lineage_table_retains_identity_and_merge_is_conservative():
    lineage = diagnostics.LineageTable()
    current, prior = {"role": "user"}, {"role": "user"}
    lineage.seed([(prior, "prior_turn"), (current, "current_turn")])
    clone = {"role": "user"}

    lineage.transfer(clone, [current])
    merged = {"role": "user"}
    lineage.transfer(merged, [current, prior])

    assert lineage.get(clone) == "current_turn"
    assert lineage.get(merged) == "mixed"
    assert lineage.get({"role": "user"}) == "unknown"
    invalid_transfer = {}
    lineage.transfer(invalid_transfer, [prior, object()])
    assert lineage.get(invalid_transfer) == "unknown"


def test_lineage_closed_and_overflow_states_are_fail_closed():
    lineage = diagnostics.LineageTable(max_associations=1)
    first, second = {}, {}
    lineage.seed([(first, "current_turn"), (second, "prior_turn")])

    assert lineage.get(first) == "current_turn"
    assert lineage.get(second) == "unknown"
    assert lineage.overflow is True
    lineage.close()
    assert lineage.get(first) == "unknown"


def test_lineage_transfer_overflow_invalidates_existing_output():
    lineage = diagnostics.LineageTable(max_transfers=0)
    row = {}
    lineage.seed([(row, "current_turn")])
    lineage.transfer(row, [row])

    assert lineage.overflow is True
    assert lineage.get(row) == "unknown"


def test_detached_attempt_releases_payload_agent_and_lineage_references():
    scope = _scope(admission_id="a" * 32)
    agent = SimpleNamespace(api_mode="chat_completions")
    scope.bind_turn_agent(agent, "session", scope.source)
    request = SimpleNamespace(payload={}, session_id="session", turn_id="turn", request_id="request", policy_digest="policy")
    token = diagnostics.activate(scope)
    try:
        with diagnostics.scoped_request(agent, request, nullcontext()):
            copied = copy_context()
            attempt = copied.run(diagnostics._attempt_context.get)[1]
            assert attempt.request is request
        assert attempt.request is None
        assert attempt.agent is None
        assert attempt.lineage is None
        with diagnostics.scoped_request(agent, request, nullcontext()):
            active_attempt = diagnostics._attempt_context.get()[1]
            scope.close()
            assert active_attempt.request is None
            assert active_attempt.agent is None
            assert active_attempt.lineage is None
    finally:
        scope.close()
        diagnostics.reset(token)


def test_wrong_recipient_decision_cannot_borrow_an_exact_request_binding():
    scope = _scope(admission_id="a" * 32)
    agent = SimpleNamespace(api_mode="chat_completions")
    scope.bind_turn_agent(agent, "session", scope.source)
    request = SimpleNamespace(payload={}, session_id="session", turn_id="turn", request_id="request", policy_digest="policy")
    route = SimpleNamespace(provider="custom", model="fixture", base_url="https://one.example.test", api_mode="chat_completions")
    decision = SimpleNamespace(**vars(route), allowed=False, session_id="session", turn_id="turn",
                               request_id="request", policy_digest="policy", reason_codes=("base64_payload",))
    token = diagnostics.activate(scope)
    try:
        with diagnostics.scoped_request(agent, request, nullcontext(), route=route):
            expected, foreign = {}, {}
            diagnostics.append_receipt_binding(expected, decision, request=request)
            decision.base_url = "https://other.example.test"
            diagnostics.append_receipt_binding(foreign, decision, request=request)
        assert expected["refusal_request_v1"]["request_binding"] == "exact"
        assert "refusal_request_v1" not in foreign
    finally:
        scope.close()
        diagnostics.reset(token)


def test_admission_close_revokes_borrowed_existing_receipt_context():
    from agent import source_provenance_diagnostics as provenance
    scope = _scope(admission_id="a" * 32)
    agent = SimpleNamespace(api_mode="chat_completions")
    scope.bind_turn_agent(agent, "session", scope.source)
    request = SimpleNamespace(payload={}, session_id="session", turn_id="turn", request_id="request", policy_digest="policy")
    owner = ("session", "turn", "request", "policy")
    token = diagnostics.activate(scope)
    try:
        context = provenance.receipt_scope(agent, None, owner, refusal_origins=lambda: ("other",))
        with diagnostics.scoped_request(agent, request, context):
            observation = provenance._receipt.get()
            scope.close()
            assert observation.closed
            assert not observation
    finally:
        scope.close()
        diagnostics.reset(token)


def test_distinct_durable_session_remains_unavailable_without_trusted_mapping():
    authority = SimpleNamespace(profile_id="profile")
    source = object()
    scope = diagnostics.DiagnosticScope(authority=authority, profile_id="profile",
        logical_session_id="logical", source=source, execution_generation=1)
    assert scope.verify_claimed_turn(authority, "logical", 1, source, "compressed-durable") is False
    assert scope.bind_turn_agent(object(), "compressed-durable", source) is False
    scope.close()
