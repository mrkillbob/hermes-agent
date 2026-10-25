"""Replay advisory finalizer admission without changing required-gate verdicts."""
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from scripts.ci.required_results import evaluate_gate
from tests.ci import _gha_expr as gha

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("result", ["success", "failure", "skipped"])
def test_timing_report_survives_failure_but_not_cancellation(cancelled, result):
    workflow = YAML(typ="base").load(
        (ROOT / ".github/workflows/ci.yaml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["ci-timings"]
    context = {
        "needs": {"all-checks-pass": {"result": result}},
        "__status__": {
            "always": True,
            "success": result == "success" and not cancelled,
            "failure": result == "failure",
            "cancelled": cancelled,
        },
    }
    assert gha.condition(job.get("if"), context) is (not cancelled)


@pytest.mark.parametrize("release", [False, True])
@pytest.mark.parametrize("result", ["cancelled", "in_progress", "queued", None])
def test_incomplete_required_work_never_becomes_success(release, result):
    verdict = evaluate_gate({"tests": {"result": result}}, release=release)
    assert verdict["ok"] is False
    assert verdict["failed"] == ["tests"]


@pytest.mark.parametrize("cancelled", [False, True])
def test_required_aggregate_still_reports_failed_dependencies(cancelled):
    workflow = YAML(typ="base").load(
        (ROOT / ".github/workflows/ci.yaml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["all-checks-pass"]
    context = {
        "__status__": {
            "always": True, "success": False,
            "failure": True, "cancelled": cancelled,
        },
    }
    assert gha.condition(job.get("if"), context) is True
    verdict = evaluate_gate({"tests": {"result": "cancelled" if cancelled else "failure"}})
    assert verdict["ok"] is False
