"""Inert PR admission replay through actual workflow graphs and evaluator CLIs.

A missing dependency or an always()/!cancelled() bypass makes held PR source
reachable; a skipped aggregate admission must fail before any PR checkout.
No hosted workflow, network request, setup action or Hermes process runs here.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from hermes_yaml import safe_load

from scripts.ci.required_results import evaluate_gate
from scripts.ci.review_resumption import WORKFLOWS as RESUMABLE_WORKFLOWS
from tests.ci import _gha_expr as gha
from tests.ci.test_review_resumption import BOT, HEAD, OLD, snapshot, thread, transport

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
FORK = "mrkillbob/hermes-agent"
METADATA_ENTRIES = {"label-rerun.yml"}


def load(path):
    return safe_load(path.read_text(encoding="utf-8-sig"))


def triggers(workflow):
    return workflow.get("on", workflow.get(True)) or {}


def pr_entries():
    return {path.name: load(path) for path in sorted(WORKFLOWS.glob("*.y*ml"))
            if set(triggers(load(path))) & {"pull_request", "pull_request_target"}
            and path.name not in METADATA_ENTRIES}


def needs_of(job):
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def context(event="pull_request", repository=FORK, needs=None):
    return {"github": {"repository": repository, "event_name": event,
                       "event": {"repository": {"default_branch": "main"},
                                 "pull_request": {"number": 8, "head": {"sha": HEAD}}}},
            "needs": needs or {}, "inputs": {"release": False, "route": "all"},
            "__status__": {"always": True, "cancelled": False, "failure": False, "success": True}}


def allowed(job, ctx):
    expression = job.get("if")
    if not re.search(r"\b(always|cancelled|success|failure)\(", str(expression)):
        if any(row.get("result") != "success" for row in ctx["needs"].values()):
            return False
    return gha.condition(expression, ctx)


@pytest.mark.parametrize("state", ["failure", "skipped", "pending", "cancelled", "unknown"])
def test_every_substantive_pr_entry_holds_all_source_consumers(state):
    violations = []
    for filename, workflow in pr_entries().items():
        jobs = workflow["jobs"]
        admission = jobs.get("review-admission")
        if admission is None:
            violations.append(f"{filename}: no substantive admission")
            continue
        assert admission["name"] == "Review admission"
        for name, job in jobs.items():
            if name in ("review-admission", "all-checks-pass"):
                continue
            needs = {key: {"result": "success", "outputs": {}} for key in needs_of(job)}
            needs["review-admission"] = {"result": state}
            if "review-admission" not in needs_of(job) or allowed(job, context(needs=needs)):
                violations.append(f"{filename}/{name}: admission {state} leaves source reachable")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("state", ["absent", "skipped", "pending", "failure", "cancelled", "unknown", "success"])
def test_qualified_pr_aggregate_requires_actual_success(state):
    needs = {"detect": {"result": "skipped"}}
    if state != "absent":
        needs["review-admission"] = {"result": state}
    verdict = evaluate_gate(needs, pull_request=True)
    assert verdict["ok"] is (state == "success")
    assert ("review-admission" in verdict["failed"]) is (state != "success")


@pytest.mark.parametrize("release", [False, True])
def test_existing_non_pr_aggregates_need_no_admission_record(release):
    assert evaluate_gate({"detect": {"result": "success"}}, release=release)["ok"]


def test_native_wakeups_and_retry_paths_cover_every_substantive_pr_entry():
    controller = load(WORKFLOWS / "review-resumption.yml")
    waking = set(triggers(controller)["workflow_run"]["workflows"])
    for filename, workflow in pr_entries().items():
        assert filename in RESUMABLE_WORKFLOWS, filename
        assert workflow["name"] in waking, workflow["name"]


def test_live_comment_observes_the_docker_pr_wrapper():
    comment = load(WORKFLOWS / "ci-review-comment.yml")
    watched = {name.strip() for step in comment["jobs"]["comment"]["steps"]
               for name in step.get("env", {}).get("WATCH_WORKFLOWS", "").splitlines()}
    assert load(WORKFLOWS / "docker-pr.yml")["name"] in watched


def test_wrappers_cannot_leave_an_independent_unguarded_child_pr_trigger():
    for filename, workflow in pr_entries().items():
        for job in workflow["jobs"].values():
            uses = job.get("uses", "")
            if uses.startswith("./.github/workflows/"):
                child = load(ROOT / uses[2:])
                assert not set(triggers(child)) & {"pull_request", "pull_request_target"}, (filename, uses)
                if filename.endswith("-pr.yml"):
                    assert "workflow_call" in triggers(child)
                    assert not job.get("with"), "A PR wrapper must keep original default inputs"
                    assert not job.get("secrets"), "A PR wrapper must never inherit release secrets"


def permission_violations(path, ceiling):
    workflow = load(path)
    ranks = {"none": 0, "read": 1, "write": 2}
    problems = []
    for name, job in workflow["jobs"].items():
        granted = job.get("permissions", workflow.get("permissions", ceiling))
        for scope, grant in granted.items():
            if ranks[grant] > ranks[ceiling.get(scope, "none")]:
                problems.append(f"{path.name}/{name}: {scope}:{grant} exceeds caller ceiling")
        uses = job.get("uses", "")
        if uses.startswith("./.github/workflows/"):
            problems.extend(permission_violations(ROOT / uses[2:], granted))
    return problems


def test_wrappers_and_stable_ci_caller_satisfy_transitive_permission_ceilings():
    for filename, workflow in pr_entries().items():
        if filename.endswith("-pr.yml"):
            assert set(workflow["permissions"].values()) == {"read"}
            assert not permission_violations(WORKFLOWS / filename, workflow["permissions"])
    stable = load(WORKFLOWS / "stable-release.yml")
    caller = stable["jobs"]["ci"]
    assert not permission_violations(ROOT / caller["uses"][2:], caller["permissions"])


@pytest.mark.parametrize("event,repository", [("push", FORK), ("workflow_dispatch", FORK),
                                            ("release", "NousResearch/hermes-agent"),
                                            ("pull_request", "NousResearch/hermes-agent")])
def test_required_aggregate_precheck_is_inactive_outside_fork_pr_policy(event, repository):
    precheck = load(WORKFLOWS / "ci.yaml")["jobs"]["all-checks-pass"]["steps"][0]
    assert not gha.condition(precheck.get("if"), context(event, repository))


@pytest.mark.parametrize("event,repository", [
    ("push", FORK), ("workflow_dispatch", FORK), ("workflow_call", FORK),
    ("release", "NousResearch/hermes-agent"), ("pull_request", "NousResearch/hermes-agent"),
])
def test_admission_executes_no_source_or_metadata_outside_qualified_fork_pr(event, repository):
    for filename, workflow in pr_entries().items():
        job = workflow["jobs"].get("review-admission")
        assert job is not None, filename
        assert not [step for step in job["steps"] if gha.condition(step.get("if"), context(event, repository))]


@pytest.mark.parametrize("state", ["failure", "skipped", "pending", "cancelled", "unknown", "absent", "success"])
def test_required_aggregate_fails_before_pr_checkout(tmp_path, state):
    job = load(WORKFLOWS / "ci.yaml")["jobs"]["all-checks-pass"]
    needs = {} if state == "absent" else {"review-admission": {"result": state}}
    ctx = context(needs=needs)
    assert allowed(job, ctx), "Required aggregate must run to report admission holds"
    first = job["steps"][0]
    assert "uses" not in first, "No PR source/action may precede the admission precheck"
    before = set(tmp_path.iterdir())
    env = {key: gha.to_string(gha.render(value, ctx)) for key, value in first.get("env", {}).items()}
    process = subprocess.run(["bash", "-eo", "pipefail", "-c", gha.render(first["run"], ctx)],
                             cwd=tmp_path, env={**os.environ, **env}, text=True,
                             capture_output=True, timeout=10, check=False)
    assert process.returncode == (0 if state == "success" else 1), process.stdout + process.stderr
    assert set(tmp_path.iterdir()) == before, "Held aggregate materialized source"


@pytest.mark.parametrize("state", ["absent", "skipped", "pending", "failure", "cancelled", "unknown", "success"])
def test_actual_aggregate_step_passes_explicit_qualified_pr_context(tmp_path, state):
    needs = {"detect": {"result": "skipped"}}
    if state != "absent":
        needs["review-admission"] = {"result": state}
    ctx = context(needs=needs)
    steps = load(WORKFLOWS / "ci.yaml")["jobs"]["all-checks-pass"]["steps"]
    step = next(item for item in steps if item.get("id") == "evaluate")
    env = {key: gha.to_string(gha.render(value, ctx)) for key, value in step["env"].items()}
    environment = {**os.environ, **env, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
                   "GITHUB_OUTPUT": str(tmp_path / "output")}
    result = subprocess.run(["bash", "-eo", "pipefail", "-c", gha.render(step["run"], ctx)],
                            cwd=ROOT, env=environment, capture_output=True, text=True,
                            timeout=10, check=False)
    assert result.returncode == (0 if state == "success" else 1), result.stdout + result.stderr


@pytest.mark.parametrize("patch,expected", [
    ({}, 0), ({"requested_reviewers": [BOT]}, 1), ({"old_head": True}, 1),
    ({"no_security": True}, 1), ({"thread": True}, 1),
])
def test_actual_admission_step_enforces_dual_current_head_passes(tmp_path, patch, expected):
    data = snapshot()
    data["pr"].update({key: value for key, value in patch.items() if key == "requested_reviewers"})
    if patch.get("old_head"):
        data["commits"][HEAD[:7]] = OLD
    if patch.get("no_security"):
        data["comments"][0]["body"] = "\n".join(line for line in data["comments"][0]["body"].splitlines()
                                                 if "Security Review" not in line and "codex-security-review:" not in line)
    if patch.get("thread"):
        data["threads"] = [thread(1)]
    _, environment = transport(tmp_path, data)
    job = load(WORKFLOWS / "ci.yaml")["jobs"].get("review-admission")
    assert job is not None, "Trusted admission entry is missing"
    assert job["permissions"] == {"contents": "read", "pull-requests": "read", "issues": "read"}
    selected = [step for step in job["steps"] if gha.condition(step.get("if"), context())]
    checkout = next(step for step in selected if "uses" in step)
    assert checkout["uses"].startswith("actions/checkout@")
    options = checkout["with"]
    assert gha.render(options["ref"], context()) == "main"
    assert options["persist-credentials"] is False
    assert options["fetch-depth"] == 1 and options["sparse-checkout"] == "scripts/ci"
    step = next(step for step in selected if "run" in step)
    env = {key: gha.to_string(gha.render(value, context())) for key, value in step["env"].items()}
    environment["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{environment['PATH']}"
    process = subprocess.run(["bash", "-eo", "pipefail", "-c", gha.render(step["run"], context())],
                             cwd=ROOT, env={**environment, **env}, capture_output=True,
                             text=True, timeout=30, check=False)
    assert process.returncode == expected, process.stdout + process.stderr
    assert json.loads(process.stdout)["state"] == ("clean" if expected == 0 else
                                                  "blocked" if patch.get("thread") else
                                                  "unknown" if patch.get("old_head") else "pending")
