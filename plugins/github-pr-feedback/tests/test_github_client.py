from __future__ import annotations

import json

import pytest

from github_pr_feedback.github_client import GitHubClient, GitHubClientError


class RecordingRunner:
    def __init__(self, responses: dict[tuple[str, ...], object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: list[str]) -> str:
        key = tuple(argv)
        self.calls.append(key)
        return json.dumps(self.responses[key])


def test_github_client_reads_paginated_canonical_feedback_with_fixed_gh_argv() -> None:
    pulls_argv = (
        "gh",
        "pr",
        "list",
        "--repo",
        "acme/widgets",
        "--state",
        "open",
        "--author",
        "owner",
        "--limit",
        "100",
        "--json",
        "number,state,headRepository,author,headRefName,headRefOid",
    )
    comments_argv = (
        "gh",
        "api",
        "--paginate",
        "--slurp",
        "repos/acme/widgets/issues/17/comments?per_page=100",
    )
    review_comments_argv = (
        "gh",
        "api",
        "--paginate",
        "--slurp",
        "repos/acme/widgets/pulls/17/comments?per_page=100",
    )
    reviews_argv = (
        "gh",
        "api",
        "--paginate",
        "--slurp",
        "repos/acme/widgets/pulls/17/reviews?per_page=100",
    )
    runner = RecordingRunner(
        {
            pulls_argv: [canonical_list_pull(), canonical_list_pull(number=18)],
            comments_argv: [[canonical_feedback("issue-1", "first")], [canonical_feedback("issue-2", "second")]],
            review_comments_argv: [[canonical_feedback("review-comment-1", "line note")]],
            reviews_argv: [
                [canonical_feedback("review-1", "submitted", submitted_at="2026-08-24T00:00:00Z")],
                [canonical_feedback("review-pending", "pending", submitted_at=None)],
            ],
        }
    )
    client = GitHubClient(runner)

    pull_requests = client.list_open_pull_requests("acme/widgets", "owner")
    feedback = client.list_feedback("acme/widgets", 17)

    assert [pull_request.number for pull_request in pull_requests] == [17, 18]
    assert [(item.kind, item.feedback_id, item.body) for item in feedback] == [
        ("issue_comment", "issue-1", "first"),
        ("issue_comment", "issue-2", "second"),
        ("review_comment", "review-comment-1", "line note"),
        ("review", "review-1", "submitted"),
    ]
    assert runner.calls[0] == pulls_argv
    assert set(runner.calls[1:]) == {comments_argv, review_comments_argv, reviews_argv}


def test_github_client_fails_closed_when_filtered_pr_list_lacks_canonical_fields() -> None:
    argv = (
        "gh",
        "pr",
        "list",
        "--repo",
        "acme/widgets",
        "--state",
        "open",
        "--author",
        "owner",
        "--limit",
        "100",
        "--json",
        "number,state,headRepository,author,headRefName,headRefOid",
    )
    runner = RecordingRunner({argv: [{"number": 17}]})

    with pytest.raises(GitHubClientError, match="missing required fields"):
        GitHubClient(runner).list_open_pull_requests("acme/widgets", "owner")


def test_github_client_fails_closed_if_owned_pr_query_hits_coverage_cap() -> None:
    argv = (
        "gh",
        "pr",
        "list",
        "--repo",
        "acme/widgets",
        "--state",
        "open",
        "--author",
        "owner",
        "--limit",
        "100",
        "--json",
        "number,state,headRepository,author,headRefName,headRefOid",
    )
    runner = RecordingRunner(
        {argv: [canonical_list_pull(number=number) for number in range(1, 101)]}
    )

    with pytest.raises(GitHubClientError, match="coverage cap"):
        GitHubClient(runner).list_open_pull_requests("acme/widgets", "owner")


def test_github_client_bounds_untrusted_feedback_body_at_intake() -> None:
    responses = feedback_responses("x" * 6000)
    client = GitHubClient(RecordingRunner(responses))

    feedback = client.list_feedback("acme/widgets", 17)

    assert len(feedback[0].body) == 2000


def test_github_client_accepts_a_submitted_review_with_no_text_body() -> None:
    responses = feedback_responses("ordinary")
    reviews_key = (
        "gh",
        "api",
        "--paginate",
        "--slurp",
        "repos/acme/widgets/pulls/17/reviews?per_page=100",
    )
    responses[reviews_key] = [
        [canonical_feedback("review-empty", None, submitted_at="2026-08-24T00:00:00Z")]
    ]

    feedback = GitHubClient(RecordingRunner(responses)).list_feedback("acme/widgets", 17)

    review = next(item for item in feedback if item.feedback_id == "review-empty")
    assert review.body == ""


def test_github_client_gets_the_current_pull_request_with_fixed_argv() -> None:
    argv = ("gh", "api", "repos/acme/widgets/pulls/17")
    runner = RecordingRunner({argv: canonical_pull()})

    pull_request = GitHubClient(runner).get_pull_request("acme/widgets", 17)

    assert pull_request.number == 17
    assert runner.calls == [argv]


def canonical_pull(number: int = 17, head_sha: str = "a" * 40) -> dict[str, object]:
    return {
        "number": number,
        "state": "open",
        "base": {"repo": {"full_name": "acme/widgets"}},
        "head": {"repo": {"full_name": "acme/widgets"}, "ref": "codex/fix", "sha": head_sha},
        "user": {"login": "owner"},
    }


def canonical_list_pull(number: int = 17, head_sha: str = "a" * 40) -> dict[str, object]:
    return {
        "number": number,
        "state": "OPEN",
        "headRepository": {"nameWithOwner": "acme/widgets"},
        "author": {"login": "owner"},
        "headRefName": "codex/fix",
        "headRefOid": head_sha,
    }


def canonical_feedback(
    feedback_id: str, body: str | None, *, submitted_at: str | None = "2026-08-24T00:00:00Z"
) -> dict[str, object]:
    return {
        "id": feedback_id,
        "body": body,
        "created_at": "2026-08-24T00:00:00Z",
        "submitted_at": submitted_at,
        "user": {"login": "reviewer", "type": "User"},
        "author_association": "MEMBER",
    }


def feedback_responses(body: str) -> dict[tuple[str, ...], object]:
    return {
        ("gh", "api", "--paginate", "--slurp", "repos/acme/widgets/issues/17/comments?per_page=100"): [
            [canonical_feedback("issue-1", body)]
        ],
        ("gh", "api", "--paginate", "--slurp", "repos/acme/widgets/pulls/17/comments?per_page=100"): [[]],
        ("gh", "api", "--paginate", "--slurp", "repos/acme/widgets/pulls/17/reviews?per_page=100"): [[]],
    }
