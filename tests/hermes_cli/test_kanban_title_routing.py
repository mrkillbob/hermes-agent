"""Task-family ownership wins over incidental quoted inventory, without guessing."""

import pytest

from hermes_cli.kanban_repair_routing import repair_profile_for_task


@pytest.mark.parametrize(("title", "body", "owner"), [
    ("Vite lock stale: 3 fixed GHSAs pending (8.2.1 -> >=8.2.4) + routine npm refresh",
     "frontend/package-lock.json; preserve the project and paid execution holds.",
     None),
    ("Regenerate Python lock: vulnerable locked deps lag security floors",
     "requirements.txt, requirements.lock.txt, requirements-security-optional.txt on origin/stable.",
     None),
    ("Model spend rebalance: reclaim dead-model + disabled-job allocations, cap top-2 profiles, fix 429 contention",
     "Inventory includes LunaBot worktree governance, federation, cron, and state.db. Privacy hold remains.",
     "resource-scheduler"),
    ("Local tooling refresh: pip-audit + serena first, then routine uv/brew minors",
     "Separate repository pins from operator-owned installations; no execution grant.",
     None),
    ("GitHub Actions pin drift: setup-uv v7->v9, setup-python/node/upload-artifact minors",
     "Resolve the repository before changing .github/workflows/ci.yml.",
     None),
    ("Diagnostic experiment: fee restatement + execution-policy attribution gap in LunaBot replay artifacts",
     "Use offline replay evidence only; execution and paid experiment holds remain.",
     None),
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
    "Diagnostic experiment: fee restatement + execution-policy attribution gap",
])
def test_new_families_in_quoted_body_do_not_claim_unknown_work(family):
    assert repair_profile_for_task("Investigate bounded task", f"Quoted source comment: {family}") is None


def test_title_ownership_preserves_explicit_pr_audit_and_quota_overrides():
    assert repair_profile_for_task("Local PR CI audit", "usage limits; worktree governance") == "pr-local-ci-auditor"
    assert repair_profile_for_task("GitHub PR feedback", "Untrusted receipt: usage limits") == "hermes-maintenance-steward"
    assert repair_profile_for_task("Model spend rebalance: review quotas", "usage limits in quoted old receipt") == "resource-scheduler"
