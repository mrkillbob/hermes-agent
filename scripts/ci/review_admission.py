"""Read-only, exact-head admission from public GitHub review metadata.

Both canonical public activity passes must complete on the exact head. A fresh
canonical clean reaction is meaningful only with that producer and clear findings.
See developer-guide/ci-review-admission.md before wiring an early gate.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from urllib.parse import urlencode

CODEX_USER_ID = 199175422
CODEX_LOGIN = "chatgpt-codex-connector[bot]"
REPOSITORY = "mrkillbob/hermes-agent"
PAGE_LIMIT = 10
REQUEST = re.compile(r"@codex\s+(?:(security)\s+)?review\b", re.IGNORECASE)
PRIORITY = re.compile(r"(?:\[(P[0-3])\]|!\[(P[0-3]) Badge\])")
THREAD_QUERY = """query($owner:String!, $name:String!, $number:Int!, $cursor:String) {
  repository(owner:$owner, name:$name) { pullRequest(number:$number) {
    headRefOid
    reviewThreads(first:100, after:$cursor) {
      pageInfo { hasNextPage endCursor }
      nodes { id isResolved isOutdated comments(first:100) {
        pageInfo { hasNextPage }
        nodes { body author { login ... on Bot { databaseId } ... on User { databaseId } }
          pullRequestReview { databaseId } }
      } }
    }
  } }
}"""


class MetadataError(RuntimeError):
    """Unavailable or incomplete metadata must never grant admission."""


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise MetadataError("Metadata timestamp has no timezone")
    return parsed


class GitHub:
    def __init__(self, repo: str):
        if repo != REPOSITORY:
            raise MetadataError("Review admission is qualified for this fork only")
        self.repo = repo

    def request(self, route: str, *, post: bool = False, payload: dict | None = None):
        args = ["gh", "api", route]
        if post:
            args += ["--method", "POST"]
        if payload is not None:
            args += ["--input", "-"]
        result = subprocess.run(args, input=json.dumps(payload) if payload else None,
                                text=True, capture_output=True, timeout=30, check=False)
        if result.returncode:
            raise MetadataError(f"GitHub API request failed: {route.split('?')[0]}")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def pages(self, route: str, key: str | None = None, **params) -> list:
        items = []
        for page in range(1, PAGE_LIMIT + 1):
            data = self.request(f"repos/{self.repo}/{route}?{urlencode(dict(params, per_page=100, page=page))}")
            batch = data[key] if key else data
            if not isinstance(batch, list):
                raise MetadataError("Malformed paginated GitHub response")
            items.extend(batch)
            if len(batch) < 100:
                if key and data.get("total_count", len(items)) > len(items):
                    raise MetadataError("Incomplete GitHub pagination")
                return items
        raise MetadataError("GitHub metadata exceeds the bounded pagination limit")

    def pr(self, number: int) -> dict:
        return self.request(f"repos/{self.repo}/pulls/{number}")

    def threads(self, number: int, head: str) -> list:
        owner, name = self.repo.split("/")
        nodes, cursor = [], None
        for _ in range(PAGE_LIMIT):
            data = self.request("graphql", payload={"query": THREAD_QUERY,
                                "variables": {"owner": owner, "name": name,
                                              "number": number, "cursor": cursor}})
            if data.get("errors"):
                raise MetadataError("GitHub review-thread query failed")
            pr = data["data"]["repository"]["pullRequest"]
            if pr["headRefOid"] != head:
                raise MetadataError("PR head changed during review-thread reads")
            connection = pr["reviewThreads"]
            nodes.extend(connection["nodes"])
            if not connection["pageInfo"]["hasNextPage"]:
                return nodes
            cursor = connection["pageInfo"]["endCursor"]
            if not cursor:
                raise MetadataError("Missing review-thread pagination cursor")
        raise MetadataError("Review threads exceed the bounded pagination limit")

    def snapshot(self, number: int, head: str) -> dict:
        from scripts.ci.review_activity import select_activity

        pr = self.pr(number)
        if pr["number"] != number or pr["base"]["repo"]["full_name"] != self.repo:
            raise MetadataError("PR repository identity mismatch")
        if pr["state"] != "open" or pr["head"]["sha"] != head:
            raise MetadataError("PR closed or expected head superseded")
        comments = self.pages(f"issues/{number}/comments")
        activity = select_activity(comments, self.repo, number, CODEX_USER_ID)
        resolved = {}
        if activity is not None:
            for row in activity["passes"].values():
                ref = row["ref"]
                if ref not in resolved:
                    sha = self.request(f"repos/{self.repo}/commits/{ref}")["sha"]
                    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not sha.startswith(ref):
                        raise MetadataError("Activity abbreviation did not resolve to its Git object")
                    resolved[ref] = sha
                row["sha"] = resolved[ref]
        return {"pr": pr, "reviews": self.pages(f"pulls/{number}/reviews"), "comments": comments,
                "threads": self.threads(number, head), "activity": activity,
                "reactions": self.pages(f"issues/{number}/reactions")}


def result(state: str, reason: str, head: str) -> dict:
    return {"state": state, "reason": reason, "head": head}


def thread_verdict(threads: list, head: str) -> dict | None:
    for thread in threads:
        if not isinstance(thread["isResolved"], bool):
            raise MetadataError("Missing thread resolution state")
        comments = thread["comments"]
        if comments["pageInfo"]["hasNextPage"]:
            raise MetadataError("Incomplete thread comments")
        if thread["isResolved"]:
            continue
        for comment in comments["nodes"]:
            author = comment["author"] or {}
            if author.get("databaseId") != CODEX_USER_ID:
                if author.get("login") in (CODEX_LOGIN, CODEX_LOGIN.removesuffix("[bot]")):
                    raise MetadataError("Canonical reviewer identity unavailable")
                continue
            priorities = {match[0] or match[1] for match in PRIORITY.findall(comment["body"])}
            if priorities & {"P0", "P1", "P2"}:
                return result("blocked", "Unresolved actionable Codex/security finding", head)
            if not priorities:
                return result("unknown", "Unclassified canonical review finding", head)
    return None


def review_kind(review: dict) -> str | None:
    lines = review["body"].strip().splitlines()
    heading = lines[0] if lines else ""
    if re.search(r"\b(?:Codex\s+)?Security Review\b", heading, re.IGNORECASE):
        return "security"
    if re.search(r"\bCodex Review\b", heading, re.IGNORECASE):
        return "code"
    return None


def latest_passes(reviews: list) -> dict:
    latest = {}
    for review in reviews:
        if not review.get("submitted_at"):
            continue
        kind = review_kind(review)
        if kind is None:
            raise MetadataError("Unrecognized canonical review completion format")
        order = (timestamp(review["submitted_at"]), review["id"])
        if kind not in latest or order > (timestamp(latest[kind]["submitted_at"]), latest[kind]["id"]):
            latest[kind] = review
    return latest


def requested_after(comments: list, activity: dict) -> bool:
    from scripts.ci.review_activity import is_summary

    for comment in comments:
        if is_summary(comment, CODEX_USER_ID):
            continue  # The canonical producer's footer describes triggers; it does not request them.
        matches = list(REQUEST.finditer(comment["body"]))
        if not matches:
            continue
        requested = timestamp(comment.get("updated_at") or comment["created_at"])
        for match in matches:
            review = activity["passes"].get("security" if match[1] else "code")
            # Independent passes cannot complete each other's requests. Equal
            # second timestamps cannot establish that completion followed a request.
            if review is None or timestamp(review["at"]) <= requested:
                return True
    return False


def standalone_finding(reviews: list, comments: list) -> bool:
    bodies = [review["body"] for review in reviews
              if review["state"] in ("COMMENTED", "APPROVED", "CHANGES_REQUESTED")]
    bodies.extend(comment["body"] for comment in comments
                  if (comment.get("user") or {}).get("id") == CODEX_USER_ID)
    return any({match[0] or match[1] for match in PRIORITY.findall(body)} & {"P0", "P1", "P2"}
               for body in bodies)


def evaluate(snapshot: dict, head: str) -> dict:
    from scripts.ci.review_activity import activity_hold, clean_witness

    pr = snapshot["pr"]
    if pr["state"] != "open" or pr["head"]["sha"] != head:
        return result("unknown", "PR closed or expected head superseded", head)
    if not isinstance(pr["requested_reviewers"], list):
        raise MetadataError("Requested-reviewer metadata unavailable")
    canonical = [r for r in snapshot["reviews"] if r["user"]["id"] == CODEX_USER_ID]
    bound = [r for r in canonical if r["commit_id"] == head]
    if any(r["state"] == "PENDING" for r in bound) or any(
            user["id"] == CODEX_USER_ID for user in pr["requested_reviewers"]):
        return result("pending", "Canonical review is pending", head)
    latest = latest_passes(bound)
    if any(review["state"] not in ("COMMENTED", "APPROVED", "CHANGES_REQUESTED")
           for review in latest.values()):
        return result("pending", "Latest canonical review is not a completed active review", head)
    if any(review["state"] == "CHANGES_REQUESTED" for review in latest.values()):
        return result("blocked", "Canonical review requests changes", head)
    hold = activity_hold(snapshot["activity"], head)
    if hold:
        return result(*hold, head)
    if requested_after(snapshot["comments"], snapshot["activity"]):
        return result("pending", "An explicit code/security review request lacks bound completion", head)
    if standalone_finding(canonical, snapshot["comments"]):
        return result("blocked", "Standalone review finding lacks thread resolution evidence", head)
    findings = thread_verdict(snapshot["threads"], head)
    if findings:
        return findings
    if not clean_witness(snapshot["activity"], snapshot["reactions"], CODEX_USER_ID, latest):
        return result("pending", "No fresh canonical clean witness after both review completions", head)
    return result("clean", "Both public current-head passes completed; fresh clean witness and clear findings", head)


def inspect(api: GitHub, number: int, head: str) -> tuple[dict, dict]:
    snapshot = api.snapshot(number, head)
    verdict = evaluate(snapshot, head)
    live = api.pr(number)
    if live["state"] != "open" or live["head"]["sha"] != head:
        raise MetadataError("PR moved after review metadata was read")
    return verdict, live


def arguments(description: str, *, discovery: bool = False) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=not discovery)
    parser.add_argument("--head", required=not discovery)
    if discovery:
        parser.add_argument("--discover", action="store_true")
    args = parser.parse_args()
    if args.pr is not None and (args.pr <= 0 or not re.fullmatch(r"[0-9a-f]{40}", args.head or "")):
        parser.error("A positive PR number and full lower-case head SHA are required")
    return args


def main() -> int:
    args = arguments(__doc__)
    try:
        verdict, _ = inspect(GitHub(args.repo), args.pr, args.head)
    except (MetadataError, KeyError, TypeError, ValueError, OSError, subprocess.TimeoutExpired):
        verdict = result("unknown", "Review metadata unavailable or incomplete", args.head)
    print(json.dumps(verdict))
    return 0 if verdict["state"] == "clean" else 1


if __name__ == "__main__":
    raise SystemExit(main())
