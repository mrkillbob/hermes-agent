"""Parse the canonical public Codex v1 security marker and activity table.

These are observed producer formats, not approval labels or mergeability signals.
Unknown format, identity, completion or reaction evidence cannot clear admission.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

SUMMARY_MARKER = "<!-- codex-pull-request-review-summary -->"
CODEX_APP_ID = 1144995
KINDS = {"📝 **Code Review**": "code", "🔒 **Security Review**": "security"}
STATUS = re.compile(r'(✅ \*\*Completed\*\*|🔄 \*\*Running\*\* since) '
                    r'<relative-time datetime="([^"]+)">([^<]+)</relative-time>')
SECURITY_MARKER = re.compile(r"<!-- codex-security-review:v1 (\{[^\n]*\}) -->")


def time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Activity timestamp requires a timezone")
    return parsed


def is_summary(comment: dict, user_id: int) -> bool:
    return (comment.get("user") or {}).get("id") == user_id and "codex-pull-request-review-summary" in comment["body"]


def activity_rows(body: str) -> dict:
    rows = {}
    lines = [line for line in body.split("<details>", 1)[0].splitlines() if line.startswith("|")]
    if lines[:2] != ["| Review | Status | Commit | Review trigger |", "| --- | --- | --- | --- |"]:
        raise ValueError("Unknown activity-table header")
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) != 6 or cells[0] or cells[-1] or cells[1] not in KINDS:
            raise ValueError("Unknown activity-table row")
        kind = KINDS[cells[1]]
        status = STATUS.fullmatch(cells[2])
        commit = re.fullmatch(r"`([0-9a-f]{7,40})`", cells[3])
        if status is None or commit is None or kind in rows or status[2] != status[3]:
            raise ValueError("Incomplete or ambiguous activity row")
        time(status[2])
        rows[kind] = {"state": "completed" if status[1].startswith("✅") else "running",
                      "at": status[2], "ref": commit[1]}
    return rows


def select_activity(comments: list, repo: str, number: int, user_id: int) -> dict | None:
    summaries = [comment for comment in comments if is_summary(comment, user_id)]
    if not summaries:
        return None
    comment = max(summaries, key=lambda c: (time(c["updated_at"]), c["id"]))
    if ((comment.get("performed_via_github_app") or {}).get("id") != CODEX_APP_ID
            or comment["issue_url"] != f"https://api.github.com/repos/{repo}/issues/{number}"
            or not comment["body"].startswith(SUMMARY_MARKER + "\n")):
        raise ValueError("Activity producer identity or format mismatch")
    markers = SECURITY_MARKER.findall(comment["body"])
    if len(markers) > 1:
        raise ValueError("Multiple security completion markers")
    security = json.loads(markers[0]) if markers else None
    if security is not None and (security["repository"] != repo or security["pullRequestNumber"] != number
                                 or not re.fullmatch(r"[0-9a-f]{40}", security["headSha"])):
        raise ValueError("Security completion repository, PR or head mismatch")
    if "codex-security-review:" in comment["body"] and security is None:
        raise ValueError("Unknown security marker version or format")
    return {"id": comment["id"], "updated_at": comment["updated_at"],
            "passes": activity_rows(comment["body"]), "security": security}


def activity_hold(activity: dict | None, head: str) -> tuple[str, str] | None:
    """Only the Code Review pass gates admission.

    The Codex producer on this fork runs Code Review automatically and does not
    run Security Review, which the fork reviews elsewhere. A Security row is
    parsed for identity (select_activity) but never holds; its findings still
    block through the thread and standalone-finding guards.
    """
    if activity is None:
        return "pending", "No canonical public activity producer"
    code = activity["passes"].get("code")
    if code is None:
        return "pending", "A code completion record is required"
    if code["state"] != "completed":
        return "pending", "The public code review pass is still running"
    if code["sha"] != head:
        return "pending", "Public review activity belongs to a superseded head"
    return None


def clean_witness(activity: dict, reactions: list, user_id: int, latest_reviews: dict) -> bool:
    canonical = [reaction for reaction in reactions if reaction["user"]["id"] == user_id]
    if any(reaction["content"] == "eyes" for reaction in canonical):
        return False
    boundary = max([time(activity["updated_at"]),
                    *(time(row["at"]) for row in activity["passes"].values()),
                    *(time(review["submitted_at"]) for review in latest_reviews.values())])
    return any(reaction["content"] == "+1" and time(reaction["created_at"]) > boundary
               for reaction in canonical)
