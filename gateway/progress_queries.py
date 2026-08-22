"""Deterministic, read-only Kanban progress replies for trusted chat sources.

This is deliberately a gateway edge helper, rather than a model prompt or a
Kanban tool.  It reads only cards that explicitly subscribed the current
platform/chat/thread source and produces a bounded response without creating
work, following paths, or interpreting prose as authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional


MAX_PROGRESS_RESPONSE_CHARS = 1_600
_MAX_GRAPH_TASKS = 24
_MAX_NEXT_TASKS = 3
_MAX_AMBIGUOUS_ROOTS = 3
_MAX_RECEIPT_CHARS = 280
_BOARD_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TASK_ID_RE = re.compile(r"\bt_[a-z0-9]+\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9]{3,}")
_SECRET_RE = re.compile(
    r"(?i)\b(?:[a-z0-9_]*(?:token|secret|password|api[_-]?key)|authorization)"
    r"\s*(?:=|:)\s*(?:bearer\s+)?(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s;,]+)"
)
_PATH_RE = re.compile(r"(?:(?:[A-Za-z]:)?[\\/](?:Users|home|private|tmp|var|etc)[^\s,;]*)")
_PROSE_COMMIT_RE = re.compile(r"\b[0-9a-f]{7,64}\b", re.IGNORECASE)
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")
_STOP_WORDS = frozenset(
    {
        "about", "also", "and", "burndown", "could", "did", "does", "else", "from",
        "have", "how", "need", "progress", "project", "status", "that", "the", "this",
        "what", "when", "where", "with", "work", "would", "your",
    }
)
_QUESTION_MARKERS = (
    "how did ", "how is ", "how's ", "what else", "what remains", "what is left",
    "what's left", "where are we", "give me an update", "status of ", "progress on ",
)
_PROGRESS_TERMS = ("burndown", "status", "progress", "remaining", "left", "next", "complete")
_ACTION_REQUEST_RE = re.compile(
    r"^(?:(?:can|could|would)\s+you\s+|please\s+)?"
    r"(?:start|fix|patch|audit|implement|create|run|investigate|change)\b",
    re.IGNORECASE,
)
_NONTERMINAL_STATUSES = frozenset({"triage", "todo", "ready", "review"})
_FAILED_STATUSES = frozenset({"failed", "blocked", "timed_out", "crashed"})


@dataclass(frozen=True)
class ProgressSource:
    """Trusted ingress identity used for exact subscription matching."""

    platform: str
    chat_id: str
    thread_id: Optional[str] = None


@dataclass(frozen=True)
class ProgressQueryResult:
    """A handled reply is safe to send without invoking the normal agent."""

    handled: bool
    response: str
    reason: str


def is_progress_query(request: object) -> bool:
    """Recognize only ordinary, bounded project-status questions."""
    if not isinstance(request, str):
        return False
    normalized = " ".join(request.casefold().split())
    if not normalized or len(normalized) > 4_000:
        return False
    if _ACTION_REQUEST_RE.match(normalized):
        return False
    has_marker = any(marker in normalized for marker in _QUESTION_MARKERS)
    has_status_term = any(term in normalized for term in _PROGRESS_TERMS)
    return has_marker or ("?" in normalized and has_status_term)


def _safe_text(value: object, *, limit: int = _MAX_RECEIPT_CHARS) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    text = _SECRET_RE.sub("[redacted]", text)
    text = _PATH_RE.sub("[path redacted]", text)
    # A hash mentioned in a free-form worker receipt is not provenance. Only
    # the explicit structured metadata fields below may identify a commit.
    text = _PROSE_COMMIT_RE.sub("[unverified commit]", text)
    return text[:limit].rstrip()


def _safe_commit(value: object) -> Optional[str]:
    candidate = str(value or "").strip()
    return candidate if _COMMIT_RE.fullmatch(candidate) else None


def _safe_branch(value: object) -> Optional[str]:
    candidate = str(value or "").strip()
    if not _BRANCH_RE.fullmatch(candidate) or ".." in candidate or candidate.startswith("/"):
        return None
    return candidate


def _source_subscription_task_ids(conn, source: ProgressSource) -> list[str]:
    rows = conn.execute(
        """
        SELECT task_id FROM kanban_notify_subs
         WHERE platform = ? AND chat_id = ? AND thread_id = ?
         ORDER BY task_id ASC
        """,
        (source.platform, source.chat_id, source.thread_id or ""),
    ).fetchall()
    return [str(row["task_id"]) for row in rows]


def _roots_for_tasks(conn, task_ids: Iterable[str]) -> list[str]:
    """Walk parent links so child subscriptions still recall their root graph."""
    from hermes_cli import kanban_db as kb

    roots: set[str] = set()
    for task_id in task_ids:
        seen: set[str] = set()
        frontier = [task_id]
        terminal: set[str] = set()
        while frontier and len(seen) < _MAX_GRAPH_TASKS:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            parents = kb.parent_ids(conn, current)
            if not parents:
                terminal.add(current)
            else:
                frontier.extend(parents)
        roots.update(terminal)
    return sorted(roots)


def _graph_task_ids(conn, root_id: str) -> list[str]:
    """Return a bounded root-plus-descendants graph without following text links."""
    from hermes_cli import kanban_db as kb

    ordered: list[str] = []
    seen: set[str] = set()
    frontier = [root_id]
    while frontier and len(ordered) < _MAX_GRAPH_TASKS:
        task_id = frontier.pop(0)
        if task_id in seen:
            continue
        seen.add(task_id)
        ordered.append(task_id)
        frontier.extend(kb.child_ids(conn, task_id))
    return ordered


def _query_terms(request: str) -> set[str]:
    return {
        word for word in _WORD_RE.findall(request.casefold())
        if word not in _STOP_WORDS and not word.startswith("t_")
    }


def _select_root(conn, root_ids: list[str], request: str):
    """Return one root, or a bounded ambiguity result.  Never guess ties."""
    from hermes_cli import kanban_db as kb

    explicit_ids = {match.casefold() for match in _TASK_ID_RE.findall(request)}
    terms = _query_terms(request)
    ranked = []
    for root_id in root_ids:
        graph_ids = _graph_task_ids(conn, root_id)
        tasks = [kb.get_task(conn, task_id) for task_id in graph_ids]
        tasks = [task for task in tasks if task is not None]
        graph_id_set = {task.id.casefold() for task in tasks}
        if explicit_ids:
            score = 100 if graph_id_set & explicit_ids else 0
        else:
            title_words = set().union(*(_query_terms(task.title) for task in tasks)) if tasks else set()
            score = len(terms & title_words)
        if score:
            root = kb.get_task(conn, root_id)
            if root is not None:
                ranked.append((score, root.created_at, root))

    if not ranked:
        if len(root_ids) == 1:
            root = kb.get_task(conn, root_ids[0])
            return ("resolved", root) if root is not None else ("no_match", None)
        return "no_match", None
    top_score = max(item[0] for item in ranked)
    top = [item for item in ranked if item[0] == top_score]
    if len(top) != 1:
        return "ambiguous", [item[2] for item in sorted(top, key=lambda item: (-item[1], item[2].id))]
    return "resolved", top[0][2]


def _task_receipt(conn, task) -> tuple[Optional[str], list[str]]:
    """Read one safe human-readable receipt plus structured commit identities."""
    from hermes_cli import kanban_db as kb

    receipt = None
    commits: list[str] = []
    branch = _safe_branch(task.branch_name)
    if branch:
        commits.append(f"branch {branch}")
    for run in reversed(kb.list_runs(conn, task.id)):
        if receipt is None:
            receipt = _safe_text(run.summary or run.error)
        metadata = run.metadata if isinstance(run.metadata, dict) else {}
        commit = _safe_commit(metadata.get("commit") or metadata.get("commit_sha"))
        if commit:
            commits.append(f"commit {commit}")
        run_branch = _safe_branch(metadata.get("branch") or metadata.get("branch_name"))
        if run_branch:
            commits.append(f"branch {run_branch}")
    return receipt, list(dict.fromkeys(commits))


def _bounded(response: str) -> str:
    return response if len(response) <= MAX_PROGRESS_RESPONSE_CHARS else response[:MAX_PROGRESS_RESPONSE_CHARS].rstrip()


def _format_progress(conn, root) -> str:
    from hermes_cli import kanban_db as kb

    graph_ids = _graph_task_ids(conn, root.id)
    tasks = [kb.get_task(conn, task_id) for task_id in graph_ids]
    tasks = [task for task in tasks if task is not None]
    children = [task for task in tasks if task.id != root.id]
    completed = sum(task.status == "done" for task in children)
    failed = sum(task.status in _FAILED_STATUSES for task in children)
    running = sum(task.status == "running" for task in children)
    next_tasks = [task for task in children if task.status in _NONTERMINAL_STATUSES]
    next_tasks.sort(key=lambda task: (task.priority * -1, task.created_at, task.id))

    lines = [
        f"Progress for `{root.id}` — {_safe_text(root.title, limit=160)}: "
        f"{completed} completed, {failed} failed, {running} running."
    ]
    if next_tasks:
        next_text = "; ".join(
            f"`{task.id}` {_safe_text(task.title, limit=100)} ({task.status})"
            for task in next_tasks[:_MAX_NEXT_TASKS]
        )
        lines.append(f"Next: {next_text}.")

    receipt = None
    identifiers: list[str] = []
    receipt_task_id = None
    latest_comment = None
    for task in tasks:
        task_receipt, task_identifiers = _task_receipt(conn, task)
        if receipt is None and task_receipt:
            receipt = task_receipt
            receipt_task_id = task.id
        identifiers.extend(task_identifiers)
        for comment in kb.list_comments(conn, task.id):
            candidate_key = (comment.created_at, comment.id)
            if latest_comment is None or candidate_key > (latest_comment.created_at, latest_comment.id):
                latest_comment = comment
    if receipt and receipt_task_id:
        lines.append(f"Latest receipt for `{receipt_task_id}`: {receipt}")
    if identifiers:
        lines.append("Structured metadata: " + ", ".join(dict.fromkeys(identifiers)) + ".")
    if latest_comment is not None:
        lines.append("Latest note: " + _safe_text(latest_comment.body) + ".")
    return _bounded(" ".join(lines))


def resolve_progress_query(
    request: object,
    *,
    source: ProgressSource,
    board: object,
) -> ProgressQueryResult:
    """Resolve a project-status question from one configured board, or fall through.

    The query executes no model/tool/git action and makes no Kanban mutation.
    Only a valid configured board and exact trusted source subscription enter the
    lookup path. Database failures deliberately preserve ordinary chat.
    """
    if not is_progress_query(request):
        return ProgressQueryResult(False, "", "irrelevant")
    if (
        not isinstance(board, str)
        or not _BOARD_RE.fullmatch(board)
        or not source.platform
        or not source.chat_id
    ):
        return ProgressQueryResult(False, "", "unavailable")
    try:
        from hermes_cli import kanban_db as kb

        with kb.connect(board=board) as conn:
            subscribed_ids = _source_subscription_task_ids(conn, source)
            root_ids = _roots_for_tasks(conn, subscribed_ids)
            if not root_ids:
                return ProgressQueryResult(
                    True,
                    "I couldn't find a subscribed project in this Discord conversation for that progress question.",
                    "no_match",
                )
            selection, value = _select_root(conn, root_ids, str(request))
            if selection == "no_match":
                return ProgressQueryResult(
                    True,
                    "I couldn't find a subscribed project matching that progress question in this Discord conversation.",
                    "no_match",
                )
            if selection == "ambiguous":
                choices = "; ".join(
                    f"`{task.id}` {_safe_text(task.title, limit=100)}"
                    for task in value[:_MAX_AMBIGUOUS_ROOTS]
                )
                return ProgressQueryResult(
                    True,
                    _bounded(f"I found multiple subscribed project roots. Please name one: {choices}."),
                    "ambiguous",
                )
            return ProgressQueryResult(True, _format_progress(conn, value), "resolved")
    except Exception:
        return ProgressQueryResult(False, "", "unavailable")
