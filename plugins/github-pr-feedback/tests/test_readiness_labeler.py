from __future__ import annotations

from types import SimpleNamespace

from github_pr_feedback.readiness import ReadyPullRequest, order_ready_queue
from github_pr_feedback.cli import _announce_ready_to_merge


def _ready(number: int, *, codex_clean: bool = False, ready_at: int = 1) -> ReadyPullRequest:
    return ReadyPullRequest("owner/repo", number, f"{number:040x}", ready_at, codex_clean)


def test_codex_clean_heads_are_first_then_oldest_ready() -> None:
    queue = order_ready_queue([_ready(3, ready_at=30), _ready(2, codex_clean=True, ready_at=99), _ready(1, ready_at=10)])
    assert [item.number for item in queue] == [2, 1, 3]


def test_ready_handoff_labels_exact_head_before_alert_comment() -> None:
    pull = SimpleNamespace(number=7, head_sha="a" * 40)

    class GitHub:
        def __init__(self) -> None:
            self.labels: list[str] = []
            self.comments: list[str] = []

        def get_pull_request(self, repository: str, number: int):
            return SimpleNamespace(head_sha=pull.head_sha, labels=tuple(self.labels))

        def add_issue_labels(self, repository: str, number: int, labels: tuple[str, ...]) -> None:
            self.labels.extend(labels)

        def ensure_issue_label(self, repository: str, label: str, *, color: str, description: str) -> None:
            return None

        def list_feedback(self, repository: str, number: int):
            return tuple(SimpleNamespace(body=body) for body in self.comments)

        def post_issue_comment(self, repository: str, number: int, body: str) -> None:
            self.comments.append(body)

    github = GitHub()
    _announce_ready_to_merge(github, "owner/repo", pull)
    assert github.labels == ["ready-to-merge"]
    assert len(github.comments) == 1
