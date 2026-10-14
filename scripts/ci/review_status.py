"""Bounded review metadata publisher; required-context activation is unqualified.

The shared Actions App and run namespace do not establish exclusive write
authority. Hosted qualification remains a blocker. Reads and PATCH are not atomic.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from scripts.ci.review_activity import CODEX_APP_ID, SUMMARY_MARKER
from scripts.ci.review_admission import CODEX_USER_ID, REQUEST, REPOSITORY, GitHub, MetadataError, evaluate

NAME = "Current Code and Security Review"
APP_ID = 15368
APP_SLUG = "github-actions"
WORKFLOW = ".github/workflows/review-status.yml"
EVENTS = {"push", "issue_comment", "workflow_run", "schedule"}
SHA = re.compile(r"[0-9a-f]{40}")
FAILURES = (MetadataError, KeyError, TypeError, ValueError, OSError, subprocess.TimeoutExpired)


def positive(value) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise MetadataError("Invalid execution identity")
    return int(value)


def run_proof(api, run_id: int, attempt: int, source: str, branch: str) -> dict:
    run = api.request(f"repos/{api.repo}/actions/runs/{run_id}/attempts/{attempt}")
    path = run["path"]
    # Actions may append @ref; source SHA and default branch are bound separately.
    workflow_file = path.partition("@")[0] if isinstance(path, str) else None
    if (run["id"] != run_id or run["run_attempt"] != attempt
            or run["repository"]["full_name"] != REPOSITORY
            or run["head_sha"] != source or run["head_branch"] != branch
            or workflow_file != WORKFLOW or run["event"] not in EVENTS):
        raise MetadataError("Server Actions execution proof mismatch")
    return run


def runtime(api, env: dict) -> dict:
    if api.repo != REPOSITORY or env.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise MetadataError("Publisher is confined to the qualified fork")
    source = env.get("GITHUB_SHA", "")
    if SHA.fullmatch(source) is None:
        raise MetadataError("Full source SHA required")
    run_id = positive(env.get("GITHUB_RUN_ID"))
    attempt = positive(env.get("GITHUB_RUN_ATTEMPT"))
    repo = api.request(f"repos/{api.repo}")
    branch = repo["default_branch"]
    if repo["full_name"] != REPOSITORY or not branch or env.get("GITHUB_REF") != f"refs/heads/{branch}":
        raise MetadataError("Publisher must execute on the server default branch")
    run = run_proof(api, run_id, attempt, source, branch)
    if env.get("GITHUB_EVENT_NAME") != run["event"]:
        raise MetadataError("Event identity mismatch")
    return {"run_id": run_id, "attempt": attempt, "source": source,
            "branch": branch, "event": run["event"]}


def open_prs(api) -> list:
    rows = []
    for page in range(1, 11):
        batch = api.request(f"repos/{api.repo}/pulls?state=open&per_page=100&page={page}")
        if not isinstance(batch, list):
            raise MetadataError("Malformed open PR listing")
        rows.extend(batch)
        if len(rows) > 200:
            raise MetadataError("Open PR bound exceeded")
        if len(batch) < 100:
            numbers = set()
            for pr in rows:
                if (pr["state"] != "open" or SHA.fullmatch(pr["head"]["sha"]) is None
                        or positive(pr["number"]) in numbers):
                    raise MetadataError("Invalid or duplicate open PR identity")
                numbers.add(pr["number"])
            return rows
    raise MetadataError("Open PR pagination bound exceeded")


def comment_trigger(event: dict) -> bool:
    if (event.get("repository", {}).get("full_name") != REPOSITORY
            or not event.get("issue", {}).get("pull_request")
            or event.get("action") not in ("created", "edited", "deleted")):
        return False
    comment = event.get("comment", {})
    body = comment.get("body", "")
    canonical = ((comment.get("user") or {}).get("id") == CODEX_USER_ID
                 and (comment.get("performed_via_github_app") or {}).get("id") == CODEX_APP_ID
                 and body.startswith(SUMMARY_MARKER + "\n")
                 and comment.get("issue_url") ==
                 f"https://api.github.com/repos/{REPOSITORY}/issues/{event['issue']['number']}")
    return canonical or bool(REQUEST.search(body))


def discover(api, env: dict, event: dict | None = None) -> dict:
    proof = runtime(api, env)
    if proof["event"] == "issue_comment" and not comment_trigger(event or {}):
        return {"include": []}
    return {"include": [{"head": head} for head in sorted({pr["head"]["sha"] for pr in open_prs(api)})]}


def external_id(repo: str, head: str, proof: dict) -> str:
    return f"review-status:v1:{repo}:{head}:{proof['run_id']}:{proof['attempt']}:{proof['source']}"


def check_identity(check: dict, head: str, identity: str, check_id: int | None = None):
    positive(check["id"])
    if (check["name"] != NAME or check["head_sha"] != head
            or check["app"]["id"] != APP_ID or check["app"]["slug"] != APP_SLUG
            or check["external_id"] != identity
            or (check_id is not None and check["id"] != check_id)):
        raise MetadataError("Check identity or ownership mismatch")


def owned_checks(api, head: str, proof: dict, own: dict, proved_runs: set):
    checks = api.pages(f"commits/{head}/check-runs", key="check_runs", filter="all", check_name=NAME)
    if len(checks) > 200:
        raise MetadataError("Check-run proof bound exceeded")
    seen_ids, seen_executions = set(), set()
    own_seen = False
    for check in checks:
        if check["name"] != NAME:
            continue
        parts = (check.get("external_id") or "").split(":")
        if (len(parts) != 7 or parts[:3] != ["review-status", "v1", api.repo]
                or parts[3] != head or SHA.fullmatch(parts[6]) is None):
            raise MetadataError("Unexpected same-name check namespace")
        check_identity(check, head, check["external_id"])
        execution = (positive(parts[4]), positive(parts[5]), parts[6])
        if check["id"] in seen_ids or execution in seen_executions:
            raise MetadataError("Duplicate same-name execution check")
        seen_ids.add(check["id"])
        seen_executions.add(execution)
        if check["id"] == own["id"]:
            check_identity(check, head, own["external_id"], own["id"])
            own_seen = True
        elif execution not in proved_runs:
            run_proof(api, *execution, proof["branch"])
            proved_runs.add(execution)
    if not own_seen:
        raise MetadataError("Fresh check missing from complete listing")


def cohort(api, head: str) -> set[int]:
    numbers = {pr["number"] for pr in open_prs(api) if pr["head"]["sha"] == head}
    if not numbers:
        raise MetadataError("No open PR uses this head")
    return numbers


def cohort_verdict(api, head: str, numbers: set[int]) -> str:
    """clean when every PR clears, pending when the only holds are still-waiting
    reviews, otherwise failure (a finding, requested changes or unusable metadata)."""
    try:
        states = {evaluate(api.snapshot(number, head), head)["state"] for number in sorted(numbers)}
    except MetadataError:
        # A PR that closed or moved mid-read is cohort churn, not unusable metadata.
        if cohort(api, head) == numbers:
            raise
        return "pending"
    if states == {"clean"}:
        return "clean"
    return "pending" if states <= {"clean", "pending"} else "failure"


def completed_at() -> str:
    return datetime.now(UTC).isoformat()


def settle(api, own: dict | None, summary: str) -> None:
    """Best-effort: turn this execution's own in-progress check into a failure."""
    if own is None:
        return
    try:
        api.request(f"repos/{api.repo}/check-runs/{own['id']}", method="PATCH",
                    payload={"status": "completed", "conclusion": "failure",
                             "completed_at": completed_at(),
                             "output": {"title": "Review admission held", "summary": summary}})
    except FAILURES:
        pass  # Left in progress, which never reads as a pass.


def find_created(api, head: str, identity: str | None) -> dict | None:
    """Recover this execution's check when its creation response was lost."""
    if identity is None:
        return None
    try:
        checks = api.pages(f"commits/{head}/check-runs", key="check_runs", filter="all", check_name=NAME)
    except FAILURES:
        return None
    return next((c for c in checks if c.get("external_id") == identity
                 and c.get("app", {}).get("id") == APP_ID), None)


def held(api, own: dict, head: str, verdict: str) -> dict:
    if verdict == "pending":
        return {"state": "pending", "reason": "Review still pending", "head": head}
    settle(api, own, "A review finding or requested change holds admission.")
    return {"state": "failure", "reason": "Review held", "head": head}


def publish(api, head: str, env: dict) -> dict:
    """Create a fresh in-progress check; promote only this execution's stable cohort.

    A review that is merely still running leaves the check in progress (pending,
    not red); a real finding or unusable metadata completes it as a failure.
    """
    own = identity = None
    promotion_attempted = False
    try:
        if SHA.fullmatch(head) is None:
            raise MetadataError("Full target SHA required")
        proof = runtime(api, env)
        identity = external_id(api.repo, head, proof)
        own = api.request(f"repos/{api.repo}/check-runs", post=True,
                          payload={"name": NAME, "head_sha": head, "external_id": identity,
                                   "status": "in_progress",
                                   "output": {"title": "Review admission pending",
                                              "summary": "Current cohort has not been proven clear."}})
        check_identity(own, head, identity)
        if own["status"] != "in_progress":
            raise MetadataError("Fresh check did not record in-progress")
        proved_runs = {(proof["run_id"], proof["attempt"], proof["source"])}
        owned_checks(api, head, proof, own, proved_runs)
        numbers = cohort(api, head)
        for _ in range(2):
            if cohort(api, head) != numbers:
                return {"state": "pending", "reason": "Cohort changed", "head": head}
            verdict = cohort_verdict(api, head, numbers)
            if verdict != "clean":
                return held(api, own, head, verdict)
        owned_checks(api, head, proof, own, proved_runs)
        current = api.request(f"repos/{api.repo}/check-runs/{own['id']}")
        check_identity(current, head, identity, own["id"])
        if current["status"] != "in_progress":
            raise MetadataError("Own check changed before promotion")
        if cohort(api, head) != numbers:
            return {"state": "pending", "reason": "Cohort changed", "head": head}
        verdict = cohort_verdict(api, head, numbers)
        if verdict != "clean":
            return held(api, own, head, verdict)
        if cohort(api, head) != numbers:
            return {"state": "pending", "reason": "Cohort changed", "head": head}
        promotion_attempted = True
        updated = api.request(f"repos/{api.repo}/check-runs/{own['id']}", method="PATCH",
                              payload={"status": "completed", "conclusion": "success",
                                       "completed_at": completed_at(),
                                       "output": {"title": "Current reviews clear",
                                                  "summary": "All open PRs sharing this head passed review admission."}})
        check_identity(updated, head, identity, own["id"])
        if updated["status"] != "completed" or updated["conclusion"] != "success":
            raise MetadataError("Success response mismatch")
        return {"state": "success", "head": head, "check_id": own["id"]}
    except FAILURES:
        if promotion_attempted:
            try:
                rolled_back = api.request(f"repos/{api.repo}/check-runs/{own['id']}", method="PATCH",
                                          payload={"status": "completed", "conclusion": "failure",
                                                   "completed_at": completed_at()})
                check_identity(rolled_back, head, identity, own["id"])
                if rolled_back["status"] != "completed" or rolled_back["conclusion"] != "failure":
                    raise MetadataError("Failure rollback response mismatch")
            except FAILURES:
                return {"state": "failure", "reason": "Promotion outcome uncertain; failure rollback unconfirmed", "head": head}
        else:
            settle(api, own or find_created(api, head, identity),
                   "Metadata unavailable or publisher proof invalid.")
        return {"state": "failure", "reason": "Metadata unavailable or publisher proof invalid", "head": head}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, choices=[REPOSITORY])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--head")
    mode.add_argument("--discover", action="store_true")
    args = parser.parse_args()
    api = GitHub(args.repo)
    try:
        if args.discover:
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8-sig")) if os.environ.get("GITHUB_EVENT_NAME") == "issue_comment" else None
            matrix = json.dumps(discover(api, dict(os.environ), event))
            if os.environ.get("GITHUB_OUTPUT"):
                with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                    output.write(f"matrix={matrix}\n")
            print(matrix)
            return 0
        verdict = publish(api, args.head, dict(os.environ))
        print(json.dumps(verdict))
        return 0 if verdict["state"] in ("success", "pending") else 1
    except FAILURES:
        print(json.dumps({"state": "failure", "reason": "Discovery metadata unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
