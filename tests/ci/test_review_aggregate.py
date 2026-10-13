"""Inert same-head aggregate invalidation; no source/job/network execution."""

import copy

import pytest

from tests.ci.test_review_resumption import BOT, HEAD, OLD, REPO, invoke, run_info, snapshot, thread

LIVE_STEPS = ("Live review admission before aggregation", "Live review admission before success")
MERGE = "e" * 40
BASE = "f" * 40


def aggregate_snapshot(conclusion="success", attempt=1):
    data = snapshot()
    data["runs"] = [dict(run_info(), conclusion=conclusion, run_attempt=attempt)]
    association = data["runs"][0]["pull_requests"][0]
    association["base"] = {"sha": BASE, "repo": {"name": "hermes-agent", "url": f"https://api.github.com/repos/{REPO}"}}
    association["url"] = f"https://api.github.com/repos/{REPO}/pulls/8"
    data["runs"][0]["referenced_workflows"] = [
        {"sha": MERGE, "ref": "refs/pull/8/merge", "path": f"{REPO}/.github/workflows/tests.yml@{MERGE}"},
    ]
    data["runtime_commit"] = {"sha": MERGE, "parents": [{"sha": BASE}, {"sha": HEAD}]}
    data["jobs"] = [
        {"id": 10, "run_id": 11, "head_sha": HEAD, "name": "Review admission",
         "status": "completed", "conclusion": "success"},
        {"id": 20, "run_id": 11, "head_sha": HEAD, "name": "All required checks pass",
         "status": "completed", "conclusion": conclusion,
         "steps": [{"name": name, "status": "completed", "conclusion": "success"}
                   for name in (*LIVE_STEPS, "Evaluate job results")]},
    ]
    if conclusion == "failure":
        data["jobs"][1]["steps"][0]["conclusion"] = "failure"
    return data


def hold(data, kind):
    without_security = "\n".join(line for line in data["comments"][0]["body"].splitlines()
                                 if "Security Review" not in line and "codex-security-review:" not in line)
    patches = {
        "request": {"comments": [*data["comments"], {"body": "@codex review", "created_at": "2026-10-13T02:00:00Z"}]},
        "eyes": {"reactions": [*data["reactions"], {"id": 2, "user": BOT, "content": "eyes", "created_at": "2026-10-13T02:00:00Z"}]},
        "changes_requested": {"reviews": [{**data["reviews"][0], "state": "CHANGES_REQUESTED"}]},
        "finding": {"threads": [thread(1)]},
        "unknown": {"review_api_error": True},
        "no_security": {"comments": [{**data["comments"][0], "body": without_security}]},
    }
    data.update(patches[kind])


@pytest.mark.parametrize("kind", ["request", "eyes", "changes_requested", "finding", "unknown", "no_security"])
@pytest.mark.parametrize("attempt", [1, 3, 5])
def test_same_head_green_is_invalidated_without_repeating_source_jobs(tmp_path, kind, attempt):
    data = aggregate_snapshot(attempt=attempt)
    hold(data, kind)
    result, retried = invoke(tmp_path, data, "resume")
    assert result.returncode == 0, result.stdout + result.stderr
    assert retried["mutations"] == [f"repos/{REPO}/actions/jobs/20/rerun"]
    _, duplicate = invoke(tmp_path, retried, "resume")
    assert duplicate["mutations"] == retried["mutations"]


def test_completed_aggregate_can_be_invalidated_while_advisory_jobs_run(tmp_path):
    data = aggregate_snapshot()
    data["runs"][0]["status"] = "in_progress"
    hold(data, "finding")
    _, retried = invoke(tmp_path, data, "resume")
    assert retried["mutations"] == [f"repos/{REPO}/actions/jobs/20/rerun"]


def test_metadata_hold_deduplicates_then_clear_restores_only_aggregate(tmp_path):
    data = aggregate_snapshot("failure", attempt=4)
    hold(data, "finding")
    _, held = invoke(tmp_path, data, "resume")
    assert held["mutations"] == []
    data["threads"][0]["isResolved"] = True
    _, cleared = invoke(tmp_path, data, "resume")
    assert cleared["mutations"] == [f"repos/{REPO}/actions/jobs/20/rerun"]


@pytest.mark.parametrize("patch", [
    {"run": {"event": "push"}}, {"run": {"head_sha": OLD}},
    {"run": {"pull_requests": []}}, {"run": {"run_attempt": 51}},
    {"aggregate": {"status": "in_progress", "conclusion": None}},
    {"aggregate": {"head_sha": OLD}}, {"aggregate": {"run_id": 99}},
    {"aggregate": {"run_attempt": 0}},
    {"aggregate": {"steps": []}}, {"workflow_mismatch": True},
    {"move_after_read": 3}, {"newer_run": True}, {"clear_after_read": 3},
])
def test_unqualified_metadata_target_never_writes(tmp_path, patch):
    data = aggregate_snapshot()
    hold(data, "finding")
    data["runs"][0].update(patch.get("run", {}))
    data["jobs"][1].update(patch.get("aggregate", {}))
    data.update({key: value for key, value in patch.items() if key not in ("run", "aggregate")})
    _, untouched = invoke(tmp_path, data, "resume")
    assert untouched["mutations"] == []


def test_partial_metadata_attempt_uses_retained_successful_admission(tmp_path):
    data = aggregate_snapshot("failure", attempt=4)
    data["history_jobs"] = copy.deepcopy(data["jobs"])
    data["attempt_jobs"] = [data["jobs"][1]]
    data["jobs"] = [data["jobs"][1]]
    _, restored = invoke(tmp_path, data, "resume")
    assert restored["mutations"] == [f"repos/{REPO}/actions/jobs/20/rerun"]


def test_latest_failed_admission_cannot_borrow_older_success(tmp_path):
    data = aggregate_snapshot("failure", attempt=3)
    data["history_jobs"] = copy.deepcopy(data["jobs"])
    data["history_jobs"].append(dict(data["jobs"][0], id=30, conclusion="failure"))
    _, held = invoke(tmp_path, data, "resume")
    assert held["mutations"] == []


def test_source_failure_and_healthy_clean_aggregate_are_not_retried(tmp_path):
    data = aggregate_snapshot("failure")
    data["jobs"][1]["steps"][0]["conclusion"] = "success"
    data["jobs"][1]["steps"].append({"name": "Evaluate job results", "conclusion": "failure"})
    _, failed = invoke(tmp_path, data, "resume")
    assert failed["mutations"] == []
    data = aggregate_snapshot()
    _, healthy = invoke(tmp_path, data, "resume")
    assert healthy["mutations"] == []


@pytest.mark.parametrize("evaluation", ["failure", "skipped", None])
def test_restoration_requires_proven_source_results_and_preserves_label_retry(tmp_path, evaluation):
    data = aggregate_snapshot("failure")
    data["jobs"][1]["steps"][-1]["conclusion"] = evaluation
    _, held = invoke(tmp_path, data, "resume")
    assert held["mutations"] == []
    data["pr"]["labels"] = [{"name": "ci-reviewed"}]
    data["timeline"] = [{"id": 100, "event": "labeled", "label": {"name": "ci-reviewed"},
                         "created_at": "2026-10-13T02:00:00Z"}]
    data["jobs"].append({"name": "Review label gate / Review label gate", "conclusion": "failure"})
    _, labeled = invoke(tmp_path, data, "resume")
    assert labeled["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun-failed-jobs"]


def test_specific_job_api_denial_is_visible_and_never_falls_back_to_full_ci(tmp_path):
    data = aggregate_snapshot()
    hold(data, "finding")
    data["rerun_denied"] = True
    result, denied = invoke(tmp_path, data, "resume")
    assert result.returncode == 1
    assert denied["mutations"] == []


@pytest.mark.parametrize("error", ["api_error", "definition_api_error"])
def test_unknown_target_or_definition_metadata_never_grants_retry(tmp_path, error):
    data = aggregate_snapshot()
    hold(data, "finding")
    data[error] = True
    result, denied = invoke(tmp_path, data, "resume")
    assert result.returncode == 1
    assert denied["mutations"] == []


@pytest.mark.parametrize("patch", [
    {"references": []}, {"reference": {"ref": "refs/heads/topic"}},
    {"reference": {"path": f"other/repo/.github/workflows/tests.yml@{MERGE}"}},
    {"reference": {"sha": OLD}}, {"parents": [{"sha": HEAD}]},
    {"parents": [{"sha": OLD}, {"sha": HEAD}]}, {"commit_sha": OLD},
    {"base_sha": "unknown"}, {"base_repo": "other"},
])
def test_unknown_or_unbound_original_runtime_revision_holds(tmp_path, patch):
    data = aggregate_snapshot()
    hold(data, "finding")
    references = data["runs"][0]["referenced_workflows"]
    references[0].update(patch.get("reference", {}))
    data["runs"][0]["referenced_workflows"] = patch.get("references", references)
    data["runtime_commit"]["parents"] = patch.get("parents", data["runtime_commit"]["parents"])
    data["runtime_commit"]["sha"] = patch.get("commit_sha", MERGE)
    data["runs"][0]["pull_requests"][0]["base"]["sha"] = patch.get("base_sha", BASE)
    if patch.get("base_repo"):
        data["runs"][0]["pull_requests"][0]["base"]["repo"]["url"] = "https://api.github.com/repos/other/hermes-agent"
    result, held = invoke(tmp_path, data, "resume")
    assert result.returncode == 1
    assert held["mutations"] == []


def test_optional_get_job_fields_do_not_block_same_execution(tmp_path):
    data = aggregate_snapshot()
    hold(data, "finding")
    data["jobs"][1]["run_attempt"] = 1
    data["actual_job_patch"] = {"runner_name": "hosted-runner", "completed_at": "2026-10-13T02:00:00Z"}
    data["actual_job_omit"] = ["run_attempt"]
    _, retried = invoke(tmp_path, data, "resume")
    assert retried["mutations"] == [f"repos/{REPO}/actions/jobs/20/rerun"]


@pytest.mark.parametrize("patch", [{"head_sha": OLD}, {"run_id": 99}, {"run_attempt": 2},
                                   {"status": "in_progress"}, {"steps": []}])
def test_get_job_stale_or_incomplete_execution_holds(tmp_path, patch):
    data = aggregate_snapshot()
    hold(data, "finding")
    data["actual_job_patch"] = patch
    _, held = invoke(tmp_path, data, "resume")
    assert held["mutations"] == []


def test_unread_e2e_label_retains_source_retry_priority_over_metadata_restore(tmp_path):
    data = aggregate_snapshot("failure")
    data["pr"]["labels"] = [{"name": "run-e2e"}]
    data["timeline"] = [{"id": 100, "event": "labeled", "label": {"name": "run-e2e"},
                         "created_at": "2026-10-13T02:00:00Z"}]
    _, retried = invoke(tmp_path, data, "resume")
    assert retried["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun"]


def test_label_review_read_failure_cannot_suppress_stale_green_invalidation(tmp_path):
    data = aggregate_snapshot()
    data["pr"]["labels"] = [{"name": "run-e2e"}]
    data["timeline"] = [{"id": 100, "event": "labeled", "label": {"name": "run-e2e"},
                         "created_at": "2026-10-13T02:00:00Z"}]
    data["review_api_error"] = True
    result, retried = invoke(tmp_path, data, "resume")
    assert result.returncode == 1, "Label metadata failure must remain visible"
    assert retried["mutations"] == [f"repos/{REPO}/actions/jobs/20/rerun"]


def attempt_history(data, metadata_count):
    original = copy.deepcopy(data["runs"][0])
    original.update(status="completed", run_attempt=1, run_started_at="2026-10-13T00:00:00Z")
    source_jobs = copy.deepcopy(data["jobs"])
    data["attempts"] = {"1": original}
    data["jobs_by_attempt"] = {"1": source_jobs}
    for number in range(2, metadata_count + 2):
        data["attempts"][str(number)] = dict(original, run_attempt=number,
                                               run_started_at=f"2026-10-13T02:{number:02}:00Z")
        aggregate = copy.deepcopy(source_jobs[1])
        aggregate.update(id=20 + number, run_attempt=number, conclusion="failure")
        aggregate["steps"][0]["conclusion"] = "failure"
        data["jobs_by_attempt"][str(number)] = [aggregate]
    latest = str(metadata_count + 1)
    data["runs"] = [copy.deepcopy(data["attempts"][latest])]
    data["jobs"] = data["jobs_by_attempt"][latest]
    data["history_jobs"] = [job for jobs in data["jobs_by_attempt"].values() for job in jobs]


@pytest.mark.parametrize("metadata_count", [1, 2])
def test_label_before_held_invalidation_survives_metadata_attempts_and_source_cap(tmp_path, metadata_count):
    data = aggregate_snapshot()
    data["pr"]["labels"] = [{"name": "run-e2e"}]
    data["timeline"] = [{"id": 100, "event": "labeled", "label": {"name": "run-e2e"},
                         "created_at": "2026-10-13T02:00:00Z"}]
    hold(data, "finding")
    _, invalidated = invoke(tmp_path, data, "resume")
    assert invalidated["mutations"] == [f"repos/{REPO}/actions/jobs/20/rerun"]
    attempt_history(data, metadata_count)
    data["threads"][0]["isResolved"] = True
    data["mutations"] = []
    result, cleared = invoke(tmp_path, data, "resume")
    assert result.returncode == 0, result.stdout + result.stderr
    assert cleared["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun"]
    # A real source retry reads the retained label, so subsequent reconciliation
    # must not repeat it even though metadata attempts previously advanced time.
    number = metadata_count + 2
    data["runs"][0].update(run_attempt=number, status="completed", run_started_at="2026-10-13T03:00:00Z")
    data["attempts"][str(number)] = copy.deepcopy(data["runs"][0])
    data["jobs_by_attempt"][str(number)] = copy.deepcopy(data["jobs_by_attempt"]["1"])
    data["jobs"] = data["jobs_by_attempt"][str(number)]
    data["history_jobs"] += data["jobs"]
    _, duplicate = invoke(tmp_path, data, "label")
    assert duplicate["mutations"] == []


def test_label_post_with_eventually_consistent_run_get_never_posts_aggregate_too(tmp_path):
    data = aggregate_snapshot("failure")
    data["pr"]["labels"] = [{"name": "run-e2e"}]
    data["timeline"] = [{"id": 100, "event": "labeled", "label": {"name": "run-e2e"},
                         "created_at": "2026-10-13T02:00:00Z"}]
    data["stale_after_post"] = True
    _, retried = invoke(tmp_path, data, "resume")
    assert retried["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun"]


@pytest.mark.parametrize("command", ["label", "resume"])
def test_partial_metadata_history_does_not_bypass_held_original_admission(tmp_path, command):
    data = aggregate_snapshot()
    attempt_history(data, 2)
    data["pr"]["labels"] = [{"name": "run-e2e"}]
    data["timeline"] = [{"id": 100, "event": "labeled", "label": {"name": "run-e2e"},
                         "created_at": "2026-10-13T02:00:00Z"}]
    hold(data, "finding")
    _, held = invoke(tmp_path, data, command)
    assert held["mutations"] == []


@pytest.mark.parametrize("patch", [{"extra_source": True}, {"workflow_mismatch": True},
                                   {"ambiguous": True}, {"platform_cap": True}, {"bad_attempt": True}])
def test_source_budget_never_exempts_unproven_or_over_limit_attempts(tmp_path, patch):
    data = aggregate_snapshot()
    attempt_history(data, 2)
    data["pr"]["labels"] = [{"name": "run-e2e"}]
    data["timeline"] = [{"id": 100, "event": "labeled", "label": {"name": "run-e2e"},
                         "created_at": "2026-10-13T02:00:00Z"}]
    if patch.get("extra_source"):
        for number in ("2", "3"):
            data["jobs_by_attempt"][number].append({"name": "Python tests", "conclusion": "success"})
    if patch.get("ambiguous"):
        for number in ("2", "3"):
            data["jobs_by_attempt"][number][0]["steps"] = []
    if patch.get("platform_cap"):
        data["runs"][0]["run_attempt"] = 51
    if patch.get("bad_attempt"):
        data["attempts"]["2"]["head_sha"] = OLD
    data["workflow_mismatch"] = patch.get("workflow_mismatch", False)
    _, held = invoke(tmp_path, data, "label")
    assert held["mutations"] == []


def test_full_admission_resumption_counts_source_attempts_independently(tmp_path):
    data = aggregate_snapshot()
    attempt_history(data, 2)
    source_jobs = copy.deepcopy(data["jobs_by_attempt"]["1"])
    source_jobs[0].update(id=40, conclusion="failure", run_attempt=4)
    source_jobs[1].update(id=50, conclusion="failure", run_attempt=4)
    source_jobs[1]["steps"][-1]["conclusion"] = "skipped"
    data["runs"][0].update(run_attempt=4, run_started_at="2026-10-13T03:00:00Z")
    data["attempts"]["4"] = copy.deepcopy(data["runs"][0])
    data["jobs_by_attempt"]["4"] = source_jobs
    data["jobs"] = source_jobs
    data["history_jobs"] += source_jobs
    result, retried = invoke(tmp_path, data, "resume")
    assert result.returncode == 0, result.stdout + result.stderr
    assert retried["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun"]
    data["runs"][0].update(run_attempt=5, status="completed", run_started_at="2026-10-13T04:00:00Z")
    data["attempts"]["5"] = copy.deepcopy(data["runs"][0])
    data["jobs_by_attempt"]["5"] = [dict(job, id=job["id"] + 100, run_attempt=5) for job in source_jobs]
    data["jobs"] = data["jobs_by_attempt"]["5"]
    data["history_jobs"] += data["jobs"]
    _, capped = invoke(tmp_path, data, "resume")
    assert capped["mutations"] == []
