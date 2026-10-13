"""Bound source retries and label freshness independently of metadata rechecks."""

from __future__ import annotations

from scripts.ci.review_admission import GitHub, MetadataError, timestamp
from scripts.ci.review_aggregate import AGGREGATE, LIVE_STEPS, MAX_METADATA_ATTEMPT, qualified_job, trusted_definition

MAX_SOURCE_ATTEMPTS = 3
METADATA_JOBS = frozenset((AGGREGATE, "CI timing report"))


def metadata_only(jobs: list, run: dict, number: int) -> bool:
    names = {job["name"] for job in jobs}
    if not names <= METADATA_JOBS or AGGREGATE not in names:
        return False
    if any(not qualified_job(job, run) or job.get("run_attempt", number) != number for job in jobs):
        return False
    aggregates = [job for job in jobs if job["name"] == AGGREGATE]
    return len(aggregates) == 1 and LIVE_STEPS <= {step["name"] for step in aggregates[0]["steps"]}


def read_attempt(api: GitHub, pr: dict, run: dict, workflow: str, number: int) -> dict:
    from scripts.ci.review_resumption import matches

    attempt = api.request(f"repos/{api.repo}/actions/runs/{run['id']}/attempts/{number}")
    if (attempt["id"] != run["id"] or attempt["run_attempt"] != number
            or not matches(attempt, pr, workflow) or attempt["status"] != "completed"):
        raise MetadataError("Source attempt identity or state changed")
    timestamp(attempt["run_started_at"])
    return attempt


def source_budget(api: GitHub, pr: dict, run: dict, workflow: str) -> tuple[int, str]:
    total = run["run_attempt"]
    if not isinstance(total, int) or not 1 <= total <= MAX_METADATA_ATTEMPT:
        raise MetadataError("Attempt history exceeds the bounded platform limit")
    latest = read_attempt(api, pr, run, workflow, total)
    # Older bootstrap workflows have no qualified metadata-only retry path.
    # Preserve their exact original cap and timestamp without guessing exemptions.
    if workflow != "ci.yaml" or not run.get("referenced_workflows"):
        return total, latest["run_started_at"]
    heavy, started, trusted = 0, None, None
    for number in range(total, 0, -1):
        attempt = latest if number == total else read_attempt(api, pr, run, workflow, number)
        jobs = api.pages(f"actions/runs/{run['id']}/attempts/{number}/jobs", "jobs")
        if metadata_only(jobs, run, number):
            if trusted is None:
                trusted = trusted_definition(api, run, pr)
            if trusted:
                continue
        # Full, failed-only source/label, empty and ambiguous executions all count.
        heavy += 1
        started = started or attempt["run_started_at"]
        if heavy >= MAX_SOURCE_ATTEMPTS:
            break
    if started is None:
        raise MetadataError("No qualified original source attempt")
    return heavy, started
