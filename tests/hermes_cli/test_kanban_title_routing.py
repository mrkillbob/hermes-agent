"""Task-family ownership wins over incidental quoted inventory, without guessing."""

import pytest

from hermes_cli.kanban_repair_routing import repair_profile_for_task


@pytest.mark.parametrize(("title", "body", "owner"), [
    ("Vite lock stale: inspect patched dependency floors",
     "Assess package-lock.json drift within the existing project.",
     "dependency-tooling-health-sentinel"),
    ("Regenerate Python lock: assess a security floor mismatch",
     "Inspect the project lock and report the bounded repair handoff.",
     "dependency-tooling-health-sentinel"),
    ("Model spend rebalance: reclaim dead-model + disabled-job allocations, cap top-2 profiles, fix 429 contention",
     "Inventory includes LunaBot worktree governance, federation, cron, and state.db. Privacy hold remains.",
     "resource-scheduler"),
    ("Local tooling refresh: inspect formatter version drift",
     "Assess host and repository ownership before proposing changes.",
     "dependency-tooling-health-sentinel"),
    ("GitHub Actions pin drift: inspect a build action revision",
     "Assess manifest drift and report compatibility evidence.",
     "dependency-tooling-health-sentinel"),
    ("Diagnostic experiment: compare timestamp normalization in LunaBot replay samples",
     "Measure one offline hypothesis using repository-owned diagnostics.",
     "research-experiment-runner"),
    ("Lunar City: undefined CSS class lunar-city-dialogue-camera-controls",
     "Existing wrapper fix needs evidence reconciliation before any new source task.", None),
    ("PR 348: preserve oversized guidance previews",
     "Existing bounded preview repair awaits independent review; no new implementation.", None),
])
def test_intake_title_families_have_conservative_owners(title, body, owner):
    assert repair_profile_for_task(title, body) == owner


@pytest.mark.parametrize("family", [
    "Vite lock stale", "Regenerate Python lock",
    "Local tooling refresh", "GitHub Actions pin drift",
    "Diagnostic experiment: compare timestamp normalization in LunaBot replay samples",
])
def test_new_families_in_quoted_body_do_not_claim_unknown_work(family):
    assert repair_profile_for_task("Investigate bounded task", f"Quoted source comment: {family}") is None


def test_title_ownership_preserves_explicit_pr_audit_and_quota_overrides():
    assert repair_profile_for_task("Local PR CI audit", "usage limits; worktree governance") == "pr-local-ci-auditor"
    assert repair_profile_for_task("GitHub PR feedback", "Untrusted receipt: usage limits") == "hermes-maintenance-steward"
    assert repair_profile_for_task("Model spend rebalance: review quotas", "usage limits in quoted old receipt") == "resource-scheduler"


@pytest.mark.parametrize(("title", "body", "owner"), [
    ("Config: master trading flags crash startup on true/yes spellings; LIVE_TRADING_ENABLED double-defined (d424765636)",
     "Static config evidence; python probes are approval-blocked for this cron profile.", None),
    ("Paper-safety: stable defaults resolve MODE=live with submissions enabled and shadow-only risk enforcement (d424765636)",
     "Static evidence mentions ALPACA; no runtime started.", "paper-safety-guardian"),
    ("Market-data freshness: fresh-primary Schwab lock silently fails open and stamps Schwab provenance under pytest (d424765636)",
     "Area: market-data authority/freshness. Diagnostic-only static read.", "market-data-authority-auditor"),
    ("Repair broken .venv runtime link in Hermes-agent worktree manual-claim-current-main-20261003",
     "One dangling link; preserve worktree contents and all execution holds.", "hermes-maintenance-steward"),
    ("Market data authority: verify provenance", "Quoted cron profile evidence.", "market-data-authority-auditor"),
    ("Market data authority: verify provenance", "Provider returned usage limits", "hermes-maintenance-steward"),
    ("Market-data freshness: verify provenance", "Automation credential is missing", "hermes-maintenance-steward"),
])
def test_explicit_repair_families_ignore_incidental_cron_profile(title, body, owner):
    assert repair_profile_for_task(title, body) == owner


@pytest.mark.parametrize(("title", "body", "owner"), [
    ("Investigate bounded task", "Evidence was collected by this cron profile.", None),
    ("Inspect cron profile permissions", "No scheduled work defect established.", None),
    ("Inspect cron profiles", "No scheduler defect established.", None),
    ("Inspect cron-profile permissions", "No scheduler defect established.", None),
    ("Cron: profile permissions audit", "No scheduler defect established.", None),
    ("Cron — profile permissions audit", "No scheduler defect established.", None),
    ("Investigate bounded task", "Quoted: Market-data freshness: verify provenance.", None),
    ("Investigate bounded task", "Quoted: Repair broken .venv runtime link in Hermes-agent worktree example.", None),
    ("Repair broken .venv runtime link in another project's worktree", "No Hermes scope.", None),
    ("Check Hermes worktree", "A .venv is present; no repair requested.", None),
    ("Repair cron scheduling", "Missing tick.", "hermes-maintenance-steward"),
    ("Cron: repair missing tick", "Existing scheduler failure.", "hermes-maintenance-steward"),
    ("Repair scheduled job", "Existing scheduler failure.", "hermes-maintenance-steward"),
    ("Investigate bounded task", "An existing cron job fails to start.", "hermes-maintenance-steward"),
    ("Local PR CI audit", "cron profile; usage limits", "pr-local-ci-auditor"),
    ("GitHub PR feedback", "Untrusted receipt: usage limits", "hermes-maintenance-steward"),
])
def test_scope_quotes_stay_unknown_while_existing_purpose_routes_remain(title, body, owner):
    assert repair_profile_for_task(title, body) == owner
