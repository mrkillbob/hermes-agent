"""Canonical, fixed-argv GitHub reads for feedback intake."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .policy import PullRequest, Reviewer


class GitHubClientError(RuntimeError):
    """Canonical GitHub data was unavailable or did not have the required shape."""


MAX_FEEDBACK_BODY_CHARS = 2000
MAX_DISCOVERED_PULL_REQUESTS = 100


class CommandRunner(Protocol):
    def run(self, argv: list[str]) -> str: ...


class SubprocessCommandRunner:
    """The only production command boundary; it never invokes a shell."""

    def run(self, argv: list[str]) -> str:
        try:
            completed = subprocess.run(
                argv,
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitHubClientError("GitHub command failed") from error
        if completed.returncode != 0:
            raise GitHubClientError("GitHub command failed")
        return completed.stdout


@dataclass(frozen=True, slots=True)
class Feedback:
    kind: str
    feedback_id: str
    reviewer: Reviewer
    body: str
    created_at: datetime
    is_bot: bool


class GitHubClient:
    """Reads only the canonical PR and review endpoints using literal argv."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or SubprocessCommandRunner()

    def list_open_pull_requests(
        self, repository: str, owner_login: str
    ) -> tuple[PullRequest, ...]:
        payload = self._json(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repository,
                "--state",
                "open",
                "--author",
                owner_login,
                "--limit",
                "100",
                "--json",
                "number,state,headRepository,author,headRefName,headRefOid",
            ]
        )
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise GitHubClientError("GitHub pull request list was not a list of objects")
        if len(payload) >= MAX_DISCOVERED_PULL_REQUESTS:
            raise GitHubClientError("GitHub owned pull request query reached its coverage cap")
        return tuple(_listed_pull_request(repository, row) for row in payload)

    def get_pull_request(self, repository: str, number: int) -> PullRequest:
        row = self._read_object(f"repos/{repository}/pulls/{number}")
        return _pull_request(row)

    def list_feedback(self, repository: str, number: int) -> tuple[Feedback, ...]:
        endpoints = (
            f"repos/{repository}/issues/{number}/comments?per_page=100",
            f"repos/{repository}/pulls/{number}/comments?per_page=100",
            f"repos/{repository}/pulls/{number}/reviews?per_page=100",
        )
        # The three canonical reads are independent; running them concurrently
        # keeps wall-clock at one read instead of three stacked 30s timeouts.
        with ThreadPoolExecutor(max_workers=len(endpoints)) as executor:
            issue_comments, review_comments, reviews = executor.map(self._read_pages, endpoints)

        feedback = [
            *(_feedback("issue_comment", row, timestamp_key="created_at") for row in issue_comments),
            *(_feedback("review_comment", row, timestamp_key="created_at") for row in review_comments),
        ]
        feedback.extend(
            _feedback("review", row, timestamp_key="submitted_at")
            for row in reviews
            if isinstance(row, dict) and row.get("submitted_at") is not None
        )
        return tuple(feedback)

    def _read_pages(self, endpoint: str) -> tuple[dict[str, Any], ...]:
        payload = self._json(["gh", "api", "--paginate", "--slurp", endpoint])
        if not isinstance(payload, list) or any(not isinstance(page, list) for page in payload):
            raise GitHubClientError("GitHub paginated response was not a list of pages")
        rows = tuple(row for page in payload for row in page)
        if any(not isinstance(row, dict) for row in rows):
            raise GitHubClientError("GitHub response row was not an object")
        return rows

    def _read_object(self, endpoint: str) -> dict[str, Any]:
        payload = self._json(["gh", "api", endpoint])
        if not isinstance(payload, dict):
            raise GitHubClientError("GitHub response was not an object")
        return payload

    def _json(self, argv: list[str]) -> object:
        try:
            return json.loads(self._runner.run(argv))
        except (json.JSONDecodeError, TypeError) as error:
            raise GitHubClientError("GitHub response was not valid JSON") from error


def _pull_request(row: dict[str, Any]) -> PullRequest:
    try:
        base = row["base"]
        head = row["head"]
        return PullRequest(
            number=row["number"],
            state=row["state"],
            base_repository=base["repo"]["full_name"],
            head_repository=head["repo"]["full_name"],
            author_login=row["user"]["login"],
            head_ref_name=head["ref"],
            head_sha=head["sha"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GitHubClientError("GitHub pull request has missing required fields") from error


def _listed_pull_request(base_repository: str, row: dict[str, Any]) -> PullRequest:
    try:
        return PullRequest(
            number=row["number"],
            state=row["state"],
            base_repository=base_repository,
            head_repository=row["headRepository"]["nameWithOwner"],
            author_login=row["author"]["login"],
            head_ref_name=row["headRefName"],
            head_sha=row["headRefOid"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GitHubClientError("GitHub pull request has missing required fields") from error


def _feedback(kind: str, row: dict[str, Any], *, timestamp_key: str) -> Feedback:
    try:
        user = row["user"]
        body = row["body"]
        if kind == "review" and body is None:
            body = ""
        if not isinstance(body, str):
            raise TypeError("body must be a string")
        return Feedback(
            kind=kind,
            feedback_id=str(row["id"]),
            reviewer=Reviewer(user["login"], row.get("author_association")),
            body=body[:MAX_FEEDBACK_BODY_CHARS],
            created_at=_timestamp(row[timestamp_key]),
            is_bot=user.get("type") == "Bot",
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GitHubClientError("GitHub feedback has missing required fields") from error


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp is required")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("timestamp is invalid") from error
    if timestamp.tzinfo is None:
        raise ValueError("timestamp requires a timezone")
    return timestamp
