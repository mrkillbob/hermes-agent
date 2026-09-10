"""Pure readiness and queue ordering rules for the merge-maintainer handoff."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReadyPullRequest:
    repository: str
    number: int
    head_sha: str
    ready_at: int
    codex_clean: bool


def order_ready_queue(items: list[ReadyPullRequest] | tuple[ReadyPullRequest, ...]) -> tuple[ReadyPullRequest, ...]:
    """Prefer Codex-clean heads, then oldest work, then by repo and PR number."""

    return tuple(sorted(items, key=lambda item: (
        not item.codex_clean, item.ready_at,
        item.repository.casefold(), item.number,
    )))
