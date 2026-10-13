"""Preserve eventual maintainer-label retries without cancelling healthy PR work."""

from __future__ import annotations

import json
import subprocess

from scripts.ci.review_admission import GitHub, MetadataError, arguments, inspect, timestamp
from scripts.ci.review_resumption import MAX_ATTEMPT, WORKFLOW_GROUPS, latest_run, matches

LABEL_WORKFLOW_GROUPS = WORKFLOW_GROUPS[:3]
LABELS = {"run-e2e", "ci-reviewed"}
REVIEW_JOB_NAMES = {"Review label gate", "Review label gate / Review label gate"}


def label_state(api: GitHub, number: int, head: str) -> tuple[dict, dict]:
    pr = api.pr(number)
    if (pr["number"] != number or pr["base"]["repo"]["full_name"] != api.repo
            or pr["state"] != "open" or pr["head"]["sha"] != head):
        raise MetadataError("PR closed, identity mismatched or head superseded")
    live_names = {label["name"] for label in pr["labels"]} & LABELS
    if not live_names:
        return pr, {}
    events = api.pages(f"issues/{number}/timeline")
    labels = {}
    for name in live_names:
        additions = [item for item in events if item.get("event") in ("labeled", "unlabeled")
                     and (item.get("label") or {}).get("name") == name]
        if not additions:
            raise MetadataError("Live label has no qualified addition timestamp")
        latest = max(additions, key=lambda item: (timestamp(item["created_at"]), item["id"]))
        if latest["event"] == "labeled":
            labels[name] = latest
    return pr, labels


def retry_kind(api: GitHub, pr: dict, run: dict, workflow: str, labels: dict) -> str | None:
    if (not matches(run, pr, workflow) or run["status"] != "completed"
            or run["run_attempt"] >= MAX_ATTEMPT):
        return None
    attempt = api.request(f"repos/{api.repo}/actions/runs/{run['id']}/attempts/{run['run_attempt']}")
    if (attempt["id"] != run["id"] or attempt["run_attempt"] != run["run_attempt"]
            or not matches(attempt, pr, workflow) or attempt["status"] != "completed"):
        raise MetadataError("Latest attempt identity or state changed")
    # The original run timestamp can persist across retries. Only this exact
    # attempt's timestamp proves that it already had a chance to read the label.
    unread = {name for name, label in labels.items()
              if timestamp(label["created_at"]) > timestamp(attempt["run_started_at"])}
    if not unread:
        return None
    jobs = api.pages(f"actions/runs/{run['id']}/jobs", "jobs", filter="latest")
    if not jobs:
        raise MetadataError("No retryable PR job context; mutable dispatch is forbidden")
    if any(job["name"] == "Review admission" for job in jobs):
        # Bootstrap runs without an admission job keep their original label
        # authority. Once the gate exists, labels cannot consume retry budget
        # before both substantive current-head passes are clear.
        verdict, _ = inspect(api, pr["number"], pr["head"]["sha"])
        if verdict["state"] != "clean":
            return None
    if "run-e2e" in unread:
        return "rerun"
    failed = {job["name"] for job in jobs if job["conclusion"] == "failure"}
    if workflow == "ci.yaml" and "ci-reviewed" in unread and failed & REVIEW_JOB_NAMES:
        return "rerun-failed-jobs"
    return None


def resume(api: GitHub, number: int, head: str) -> list[dict]:
    pr, labels = label_state(api, number, head)
    if not labels:
        return [{"action": "none", "reason": "No live maintainer label addition"}]
    actions = []
    for group in LABEL_WORKFLOW_GROUPS:
        selected = latest_run(api, pr, group)
        if selected is None:
            continue
        run, workflow = selected
        kind = retry_kind(api, pr, run, workflow, labels)
        if kind is None:
            continue
        newest = latest_run(api, pr, group)
        if newest is None or newest[0]["id"] != run["id"] or newest[1] != workflow:
            continue
        live_run = api.request(f"repos/{api.repo}/actions/runs/{run['id']}")
        live_pr, live_labels = label_state(api, number, head)
        if (live_labels != labels or live_run["run_attempt"] != run["run_attempt"]
                or retry_kind(api, live_pr, live_run, workflow, live_labels) != kind):
            continue
        api.request(f"repos/{api.repo}/actions/runs/{run['id']}/{kind}", post=True)
        actions.append({"run": run["id"], "head": head, "action": kind})
    return actions


def main() -> int:
    args = arguments(__doc__)
    try:
        for action in resume(GitHub(args.repo), args.pr, args.head):
            print(json.dumps(action))
    except (MetadataError, KeyError, TypeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"action": "hold", "reason": type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
