"""Refresh the required CI result without rerunning successful source lanes."""

from __future__ import annotations

import re
import subprocess
from urllib.parse import urlencode

from scripts.ci.review_admission import GitHub, MetadataError, inspect, result

AGGREGATE = "All required checks pass"
LIVE_STEPS = frozenset(("Live review admission before aggregation", "Live review admission before success"))
MAX_METADATA_ATTEMPT = 51  # GitHub permits 50 reruns, including subset reruns.
WORKFLOW_PATH = ".github/workflows/ci.yaml"


def review_state(api: GitHub, number: int, head: str) -> tuple[dict, dict]:
    try:
        return inspect(api, number, head)
    except (MetadataError, KeyError, TypeError, ValueError, OSError, subprocess.TimeoutExpired):
        # Incomplete review reads may invalidate green, but cannot authorize
        # source retries. A separate successful PR identity read is mandatory.
        pr = api.pr(number)
        if (pr["number"] != number or pr["base"]["repo"]["full_name"] != api.repo
                or pr["state"] != "open" or pr["head"]["sha"] != head):
            raise MetadataError("PR closed, identity mismatched or head superseded")
        return result("unknown", "Review metadata unavailable or incomplete", head), pr


def qualified_job(job: dict, run: dict) -> bool:
    return (isinstance(job.get("id"), int) and job["id"] > 0
            and job.get("run_id") == run["id"] and job.get("head_sha") == run["head_sha"]
            and job.get("status") == "completed")


def same_execution(actual: dict, listed: dict, run: dict) -> bool:
    keys = ("id", "run_id", "head_sha", "name", "status", "conclusion")
    if (not qualified_job(actual, run) or any(actual[key] != listed[key] for key in keys)
            or actual.get("run_attempt", run["run_attempt"]) != run["run_attempt"]):
        return False
    # GET/list may differ in optional timestamps, runner fields or run_attempt.
    # Every execution-relevant step must still have the same state.
    def states(job):
        return sorted((step["name"], step["status"], step["conclusion"]) for step in job["steps"])
    return states(actual) == states(listed)


def target(api: GitHub, run: dict, verdict: dict) -> dict | None:
    if run["status"] not in ("completed", "in_progress") or run["run_attempt"] >= MAX_METADATA_ATTEMPT:
        return None
    # Subset retries need not repeat admission. The attempt endpoint proves
    # which immutable aggregate ID belongs to the current execution.
    jobs = api.pages(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs")
    aggregates = [job for job in jobs if job["name"] == AGGREGATE]
    if len(aggregates) != 1 or not qualified_job(aggregates[0], run):
        return None
    job = aggregates[0]
    if job.get("run_attempt", run["run_attempt"]) != run["run_attempt"]:
        return None
    steps = {step["name"]: step for step in job["steps"]}
    if not LIVE_STEPS <= steps.keys():
        return None  # Older workflow definitions cannot enforce a live hold.
    history = api.pages(f"actions/runs/{run['id']}/jobs", "jobs", filter="all")
    admissions = [entry for entry in history if entry["name"] == "Review admission"]
    if not admissions:
        return None
    admission = max(admissions, key=lambda entry: entry["id"])
    if not qualified_job(admission, run) or admission["conclusion"] != "success":
        return None
    if verdict["state"] != "clean":
        return job if job["conclusion"] == "success" else None
    failed = {name for name, step in steps.items() if step["conclusion"] == "failure"}
    source_passed = steps.get("Evaluate job results", {}).get("conclusion") == "success"
    return job if job["conclusion"] == "failure" and source_passed and failed and failed <= LIVE_STEPS else None


def runtime_revision(api: GitHub, run: dict, pr: dict) -> str:
    references = run["referenced_workflows"]
    if not references:
        raise MetadataError("Original workflow revision unavailable")
    revisions = set()
    for item in references:
        sha = item["sha"]
        if (not re.fullmatch(r"[0-9a-f]{40}", sha)
                or item["ref"] != f"refs/pull/{pr['number']}/merge"
                or not item["path"].startswith(f"{api.repo}/.github/workflows/")
                or not item["path"].endswith(f"@{sha}")):
            raise MetadataError("Unqualified workflow runtime reference")
        revisions.add(sha)
    if len(revisions) != 1:
        raise MetadataError("Ambiguous workflow runtime revision")
    sha = revisions.pop()
    associations = [item for item in run["pull_requests"] if item["number"] == pr["number"]]
    if len(associations) != 1:
        raise MetadataError("Original PR association unavailable")
    base = associations[0]["base"]["sha"]
    if (not re.fullmatch(r"[0-9a-f]{40}", base)
            or associations[0]["base"]["repo"]["url"] != f"https://api.github.com/repos/{api.repo}"
            or associations[0]["url"] != f"https://api.github.com/repos/{api.repo}/pulls/{pr['number']}"):
        raise MetadataError("Original base revision or repository unavailable")
    commit = api.request(f"repos/{api.repo}/git/commits/{sha}")
    if (commit["sha"] != sha or len(commit["parents"]) != 2
            or {item["sha"] for item in commit["parents"]} != {base, pr["head"]["sha"]}):
        raise MetadataError("Runtime merge revision does not match original PR parents")
    return sha


def trusted_definition(api: GitHub, run: dict, pr: dict) -> bool:
    revision = runtime_revision(api, run, pr)
    branch = api.request(f"repos/{api.repo}")["default_branch"]
    blobs = []
    for ref in (branch, revision):
        blob = api.request(f"repos/{api.repo}/contents/{WORKFLOW_PATH}?{urlencode({'ref': ref})}")
        if (blob["type"] != "file" or blob["path"] != WORKFLOW_PATH
                or not re.fullmatch(r"[0-9a-f]{40}", blob["sha"])):
            raise MetadataError("CI workflow definition identity unavailable")
        blobs.append(blob["sha"])
    # Compare the entire workflow, including ALL aggregate dependents. Matching
    # names/steps alone cannot prove that a PR-controlled rerun is metadata-only.
    return blobs[0] == blobs[1]


def resume(api: GitHub, number: int, head: str, verdict: dict, pr: dict) -> list[dict]:
    from scripts.ci.review_resumption import latest_run, matches

    selected = latest_run(api, pr, ("ci.yaml",))
    if selected is None:
        return []
    run, workflow = selected
    job = target(api, run, verdict)
    if job is None:
        return []
    if not trusted_definition(api, run, pr):
        return [{"run": run["id"], "action": "hold", "reason": "CI definition differs from trusted default branch"}]
    latest = latest_run(api, pr, ("ci.yaml",))
    if latest is None or latest[0]["id"] != run["id"]:
        return []
    live_run = api.request(f"repos/{api.repo}/actions/runs/{run['id']}")
    live_verdict, live_pr = review_state(api, number, head)
    if not matches(live_run, live_pr, workflow) or live_run["run_attempt"] != run["run_attempt"]:
        return []
    live_job = target(api, live_run, live_verdict)
    if live_job is None or live_job["id"] != job["id"] or not trusted_definition(api, live_run, live_pr):
        return []
    actual = api.request(f"repos/{api.repo}/actions/jobs/{job['id']}")
    if not same_execution(actual, live_job, live_run):
        return []
    # Last metadata read before the write: a clear/held transition must still
    # require the same action; a moved head or unknown target forbids the write.
    final_verdict, final_pr = review_state(api, number, head)
    if (not matches(live_run, final_pr, workflow)
            or (final_verdict["state"] == "clean") != (live_verdict["state"] == "clean")):
        return []
    api.request(f"repos/{api.repo}/actions/jobs/{job['id']}/rerun", post=True)
    return [{"run": run["id"], "job": job["id"], "head": head, "action": "rerun-aggregate",
             "state": final_verdict["state"]}]
