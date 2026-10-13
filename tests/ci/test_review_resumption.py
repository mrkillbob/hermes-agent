"""Inert GitHub API contracts for exact-head review admission and resumption.

The child executes the production CLI; a fake gh transport serves recorded-shape
metadata and records requests. No GitHub writes, credentials or model calls occur.
"""

import copy
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_yaml import safe_load

ROOT = Path(__file__).resolve().parents[2]
HEAD = "a" * 40
OLD = "b" * 40
REPO = "mrkillbob/hermes-agent"
BOT = {"id": 199175422, "login": "chatgpt-codex-connector[bot]", "type": "Bot"}
ACTIVITY_FIXTURES = json.loads((ROOT / "tests/ci/fixtures/codex_review_activity.json").read_text(encoding="utf-8-sig"))


def activity_comment():
    comment = copy.deepcopy(ACTIVITY_FIXTURES[0]["comment"])
    comment["issue_url"] = f"https://api.github.com/repos/{REPO}/issues/8"
    comment["body"] = (comment["body"].replace(ACTIVITY_FIXTURES[0]["head"], HEAD)
                       .replace('"pullRequestNumber":225', '"pullRequestNumber":8')
                       .replace("`5cf69e0`", f"`{HEAD[:7]}`").replace("2026-10-11", "2026-10-13"))
    comment["created_at"] = "2026-10-13T01:24:56Z"
    comment["updated_at"] = "2026-10-13T01:28:04Z"
    return comment


def snapshot():
    return {
        "pr": {"number": 8, "state": "open", "head": {"sha": HEAD,
               "ref": "topic", "repo": {"full_name": REPO, "id": 42}},
               "base": {"repo": {"full_name": REPO}}, "requested_reviewers": [],
               "labels": [], "mergeable": False, "mergeStateStatus": "BLOCKED"},
        "reviews": [{"id": 9, "user": BOT, "state": "COMMENTED", "commit_id": HEAD,
                     "submitted_at": "2026-10-13T01:00:00Z", "body": "Codex Review\nHere are some automated review suggestions."}],
        "comments": [activity_comment()], "threads": [], "timeline": [], "runs": [], "jobs": [],
        "commits": {HEAD[:7]: HEAD},
        "reactions": [{"id": 1, "user": BOT, "content": "+1", "created_at": "2026-10-13T01:28:06Z"}],
        "mutations": [], "pr_reads": 0,
    }


def thread(number, resolved=False, priority="P2"):
    return {"id": str(number), "isResolved": resolved, "isOutdated": True,
            "comments": {"pageInfo": {"hasNextPage": False}, "nodes": [{
                "author": {"login": BOT["login"], "databaseId": BOT["id"]},
                "body": f"![{priority} Badge](https://img.shields.io/badge/{priority}-yellow)",
                "pullRequestReview": {"databaseId": 9},
            }]}}


def run_info():
    return {"id": 11, "event": "pull_request", "path": ".github/workflows/ci.yaml",
            "head_sha": HEAD, "head_repository": {"id": 42, "full_name": REPO},
            "pull_requests": [{"number": 8, "head": {"sha": HEAD}}],
            "status": "completed", "conclusion": "failure", "run_attempt": 1,
            "run_started_at": "2026-10-13T00:00:00Z"}


FAKE_GH = r'''
import json, pathlib, sys
state_file = pathlib.Path(__file__).with_name("state.json")
data = json.loads(state_file.read_text())
args = sys.argv[1:]
if args[0] in ("run", "pr", "workflow"):
    if args[:2] == ["run", "list"]:
        answer = "11 completed" if data["runs"] else ""
    elif args[:2] == ["run", "view"]:
        answer = str(len(data["jobs"])) if "jobs" in args else "completed"
    elif args[:2] == ["pr", "view"]:
        answer = data["pr"]["head"]["sha"]
    elif args[:2] == ["run", "rerun"]:
        data["mutations"].append("failed-only" if "--failed" in args else "full")
        answer = ""
    else:
        raise AssertionError(f"Forbidden legacy action: {args}")
    state_file.write_text(json.dumps(data))
    print(answer)
    sys.exit(0)
route = args[1]
path = route.split("?")[0]
if data.get("api_error"):
    sys.exit(1)
if data.get("review_api_error") and path.endswith("/reviews"):
    sys.exit(1)
if "--method" in args:
    assert args[args.index("--method") + 1] == "POST", args
    assert path.endswith(("/rerun", "/rerun-failed-jobs")), path
    if data.get("rerun_denied"):
        sys.exit(1)
    data["mutations"].append(path)
    data["runs"][0]["status"] = "queued"
    if data.get("stale_after_post"):
        data["runs"][0]["status"] = "completed"
    if "/actions/jobs/" in path:
        for job in data["jobs"]:
            if str(job.get("id")) == path.split("/actions/jobs/", 1)[1].split("/", 1)[0]:
                job.update(status="queued", conclusion=None)
    result = {}
elif path == "graphql":
    request = json.load(sys.stdin)
    assert request["variables"]["number"] == data["pr"]["number"]
    result = {"data": {"repository": {"pullRequest": {
        "headRefOid": data["pr"]["head"]["sha"],
        "reviewThreads": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                          "nodes": data["threads"]}}}}}
elif path.endswith(f"/pulls/{data['pr']['number']}"):
    data["pr_reads"] += 1
    if data.get("clear_after_read") and data["pr_reads"] >= data["clear_after_read"]:
        data["threads"] = []
    result = data["pr"]
    if data.get("move_after_read") and data["pr_reads"] >= data["move_after_read"]:
        result = dict(result, head=dict(result["head"], sha="b" * 40))
elif path.endswith(f"/pulls/{data['pr']['number']}/reviews"):
    result = data["reviews"]
elif path.endswith(f"/issues/{data['pr']['number']}/comments"):
    data["comment_reads"] = data.get("comment_reads", 0) + 1
    result = data["comments"]
    if data.get("running_after_read") and data["comment_reads"] > 1:
        result = [dict(c, body=c["body"].replace("✅ **Completed**", "🔄 **Running** since", 1)) for c in result]
elif path.endswith(f"/issues/{data['pr']['number']}/reactions"):
    result = data["reactions"]
elif "/commits/" in path:
    if data.get("commit_error"):
        sys.exit(1)
    result = data.get("runtime_commit") if "/git/commits/" in path else {"sha": data["commits"][path.rsplit("/", 1)[-1]]}
elif path.endswith(f"/issues/{data['pr']['number']}/timeline"):
    result = data["timeline"]
elif path.endswith("/pulls"):
    result = [data["pr"]]
elif path.endswith("/contents/.github/workflows/ci.yaml"):
    if data.get("definition_api_error"):
        sys.exit(1)
    result = {"type": "file", "path": ".github/workflows/ci.yaml", "sha": "c" * 40}
    if data.get("workflow_mismatch") and not route.endswith("ref=main"):
        result["sha"] = "d" * 40
elif path == "repos/mrkillbob/hermes-agent":
    result = {"default_branch": "main"}
elif "/actions/jobs/" in path:
    result = next(job for job in data["jobs"] if str(job.get("id")) == path.rsplit("/", 1)[-1])
    result = dict(result, **data.get("actual_job_patch", {}))
    for key in data.get("actual_job_omit", []):
        result.pop(key, None)
elif "/actions/runs/" in path and path.endswith("/jobs"):
    jobs = data["jobs"] if path.endswith("/11/jobs") else []
    if "/attempts/" in path:
        attempt_number = path.split("/attempts/", 1)[1].split("/", 1)[0]
        jobs = data.get("jobs_by_attempt", {}).get(attempt_number, data.get("attempt_jobs", data["jobs"]))
    elif "filter=all" in route:
        jobs = data.get("history_jobs", data["jobs"])
    result = {"jobs": jobs, "total_count": len(jobs)}
elif path.endswith("/actions/runs/11"):
    result = data["runs"][0]
elif "/actions/runs/11/attempts/" in path:
    attempt_number = path.rsplit("/", 1)[-1]
    result = data.get("attempts", {}).get(attempt_number, data.get("attempt", data["runs"][0]))
elif "/actions/workflows/" in path:
    data["run_reads"] = data.get("run_reads", 0) + 1
    workflow = path.split("/actions/workflows/", 1)[1].split("/", 1)[0]
    matching = data.get("workflow_runs", {}).get(workflow, data["runs"] if workflow == "ci.yaml" else [])
    result = {"workflow_runs": matching, "total_count": len(matching)}
    if data.get("newer_run") and data["run_reads"] > 1 and path.endswith("ci.yaml/runs"):
        result = {"workflow_runs": [dict(data["runs"][0], id=12, status="in_progress")], "total_count": 1}
else:
    raise AssertionError(f"Unexpected API request: {args}")
state_file.write_text(json.dumps(data))
print(json.dumps(result))
'''


def transport(tmp_path, data):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(data))
    (tmp_path / "fake_gh.py").write_text(FAKE_GH)
    gh = tmp_path / "gh"
    gh.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(tmp_path / 'fake_gh.py'))} \"$@\"\n")
    gh.chmod(0o700)
    environment = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"}
    return state, environment


def invoke(tmp_path, data, command="check", event=None):
    state, environment = transport(tmp_path, data)
    module = {"check": "review_admission", "resume": "review_resumption", "label": "label_resumption"}[command]
    arguments = ["--repo", REPO, "--pr", str(data["pr"]["number"]), "--head", data["pr"]["head"]["sha"]]
    if event is not None:
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(event))
        environment.update(GITHUB_EVENT_PATH=str(event_file), GITHUB_OUTPUT=str(tmp_path / "outputs"),
                           GITHUB_EVENT_NAME=event["event_name"])
        arguments = ["--repo", REPO, "--discover"]
    result = subprocess.run(
        [sys.executable, "-m", f"scripts.ci.{module}", *arguments],
        cwd=ROOT, capture_output=True, text=True, env=environment, timeout=30,
        check=False,
    )
    return result, json.loads(state.read_text())


def verdict(result):
    assert result.stdout.strip(), result.stderr
    return json.loads(result.stdout.splitlines()[0])["state"]


def test_bound_completion_admits_even_when_github_is_blocked(tmp_path):
    result, data = invoke(tmp_path, snapshot())
    assert result.returncode == 0, result.stderr
    assert verdict(result) == "clean"
    assert data["mutations"] == []


@pytest.mark.parametrize("patch", [
    {"reviews": []},  # A thumbs-up on the PR has no reviewed SHA.
    {"review": {"commit_id": OLD}},
    {"review": {"user": {**BOT, "id": 123}}},
    {"pr": {"requested_reviewers": [BOT]}},
    {"comments": [{"body": "@codex security review", "created_at": "2026-10-13T00:30:00Z"}]},
])
def test_pending_or_unbound_review_cannot_admit(tmp_path, patch):
    data = snapshot()
    data["comments"] = []  # Submitted reviews alone cannot clear both required public passes.
    data["reviews"][0].update(patch.get("review", {}))
    data["pr"].update(patch.get("pr", {}))
    data.update({key: value for key, value in patch.items() if key not in ("pr", "review")})
    result, data = invoke(tmp_path, data)
    assert verdict(result) == "pending"
    assert result.returncode == 1
    assert data["mutations"] == []


def test_resolution_rechecks_every_blocker_including_outdated_threads(tmp_path):
    data = snapshot()
    data["threads"] = [thread(1, resolved=True), thread(2)]
    result, _ = invoke(tmp_path, data)
    assert verdict(result) == "blocked"
    data["threads"][1]["isResolved"] = True
    result, _ = invoke(tmp_path, data)
    assert verdict(result) == "clean"


def test_unrecognized_canonical_findings_hold_instead_of_disappearing(tmp_path):
    data = snapshot()
    data["threads"] = [thread(1, priority="unknown")]
    result, _ = invoke(tmp_path, data)
    assert verdict(result) == "unknown"


def test_standalone_review_finding_cannot_appear_clean_without_threads(tmp_path):
    data = snapshot()
    data["reviews"][0]["body"] = "Codex Review\n[P1] Actionable security finding"
    result, data = invoke(tmp_path, data)
    assert verdict(result) == "blocked"
    assert data["mutations"] == []


def test_dismissed_latest_review_does_not_revive_older_completion(tmp_path):
    data = snapshot()
    data["reviews"].append({**data["reviews"][0], "id": 10, "state": "DISMISSED",
                            "submitted_at": "2026-10-13T02:00:00Z"})
    result, data = invoke(tmp_path, data)
    assert verdict(result) == "pending"
    assert data["mutations"] == []


def test_same_second_latest_review_uses_immutable_id(tmp_path):
    data = snapshot()
    data["reviews"].append({**data["reviews"][0], "id": 10, "state": "DISMISSED"})
    process, data = invoke(tmp_path, data)
    assert verdict(process) == "pending"
    assert data["mutations"] == []


@pytest.mark.parametrize("first_kind,last_kind,review_request,state", [
    ("Security Review", "Codex Review", "@codex security review", "DISMISSED"),
    ("Security Review", "Codex Review", "@codex security review", "CHANGES_REQUESTED"),
    ("Codex Review", "Security Review", "@codex review", "CHANGES_REQUESTED"),
])
def test_later_different_pass_cannot_clear_its_blocked_sibling(tmp_path, first_kind, last_kind, review_request, state):
    data = snapshot()
    template = data["reviews"][0]
    data["reviews"] = [dict(template, body=first_kind),
                       dict(template, id=10, body=first_kind, state=state,
                            submitted_at="2026-10-13T02:00:00Z"),
                       dict(template, id=11, body=last_kind, submitted_at="2026-10-13T03:00:00Z")]
    data["comments"] = [{"body": review_request, "created_at": "2026-10-13T00:30:00Z"}]
    process, data = invoke(tmp_path, data)
    assert verdict(process) == ("pending" if state == "DISMISSED" else "blocked")
    assert data["mutations"] == []


def test_security_completion_cannot_complete_a_later_code_request(tmp_path):
    data = snapshot()
    data["reviews"].append(dict(data["reviews"][0], id=10, body="Security Review",
                                submitted_at="2026-10-13T03:00:00Z"))
    data["comments"] = [{"body": "@codex review", "created_at": "2026-10-13T02:00:00Z"}]
    process, data = invoke(tmp_path, data)
    assert verdict(process) == "pending"
    assert data["mutations"] == []


def test_unrecognized_review_completion_format_is_unknown(tmp_path):
    data = snapshot()
    data["reviews"][0]["body"] = "Unable to review: quota exhausted"
    process, data = invoke(tmp_path, data)
    assert verdict(process) == "unknown"
    assert data["mutations"] == []


def test_pending_to_clean_resumes_held_run_once(tmp_path):
    data = snapshot()
    data["runs"] = [run_info()]
    data["jobs"] = [{"name": "Review admission", "conclusion": "failure"}]
    pending = copy.deepcopy(data)
    pending["reviews"] = []
    pending["comments"] = []
    _, held = invoke(tmp_path, pending, "resume")
    assert held["mutations"] == []
    result, resumed = invoke(tmp_path, data, "resume")
    assert result.returncode == 0, result.stderr
    assert resumed["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun"]
    _, duplicate = invoke(tmp_path, resumed, "resume")
    assert duplicate["mutations"] == resumed["mutations"]


def test_finding_resolution_resumes_after_last_blocker_only(tmp_path):
    data = snapshot()
    data["runs"] = [run_info()]
    data["jobs"] = [{"name": "Review admission", "conclusion": "failure"}]
    data["threads"] = [thread(1, resolved=True), thread(2)]
    _, blocked = invoke(tmp_path, data, "resume")
    assert blocked["mutations"] == []
    data["threads"][1]["isResolved"] = True
    _, resumed = invoke(tmp_path, data, "resume")
    assert resumed["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun"]


def test_controller_preserves_independent_sensitive_label_authority(tmp_path):
    data = snapshot()
    data["runs"] = [run_info()]
    data["jobs"] = [{"name": "Review label gate / Review label gate", "conclusion": "failure"}]
    _, unapproved = invoke(tmp_path, data, "resume")
    assert unapproved["mutations"] == []
    data["pr"]["labels"] = [{"name": "ci-reviewed"}]
    data["timeline"] = [{"id": 100, "event": "labeled", "label": {"name": "ci-reviewed"},
                         "created_at": "2026-10-13T02:00:00Z"}]
    _, approved = invoke(tmp_path, data, "resume")
    assert approved["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun-failed-jobs"]
    assert approved["pr"]["labels"] == data["pr"]["labels"]


@pytest.mark.parametrize("patch", [
    {"run": {"status": "in_progress"}},
    {"jobs": [{"name": "Python tests", "conclusion": "failure"}]},
    {"jobs": []}, {"runs": []},
    {"run": {"head_sha": OLD}},
    {"run": {"pull_requests": [{"number": 99, "head": {"sha": HEAD}}]}},
    {"run": {"run_attempt": 3}},
    {"run": {"head_repository": {"id": 99, "full_name": "another/fork"}}},
])
def test_ineligible_run_never_dispatches_or_cancels(tmp_path, patch):
    data = snapshot()
    data["runs"] = [run_info()]
    data["jobs"] = [{"name": "Review admission", "conclusion": "failure"}]
    data["runs"][0].update(patch.get("run", {}))
    data.update({key: value for key, value in patch.items() if key != "run"})
    process, result = invoke(tmp_path, data, "resume")
    assert process.returncode == 0, process.stderr
    assert result["mutations"] == []


def test_head_movement_and_failed_metadata_stop_writes(tmp_path):
    data = snapshot()
    data["runs"] = [run_info()]
    data["jobs"] = [{"name": "Review admission", "conclusion": "failure"}]
    data["move_after_read"] = 2
    _, moved = invoke(tmp_path, data, "resume")
    assert moved["mutations"] == []
    data["api_error"] = True
    result, failed = invoke(tmp_path, data, "resume")
    assert result.returncode == 1
    assert failed["mutations"] == []


def test_new_healthy_run_before_retry_leaves_old_run_alone(tmp_path):
    data = snapshot()
    data["runs"] = [run_info()]
    data["jobs"] = [{"name": "Review admission", "conclusion": "failure"}]
    data["newer_run"] = True
    result, data = invoke(tmp_path, data, "resume")
    assert result.returncode == 0, result.stderr
    assert data["mutations"] == []


@pytest.mark.parametrize("event", [
    {"event_name": "schedule"},
    {"event_name": "workflow_run", "workflow_run": {"event": "pull_request_review", "head_sha": HEAD,
                                                     "pull_requests": [{"number": 8, "head": {"sha": HEAD}}]}},
])
def test_native_and_scheduled_discovery_use_exact_pr_identity(tmp_path, event):
    process, data = invoke(tmp_path, snapshot(), "resume", event)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {"include": [{"number": 8, "head": HEAD}]}
    assert data["mutations"] == []


def test_unbound_native_event_waits_for_scheduled_reconciliation(tmp_path):
    event = {"event_name": "workflow_run", "workflow_run": {
        "event": "pull_request_review", "head_sha": HEAD, "pull_requests": []}}
    process, data = invoke(tmp_path, snapshot(), "resume", event)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {"include": []}
    assert data["mutations"] == []


def test_controller_reconciles_through_shared_label_owner_without_duplicates(tmp_path):
    data = snapshot()
    data["runs"] = [run_info()]
    data["jobs"] = [{"name": "Python tests", "conclusion": "success"}]
    data["pr"]["labels"] = [{"name": "run-e2e"}]
    data["timeline"] = [{"id": 100, "event": "labeled", "label": {"name": "run-e2e"},
                         "created_at": "2026-10-13T02:00:00Z"}]
    process, resumed = invoke(tmp_path, data, "resume")
    assert process.returncode == 0, process.stderr
    assert resumed["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun"]
    data["runs"][0]["run_started_at"] = "2026-10-13T02:01:00Z"
    process, classified = invoke(tmp_path, data, "resume")
    assert process.returncode == 0, process.stderr
    assert classified["mutations"] == []


def test_incomplete_thread_pagination_never_admits(tmp_path):
    data = snapshot()
    data["threads"] = [thread(1, resolved=True)]
    data["threads"][0]["comments"]["pageInfo"]["hasNextPage"] = True
    process, data = invoke(tmp_path, data)
    assert verdict(process) == "unknown"
    assert data["mutations"] == []


@pytest.mark.parametrize("jobs,code,mutations", [
    ([{"name": "Review label gate", "conclusion": "failure"}], 0,
     [f"repos/{REPO}/actions/runs/11/rerun-failed-jobs"]),
    ([], 1, []),
])
def test_maintainer_label_retry_survives_without_inventing_a_dispatch(tmp_path, jobs, code, mutations):
    data = label_snapshot()
    data["pr"]["labels"][0]["name"] = "ci-reviewed"
    data["timeline"][0]["label"]["name"] = "ci-reviewed"
    data["comments"] = []  # Bootstrap label approval never invents substantive completion.
    data["reviews"] = []
    data["jobs"] = jobs
    process, result = invoke(tmp_path, data, "label")
    assert process.returncode == code, process.stdout + process.stderr
    assert result["mutations"] == mutations


def test_actual_completed_both_passes_and_fresh_clean_witness_admit_without_review_submission(tmp_path):
    fixture = ACTIVITY_FIXTURES[0]
    data = snapshot()
    data["pr"]["number"] = fixture["pr"]
    data["pr"]["head"]["sha"] = fixture["head"]
    data.update(comments=[fixture["comment"]], reviews=[], reactions=fixture["reactions"],
                commits={fixture["abbreviation"]: fixture["head"]})
    process, data = invoke(tmp_path, data)
    assert verdict(process) == "clean", process.stdout
    assert data["mutations"] == []


def test_actual_running_activity_holds_with_canonical_eyes(tmp_path):
    fixture = ACTIVITY_FIXTURES[-1]
    data = snapshot()
    data["pr"]["number"] = fixture["pr"]
    data["pr"]["head"]["sha"] = fixture["head"]
    data.update(comments=[fixture["comment"]], reviews=[], reactions=fixture["reactions"],
                commits={fixture["abbreviation"]: fixture["head"]})
    process, data = invoke(tmp_path, data)
    assert verdict(process) == "pending"
    assert data["mutations"] == []


@pytest.mark.parametrize("patch", [
    {"reactions": []},
    {"reactions": [{"user": BOT, "content": "+1", "created_at": "2026-10-13T01:28:03Z"}]},
    {"reactions": [{"user": {"id": 123}, "content": "+1", "created_at": "2026-10-13T01:28:06Z"}]},
    {"reactions": [{"user": BOT, "content": "eyes", "created_at": "2026-10-13T01:00:00Z"}]},
])
def test_completion_alone_or_stale_impostor_witness_cannot_clear(tmp_path, patch):
    data = snapshot()
    data.update(patch)
    process, data = invoke(tmp_path, data)
    assert verdict(process) == "pending"
    assert data["mutations"] == []


@pytest.mark.parametrize("change", ["missing_security_row", "missing_security_json", "wrong_repo", "wrong_pr", "wrong_head"])
def test_both_required_passes_and_security_identity_are_enforced(tmp_path, change):
    data = snapshot()
    body = data["comments"][0]["body"]
    changes = {
        "missing_security_row": lambda text: "\n".join(line for line in text.splitlines() if not line.startswith("| 🔒")),
        "missing_security_json": lambda text: "\n".join(line for line in text.splitlines() if not line.startswith("<!-- codex-security-review:")),
        "wrong_repo": lambda text: text.replace('"repository":"mrkillbob/hermes-agent"', '"repository":"other/repo"'),
        "wrong_pr": lambda text: text.replace('"pullRequestNumber":8', '"pullRequestNumber":9'),
        "wrong_head": lambda text: text.replace(f'"headSha":"{HEAD}"', f'"headSha":"{OLD}"'),
    }
    data["comments"][0]["body"] = changes[change](body)
    process, data = invoke(tmp_path, data)
    assert verdict(process) != "clean"
    assert data["mutations"] == []


@pytest.mark.parametrize("resolution", ["wrong", "ambiguous"])
def test_abbreviation_must_resolve_via_api_to_the_exact_live_head(tmp_path, resolution):
    data = snapshot()
    data["commits"] = {HEAD[:7]: OLD}
    data["commit_error"] = resolution == "ambiguous"
    process, data = invoke(tmp_path, data)
    assert verdict(process) != "clean"
    assert data["mutations"] == []


def test_new_running_transition_before_retry_stops_write(tmp_path):
    data = snapshot()
    data["runs"] = [run_info()]
    data["jobs"] = [{"name": "Review admission", "conclusion": "failure"}]
    data["running_after_read"] = True
    process, data = invoke(tmp_path, data, "resume")
    assert process.returncode == 0, process.stderr
    assert data["comment_reads"] >= 2
    assert data["mutations"] == []


def test_canonical_summary_footer_is_not_an_explicit_review_request(tmp_path):
    data = snapshot()
    assert '@codex security review' in data["comments"][0]["body"]
    process, data = invoke(tmp_path, data)
    assert verdict(process) == "clean"
    assert data["mutations"] == []


@pytest.mark.parametrize("user_id,count", [(199175422, 1), (123, 0)])
def test_issue_comment_wakeup_uses_canonical_producer_and_live_pr_head(tmp_path, user_id, count):
    data = snapshot()
    event = {"event_name": "issue_comment", "issue": {"number": 8,
             "pull_request": {"url": f"https://api.github.com/repos/{REPO}/pulls/8"}},
             "comment": {**data["comments"][0], "user": {"id": user_id}}}
    process, data = invoke(tmp_path, data, "resume", event)
    assert process.returncode == 0, process.stdout + process.stderr
    assert json.loads(process.stdout) == {"include": [{"number": 8, "head": HEAD}] if count else []}
    assert data["mutations"] == []


@pytest.mark.parametrize("patch", [{"performed_via_github_app": None}, {"user": None},
                                   {"performed_via_github_app": {"id": 123}}])
def test_unknown_producer_identity_never_throws_or_clears(tmp_path, patch):
    data = snapshot()
    data["comments"][0].update(patch)
    process, data = invoke(tmp_path, data)
    assert verdict(process) in ("unknown", "pending")
    assert data["mutations"] == []


@pytest.mark.parametrize("replacement", ["❌ **Failed**", "✅ **Unknown**"])
def test_failed_or_unrecognized_activity_state_cannot_clear(tmp_path, replacement):
    data = snapshot()
    data["comments"][0]["body"] = data["comments"][0]["body"].replace("✅ **Completed**", replacement, 1)
    process, data = invoke(tmp_path, data)
    assert verdict(process) == "unknown"
    assert data["mutations"] == []


def test_fresh_clean_witness_cannot_override_active_canonical_eyes(tmp_path):
    data = snapshot()
    data["reactions"].append({"user": BOT, "content": "eyes", "created_at": "2026-10-13T01:29:00Z"})
    process, data = invoke(tmp_path, data)
    assert verdict(process) == "pending"
    assert data["mutations"] == []


@pytest.mark.parametrize("priority", ["P0", "P1", "P2"])
def test_canonical_standalone_issue_finding_blocks_even_with_fresh_clean_witness(tmp_path, priority):
    data = snapshot()
    data["comments"].append({"id": 100, "user": BOT, "body": f"[{priority}] Actionable security finding",
                             "created_at": "2026-10-13T01:00:00Z", "updated_at": "2026-10-13T01:00:00Z"})
    process, data = invoke(tmp_path, data)
    assert verdict(process) == "blocked"
    assert data["mutations"] == []


def test_prior_head_standalone_review_finding_does_not_disappear_on_push(tmp_path):
    data = snapshot()
    data["reviews"].append({**data["reviews"][0], "id": 10, "commit_id": OLD,
                            "body": "Codex Review\n[P1] Actionable earlier-head finding"})
    process, data = invoke(tmp_path, data)
    assert verdict(process) == "blocked"
    assert data["mutations"] == []


def label_snapshot():
    data = snapshot()
    data["pr"]["labels"] = [{"name": "run-e2e"}]
    data["timeline"] = [{"id": 100, "event": "labeled", "label": {"name": "run-e2e"},
                         "created_at": "2026-10-13T00:10:00Z"}]
    data["runs"] = [run_info()]
    data["jobs"] = [{"name": "Python tests", "conclusion": "success"}]
    return data


def test_long_running_label_event_gets_one_eventual_completed_retry(tmp_path):
    data = label_snapshot()
    data["runs"][0]["status"] = "in_progress"
    process, active = invoke(tmp_path, data, "label")
    assert process.returncode == 0, process.stdout + process.stderr
    assert active["mutations"] == []
    active["runs"][0]["status"] = "completed"
    process, retried = invoke(tmp_path, active, "label")
    assert process.returncode == 0, process.stdout + process.stderr
    assert retried["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun"]
    retried["runs"][0].update(status="completed", run_attempt=2)
    retried["attempt"] = {**retried["runs"][0], "run_started_at": "2026-10-13T00:20:00Z"}
    process, duplicate = invoke(tmp_path, retried, "label")
    assert process.returncode == 0, process.stdout + process.stderr
    assert duplicate["mutations"] == retried["mutations"]


def test_review_label_completed_event_compensates_and_never_duplicates(tmp_path):
    data = label_snapshot()
    data["pr"]["labels"][0]["name"] = "ci-reviewed"
    data["timeline"][0]["label"]["name"] = "ci-reviewed"
    data["jobs"] = [{"name": "Review label gate / Review label gate", "conclusion": "failure"}]
    process, retried = invoke(tmp_path, data, "label")
    assert process.returncode == 0, process.stdout + process.stderr
    assert retried["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun-failed-jobs"]
    retried["runs"][0].update(status="completed", run_attempt=2)
    retried["attempt"] = {**retried["runs"][0], "run_started_at": "2026-10-13T00:20:00Z"}
    process, duplicate = invoke(tmp_path, retried, "label")
    assert process.returncode == 0, process.stdout + process.stderr
    assert duplicate["mutations"] == retried["mutations"]


def test_held_review_consumer_recovers_replaced_pending_label_event(tmp_path):
    data = label_snapshot()
    data["comments"] = []  # The survivor's substantive admission remains held.
    data["pr"]["labels"][0]["name"] = "ci-reviewed"
    data["timeline"][0]["label"]["name"] = "ci-reviewed"
    data["jobs"] = [{"name": "Review label gate / Review label gate", "conclusion": "failure"}]
    process, recovered = invoke(tmp_path, data, "resume")
    assert process.returncode == 0, process.stdout + process.stderr
    assert recovered["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun-failed-jobs"]
    process, _ = invoke(tmp_path, data)
    assert verdict(process) == "pending"
    recovered["runs"][0].update(status="completed", run_attempt=2)
    recovered["attempt"] = {**recovered["runs"][0], "run_started_at": "2026-10-13T00:20:00Z"}
    process, duplicate = invoke(tmp_path, recovered, "resume")
    assert process.returncode == 0, process.stdout + process.stderr
    assert duplicate["mutations"] == recovered["mutations"]


@pytest.mark.parametrize("patch", [
    {"pr": {"labels": []}}, {"pr": {"head": {"sha": OLD, "ref": "new", "repo": {"full_name": REPO, "id": 42}}}},
    {"run": {"pull_requests": []}}, {"run": {"head_sha": OLD}}, {"run": {"run_attempt": 3}},
    {"jobs": []}, {"newer_run": True}, {"runs": []},
])
def test_label_completion_reconciliation_does_not_revive_ineligible_work(tmp_path, patch):
    data = label_snapshot()
    data["pr"].update(patch.get("pr", {}))
    data["runs"][0].update(patch.get("run", {}))
    data.update({key: value for key, value in patch.items() if key not in ("pr", "run")})
    process, data = invoke(tmp_path, data, "label")
    assert data["mutations"] == [], process.stdout + process.stderr


def test_label_and_review_retry_writers_share_the_same_per_pr_concurrency_key():
    label = safe_load((ROOT / ".github/workflows/label-rerun.yml").read_text(encoding="utf-8-sig"))
    review = safe_load((ROOT / ".github/workflows/review-resumption.yml").read_text(encoding="utf-8-sig"))
    label_group = label["concurrency"]["group"]
    review_group = review["jobs"]["resume"]["concurrency"]["group"]
    shared_prefix = review_group.split("${{ matrix.number }}")[0]
    assert label_group.startswith(shared_prefix)
    assert "github.event.pull_request.number" in label_group
    assert "github.event.workflow_run.pull_requests[0].number" in label_group
    assert label["concurrency"]["cancel-in-progress"] is False


def test_gated_run_label_waits_for_both_reviews_without_consuming_retry_cap(tmp_path):
    data = label_snapshot()
    data["jobs"].append({"name": "Review admission", "conclusion": "failure"})
    data["comments"] = []
    process, held = invoke(tmp_path, data, "label")
    assert process.returncode == 0, process.stdout + process.stderr
    assert held["mutations"] == []
    held["comments"] = [activity_comment()]
    process, resumed = invoke(tmp_path, held, "label")
    assert process.returncode == 0, process.stdout + process.stderr
    assert resumed["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun"]


def test_public_clean_wrapper_completion_resumes_full_pr_run(tmp_path):
    data = snapshot()
    data["runs"] = [dict(run_info(), path=".github/workflows/docker-pr.yml")]
    data["workflow_runs"] = {"docker-pr.yml": data["runs"]}
    data["jobs"] = [{"name": "Review admission", "conclusion": "failure"}]
    process, resumed = invoke(tmp_path, data, "resume")
    assert process.returncode == 0, process.stdout + process.stderr
    assert resumed["mutations"] == [f"repos/{REPO}/actions/runs/11/rerun"]


@pytest.mark.parametrize("command", ["label", "resume"])
def test_wrapper_replacement_prevents_reviving_old_alias_run(tmp_path, command):
    data = label_snapshot()
    data["runs"] = [dict(run_info(), path=".github/workflows/docker.yml")]
    replacement = dict(data["runs"][0], id=12, path=".github/workflows/docker-pr.yml", status="in_progress")
    data["workflow_runs"] = {"docker.yml": data["runs"], "docker-pr.yml": [replacement]}
    data["jobs"] = [{"name": "Review admission", "conclusion": "failure"}]
    process, quiet = invoke(tmp_path, data, command)
    assert process.returncode == 0, process.stdout + process.stderr
    assert quiet["mutations"] == []
