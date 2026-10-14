"""Pure coordination contract behavior; no dispatch or controller effects."""

import copy
import importlib
import importlib.util
import json
from collections import UserDict
from types import MappingProxyType

import pytest


SCOPE = "24de9409-0bf0-4e9f-96d3-2a4e564123f0"
OTHER_SCOPE = "6a18ef2b-9cd8-4e7c-a758-c2fe3555bf01"


def load_contract():
    assert importlib.util.find_spec("hermes_cli.kanban_event_contract") is not None, "event contract is missing"
    return importlib.import_module("hermes_cli.kanban_event_contract")


def identity():
    return {
        "scope_id": SCOPE, "board": "engineering", "task_id": "card-42", "run_id": 7,
        "root_task_id": "root-1", "dependency_parent_ids": ["parent-2"],
        "parent_run_ref": {"scope_id": SCOPE, "board": "engineering", "task_id": "root-1", "run_id": 5},
        "worker": {"host_id": "mac-1", "instance_id": "worker-7", "process_start_id": "birth-71"},
        "profile": {"scope_id": OTHER_SCOPE, "name": "developer"},
        "repository_ref": {
            "repository_id": 123, "repository": "owner/project", "pr_number": 42,
            "target_head": "a" * 40, "base_sha": "b" * 40, "execution_sha": "c" * 40,
            "merge_parent_shas": ["a" * 40, "b" * 40],
        },
        "source_ref": {"source": "controller", "id": "delivery-4", "revision": "revision-1", "attempt": None},
        "causation_id": f"{SCOPE}:3",
    }


def payload(event_type):
    if event_type == "task.claimed":
        return {"contract_revision": "contract-1", "claim_ref": "claim-7", "deadline": 1700000900}
    if event_type == "run.ended":
        return {"outcome": "completed", "reason_code": "completed", "receipt_ref": None, "diagnostic_ref": None}
    data = {"contract_revision": "contract-1", "contract_digest": "d" * 64, "context_revision": "context-2", "session_ref": "session-3"}
    if event_type == "context.compacted":
        data.update(attempt_id="compact-1", outcome="committed", token_count_method="estimate_rough", tokens_before=5000, tokens_after=2000)
    return data


def event(contract, event_type="task.claimed", *, event_identity=None, event_payload=None, seq=10):
    return contract.build_event(
        event_type=event_type, identity=identity() if event_identity is None else event_identity,
        seq=seq, created_at=1700000000, observed_at=1700000001,
        payload=payload(event_type) if event_payload is None else event_payload,
    )


@pytest.mark.parametrize("event_type", ["task.claimed", "run.ended", "context.bound", "context.compacted"])
def test_round_trip_preserves_explicit_identity_and_closed_payload(event_type):
    contract = load_contract()
    record = event(contract, event_type)
    assert record["schema"] == "hermes.coordination.event"
    assert record["version"] == "1.0"
    assert record["event_id"] == f"{SCOPE}:10"
    assert record["parent_run_ref"]["run_id"] == 5
    assert record["dependency_parent_ids"] == ["parent-2"]
    assert record["repository_ref"]["target_head"] == "a" * 40
    assert record["payload"] == payload(event_type)
    assert contract.validate_event(json.loads(json.dumps(record))) == record


def test_unknown_worker_profile_repository_are_preserved_as_null():
    contract = load_contract()
    data = identity()
    for name in ("worker", "profile", "repository_ref", "source_ref", "parent_run_ref", "root_task_id", "causation_id"):
        data[name] = None
    record = event(contract, event_identity=data)
    assert record["worker"] is None
    assert record["profile"] is None
    assert record["repository_ref"] is None


def test_sequence_gaps_are_valid_and_scope_separates_ids():
    contract = load_contract()
    first = event(contract, seq=11)
    later = event(contract, seq=31)
    data = identity()
    data.update(scope_id=OTHER_SCOPE, causation_id=None)
    other = event(contract, seq=11, event_identity=data)
    assert first["event_id"] != later["event_id"] != other["event_id"]
    assert first["event_id"] != other["event_id"]


def test_builder_and_validator_detach_caller_owned_objects():
    contract = load_contract()
    data, details = identity(), payload("task.claimed")
    record = event(contract, event_identity=data, event_payload=details)
    validated = contract.validate_event(record)
    data["dependency_parent_ids"].append("late-parent")
    details["claim_ref"] = "late-claim"
    record["repository_ref"]["target_head"] = "f" * 40
    assert validated["dependency_parent_ids"] == ["parent-2"]
    assert validated["payload"]["claim_ref"] == "claim-7"
    assert validated["repository_ref"]["target_head"] == "a" * 40


@pytest.mark.parametrize("mapping", [UserDict, MappingProxyType])
@pytest.mark.parametrize("field", ["payload", "worker", "profile", "repository_ref", "source_ref", "parent_run_ref"])
def test_valid_nested_mappings_are_normalized_to_detached_json(mapping, field):
    contract = load_contract()
    record = event(contract)
    original = record[field]
    record[field] = mapping(original)
    validated = contract.validate_event(mapping(record))
    assert type(validated[field]) is dict
    assert validated == json.loads(json.dumps(validated))
    original[next(iter(original))] = "changed-after-validation"
    assert "changed-after-validation" not in json.dumps(validated)


@pytest.mark.parametrize("mapping", [UserDict, MappingProxyType])
def test_builder_accepts_mapping_identity_and_payload(mapping):
    contract = load_contract()
    data, details = identity(), payload("task.claimed")
    record = event(contract, event_identity=mapping(data), event_payload=mapping(details))
    details["claim_ref"] = "changed-after-build"
    assert record["payload"]["claim_ref"] == "claim-7"


@pytest.mark.parametrize("container", ["payload", "worker", "profile", "repository_ref", "source_ref", "parent_run_ref", None])
@pytest.mark.parametrize("name", ["prompt", "secret", "reasoning", "pending_execution"])
def test_extra_content_fields_are_rejected_without_echoing_values(container, name):
    contract = load_contract()
    record = event(contract)
    target = record if container is None else record[container]
    target[name] = "SENSITIVE_FIXTURE_DO_NOT_ECHO"
    with pytest.raises(contract.ContractError) as error:
        contract.validate_event(record)
    assert "SENSITIVE_FIXTURE_DO_NOT_ECHO" not in str(error.value)


@pytest.mark.parametrize("field,value", [
    ("schema", "foreign.event"), ("version", "2.0"), ("version", "1.1"),
    ("type", "acceptance.recorded"), ("seq", True), ("seq", 2.5), ("seq", 0),
    ("seq", 2**63), ("run_id", None), ("run_id", False), ("run_id", -1),
    ("created_at", True), ("observed_at", -1), ("scope_id", "not-a-scope"),
    ("event_id", f"{OTHER_SCOPE}:10"), ("event_id", f"{SCOPE}:11"),
    ("causation_id", f"{SCOPE}:10"), ("task_id", "bad\nreference"),
])
def test_invalid_envelope_is_rejected(field, value):
    contract = load_contract()
    record = event(contract)
    record[field] = value
    with pytest.raises(contract.ContractError):
        contract.validate_event(record)


@pytest.mark.parametrize("field", ["run_id", "worker", "profile", "repository_ref", "payload", "parent_run_ref"])
def test_required_nullable_keys_cannot_be_omitted(field):
    contract = load_contract()
    record = event(contract)
    del record[field]
    with pytest.raises(contract.ContractError):
        contract.validate_event(record)


@pytest.mark.parametrize("field,value", [
    ("target_head", "abc1234"), ("repository_id", True), ("pr_number", 0),
    ("base_sha", "a" * 64), ("merge_parent_shas", ["a" * 39]),
])
def test_repository_identity_requires_full_consistent_shas_and_exact_ids(field, value):
    contract = load_contract()
    data = identity()
    data["repository_ref"][field] = value
    with pytest.raises(contract.ContractError):
        event(contract, event_identity=data)


def test_sha256_repository_identity_is_supported():
    contract = load_contract()
    data = identity()
    repository = data["repository_ref"]
    for field in ("target_head", "base_sha", "execution_sha"):
        repository[field] = "a" * 64
    repository["merge_parent_shas"] = ["b" * 64]
    assert event(contract, event_identity=data)["repository_ref"]["target_head"] == "a" * 64


def test_large_dependency_graph_requires_reference_without_truncation():
    contract = load_contract()
    data = identity()
    data["dependency_parent_ids"] = [f"parent-{n}" for n in range(33)]
    with pytest.raises(contract.ContractError):
        event(contract, event_identity=data)
    data.update(dependency_parent_ids=[], dependency_graph_ref="graph-5", dependency_count=33)
    record = event(contract, event_identity=data)
    assert record["dependency_count"] == 33
    assert record["dependency_graph_ref"] == "graph-5"


@pytest.mark.parametrize("parents", [["same", "same"], ["card-42"]])
def test_ambiguous_dependency_identity_is_rejected(parents):
    contract = load_contract()
    data = identity()
    data["dependency_parent_ids"] = parents
    with pytest.raises(contract.ContractError):
        event(contract, event_identity=data)


def test_serialized_event_cannot_exceed_16_kib():
    contract = load_contract()
    data = identity()
    data["dependency_parent_ids"] = [f"{n:02d}" + "x" * 510 for n in range(32)]
    before = copy.deepcopy(data)
    with pytest.raises(contract.ContractError):
        event(contract, event_identity=data)
    assert data == before


def test_run_end_cannot_assert_acceptance_or_contradict_outcome():
    contract = load_contract()
    details = payload("run.ended")
    details["accepted"] = True
    with pytest.raises(contract.ContractError):
        event(contract, "run.ended", event_payload=details)
    del details["accepted"]
    details["reason_code"] = "worker_lost"
    with pytest.raises(contract.ContractError):
        event(contract, "run.ended", event_payload=details)


@pytest.mark.parametrize("field,value", [("outcome", "accepted"), ("contract_digest", "abc"), ("tokens_before", False), ("tokens_after", -1), ("token_count_method", "made_up")])
def test_compaction_is_classified_and_counts_are_labelled(field, value):
    contract = load_contract()
    details = payload("context.compacted")
    details[field] = value
    with pytest.raises(contract.ContractError):
        event(contract, "context.compacted", event_payload=details)


def test_unknown_token_counts_stay_unknown():
    contract = load_contract()
    details = payload("context.compacted")
    details.update(tokens_before=None, tokens_after=None, token_count_method="unknown")
    assert event(contract, "context.compacted", event_payload=details)["payload"]["tokens_after"] is None
    details["tokens_after"] = 0
    with pytest.raises(contract.ContractError):
        event(contract, "context.compacted", event_payload=details)


@pytest.mark.parametrize("method", ["measured", "estimate_rough"])
def test_named_counting_method_requires_a_count(method):
    contract = load_contract()
    details = payload("context.compacted")
    details.update(tokens_before=None, tokens_after=None, token_count_method=method)
    with pytest.raises(contract.ContractError):
        event(contract, "context.compacted", event_payload=details)


@pytest.mark.parametrize("outcome,reason", [
    ("review_requested", "review_requested"),
    ("changes_requested", "changes_requested"),
    ("stale", "unknown"),
    ("spawn_failed", "unknown"),
    ("rate_limited", "rate_limited"),
    ("gave_up", "retry_exhausted"),
    ("workspace_deferred", "workspace_deferred"),
    ("watchdog_blocked", "policy_hold"),
    ("watchdog_deferred", "policy_hold"),
    ("routed_to_repair_profile", "repair_routed"),
    ("scheduled", "scheduled"),
])
def test_legacy_terminal_classifications_are_preserved_as_observations(outcome, reason):
    contract = load_contract()
    details = {"outcome": outcome, "reason_code": reason, "receipt_ref": None, "diagnostic_ref": None}
    record = event(contract, "run.ended", event_payload=details)
    assert record["payload"]["outcome"] == outcome
    assert record["payload"]["reason_code"] == reason
    assert record["payload"]["receipt_ref"] is None
    assert "accepted" not in record["payload"]
    assert "termination_confirmed" not in record["payload"]


@pytest.mark.parametrize("outcome", ["review_requested", "changes_requested", "stale", "spawn_failed"])
def test_legacy_nonacceptance_outcomes_cannot_be_relabelled_completed(outcome):
    contract = load_contract()
    details = {"outcome": outcome, "reason_code": "completed", "receipt_ref": None, "diagnostic_ref": None}
    with pytest.raises(contract.ContractError):
        event(contract, "run.ended", event_payload=details)


@pytest.mark.parametrize("outcome", [None, "running", "skipped", "future_outcome"])
def test_missing_nonterminal_or_unqualified_outcomes_are_not_promoted(outcome):
    contract = load_contract()
    details = {"outcome": outcome, "reason_code": "unknown", "receipt_ref": None, "diagnostic_ref": None}
    with pytest.raises(contract.ContractError):
        event(contract, "run.ended", event_payload=details)
