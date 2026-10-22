"""Same-content source reads must retain their actual independently granted paths."""
from tests.agent.test_source_context_replay_capacity import validated_composites


def test_batched_identical_prefixes_and_distinct_guidance_keep_exact_source_identities(tmp_path, monkeypatch):
    validated_composites(tmp_path, monkeypatch, 2, batched=True, shared_guidance=False, shared_prefix=True, bind_call_ids=True)


def test_legacy_identical_prefixes_remain_ambiguous_without_a_producer_binding(tmp_path, monkeypatch):
    import pytest
    from agent.llm_egress_firewall import EgressBlocked

    with pytest.raises(EgressBlocked):
        validated_composites(tmp_path, monkeypatch, 2, batched=True, shared_guidance=False, shared_prefix=True)
