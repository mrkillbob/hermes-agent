"""Regression coverage for scoped operational blockers and legacy routing order."""

import pytest

from hermes_cli.kanban_repair_routing import repair_profile_for_task


INTAKE_TITLES = [
    ("Vite lock stale: inspect dependencies", "dependency-tooling-health-sentinel"),
    ("Regenerate Python lock: inspect dependencies", "dependency-tooling-health-sentinel"),
    ("Local tooling refresh: inspect dependencies", "dependency-tooling-health-sentinel"),
    ("GitHub Actions pin drift: inspect dependencies", "dependency-tooling-health-sentinel"),
    ("Diagnostic experiment: compare LunaBot replay order", "research-experiment-runner"),
]

BLOCKERS = [
    "Automation credential is missing.",
    "The automation credential is missing.",
    "GH auth failure prevented the workflow from running.",
    "Provider returned usage limits for this account.",
    "Workflow quota is exhausted.",
    "The workflow quota is exhausted.",
    "Provider quota was exceeded.",
    "Account usage limits were exceeded.",
    "Usage limits blocked the workflow.",
    "Quota prevented the workflow from running.",
    "Automation usage limits have been exhausted.",
    "Quota failures are blocking the workflow.",
    "LunaBot diagnostics failed.",
    "The LunaBot diagnostics failed.",
    "LunaBot diagnostics have failed.",
    "LunaBot diagnostic has failed.",
    "LunaBot diagnostics returned an error.",
    "LunaBot diagnostic is unavailable.",
    "LunaBot diagnostics were blocked.",
]


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("body", BLOCKERS)
def test_operational_blockers_precede_each_intake_title(title, owner, body):
    assert repair_profile_for_task(title, body) == "hermes-maintenance-steward"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("body", [
    "Ordinary assessment with no blocker.",
    "Inventory: Automation credential is missing.",
    "Inventory: GH auth failure stopped the workflow.",
    "Quoted: Provider returned usage limits for this account.",
    "Quoted: Workflow quota is exhausted.",
    "Inventory: Usage limits blocked the workflow.",
    "Quoted: Quota prevented the workflow from running.",
    "Inventory: Usage limits were exceeded; LunaBot diagnostics failed.",
    "Quoted: LunaBot diagnostics were blocked.",
])
def test_inventory_and_quote_prefixes_do_not_override_intake_owner(title, owner, body):
    assert repair_profile_for_task(title, body) == owner


@pytest.mark.parametrize(("title", "owner"), [
    ("Local PR CI audit", "pr-local-ci-auditor"),
    ("GitHub PR feedback", "pr-repair-steward"),
    ("Worktree governance audit", "worktree-change-governance-steward"),
    ("Federated runner launch", "federation-steward"),
    ("GUI launch/cancel race", "runtime-correctness-steward"),
    ("Pytest suite coverage", "test-contract-steward"),
    ("Community discovery", "nerdy-content-scout"),
    ("Synthesis of bounded children", "synthesizer"),
    ("Market data authority freshness", "market-data-authority-auditor"),
    ("White Knight issue relevance intake", "hermes-white-knight"),
    ("Upstream PR for nousresearch", "hermes-upstream-auditor"),
    ("Cross-dept handoff audit", "review-verification-steward"),
    ("Research cycle phase 18", "lunabot-research-lab-director"),
    ("Classifier unknown-handler gap", "content-classifier"),
    ("Model spend rebalance", "resource-scheduler"),
    ("Circular dependency approval bottleneck", "operations-steward"),
    ("Paper-safety broker safety", "paper-safety-guardian"),
    ("Alpaca broker validation", "coding-expert"),
    ("dashboard.secret state.db repair", "hermes-maintenance-steward"),
    ("Repair scheduled job", "hermes-maintenance-steward"),
    ("Dependency skew", "dependency-tooling-health-sentinel"),
    ("Resolve rnd adversarial-fuzz test", "rnd-adversarial-tester"),
])
def test_legacy_table_owners_survive_new_blocker_wording(title, owner):
    assert repair_profile_for_task(title, "Workflow quota is exhausted; LunaBot diagnostics failed.") == owner


def test_legacy_maintenance_still_precedes_generic_repair_rows_but_local_audit_precedes_it():
    body = "Usage limits were exceeded."
    assert repair_profile_for_task("GitHub PR feedback", body) == "hermes-maintenance-steward"
    assert repair_profile_for_task("Worktree governance audit", body) == "hermes-maintenance-steward"
    assert repair_profile_for_task("Federated runner launch", body) == "hermes-maintenance-steward"
    assert repair_profile_for_task("Local PR CI audit", body) == "pr-local-ci-auditor"


def test_p2_local_pr_audit_and_credential_examples():
    assert repair_profile_for_task("Local PR CI audit", "Usage limits were exceeded") == "pr-local-ci-auditor"
    assert repair_profile_for_task(
        "GitHub Actions pin drift", "Automation credential is missing",
    ) == "hermes-maintenance-steward"


@pytest.mark.parametrize("title", [
    "GitHub Actions pin drift: inspect. Automation credential is missing.",
    "Diagnostic experiment: compare LunaBot replay. Usage limits were exceeded.",
])
def test_legacy_blocker_in_intake_title_suffix_keeps_maintenance_owner(title):
    assert repair_profile_for_task(title, None) == "hermes-maintenance-steward"


def test_model_spend_exception_and_unknown_title_are_preserved():
    assert repair_profile_for_task("Model spend rebalance: audit allocations", "Automation credential is missing") == "resource-scheduler"
    assert repair_profile_for_task("Unrecognized task", "Usage limits were exceeded") == "hermes-maintenance-steward"
    assert repair_profile_for_task("Unrecognized task", "GitHub Actions pin drift") is None


@pytest.mark.parametrize("body", [
    "Workflow quota is exhausted.",
    "LunaBot diagnostics failed.",
])
def test_unknown_title_does_not_route_on_new_only_blocker_language(body):
    assert repair_profile_for_task("Unrecognized task", body) is None


@pytest.mark.parametrize("body", [
    "Inventory: Automation credential is missing.",
    "Quoted: The automation credential is missing.",
    "Inventory: Workflow quota is exhausted.",
    "Quoted: Account usage limits were exceeded.",
    "Inventory: LunaBot diagnostics failed.",
    "Inventory: LunaBot diagnostics have failed.",
    "Quoted: LunaBot diagnostic has failed.",
    "Inventory: LunaBot diagnostics returned an error.",
    "Quota failure.",
    "LunaBot diagnostics failure.",
])
def test_unprefixed_intake_title_only_does_not_claim_inventory_or_bare_nouns(body):
    assert repair_profile_for_task("GitHub Actions pin drift", body) == "dependency-tooling-health-sentinel"
