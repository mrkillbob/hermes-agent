"""Effective runtime policy identity binds actual native dispatch and admission."""
import pytest

from agent.llm_egress_firewall import EgressBlocked
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.native_metadata_capture import captured_native_metadata, native_metadata_execution_scope
from agent.source_provenance import DEFAULT_POLICY_DIGEST
from tests.agent.test_native_metadata_capture import _owner
from tests.agent.test_native_metadata_egress import _configure, _actual_dispatch_and_commit, _frames


_CASES = [
    pytest.param({}, DEFAULT_POLICY_DIGEST, id="production-default"),
    pytest.param({"llm_egress_policy_digest": "a" * 64}, "a" * 64, id="legacy-alias"),
    pytest.param({"_llm_egress_policy_digest": "b" * 64}, "b" * 64, id="explicit-private"),
    pytest.param({"_llm_egress_policy_digest": "b" * 64, "llm_egress_policy_digest": "a" * 64}, "b" * 64, id="private-precedence"),
    pytest.param({"_llm_egress_policy_digest": "", "llm_egress_policy_digest": "a" * 64}, "a" * 64, id="empty-private-falls-back"),
    pytest.param({"_llm_egress_policy_digest": "", "llm_egress_policy_digest": ""}, DEFAULT_POLICY_DIGEST, id="empty-both-default"),
    pytest.param({"_llm_egress_policy_digest": 0, "llm_egress_policy_digest": 17}, "17", id="existing-truthiness-and-str"),
]


def _set_policy(agent, attributes):
    for name in ("_llm_egress_policy_digest", "llm_egress_policy_digest"):
        if hasattr(agent, name):
            delattr(agent, name)
    for name, value in attributes.items():
        setattr(agent, name, value)


def _authorize_initial(agent):
    return authorize_agent_sdk_kwargs(agent, {
        "messages": [{"role": "user", "content": "Bounded invented task."}]})[1]


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize(("attributes", "expected"), _CASES)
def test_actual_dispatch_and_wire_use_authorizers_effective_policy(tmp_path, monkeypatch, protocol, attributes, expected):
    with _owner() as f:
        _configure(f, tmp_path, protocol)
        _set_policy(f.agent, attributes)
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            assert _authorize_initial(f.agent).decision.policy_digest == expected
            content = _actual_dispatch_and_commit(f, monkeypatch)
            f.agent._current_api_request_id = "turn-1:api:2"
            body = _frames(protocol, f.args, content)
            try:
                wire, receipt = authorize_agent_sdk_kwargs(f.agent, body)
                reasons = ()
            except EgressBlocked as error:
                wire, receipt, reasons = None, None, error.decision.reason_codes
            assert reasons == (), "Actual default/alias policy lost native proof: " + repr(reasons)
            assert wire == body and receipt.allowed
            assert receipt.decision.policy_digest == expected
            record, = captured_native_metadata(f.agent)
            assert record.policy_digest == expected
            for name in ("_llm_egress_policy_digest", "llm_egress_policy_digest"):
                if name not in attributes:
                    assert not hasattr(f.agent, name)  # No agent configuration is invented.


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize(("attributes", "changed_attribute"), [
    pytest.param({}, "_llm_egress_policy_digest", id="default-changed"),
    pytest.param({"llm_egress_policy_digest": "a" * 64}, "llm_egress_policy_digest", id="legacy-changed"),
    pytest.param({"_llm_egress_policy_digest": "b" * 64}, "_llm_egress_policy_digest", id="private-changed"),
])
def test_changed_effective_policy_still_invalidates_actual_capture(tmp_path, monkeypatch, protocol, attributes, changed_attribute):
    with _owner() as f:
        _configure(f, tmp_path, protocol)
        _set_policy(f.agent, attributes)
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            initial = _authorize_initial(f.agent)
            content = _actual_dispatch_and_commit(f, monkeypatch)
            record, = captured_native_metadata(f.agent)
            assert record.policy_digest == initial.decision.policy_digest
            setattr(f.agent, changed_attribute, "c" * 64)
            f.agent._current_api_request_id = "turn-1:api:2"
            with pytest.raises(EgressBlocked) as error:
                authorize_agent_sdk_kwargs(f.agent, _frames(protocol, f.args, content))
            assert error.value.decision.policy_digest == "c" * 64


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
def test_ignored_legacy_alias_change_does_not_invalidate_explicit_policy(tmp_path, monkeypatch, protocol):
    with _owner() as f:
        _configure(f, tmp_path, protocol)
        _set_policy(f.agent, {"_llm_egress_policy_digest": "b" * 64, "llm_egress_policy_digest": "a" * 64})
        with native_metadata_execution_scope(f.authority, f.policy, f.source, f.agent.session_id):
            assert _authorize_initial(f.agent).decision.policy_digest == "b" * 64
            content = _actual_dispatch_and_commit(f, monkeypatch)
            f.agent.llm_egress_policy_digest = "c" * 64
            f.agent._current_api_request_id = "turn-1:api:2"
            body = _frames(protocol, f.args, content)
            wire, receipt = authorize_agent_sdk_kwargs(f.agent, body)
            assert wire == body and receipt.decision.policy_digest == "b" * 64
