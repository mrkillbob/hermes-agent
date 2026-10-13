"""Bounded metadata-only retries; never dispatch, cancel, label, resolve or merge."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from scripts.ci.review_admission import GitHub, MetadataError, arguments, inspect

# Aliases share one logical lane: a healthy new wrapper replaces the old entry.
WORKFLOW_GROUPS = (("ci.yaml",), ("docker-pr.yml", "docker.yml"), ("nix-pr.yml", "nix.yml"),
                   ("plugin-catalog-ci.yml",), ("install-e2e-pr.yml", "install-e2e.yml"),
                   ("pm-bundle-pr.yml", "pm-bundle.yml"), ("termux-verify-pr.yml", "termux-verify.yml"),
                   ("sandbox-image.yml",), ("windows-bundle-sdk.yml",))
WORKFLOWS = tuple(workflow for group in WORKFLOW_GROUPS for workflow in group)
MAX_ATTEMPT = 3  # At most two automatic retry requests per original run.


def matches(run: dict, pr: dict, workflow: str) -> bool:
    head = pr["head"]
    return (run["event"] == "pull_request" and run["head_sha"] == head["sha"]
            and run["path"].split("@")[0] == f".github/workflows/{workflow}"
            and run["head_repository"]["id"] == head["repo"]["id"]
            and run["head_repository"]["full_name"] == head["repo"]["full_name"]
            and any(item["number"] == pr["number"] and item["head"]["sha"] == head["sha"]
                    for item in run["pull_requests"]))


def latest_run(api: GitHub, pr: dict, workflows: tuple[str, ...]) -> tuple[dict, str] | None:
    qualified = []
    for workflow in workflows:
        runs = api.pages(f"actions/workflows/{workflow}/runs", "workflow_runs",
                         event="pull_request", head_sha=pr["head"]["sha"])
        qualified.extend((run, workflow) for run in runs if matches(run, pr, workflow))
    return max(qualified, key=lambda item: item[0]["id"]) if qualified else None


def retry_kind(run: dict, jobs: list) -> str | None:
    if run["status"] != "completed" or run["run_attempt"] >= MAX_ATTEMPT or not jobs:
        return None
    failed = {job["name"] for job in jobs if job["conclusion"] == "failure"}
    if "Review admission" in failed:
        return "rerun"
    return None


def resume(api: GitHub, number: int, head: str) -> list[dict]:
    verdict, pr = inspect(api, number, head)
    if verdict["state"] != "clean":
        return [{"pr": number, **verdict, "action": "hold"}]
    actions = []
    for group in WORKFLOW_GROUPS:
        selected = latest_run(api, pr, group)
        if selected is None:
            actions.append({"workflow": group[0], "action": "none", "reason": "No qualified PR run"})
            continue
        run, workflow = selected
        jobs = api.pages(f"actions/runs/{run['id']}/jobs", "jobs", filter="latest")
        kind = retry_kind(run, jobs)
        if kind is None:
            actions.append({"run": run["id"], "action": "none", "reason": "Run needs no automatic retry"})
            continue
        latest = latest_run(api, pr, group)
        if latest is None or latest[0]["id"] != run["id"] or latest[1] != workflow:
            actions.append({"run": run["id"], "action": "none", "reason": "A newer PR run exists"})
            continue
        # Re-evaluate live review, producer, PR identity and the latest attempt just
        # before each write. The run ID pins the original PR event and code SHA.
        live_run = api.request(f"repos/{api.repo}/actions/runs/{run['id']}")
        live_jobs = api.pages(f"actions/runs/{run['id']}/jobs", "jobs", filter="latest")
        live_verdict, live_pr = inspect(api, number, head)
        if (live_verdict["state"] != "clean" or not matches(live_run, live_pr, workflow)
                or live_run["run_attempt"] != run["run_attempt"]
                or retry_kind(live_run, live_jobs) != kind):
            actions.append({"run": run["id"], "action": "hold", "reason": "Metadata changed before retry"})
            continue
        api.request(f"repos/{api.repo}/actions/runs/{run['id']}/{kind}", post=True)
        actions.append({"run": run["id"], "head": head, "action": kind})
    return actions


def discover(api: GitHub) -> list[dict]:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8-sig"))
    if os.environ["GITHUB_EVENT_NAME"] == "issue_comment":
        from scripts.ci.review_activity import CODEX_APP_ID, is_summary
        from scripts.ci.review_admission import CODEX_USER_ID

        issue, comment = event["issue"], event["comment"]
        if (not is_summary(comment, CODEX_USER_ID)
                or (comment.get("performed_via_github_app") or {}).get("id") != CODEX_APP_ID
                or issue.get("pull_request", {}).get("url") != f"https://api.github.com/repos/{api.repo}/pulls/{issue['number']}"):
            return []
        pr = api.pr(issue["number"])
        return [{"number": pr["number"], "head": pr["head"]["sha"]}] if pr["state"] == "open" else []
    if os.environ["GITHUB_EVENT_NAME"] == "workflow_run":
        run = event["workflow_run"]
        if run["event"] not in ("pull_request", "pull_request_review", "pull_request_review_comment"):
            return []
        return [{"number": pr["number"], "head": pr["head"]["sha"]}
                for pr in run["pull_requests"] if pr["head"]["sha"] == run["head_sha"]]
    if os.environ["GITHUB_EVENT_NAME"] != "schedule":
        raise MetadataError("Unsupported reconciliation event")
    prs = api.pages("pulls", state="open")
    if len(prs) > 200:
        raise MetadataError("Open PRs exceed the bounded reconciliation matrix")
    return [{"number": pr["number"], "head": pr["head"]["sha"]} for pr in prs]


def main() -> int:
    args = arguments(__doc__, discovery=True)
    try:
        api = GitHub(args.repo)
        if args.discover:
            output = {"include": discover(api)}
            print(json.dumps(output))
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                stream.write(f"matrix={json.dumps(output)}\n")
        else:
            if args.pr is None:
                raise MetadataError("PR identity required")
            from scripts.ci.label_resumption import resume as resume_labels

            # Any surviving writer must reconcile live label state: GitHub can
            # replace pending same-group events. This is the single label retry
            # implementation. Bootstrap runs keep their original label authority;
            # gated runs recheck substantive admission before label retries.
            for action in resume_labels(api, args.pr, args.head):
                print(json.dumps(action))
            for action in resume(api, args.pr, args.head):
                print(json.dumps(action))
    except (MetadataError, KeyError, TypeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"action": "hold", "reason": type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
