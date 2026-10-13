"""Admission/aggregate command contracts using inert GitHub API responses.

The production CLIs decide exit codes, emitted results and retry requests. These
tests do not establish hosted trigger, dependency, checkout or permission wiring.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.ci.test_review_aggregate import hold
from tests.ci.test_review_resumption import BOT, HEAD, REPO, invoke, run_info, snapshot, transport

ROOT = Path(__file__).resolve().parents[2]


def cli(module, arguments, environment, input_text=None):
    return subprocess.run([sys.executable, "-m", f"scripts.ci.{module}", *arguments],
                          input=input_text, cwd=ROOT, env=environment, text=True,
                          capture_output=True, timeout=30, check=False)


@pytest.mark.parametrize("admission", ["absent", "skipped", "pending", "failure", "cancelled", "unknown", "success"])
def test_pr_aggregate_cli_requires_actual_admission_success(tmp_path, admission):
    needs = {"detect": {"result": "skipped"}}
    if admission != "absent":
        needs["review-admission"] = {"result": admission}
    output = tmp_path / "result"
    result = cli("required_results", ["--pull-request"],
                 {**os.environ, "GITHUB_OUTPUT": str(output)}, json.dumps(needs))
    assert result.returncode == (0 if admission == "success" else 1), result.stdout + result.stderr
    key, emitted = output.read_text().strip().split("=", 1)
    assert key == "needs-json"
    assert json.loads(emitted) == {name: item["result"] for name, item in needs.items()}
    assert ("::error::" in result.stdout) is (admission != "success")


@pytest.mark.parametrize("arguments", [[], ["--release"]])
def test_non_pr_aggregate_cli_preserves_historical_admission_free_context(tmp_path, arguments):
    needs = {"detect": {"result": "success"}}
    result = cli("required_results", arguments,
                 {**os.environ, "GITHUB_OUTPUT": str(tmp_path / "result")}, json.dumps(needs))
    assert result.returncode == 0, result.stdout + result.stderr


def complete_next_public_pass(data):
    producer = snapshot()["comments"][0]
    producer["body"] = producer["body"].replace("T01:", "T03:")
    producer["updated_at"] = "2026-10-13T03:28:04Z"
    data["comments"] = [producer, *data["comments"][1:]]
    data["reviews"].append({**data["reviews"][0], "id": 10, "state": "COMMENTED",
                            "submitted_at": "2026-10-13T03:00:00Z"})
    data["reactions"] = [{"id": 3, "user": BOT, "content": "+1", "created_at": "2026-10-13T03:28:06Z"}]
    data["review_api_error"] = False
    for thread in data["threads"]:
        thread["isResolved"] = True


@pytest.mark.parametrize("change", ["request", "eyes", "changes_requested", "finding", "unknown", "no_security"])
def test_admission_cli_rechecks_same_head_review_lifecycle(tmp_path, change):
    data = snapshot()
    result, data = invoke(tmp_path, data)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "clean"
    hold(data, change)
    result, data = invoke(tmp_path, data)
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] != "clean"
    assert json.loads(result.stdout)["head"] == HEAD
    complete_next_public_pass(data)
    result, data = invoke(tmp_path, data)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "clean"
    assert data["mutations"] == [], "Admission is a read-only decision"


@pytest.mark.parametrize("workflow", [
    "ci.yaml", "docker-pr.yml", "nix-pr.yml", "plugin-catalog-ci.yml", "install-e2e-pr.yml",
    "pm-bundle-pr.yml", "termux-verify-pr.yml", "sandbox-image.yml", "windows-bundle-sdk.yml",
])
def test_clear_review_controller_resumes_the_original_pr_run_for_supported_lane(tmp_path, workflow):
    data = snapshot()
    run = dict(run_info(), path=f".github/workflows/{workflow}")
    data["runs"] = [run]
    data["workflow_runs"] = {workflow: [run]}
    data["jobs"] = [{"name": "Review admission", "conclusion": "failure"}]
    result, retried = invoke(tmp_path, data, "resume")
    assert result.returncode == 0, result.stdout + result.stderr
    assert retried["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun"]
    # Reflect the accepted retry's queued state in both API response surfaces.
    retried["workflow_runs"][workflow] = retried["runs"]
    result, duplicate = invoke(tmp_path, retried, "resume")
    assert result.returncode == 0, result.stdout + result.stderr
    assert duplicate["mutations"] == retried["mutations"]


@pytest.mark.parametrize("event", [
    {"event_name": "schedule"},
    {"event_name": "workflow_run", "workflow_run": {"event": "pull_request_review", "head_sha": HEAD,
                                                     "pull_requests": [{"number": 8, "head": {"sha": HEAD}}]}},
    {"event_name": "workflow_run", "workflow_run": {"event": "pull_request_review_comment", "head_sha": HEAD,
                                                     "pull_requests": [{"number": 8, "head": {"sha": HEAD}}]}},
])
def test_controller_cli_consumes_discovered_exact_head_target(tmp_path, event):
    data = snapshot()
    data["runs"] = [run_info()]
    data["jobs"] = [{"name": "Review admission", "conclusion": "failure"}]
    result, data = invoke(tmp_path, data, "resume", event)
    assert result.returncode == 0, result.stdout + result.stderr
    target, = json.loads(result.stdout)["include"]
    state, environment = transport(tmp_path, data)
    result = cli("review_resumption", ["--repo", REPO, "--pr", str(target["number"]), "--head", target["head"]], environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(state.read_text())["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun"]


def test_admission_cli_rejects_unqualified_repository_without_api_activity(tmp_path):
    state, environment = transport(tmp_path, snapshot())
    result = cli("review_admission", ["--repo", "NousResearch/hermes-agent", "--pr", "8", "--head", HEAD], environment)
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "unknown"
    data = json.loads(state.read_text())
    assert data["pr_reads"] == 0
    assert data["mutations"] == []


def test_admission_cli_fails_when_head_moves_during_metadata_reads(tmp_path):
    data = snapshot()
    data["move_after_read"] = 2
    result, moved = invoke(tmp_path, data)
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "unknown"
    assert moved["mutations"] == []
