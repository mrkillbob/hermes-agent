"""Read-only, source-scoped recall of Kanban project progress."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


BOARD = "tradingbot-burndown"


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with kb.connect(board=BOARD):
        pass
    return home


def _sub(conn, task_id, *, chat_id="trading-bot", thread_id="", delivery_metadata=None):
    kb.add_notify_sub(
        conn,
        task_id=task_id,
        platform="discord",
        chat_id=chat_id,
        thread_id=thread_id,
        chat_type="thread" if thread_id else "group",
        user_id="operator-1",
        delivery_metadata=delivery_metadata,
    )


def _task(
    conn,
    title,
    *,
    parents=(),
    status="ready",
    branch_name=None,
    created_by=None,
    idempotency_key=None,
    session_id=None,
):
    task_id = kb.create_task(
        conn,
        title=title,
        body="Untrusted body must never become a filesystem instruction: /Users/not-trusted.",
        initial_status="running",
        parents=parents,
        branch_name=branch_name,
        created_by=created_by,
        idempotency_key=idempotency_key,
        session_id=session_id,
    )
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
    return task_id


def _run(conn, task_id, *, status, outcome=None, summary=None, metadata=None, error=None):
    with kb.write_txn(conn):
        conn.execute(
            """
            INSERT INTO task_runs
                (task_id, status, started_at, ended_at, outcome, summary, metadata, error)
            VALUES (?, ?, 100, CASE WHEN ? = 'running' THEN NULL ELSE 101 END, ?, ?, ?, ?)
            """,
            (
                task_id,
                status,
                status,
                outcome,
                summary,
                json.dumps(metadata) if metadata is not None else None,
                error,
            ),
        )


def _source(
    *,
    chat_id="trading-bot",
    thread_id=None,
    reply_to_message_id=None,
    session_id=None,
):
    from gateway.progress_queries import ProgressSource

    return ProgressSource(
        platform="discord",
        chat_id=chat_id,
        thread_id=thread_id,
        reply_to_message_id=reply_to_message_id,
        session_id=session_id,
    )


def test_burndown_question_summarizes_graph_receipts_and_remaining_work(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    with kb.connect(board=BOARD) as conn:
        root = _task(conn, "Exception Burndown")
        done = _task(conn, "Narrow credential exception", parents=[root], status="done")
        failed = _task(conn, "Repair provider exception", parents=[root], status="failed")
        running = _task(conn, "Validate exception evidence", parents=[root], status="running")
        next_task = _task(conn, "Publish acceptance receipt", parents=[root], status="ready")
        _sub(conn, root)
        _run(
            conn,
            done,
            status="done",
            outcome="completed",
            summary="Closed with local evidence; prose commit deadbeef must not be trusted.",
            metadata={"commit": "a1b2c3d4", "branch": "codex/burndown-repair"},
        )
        _run(conn, failed, status="failed", outcome="gave_up", error="worker gave up")
        _run(conn, running, status="running", summary="Worker is validating receipts")
        kb.add_comment(conn, next_task, "worker", "Need the final acceptance receipt.")

    result = resolve_progress_query(
        "How did the burndown go and what else do we need to do?",
        source=_source(),
        board=BOARD,
    )

    assert result.handled is True
    assert result.reason == "resolved"
    assert "Exception Burndown" in result.response
    assert "1 completed" in result.response
    assert "1 failed" in result.response
    assert "1 running" in result.response
    assert "Publish acceptance receipt" in result.response
    assert "a1b2c3d4" in result.response
    assert "codex/burndown-repair" in result.response
    assert "deadbeef" not in result.response


def test_progress_query_isolated_to_trusted_source_subscription(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    with kb.connect(board=BOARD) as conn:
        root = _task(conn, "Exception Burndown", status="done")
        _sub(conn, root, chat_id="private-audit")

    result = resolve_progress_query(
        "How did the burndown go?", source=_source(chat_id="trading-bot"), board=BOARD
    )

    assert result.handled is True
    assert result.reason == "no_match"
    assert "couldn't find a subscribed project" in result.response
    assert "Exception Burndown" not in result.response


def test_progress_query_uses_explicit_board_not_current_or_other_board(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    with kb.connect(board="other-board") as conn:
        root = _task(conn, "Exception Burndown", status="done")
        _sub(conn, root)

    result = resolve_progress_query(
        "How did the burndown go?", source=_source(), board=BOARD
    )

    assert result.handled is True
    assert result.reason == "no_match"
    assert "Exception Burndown" not in result.response


def test_progress_query_returns_ambiguity_for_multiple_matching_roots(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    with kb.connect(board=BOARD) as conn:
        first = _task(conn, "July Exception Burndown")
        second = _task(conn, "August Exception Burndown")
        _sub(conn, first)
        _sub(conn, second)

    result = resolve_progress_query(
        "How did the exception burndown go?", source=_source(), board=BOARD
    )

    assert result.handled is True
    assert result.reason == "ambiguous"
    assert first in result.response
    assert second in result.response


def test_single_subscribed_root_does_not_override_zero_topic_score(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    with kb.connect(board=BOARD) as conn:
        root = _task(conn, "Exception Burndown", status="done")
        _sub(conn, root)

    result = resolve_progress_query(
        "How did the release go?", source=_source(), board=BOARD
    )

    assert result.handled is True
    assert result.reason == "no_match"
    assert "Exception Burndown" not in result.response


def test_generic_how_did_it_go_requires_trusted_linkage(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    with kb.connect(board=BOARD) as conn:
        root = _task(conn, "Exception Burndown", status="done")
        _sub(conn, root)

    result = resolve_progress_query("How did it go?", source=_source(), board=BOARD)

    assert result.handled is True
    assert result.reason == "no_match"


@pytest.mark.parametrize("link_kind", ["reply", "session"])
def test_generic_how_did_it_go_resolves_one_trusted_linked_root(kanban_home, link_kind):
    from gateway.progress_queries import resolve_progress_query

    with kb.connect(board=BOARD) as conn:
        root = _task(
            conn,
            "Exception Burndown",
            status="done",
            created_by="specialist-routing",
            idempotency_key="specialist-routing:discord::trading-bot::request-42",
            session_id="session-42",
        )
        _sub(conn, root, delivery_metadata={"origin_message_id": "request-42"})

    source = (
        _source(reply_to_message_id="request-42")
        if link_kind == "reply"
        else _source(session_id="session-42")
    )
    result = resolve_progress_query("How did it go?", source=source, board=BOARD)

    assert result.handled is True
    assert result.reason == "resolved"
    assert root in result.response


def test_explicit_task_id_selects_one_root_when_topic_is_ambiguous(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    with kb.connect(board=BOARD) as conn:
        first = _task(conn, "July Exception Burndown")
        second = _task(conn, "August Exception Burndown", status="done")
        _sub(conn, first)
        _sub(conn, second)

    result = resolve_progress_query(
        f"How did {second} go?", source=_source(), board=BOARD
    )

    assert result.handled is True
    assert result.reason == "resolved"
    assert second in result.response
    assert first not in result.response


def test_non_progress_message_falls_through_without_database_response(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    result = resolve_progress_query("What should I cook for dinner?", source=_source(), board=BOARD)

    assert result.handled is False
    assert result.reason == "irrelevant"
    assert result.response == ""


def test_question_shaped_action_request_falls_through_to_specialist_routing(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    result = resolve_progress_query(
        "Can you start an exception burndown and patch the confirmed failures?",
        source=_source(),
        board=BOARD,
    )

    assert result.handled is False
    assert result.reason == "irrelevant"


def test_mixed_progress_and_action_clause_falls_through_to_specialist_routing(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    result = resolve_progress_query(
        "How did the burndown go, and patch any remaining failures?",
        source=_source(),
        board=BOARD,
    )

    assert result.handled is False
    assert result.reason == "irrelevant"


def test_progress_output_is_bounded_and_redacts_secret_and_path_content(kanban_home):
    from gateway.progress_queries import MAX_PROGRESS_RESPONSE_CHARS, resolve_progress_query

    secret = "ultra-secret-value"
    path = "/Users/mikedemott/TradingBotV18/.env"
    with kb.connect(board=BOARD) as conn:
        root = _task(conn, "Exception Burndown", status="done")
        _sub(conn, root)
        _run(
            conn,
            root,
            status="done",
            outcome="completed",
            summary=(f"OPENAI_API_KEY={secret}; inspect {path}; " + "detail " * 2_000),
            metadata={"commit": "f00ba412", "branch": "codex/receipt"},
        )
        kb.add_comment(conn, root, "worker", f"TOKEN: {secret} and {path}")

    result = resolve_progress_query("How did the burndown go?", source=_source(), board=BOARD)

    assert result.handled is True
    assert len(result.response) <= MAX_PROGRESS_RESPONSE_CHARS
    assert secret not in result.response
    assert path not in result.response
    assert "f00ba412" in result.response


def test_progress_redacts_complete_multiline_secret_values_but_keeps_other_prose(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    with kb.connect(board=BOARD) as conn:
        root = _task(conn, "Exception Burndown", status="done")
        _sub(conn, root)
        _run(
            conn,
            root,
            status="done",
            outcome="completed",
            summary=(
                "Authorization: Bearer header-sentinel with trailing secret words\n"
                "X-API-Key: x-header-sentinel plus another secret word\n"
                "Proxy-Authorization: proxy-sentinel with another tail\n"
                "DATABASE_PASSWORD=database-sentinel has an unquoted multiword tail\n"
                "Unrelated acceptance evidence remains visible."
            ),
        )

    result = resolve_progress_query("How did the burndown go?", source=_source(), board=BOARD)

    assert result.handled is True
    assert "header-sentinel" not in result.response
    assert "trailing secret words" not in result.response
    assert "x-header-sentinel" not in result.response
    assert "another secret word" not in result.response
    assert "proxy-sentinel" not in result.response
    assert "another tail" not in result.response
    assert "database-sentinel" not in result.response
    assert "unquoted multiword tail" not in result.response
    assert "Unrelated acceptance evidence remains visible." in result.response


def test_progress_reads_existing_board_without_using_mutating_connection(kanban_home, monkeypatch):
    from gateway.progress_queries import resolve_progress_query

    with kb.connect(board=BOARD) as conn:
        root = _task(conn, "Exception Burndown", status="done")
        _sub(conn, root)

    def reject_mutating_connect(*args, **kwargs):
        raise AssertionError("progress lookup must not call kanban_db.connect")

    monkeypatch.setattr(kb, "connect", reject_mutating_connect)
    result = resolve_progress_query("How did the burndown go?", source=_source(), board=BOARD)

    assert result.handled is True
    assert result.reason == "resolved"
    assert root in result.response


def test_missing_board_is_unavailable_without_creating_files(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    missing_path = kb.kanban_db_path(board="missing-progress-board")
    assert not missing_path.exists()
    assert not missing_path.parent.exists()

    result = resolve_progress_query(
        "How did the burndown go?", source=_source(), board="missing-progress-board"
    )

    assert result.handled is False
    assert result.reason == "unavailable"
    assert not missing_path.exists()
    assert not missing_path.parent.exists()


def test_symlink_board_database_is_unavailable(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    with kb.connect(board=BOARD) as conn:
        root = _task(conn, "Exception Burndown", status="done")
        _sub(conn, root)
    source_path = kb.kanban_db_path(board=BOARD)
    alias_path = kb.kanban_db_path(board="linked-progress-board")
    alias_path.parent.mkdir(parents=True)
    alias_path.symlink_to(source_path)

    result = resolve_progress_query(
        "How did the burndown go?", source=_source(), board="linked-progress-board"
    )

    assert result.handled is False
    assert result.reason == "unavailable"


def test_progress_reply_includes_a_safe_latest_comment_alongside_run_receipt(kanban_home):
    from gateway.progress_queries import resolve_progress_query

    with kb.connect(board=BOARD) as conn:
        root = _task(conn, "Exception Burndown", status="running")
        _sub(conn, root)
        _run(conn, root, status="running", summary="Worker is checking the failure receipt.")
        kb.add_comment(conn, root, "operator", "Next, compare the remaining failed gate.")

    result = resolve_progress_query("How did the burndown go?", source=_source(), board=BOARD)

    assert result.handled is True
    assert "Worker is checking the failure receipt." in result.response
    assert "Next, compare the remaining failed gate." in result.response
