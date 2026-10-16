"""Exact-card ticks cannot sweep unrelated cards or newly produced children."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


@pytest.fixture
def conn(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    with kbc.connect_closing() as connection:
        yield connection


def _snapshot(conn, ids):
    return {
        table: [tuple(row) for row in conn.execute(
            f"SELECT * FROM {table} WHERE {column} IN ({','.join('?' for _ in ids)}) ORDER BY rowid",
            ids,
        )]
        for table, column in (("tasks", "id"), ("task_events", "task_id"),
                              ("task_runs", "task_id"), ("task_comments", "task_id"))
    }


@pytest.mark.parametrize("dry_run", [False, True])
def test_exact_tick_leaves_unselected_cards_and_claims_unchanged(conn, dry_run):
    selected = kb.create_task(conn, title="selected", assignee="default")
    untouched = [kb.create_task(conn, title="unselected", assignee=None)]
    for status in ("todo", "blocked", "running", "done", "triage", "review"):
        tid = kb.create_task(conn, title=status, assignee="task-orchestrator")
        conn.execute("UPDATE tasks SET status = ?, claim_lock = ?, claim_expires = 1 WHERE id = ?",
                     (status, "old-owner" if status == "running" else None, tid))
        kb.add_comment(conn, tid, "human", "preserve this evidence")
        untouched.append(tid)
    conn.commit()
    before = _snapshot(conn, untouched)
    spawned = []

    def spawn(task, workspace, board=None):
        spawned.append(task.id)
        kb.create_task(conn, title="new child", assignee="default")
        return os.getpid()

    result = kbd.dispatch_once(conn, task_ids=[selected, selected], spawn_fn=spawn, dry_run=dry_run)
    assert [entry[0] for entry in result.spawned] == [selected]
    assert spawned == ([] if dry_run else [selected])
    assert _snapshot(conn, untouched) == before
    assert result.reclaimed == result.promoted == 0
    if not dry_run:
        child = conn.execute("SELECT id FROM tasks WHERE title = 'new child'").fetchone()[0]
        child_before = _snapshot(conn, [child])
        kbd.dispatch_once(conn, task_ids=[selected], spawn_fn=spawn)
        assert spawned == [selected]
        assert _snapshot(conn, [child]) == child_before


@pytest.mark.parametrize("selector", [[], [""], ["  "], [None], [1], "t_abcd", ["t_*"], ["t_a", ""]])
def test_invalid_selector_rejected_before_lock_or_mutation(conn, monkeypatch, selector):
    tid = kb.create_task(conn, title="unchanged", assignee="default")
    before = _snapshot(conn, [tid])

    def forbidden_lock():
        pytest.fail("invalid selection reached host admission lock")

    monkeypatch.setattr(kbc, "_dispatch_host_admission_lock", forbidden_lock)
    with pytest.raises(ValueError):
        kbd.dispatch_once(conn, task_ids=selector)
    assert _snapshot(conn, [tid]) == before


def test_exact_unknown_id_does_not_fall_back_to_all_cards(conn):
    tid = kb.create_task(conn, title="unchanged", assignee="default")
    before = _snapshot(conn, [tid])
    result = kbd.dispatch_once(conn, task_ids=["t_does_not_exist"])
    assert result.spawned == []
    assert _snapshot(conn, [tid]) == before


@pytest.mark.parametrize("status", ["todo", "blocked", "running", "done", "triage"])
def test_exact_selection_does_not_recover_or_promote_selected_other_lanes(conn, status):
    tid = kb.create_task(conn, title="staged or held", assignee="default")
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, tid))
    conn.commit()
    before = _snapshot(conn, [tid])
    result = kbd.dispatch_once(conn, task_ids=[tid])
    assert result.spawned == []
    assert _snapshot(conn, [tid]) == before


def test_exact_selection_keeps_host_capacity_from_unselected_running_cards(conn):
    running = kb.create_task(conn, title="already running", assignee="default")
    kb.claim_task(conn, running)
    selected = kb.create_task(conn, title="selected", assignee="default")
    before = _snapshot(conn, [selected, running])
    result = kbd.dispatch_once(conn, task_ids=[selected], max_in_progress=1)
    assert result.host_capacity_saturated
    assert result.spawned == []
    assert _snapshot(conn, [selected, running]) == before


def test_exact_selection_keeps_dependency_claim_gate(conn):
    parent = kb.create_task(conn, title="unfinished parent", assignee="default")
    child = kb.create_task(conn, title="selected child", assignee="default")
    kb.link_tasks(conn, parent, child)
    conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (child,))
    conn.commit()
    result = kbd.dispatch_once(conn, task_ids=[child], spawn_fn=lambda *_: pytest.fail("dependency bypass"))
    assert result.spawned == []
    assert kb.get_task(conn, child).status == "todo"


def test_cli_exact_ids_dispatch_only_selected_cards(conn, monkeypatch, capsys):
    from hermes_cli import kanban_ops, kanban_parser
    selected = kb.create_task(conn, title="selected", assignee="default")
    other = kb.create_task(conn, title="other", assignee="default")
    before = _snapshot(conn, [other])
    parser = argparse.ArgumentParser()
    kanban_parser.build_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["kanban", "dispatch", "--task-id", selected, "--task-id", selected,
                              "--dry-run", "--json"])
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"kanban": {}})
    assert kanban_ops._cmd_dispatch(args) == 0
    import json
    assert [item["task_id"] for item in json.loads(capsys.readouterr().out)["spawned"]] == [selected]
    assert _snapshot(conn, [other]) == before


def test_exact_selection_includes_only_named_review_cards(conn, monkeypatch):
    monkeypatch.setattr(kbd, "review_dispatch_enabled", lambda: True)
    chosen = kb.create_task(conn, title="chosen review", assignee="default")
    other = kb.create_task(conn, title="other review", assignee="default")
    conn.execute("UPDATE tasks SET status = 'review'")
    conn.commit()
    before = _snapshot(conn, [other])
    result = kbd.dispatch_once(conn, task_ids=(chosen,), spawn_fn=lambda *_: os.getpid())
    assert [entry[0] for entry in result.spawned] == [chosen]
    assert _snapshot(conn, [other]) == before
    assert kb.get_task(conn, chosen).status == "running"


def test_exact_selection_respects_dispatch_profile_allowlist(conn):
    (Path(os.environ["HERMES_HOME"]) / "config.yaml").write_text(
        "kanban:\n  dispatch_profiles: []\n", encoding="utf-8",
    )
    tid = kb.create_task(conn, title="selected", assignee="default")
    result = kbd.dispatch_once(conn, task_ids=[tid], spawn_fn=lambda *_: pytest.fail("profile gate bypass"))
    assert result.spawned == []
    assert kb.get_task(conn, tid).status == "ready"


def test_exact_selection_keeps_model_capacity_between_selected_cards(conn):
    ids = [kb.create_task(conn, title=title, assignee="default", provider_override="openai",
                          model_override="test-model") for title in ("one", "two")]
    conn.execute("UPDATE tasks SET priority = 10 WHERE id = ?", (ids[0],))
    conn.commit()
    result = kbd.dispatch_once(conn, task_ids=list(reversed(ids)), dry_run=True, max_in_progress_per_model=1)
    assert [entry[0] for entry in result.spawned] == [ids[0]]
    assert [entry[0] for entry in result.skipped_per_model_capped] == [ids[1]]


def test_exact_selection_defers_on_memory_pressure(conn, monkeypatch):
    tid = kb.create_task(conn, title="selected", assignee="default")
    before = _snapshot(conn, [tid])
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "critical")
    result = kbd.dispatch_once(conn, task_ids=[tid])
    assert result.spawned == []
    assert result.memory_pressure == "critical"
    assert _snapshot(conn, [tid]) == before


def test_selector_deduplicates_stably_without_treating_none_as_empty():
    from hermes_cli.kanban_dispatch_tick import normalize_task_ids
    assert normalize_task_ids(None) is None
    assert normalize_task_ids(["t_second", "t_first", "t_second"]) == ("t_second", "t_first")
