"""Deterministic Kanban handoff ownership contracts."""

from hermes_cli.kanban_repair_routing import repair_profile_for_task


def test_known_repair_scopes_bypass_router_profiles():
    assert repair_profile_for_task(
        "Verify market data authority freshness",
        "Check stale market-data provenance and freshness.",
    ) == "market-data-authority-auditor"
    assert repair_profile_for_task(
        "Upstream PR audit",
        "Compare NousResearch upstream/main and the current PR head.",
    ) == "hermes-upstream-auditor"
    assert repair_profile_for_task(
        "Cross-dept handoff audit: t_e4ca69b2 / t_e181340d missing steward evidence",
        "Blocked cross-dept tasks assigned review-verification-steward; no sign-off evidence found.",
    ) == "review-verification-steward"
    assert repair_profile_for_task(
        "Local PR CI audit",
        "Run the exact local CI audit for this pull request.",
    ) == "pr-local-ci-auditor"
    assert repair_profile_for_task("Federated runner smoke test", "Validate federation handoff.") == "federation-steward"
    assert repair_profile_for_task("Create pytest suite", "Add test coverage for the normalizer.") == "test-contract-steward"
    assert repair_profile_for_task("Useful content discovery", "Scout relevant community material.") == "nerdy-content-scout"
    assert repair_profile_for_task("Synthesize gaps", "Route bounded children or IDLE.") == "synthesizer"
    assert repair_profile_for_task("GitHub PR feedback", "Untrusted receipt: usage limits") == "hermes-maintenance-steward"
    assert repair_profile_for_task(
        "[White Knight] Issue relevance intake 20260922-1",
        "Resolve the upstream issue relevance intake and workspace admission.",
    ) == "hermes-white-knight"
    assert repair_profile_for_task(
        "Diagnostic: research-scout priority finding — classifier gap (8,736 handlers, 6,666 unknown)",
        "Research cycle summary for Phase 18.",
    ) == "lunabot-research-lab-director"
    assert repair_profile_for_task(
        "Diagnose classifier unknown-handler backlog (6,666)",
        "Unclassified handlers need a classifier gap audit.",
    ) == "content-classifier"
    assert repair_profile_for_task(
        "Model spend rebalance — profile/group budget over 7d",
        "Rebalance the model budget.",
    ) == "resource-scheduler"
    assert repair_profile_for_task(
        "Dep skew: numpy/pandas/httpx/pydantic/litellm missing/bounds unverified",
        "Dependency skew requires a bounds audit.",
    ) == "dependency-tooling-health-sentinel"


def test_failed_auth_and_launch_races_have_repair_owners():
    assert repair_profile_for_task("Hermes github-pr-feedback gh auth failure", "") == "hermes-maintenance-steward"
    assert repair_profile_for_task("Cancellation race", "start_gui_command before process registration") == "runtime-correctness-steward"
    assert repair_profile_for_task(
        "Independently verify GUI launch/cancel and stale-reconciliation race",
        "Behavioral test for cancel_gui_run and _reconcile_stale_active_run.",
    ) == "runtime-correctness-steward"

def test_unknown_scope_stays_unassigned_for_explicit_triage():
    assert repair_profile_for_task("Investigate an unclear issue", "Needs more scope.") is None
