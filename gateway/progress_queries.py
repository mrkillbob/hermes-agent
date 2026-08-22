"""Deterministic, read-only Kanban progress replies for trusted chat sources.

This is deliberately a gateway edge helper, rather than a model prompt or a
Kanban tool.  It reads only cards that explicitly subscribed the current
platform/chat/thread source and produces a bounded response without creating
work, following paths, or interpreting prose as authority.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from gateway.configured_board import configured_board_db_path


MAX_PROGRESS_RESPONSE_CHARS = 1_600
_MAX_GRAPH_TASKS = 24
_MAX_NEXT_TASKS = 3
_MAX_AMBIGUOUS_ROOTS = 3
_MAX_RECEIPT_CHARS = 280
_MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
_SNAPSHOT_COPY_CHUNK_BYTES = 1024 * 1024
_SNAPSHOT_ATTEMPTS = 3
_BOARD_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TASK_ID_RE = re.compile(r"\bt_[a-z0-9]+\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9]{3,}")
_SECRET_KEY_PATTERN = r"[a-z0-9_-]*(?:token|secret|password|authorization|api[_-]?key)"
_SECRET_LINE_RE = re.compile(
    rf"(?i)^(\s*(?:{_SECRET_KEY_PATTERN})\s*(?:=|:)\s*).*$"
)
_SECRET_RE = re.compile(
    rf"(?i)\b(?:{_SECRET_KEY_PATTERN})"
    r"\s*(?:=|:)\s*(?:bearer\s+)?[^\n]*"
)
_PATH_RE = re.compile(r"(?:(?:[A-Za-z]:)?[\\/](?:Users|home|private|tmp|var|etc)[^\s,;]*)")
_PROSE_COMMIT_RE = re.compile(r"\b[0-9a-f]{7,64}\b", re.IGNORECASE)
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")
_STOP_WORDS = frozenset(
    {
        "about", "also", "and", "could", "did", "does", "else", "from",
        "have", "how", "need", "progress", "project", "status", "that", "the", "this",
        "what", "when", "where", "with", "work", "would", "your",
    }
)
_QUESTION_MARKERS = (
    "how did ",
    "how is ",
    "how's ",
    "what remains",
    "what is left",
    "what's left",
    "where are we",
    "give me an update",
    "status of ",
    "progress on ",
)
_PROGRESS_TERMS = ("burndown", "status", "progress", "remaining", "left", "next", "complete")
_ACTION_VERBS = (
    r"start|fix|patch|audit|implement|create|run|investigate|change|finish|complete|"
    r"delegate|resolve|remediate|address|repair|review|validate|verify|test|debug|deploy|update"
)
_ACTION_REQUEST_RE = re.compile(
    rf"(?:"
    rf"^(?:(?:can|could|would)\s+you\s+|please\s+)?(?:{_ACTION_VERBS})\b"
    rf"|\b(?:and|then|also)\s+(?:(?:then|please)\s+)?(?:{_ACTION_VERBS})\b"
    rf"|\b(?:can|could|would|will|should)\s+(?:you|we)\s+(?:{_ACTION_VERBS})\b"
    rf"|[;,.!?]\s*(?:please\s+)?(?:{_ACTION_VERBS})\b"
    rf"|\b(?:ask|tell|have)\s+(?:the\s+)?(?:bot|agent|hermes|it|them)\s+"
    rf"(?:to\s+)?(?:{_ACTION_VERBS})\b"
    rf"|\b(?:ask|tell|have|get|let)\s+(?:the\s+)?(?:[a-z0-9_-]+\s+){{0,4}}"
    rf"(?:to\s+)?(?:{_ACTION_VERBS})\b"
    rf"|\b(?:i\s+)?(?:want|need)\s+(?:you|it|them|the\s+bot|hermes)\s+to\s+"
    rf"(?:{_ACTION_VERBS})\b"
    rf"|\b(?:(?:i|we)\s+would|(?:i|we)['’]d)\s+like\s+"
    rf"(?:(?:you|it|them|the\s+bot|hermes)\s+)?to\s+(?:{_ACTION_VERBS})\b"
    rf")",
    re.IGNORECASE,
)
_READ_ONLY_UPDATE_RE = re.compile(r"\bupdate\s+me\s+(?:on|about)\b", re.IGNORECASE)
_GENERIC_PROGRESS_RE = re.compile(r"^how did it go\?$", re.IGNORECASE)
_NONTERMINAL_STATUSES = frozenset({"triage", "todo", "ready", "review"})
_FAILED_STATUSES = frozenset({"failed", "timed_out", "crashed"})


@dataclass(frozen=True)
class ProgressSource:
    """Trusted ingress identity used for exact subscription matching."""

    platform: str
    chat_id: str
    thread_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None


@dataclass(frozen=True)
class ProgressQueryResult:
    """A handled reply is safe to send without invoking the normal agent."""

    handled: bool
    response: str
    reason: str


class _SnapshotChangedError(OSError):
    """The captured source identity or byte extent changed during copy."""


def is_progress_query(request: object) -> bool:
    """Recognize only ordinary, bounded project-status questions."""
    if not isinstance(request, str):
        return False
    normalized = " ".join(request.casefold().split())
    if not normalized or len(normalized) > 4_000:
        return False
    action_candidate = _READ_ONLY_UPDATE_RE.sub("status on", normalized)
    if _ACTION_REQUEST_RE.search(action_candidate):
        return False
    has_marker = any(marker in normalized for marker in _QUESTION_MARKERS) or bool(
        _READ_ONLY_UPDATE_RE.search(normalized)
    )
    has_status_term = any(term in normalized for term in _PROGRESS_TERMS)
    return has_marker or ("?" in normalized and has_status_term)


def _safe_text(value: object, *, limit: int = _MAX_RECEIPT_CHARS) -> str:
    raw = str(value or "").replace("\x00", " ")
    redacted_lines = []
    for line in raw.splitlines() or [raw]:
        line = _SECRET_LINE_RE.sub(lambda match: match.group(1) + "[redacted]", line)
        redacted_lines.append(_SECRET_RE.sub("[redacted]", line))
    text = " ".join("\n".join(redacted_lines).split())
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


def _source_subscription_rows(conn, source: ProgressSource) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT task_id, delivery_metadata FROM kanban_notify_subs
         WHERE platform = ? AND chat_id = ? AND thread_id = ?
         ORDER BY task_id ASC
        """,
        (source.platform, source.chat_id, source.thread_id or ""),
    ).fetchall()
    return list(rows)


def _regular_file_signature(path: Path) -> tuple[int, int, int, int]:
    file_stat = path.lstat()
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise OSError("Kanban snapshot source must be a regular non-symlink file")
    return (file_stat.st_dev, file_stat.st_ino, file_stat.st_size, file_stat.st_mtime_ns)


def _optional_file_signature(path: Path) -> Optional[tuple[int, int, int, int]]:
    try:
        return _regular_file_signature(path)
    except FileNotFoundError:
        return None


def _snapshot_source_state(
    source: Path,
) -> tuple[tuple[int, int, int, int], Optional[tuple[int, int, int, int]]]:
    return _regular_file_signature(source), _optional_file_signature(Path(str(source) + "-wal"))


def _copy_snapshot_file(
    source: Path,
    destination: Path,
    expected: tuple[int, int, int, int],
) -> None:
    """Copy the captured byte extent, then perform one bounded EOF probe."""
    expected_size = expected[2]
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise _SnapshotChangedError("Kanban snapshot source changed before copy") from exc
    try:
        opened_stat = os.fstat(descriptor)
        opened_signature = (
            opened_stat.st_dev,
            opened_stat.st_ino,
            opened_stat.st_size,
            opened_stat.st_mtime_ns,
        )
        if opened_signature != expected or not stat.S_ISREG(opened_stat.st_mode):
            raise _SnapshotChangedError("Kanban snapshot source changed before copy")
        remaining = expected_size
        with destination.open("xb") as writer:
            while remaining:
                chunk = os.read(descriptor, min(_SNAPSHOT_COPY_CHUNK_BYTES, remaining))
                if not chunk:
                    raise _SnapshotChangedError(
                        "Kanban snapshot source changed during copy"
                    )
                if writer.write(chunk) != len(chunk):
                    raise OSError("Kanban snapshot destination write was incomplete")
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise _SnapshotChangedError("Kanban snapshot source changed during copy")

        closed_stat = os.fstat(descriptor)
        closed_signature = (
            closed_stat.st_dev,
            closed_stat.st_ino,
            closed_stat.st_size,
            closed_stat.st_mtime_ns,
        )
        try:
            path_signature = _regular_file_signature(source)
        except OSError as exc:
            raise _SnapshotChangedError("Kanban snapshot source changed after copy") from exc
        if closed_signature != expected or path_signature != expected:
            raise _SnapshotChangedError("Kanban snapshot source changed after copy")
    finally:
        os.close(descriptor)
    if destination.stat().st_size != expected_size:
        raise _SnapshotChangedError("Kanban snapshot source changed during copy")


@contextmanager
def _open_existing_board_readonly(board: str):
    """Query a validated private DB+WAL snapshot without opening the source DB."""
    source = configured_board_db_path(board)
    wal_source = Path(str(source) + "-wal")

    for _attempt in range(_SNAPSHOT_ATTEMPTS):
        before = _snapshot_source_state(source)
        total_size = before[0][2] + (before[1][2] if before[1] is not None else 0)
        if total_size > _MAX_SNAPSHOT_BYTES:
            raise OSError("Kanban snapshot exceeds the bounded copy limit")

        with tempfile.TemporaryDirectory(prefix="hermes-progress-") as temp_dir:
            try:
                snapshot = Path(temp_dir) / "kanban.db"
                _copy_snapshot_file(source, snapshot, before[0])
                if before[1] is not None:
                    _copy_snapshot_file(wal_source, Path(str(snapshot) + "-wal"), before[1])
                if _snapshot_source_state(source) != before:
                    raise _SnapshotChangedError(
                        "Kanban board changed during snapshot copy"
                    )

            except _SnapshotChangedError:
                continue

            conn = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)
            try:
                conn.row_factory = sqlite3.Row
                checks = conn.execute("PRAGMA quick_check").fetchall()
                if not checks or any(str(row[0]).casefold() != "ok" for row in checks):
                    raise sqlite3.DatabaseError("Kanban snapshot integrity check failed")
                yield conn
                return
            finally:
                conn.close()
    raise OSError("Kanban board changed during bounded snapshot attempts")


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


def _graph_task_ids(conn, root_id: str) -> tuple[list[str], bool]:
    """Return bounded graph IDs and whether unvisited descendants remain."""
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
    truncated = any(task_id not in seen for task_id in frontier)
    return ordered, truncated


def _query_terms(request: str) -> set[str]:
    return {
        word for word in _WORD_RE.findall(request.casefold())
        if word not in _STOP_WORDS and not word.startswith("t_")
    }


def _root_has_trusted_linkage(
    conn,
    root_id: str,
    source: ProgressSource,
    subscription_rows: list[sqlite3.Row],
) -> bool:
    """Bind a generic follow-up to structured reply provenance only."""
    from hermes_cli import kanban_db as kb

    graph_ids = set(_graph_task_ids(conn, root_id)[0])
    reply_id = str(source.reply_to_message_id or "")
    if not reply_id:
        return False
    for row in subscription_rows:
        if str(row["task_id"]) not in graph_ids:
            continue
        try:
            metadata = json.loads(row["delivery_metadata"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        if isinstance(metadata, dict) and any(
            str(metadata.get(key) or "") == reply_id
            for key in ("origin_message_id", "message_id", "reply_to_message_id", "prompt_message_id")
        ):
            return True
    for task_id in graph_ids:
        task = kb.get_task(conn, task_id)
        key = str(task.idempotency_key or "") if task is not None else ""
        if (
            task is not None
            and task.created_by == "specialist-routing"
            and key.startswith("specialist-routing:")
            and key.rsplit(":", 1)[-1] == reply_id
        ):
            return True
    return False


def _select_root(
    conn,
    root_ids: list[str],
    request: str,
    *,
    source: ProgressSource,
    subscription_rows: list[sqlite3.Row],
):
    """Return one root, or a bounded ambiguity result.  Never guess ties."""
    from hermes_cli import kanban_db as kb

    explicit_ids = {match.casefold() for match in _TASK_ID_RE.findall(request)}
    terms = _query_terms(request)
    ranked = []
    for root_id in root_ids:
        graph_ids, _truncated = _graph_task_ids(conn, root_id)
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
        normalized = " ".join(request.casefold().split())
        if _GENERIC_PROGRESS_RE.fullmatch(normalized):
            linked = [
                kb.get_task(conn, root_id)
                for root_id in root_ids
                if _root_has_trusted_linkage(
                    conn, root_id, source, subscription_rows
                )
            ]
            linked = [task for task in linked if task is not None]
            if len(linked) == 1:
                return "resolved", linked[0]
            if len(linked) > 1:
                return "ambiguous", linked
        return "no_match", None
    top_score = max(item[0] for item in ranked)
    top = [item for item in ranked if item[0] == top_score]
    if len(top) != 1:
        return "ambiguous", [item[2] for item in sorted(top, key=lambda item: (-item[1], item[2].id))]
    return "resolved", top[0][2]


def _task_receipt(
    conn, task
) -> tuple[Optional[tuple[int, int, int]], Optional[str], list[str]]:
    """Read the newest safe receipt key plus structured commit identities."""
    from hermes_cli import kanban_db as kb

    receipt = None
    receipt_key = None
    commits: list[str] = []
    branch = _safe_branch(task.branch_name)
    if branch:
        commits.append(f"branch {branch}")
    for run in kb.list_runs(conn, task.id):
        candidate = _safe_text(run.summary or run.error)
        key = (
            run.ended_at if run.ended_at is not None else run.started_at,
            run.started_at,
            run.id,
        )
        if candidate and (receipt_key is None or key > receipt_key):
            receipt = candidate
            receipt_key = key
        metadata = run.metadata if isinstance(run.metadata, dict) else {}
        commit = _safe_commit(metadata.get("commit") or metadata.get("commit_sha"))
        if commit:
            commits.append(f"commit {commit}")
        run_branch = _safe_branch(metadata.get("branch") or metadata.get("branch_name"))
        if run_branch:
            commits.append(f"branch {run_branch}")
    return receipt_key, receipt, list(dict.fromkeys(commits))


def _bounded(response: str) -> str:
    return response if len(response) <= MAX_PROGRESS_RESPONSE_CHARS else response[:MAX_PROGRESS_RESPONSE_CHARS].rstrip()


def _format_progress(conn, root) -> str:
    from hermes_cli import kanban_db as kb

    graph_ids, truncated = _graph_task_ids(conn, root.id)
    tasks = [kb.get_task(conn, task_id) for task_id in graph_ids]
    tasks = [task for task in tasks if task is not None]
    children = [task for task in tasks if task.id != root.id]
    completed = sum(task.status == "done" for task in tasks)
    failed = sum(task.status in _FAILED_STATUSES for task in tasks)
    blocked = sum(task.status == "blocked" for task in tasks)
    running = sum(task.status == "running" for task in tasks)
    next_tasks = [task for task in children if task.status in _NONTERMINAL_STATUSES]
    next_tasks.sort(key=lambda task: (task.priority * -1, task.created_at, task.id))

    lines = [
        f"Progress for `{root.id}` — {_safe_text(root.title, limit=160)}: "
        f"{completed} completed, {failed} failed, {blocked} blocked, {running} running."
    ]
    if truncated:
        lines.append(
            f"Scope: counts and receipt cover only the first {len(tasks)} tasks "
            "in this bounded graph traversal."
        )
    if next_tasks:
        next_text = "; ".join(
            f"`{task.id}` {_safe_text(task.title, limit=100)} ({task.status})"
            for task in next_tasks[:_MAX_NEXT_TASKS]
        )
        next_label = (
            f"Next (partial; first {len(tasks)} tasks only)" if truncated else "Next"
        )
        lines.append(f"{next_label}: {next_text}.")

    receipt = None
    identifiers: list[str] = []
    receipt_task_id = None
    receipt_key = None
    latest_comment = None
    for task in tasks:
        task_receipt_key, task_receipt, task_identifiers = _task_receipt(conn, task)
        if task_receipt and (receipt_key is None or task_receipt_key > receipt_key):
            receipt_key = task_receipt_key
            receipt = task_receipt
            receipt_task_id = task.id
        identifiers.extend(task_identifiers)
        for comment in kb.list_comments(conn, task.id):
            candidate_key = (comment.created_at, comment.id)
            if latest_comment is None or candidate_key > (latest_comment.created_at, latest_comment.id):
                latest_comment = comment
    if receipt and receipt_task_id:
        if truncated:
            lines.append(
                f"Newest receipt within first {len(tasks)} tasks for "
                f"`{receipt_task_id}`: {receipt}"
            )
        else:
            lines.append(f"Latest receipt for `{receipt_task_id}`: {receipt}")
    if identifiers:
        lines.append("Structured metadata: " + ", ".join(dict.fromkeys(identifiers)) + ".")
    if latest_comment is not None:
        if truncated:
            lines.append(
                f"Newest note within first {len(tasks)} tasks: "
                + _safe_text(latest_comment.body)
                + "."
            )
        else:
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
        with _open_existing_board_readonly(board) as conn:
            subscription_rows = _source_subscription_rows(conn, source)
            subscribed_ids = [str(row["task_id"]) for row in subscription_rows]
            root_ids = _roots_for_tasks(conn, subscribed_ids)
            if not root_ids:
                return ProgressQueryResult(
                    True,
                    "I couldn't find a subscribed project in this Discord conversation for that progress question.",
                    "no_match",
                )
            selection, value = _select_root(
                conn,
                root_ids,
                str(request),
                source=source,
                subscription_rows=subscription_rows,
            )
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
