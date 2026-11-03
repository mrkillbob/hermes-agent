"""Required finalizers do not serialize newer CI, and checkout cannot consume the test budget."""

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from scripts.ci.required_results import evaluate_gate
from tests.ci import _gha_expr as gha

ROOT = Path(__file__).resolve().parents[2]


def _context(event, release, run):
    return {
        "github": {
            "repository": "Coding-Sensations/hermes-agent",
            "event_name": event, "ref": "refs/pull/17/merge" if event == "pull_request" else "refs/heads/main", "run_id": run,
        },
        "inputs": {"release": release},
        "__status__": {"always": True, "success": False, "failure": True, "cancelled": True},
    }


@pytest.mark.parametrize(
    ("event", "release", "supersede"),
    [
        ("pull_request", False, True),
        ("pull_request", None, True),
        ("push", False, False),
        ("workflow_dispatch", False, False),
        ("workflow_dispatch", True, False),
    ],
)
def test_stale_workload_groups_do_not_contain_required_finalizers(event, release, supersede):
    workflow = YAML(typ="base").load((ROOT / ".github/workflows/ci.yaml").read_text(encoding="utf-8"))
    assert "concurrency" not in workflow, "an old always-finalizer must not retain a workflow-wide slot"
    jobs = workflow["jobs"]
    context = _context(event, release, 12)
    previous = _context(event, release, 11)
    groups = []
    for name, job in jobs.items():
        if name in ("all-checks-pass", "ci-timings", "review-labels"):
            assert "concurrency" not in job
            continue
        needs = job.get("needs", [])
        assert set([needs] if isinstance(needs, str) else needs) <= {"detect"}
        policy = job["concurrency"]
        group = gha.render(policy["group"], context)
        groups.append(group)
        assert gha.condition(policy["cancel-in-progress"], context) is supersede
        old_group = gha.render(policy["group"], previous)
        assert (group != old_group) is bool(release)
    assert len(set(groups)) == len(groups), "sibling jobs must never cancel one another"
    assert gha.condition(jobs["all-checks-pass"]["if"], context) is True
    for result in ("failure", "cancelled", "abandoned"):
        assert evaluate_gate({"tests": {"result": result}})["ok"] is False


def test_full_e2e_has_separate_bounded_checkout_and_test_budgets():
    workflow = YAML(typ="base").load((ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["e2e"]
    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    tests = next(step for step in job["steps"] if step.get("name") == "Run e2e tests")
    context = _context("schedule", False, 12)
    total = int(gha.render(job["timeout-minutes"], context))
    fetching = int(gha.render(checkout["timeout-minutes"], context))
    running = int(gha.render(tests["timeout-minutes"], context))
    assert 0 < fetching <= 30
    assert running >= 45, "fetch/setup must not consume the old full execution window"
    assert total >= fetching + running + 20, "locked setup, UI build and cleanup need their own headroom"
    assert str(tests["env"]["HERMES_TEST_WORKERS"]) == "3"
    assert str(tests["env"]["HERMES_TEST_FILE_TIMEOUT"]) == "900"
    assert str(tests["env"]["HERMES_TEST_FILE_RETRIES"]) == "0"
