"""CLI ownership tests for durable conversation worktrees."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import cli as cli_module
import hermes_state


@dataclass(frozen=True)
class _Binding:
    root_session_id: str
    path: Path
    branch: str
    base_commit: str


class _SessionDB:
    def __init__(self, *, roots: dict[str, str] | None = None) -> None:
        self.roots = roots or {}
        self.ended: list[str] = []

    def get_conversation_root(self, session_id: str) -> str:
        return self.roots.get(session_id, session_id)

    def end_session(self, session_id: str, _reason: str) -> None:
        self.ended.append(session_id)

    def delete_session_if_empty(self, _session_id: str) -> bool:
        return False

    def create_session(self, **_kwargs) -> None:
        return None


class _Manager:
    def __init__(self) -> None:
        self.bound_roots: list[str] = []
        self.resolved_roots: list[str] = []
        self.events: list[str] = []
        self.fail_next = False

    @staticmethod
    def _binding(root_session_id: str) -> _Binding:
        return _Binding(
            root_session_id=root_session_id,
            path=Path("/repo/.worktrees") / root_session_id,
            branch=f"hermes/session/{root_session_id}",
            base_commit="a" * 40,
        )

    def bind_new_root_session(self, root_session_id: str, *, conversation_kind: str):
        assert conversation_kind == "interactive"
        self.events.append("bind")
        if self.fail_next:
            raise RuntimeError("bootstrap failed")
        self.bound_roots.append(root_session_id)
        return self._binding(root_session_id)

    def resolve_existing_session(self, root_session_id: str):
        self.events.append("resolve")
        self.resolved_roots.append(root_session_id)
        return self._binding(root_session_id)


def _enabled_config() -> dict:
    config = deepcopy(cli_module.CLI_CONFIG)
    config["conversation_worktree"] = {
        "enabled": True,
        "source_worktree": "/repo/stable",
        "worktree_root": "/repo/.worktrees",
        "retain_until_explicit_cleanup": True,
    }
    return config


def _build_cli(monkeypatch, manager: _Manager, db: _SessionDB | None = None, **kwargs):
    db = db or _SessionDB()
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "cli")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setattr(cli_module, "CLI_CONFIG", _enabled_config())
    monkeypatch.setattr(hermes_state, "SessionDB", lambda: db)
    monkeypatch.setattr(cli_module, "_run_state_db_auto_maintenance", lambda _db: None)
    monkeypatch.setattr(cli_module, "_run_checkpoint_auto_maintenance", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "_build_cli_conversation_worktree_manager",
        lambda _config, _db: manager,
    )
    return cli_module.HermesCLI(compact=True, **kwargs), db


def test_enabled_conversation_policy_binds_initial_cli_root(monkeypatch):
    manager = _Manager()

    cli, _db = _build_cli(monkeypatch, manager)

    assert cli.working_directory == f"/repo/.worktrees/{cli.session_id}"
    assert manager.bound_roots == [cli.session_id]
    assert cli.agent is None
    assert "certified Git worktree" in cli.system_prompt


def test_cli_resume_reuses_durable_root_binding(monkeypatch):
    manager = _Manager()
    db = _SessionDB(roots={"compressed-tip": "durable-root"})

    cli, _db = _build_cli(monkeypatch, manager, db, resume="compressed-tip")

    assert manager.bound_roots == []
    assert manager.resolved_roots == ["durable-root"]
    assert cli.working_directory == "/repo/.worktrees/durable-root"
    assert cli.session_id == "compressed-tip"


def test_cli_resume_fails_closed_when_durable_binding_is_missing(monkeypatch):
    manager = _Manager()
    manager.resolve_existing_session = MagicMock(return_value=None)

    with pytest.raises(RuntimeError, match="no ready conversation worktree"):
        _build_cli(monkeypatch, manager, _SessionDB(), resume="missing")


def test_managed_resume_cannot_restore_the_stable_source_cwd(monkeypatch):
    manager = _Manager()
    cli, _db = _build_cli(monkeypatch, manager)
    managed = cli.working_directory

    cli._restore_session_cwd({"cwd": "/repo/stable"}, quiet=True)

    assert cli.working_directory == managed
    assert cli_module.os.environ["TERMINAL_CWD"] == managed


def test_managed_resume_retargets_to_the_selected_root_binding(monkeypatch):
    manager = _Manager()
    db = _SessionDB(roots={"selected-tip": "selected-root"})
    cli, _db = _build_cli(monkeypatch, manager, db)
    cli.session_id = "selected-tip"

    cli._restore_session_cwd({"cwd": "/repo/stable"}, quiet=True)

    assert manager.resolved_roots == ["selected-root"]
    assert cli.working_directory == "/repo/.worktrees/selected-root"


def test_list_only_cli_does_not_allocate_a_conversation_root(monkeypatch):
    manager = _Manager()

    cli, _db = _build_cli(
        monkeypatch,
        manager,
        manage_conversation_worktree=False,
    )

    assert manager.bound_roots == []
    assert cli._conversation_worktree_manager is None


def test_cli_new_allocates_another_worktree_before_agent_reset(monkeypatch):
    manager = _Manager()
    cli, _db = _build_cli(monkeypatch, manager)
    old = cli.working_directory
    manager.events.clear()
    cli.agent = MagicMock()
    cli.agent._memory_manager = None
    cli.agent.reset_session_state.side_effect = lambda: manager.events.append("reset")

    assert cli.new_session(silent=True) is True

    assert cli.working_directory != old
    assert manager.bound_roots == [manager.bound_roots[0], cli.session_id]
    assert manager.events.index("bind") < manager.events.index("reset")


def test_cli_new_binding_failure_leaves_old_session_usable(monkeypatch):
    manager = _Manager()
    cli, db = _build_cli(monkeypatch, manager)
    old_id = cli.session_id
    old_working_directory = cli.working_directory
    cli.conversation_history = [{"role": "user", "content": "keep me"}]
    manager.fail_next = True

    assert cli.new_session(silent=True) is False

    assert cli.session_id == old_id
    assert cli.working_directory == old_working_directory
    assert cli.conversation_history == [{"role": "user", "content": "keep me"}]
    assert db.ended == []


@pytest.mark.parametrize(
    ("source", "kanban_task", "expected"),
    [
        ("cli", "", False),
        ("tool", "", True),
        ("kanban", "task-1", True),
    ],
)
def test_legacy_worktree_is_mutually_exclusive_only_for_interactive_roots(
    monkeypatch, source, kanban_task, expected
):
    monkeypatch.setenv("HERMES_SESSION_SOURCE", source)
    if kanban_task:
        monkeypatch.setenv("HERMES_KANBAN_TASK", kanban_task)
    else:
        monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)

    assert cli_module._should_use_legacy_worktree(
        worktree=True,
        shorthand=False,
        config=_enabled_config(),
    ) is expected


def test_disabled_policy_preserves_legacy_worktree_flag(monkeypatch):
    config = _enabled_config()
    config["conversation_worktree"]["enabled"] = False
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "cli")

    assert cli_module._should_use_legacy_worktree(
        worktree=True,
        shorthand=False,
        config=config,
    ) is True
