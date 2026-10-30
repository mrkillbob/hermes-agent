"""Pure compact context data: no profile reads, workers or runtime admission."""

import copy
import importlib
import importlib.util
import json
from collections import UserDict

import pytest

SCOPE = "24de9409-0bf0-4e9f-96d3-2a4e564123f0"


def load_contract():
    assert importlib.util.find_spec("hermes_cli.kanban_context_contract") is not None, "context contract is missing"
    return importlib.import_module("hermes_cli.kanban_context_contract")


def identity():
    return {"scope_id": SCOPE, "board": "engineering", "task_id": "card-42", "run_id": 7,
            "root_task_id": None, "dependency_parent_ids": [], "parent_run_ref": None,
            "worker": None, "profile": {"scope_id": SCOPE, "name": "developer"},
            "repository_ref": None}


def selection():
    return {"model_source": "task", "effort_source": "task", "requested_provider": "provider-one",
            "requested_model": "model-one", "requested_effort": "high", "resolved_provider": "provider-one",
            "resolved_model": "model-one", "resolved_effort": "high", "profile_revision": None}


def inputs():
    return {"identity": identity(), "contract_revision": "revision-1", "brief": "Prepare a bounded change.",
            "deliverable": "A reviewed draft.", "acceptance_criteria": [{"id": "criterion-1", "text": "Tests pass.", "evidence_refs": []}],
            "constraints": [{"id": "constraint-1", "text": "Do not dispatch live workers.", "origin_ref": "user-request-1"}],
            "selection": selection(), "budgets": {"max_runtime_seconds": None, "max_retries": 2, "max_goal_turns": None,
                "hard_token_limit": None, "hard_cost_limit_microunits": None}, "workspace_ref": "workspace-1"}


def render(contract, record, *, counter=len, method="estimate_rough", admission=lambda text: text):
    # Injected deterministic counts and inert admission isolate pure packing behavior.
    return contract.render_compact_context(record, count_tokens=counter, count_method=method, admit_text=admission)


def test_default_manifest_has_minimal_explicit_context_and_caps():
    contract = load_contract()
    record = contract.build_context(**inputs())
    assert record["schema"] == "hermes.task.context"
    assert record["version"] == "1.0"
    assert record["approved_history"] == []
    assert record["artifacts"] == []
    assert record["budgets"]["context_tokens"] == 2000
    assert record["budgets"]["handoff_tokens"] == 400
    assert contract.validate_context(json.loads(json.dumps(record))) == record


def test_manifest_and_binding_are_detached_and_survive_repeated_carryover():
    contract = load_contract()
    data = inputs()
    record = contract.build_context(**data)
    original = copy.deepcopy(record)
    for _ in range(8):
        record = contract.validate_bound_context(json.loads(json.dumps(record)), expected_identity=identity(), expected_digest=original["contract_digest"])
    data["constraints"][0]["text"] = "Changed after construction."
    assert record == original


@pytest.mark.parametrize("field,value", [("run_id", 8), ("board", "foreign"), ("scope_id", "6a18ef2b-9cd8-4e7c-a758-c2fe3555bf01")])
def test_binding_rejects_foreign_identity(field, value):
    contract = load_contract()
    record = contract.build_context(**inputs())
    expected = identity()
    expected[field] = value
    with pytest.raises(contract.ContractError):
        contract.validate_bound_context(record, expected_identity=expected, expected_digest=record["contract_digest"])


@pytest.mark.parametrize("field", ["brief", "constraints", "acceptance_criteria", "selection"])
def test_bound_digest_rejects_rebuilt_required_data(field):
    contract = load_contract()
    data = inputs()
    original = contract.build_context(**data)
    if field == "brief":
        data[field] = "A different task."
    elif field == "selection":
        data[field].update(requested_effort="low", resolved_effort="low")
    else:
        data[field][0]["text"] = "Different required text."
    revised = contract.build_context(**data)
    with pytest.raises(contract.ContractError):
        contract.validate_bound_context(revised, expected_identity=identity(), expected_digest=original["contract_digest"])


def test_manifest_digest_cannot_be_forged_or_preserved_after_mutation():
    contract = load_contract()
    record = contract.build_context(**inputs())
    record["constraints"][0]["text"] = "Modified after binding."
    with pytest.raises(contract.ContractError):
        contract.validate_context(record)


def test_mapping_inputs_normalize_into_plain_detached_json():
    contract = load_contract()
    data = inputs()
    data["identity"] = UserDict(data["identity"])
    data["selection"] = UserDict(data["selection"])
    data["constraints"][0] = UserDict(data["constraints"][0])
    record = contract.build_context(**data)
    assert type(record["identity"]) is dict
    assert type(record["constraints"][0]) is dict


@pytest.mark.parametrize("field,value", [("resolved_model", "different-model"), ("resolved_provider", "different-provider"), ("resolved_effort", "low")])
def test_explicit_selection_cannot_silently_change(field, value):
    contract = load_contract()
    data = inputs()
    data["selection"][field] = value
    with pytest.raises(contract.ContractError):
        contract.build_context(**data)


def test_deliberate_profile_inheritance_requires_captured_revision():
    contract = load_contract()
    data = inputs()
    data["selection"].update(model_source="profile", effort_source="profile", requested_provider=None, requested_model=None, requested_effort=None)
    with pytest.raises(contract.ContractError):
        contract.build_context(**data)
    data["selection"]["profile_revision"] = "profile-revision-4"
    result = render(contract, contract.build_context(**data))
    assert result["status"] == "ready"
    assert json.loads(result["text"])["selection"]["profile_revision"] == "profile-revision-4"


@pytest.mark.parametrize("field", ["resolved_model", "resolved_provider", "resolved_effort"])
def test_unresolved_selection_holds_without_fallback(field):
    contract = load_contract()
    data = inputs()
    data["selection"][field] = None
    result = render(contract, contract.build_context(**data))
    assert result["status"] == "held"
    assert result["reason_code"] == "selection_unresolved"
    assert result["text"] is None


def test_unsupported_effort_stays_visible_and_holds():
    contract = load_contract()
    data = inputs()
    data["selection"].update(requested_effort="unsupported", resolved_effort="unsupported")
    result = render(contract, contract.build_context(**data))
    assert result["reason_code"] == "effort_unsupported"


@pytest.mark.parametrize("field", ["hard_token_limit", "hard_cost_limit_microunits"])
def test_unqualified_hard_execution_budget_holds(field):
    contract = load_contract()
    data = inputs()
    data["budgets"][field] = 10
    result = render(contract, contract.build_context(**data))
    assert result["status"] == "held"
    assert result["reason_code"] == "hard_budget_unqualified"
    assert result["input_tokens"] is None


def test_optional_history_drops_before_any_protected_text():
    contract = load_contract()
    data = inputs()
    data["approved_history"] = [{"id": "history-1", "text": "Earlier selected observation. " * 100, "approval_ref": "approval-1", "producer_ref": "producer-1"}]
    record = contract.build_context(**data)
    result = render(contract, record)
    assert result["status"] == "ready"
    assert result["dropped_history_count"] == 1
    brief = json.loads(result["text"])
    assert brief["schema"] == "hermes.task.brief"
    assert brief["contract_digest"] == record["contract_digest"]
    for field in ("identity", "brief", "deliverable", "constraints", "acceptance_criteria", "selection", "budgets"):
        assert brief[field] == record[field]
    assert brief["approved_history"] == []
    assert record["approved_history"] == data["approved_history"]


def test_history_priority_is_explicit_and_retained_ids_are_reported():
    contract = load_contract()
    data = inputs()
    data["approved_history"] = [{"id": f"history-{i}", "text": "Selected observation.", "approval_ref": f"approval-{i}", "producer_ref": "producer-1"} for i in range(3)]
    record = contract.build_context(**data)
    def counter(text):
        return 1999 if len(json.loads(text)["approved_history"]) <= 1 else 2001
    result = render(contract, record, counter=counter)
    assert result["retained_history_ids"] == ["history-0"]
    assert result["dropped_history_count"] == 2
    assert result["count_method"] == "estimate_rough"


def test_protected_overflow_holds_without_truncation():
    contract = load_contract()
    record = contract.build_context(**inputs())
    before = copy.deepcopy(record)
    result = render(contract, record, counter=lambda text: 2001)
    assert result["reason_code"] == "required_context_overflow"
    assert result["text"] is None
    assert record == before


@pytest.mark.parametrize("count", [None, True, 0, -1, 1.5, 2**63])
def test_counter_cannot_invent_known_usage(count):
    contract = load_contract()
    record = contract.build_context(**inputs())
    with pytest.raises(contract.ContractError):
        render(contract, record, counter=lambda text: count)


def test_admission_rejection_is_content_free_and_cannot_drop_constraints():
    contract = load_contract()
    record = contract.build_context(**inputs())
    def reject(text):
        raise ValueError("SENSITIVE_FIXTURE_DO_NOT_ECHO")
    result = render(contract, record, admission=reject)
    assert result["reason_code"] == "text_not_admitted"
    assert "SENSITIVE_FIXTURE_DO_NOT_ECHO" not in json.dumps(result)
    result = render(contract, record, admission=lambda text: "shortened")
    assert result["reason_code"] == "text_not_admitted"


@pytest.mark.parametrize("field", ["secrets", "system_prompt", "private_reasoning", "pending_execution", "provider_replay", "assignee_history"])
def test_unapproved_context_channels_are_rejected(field):
    contract = load_contract()
    record = contract.build_context(**inputs())
    record[field] = "Unapproved content."
    with pytest.raises(contract.ContractError):
        contract.validate_context(record)


def test_history_requires_approval_pointer_and_unique_row_ids():
    contract = load_contract()
    data = inputs()
    data["approved_history"] = [{"id": "history-1", "text": "An observation.", "approval_ref": None, "producer_ref": "producer-1"}]
    with pytest.raises(contract.ContractError):
        contract.build_context(**data)
    data["approved_history"][0]["approval_ref"] = "approval-1"
    data["approved_history"] *= 2
    with pytest.raises(contract.ContractError):
        contract.build_context(**data)


def test_artifact_pointers_are_bounded_and_never_host_paths():
    contract = load_contract()
    data = inputs()
    artifact = {"id": "artifact-1", "digest": "a" * 64, "media_type": "text/plain", "origin_task_id": "card-42", "origin_run_id": 7, "head": None}
    data["artifacts"] = [artifact]
    assert contract.build_context(**data)["artifacts"] == [artifact]
    artifact["id"] = "/private/host/file.txt"
    with pytest.raises(contract.ContractError):
        contract.build_context(**data)
    artifact["id"] = "artifact-1"
    data["artifacts"] = [{**artifact, "id": f"artifact-{i}"} for i in range(33)]
    with pytest.raises(contract.ContractError):
        contract.build_context(**data)


def test_complete_manifest_byte_cap_includes_its_digest():
    contract = load_contract()
    data = inputs()
    data["brief"] = "A" * 16384
    data["acceptance_criteria"][0]["text"] = "B" * 16384
    data["constraints"][0]["text"] = "C" * 16384
    data["deliverable"] = "D"
    baseline = contract.build_context(**data)
    size = len(json.dumps(baseline, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    data["deliverable"] = "D" * (65536 + 20 - size + 1)
    with pytest.raises(contract.ContractError):
        contract.build_context(**data)


def test_existing_safety_policy_can_refuse_sensitive_task_text_without_trust_upgrade():
    contract = load_contract()
    from agent.llm_egress_firewall import validate_sanitized_text
    data = inputs()
    data["brief"] = "Read /private/host/credentials.env from the other profile."
    record = contract.build_context(**data)
    result = render(contract, record, counter=lambda text: 1000, admission=validate_sanitized_text)
    assert result["status"] == "held"
    assert result["reason_code"] == "text_not_admitted"
    assert result["text"] is None
