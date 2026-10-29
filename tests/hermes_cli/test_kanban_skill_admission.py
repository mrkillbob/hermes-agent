"""Local task skill admission preserves external queues and effective skill roots."""
import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.config import atomic_config_write
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.profiles import resolve_profile_env


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    for key in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(key, raising=False)
    profile = home / "profiles" / "synthetic-worker"
    profile.mkdir(parents=True)
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused"})
    assert Path(resolve_profile_env("synthetic-worker")) == profile
    kb.create_board("skills-probe")
    with connect_closing(board="skills-probe") as conn:
        yield conn, profile


def create(conn, assignee, skills):
    return kb.create_task(conn, title="Synthetic skill readiness", assignee=assignee,
                          skills=skills, initial_status="todo", board="skills-probe")


def test_known_local_profile_missing_skill_refused_at_creation(board):
    conn, _ = board
    with pytest.raises(ValueError, match="skill"):
        create(conn, "synthetic-worker", ["definitely-absent-synthetic-skill"])


def test_known_local_profile_missing_skill_refused_at_assignment(board):
    conn, _ = board
    tid = create(conn, "external-control-plane", ["definitely-absent-synthetic-skill"])
    with pytest.raises(ValueError, match="skill"):
        kb.assign_task(conn, tid, "synthetic-worker")


def test_external_control_plane_does_not_require_local_profile(board):
    conn, _ = board
    tid = create(conn, "external-control-plane", ["externally-resolved-skill"])
    assert conn.execute("SELECT assignee FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "external-control-plane"


def test_existing_local_skill_is_admitted(board):
    conn, profile = board
    skill = profile / "skills" / "synthetic-present"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: synthetic-present\ndescription: fixture\n---\nPublic synthetic instructions.\n")
    assert create(conn, "synthetic-worker", ["synthetic-present"])


def skill_at(root, name="synthetic-shared", content="Synthetic instructions."):
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: fixture\n---\n{content}\n")
    return path


@pytest.mark.parametrize("tier", ["shared", "external", "create_dir"])
def test_assigned_profile_resolves_other_supported_roots(board, tmp_path, tier):
    conn, profile = board
    root = tmp_path / ".agents" / "skills" if tier == "shared" else tmp_path / tier
    skill_at(root)
    if tier != "shared":
        value = [str(root)] if tier == "external" else str(root)
        atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
            "external_dirs" if tier == "external" else "create_dir": value}})
    assert create(conn, "synthetic-worker", ["synthetic-shared"])


def test_creator_profile_skill_does_not_satisfy_assignee(board):
    conn, profile = board
    skill_at(profile.parent.parent / "skills")
    with pytest.raises(ValueError, match="unavailable"):
        create(conn, "synthetic-worker", ["synthetic-shared"])


def test_assignment_refusal_preserves_row_and_history(board):
    conn, _ = board
    tid = create(conn, "external-control-plane", ["missing-synthetic"])
    before = tuple(conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())
    events = [tuple(r) for r in conn.execute("SELECT * FROM task_events WHERE task_id=?", (tid,))]
    with pytest.raises(ValueError, match="unavailable"):
        kb.assign_task(conn, tid, "synthetic-worker")
    assert tuple(conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()) == before
    assert [tuple(r) for r in conn.execute("SELECT * FROM task_events WHERE task_id=?", (tid,))] == events


def test_unassigned_and_namespaced_plugin_tasks_remain_permissive(board, monkeypatch):
    conn, _ = board
    from hermes_cli import plugins

    def forbid_discovery(*args, **kwargs):
        pytest.fail("intake must not discover or execute plugin code")

    monkeypatch.setattr(plugins, "discover_plugins", forbid_discovery)
    assert create(conn, None, ["resolved-after-assignment"])
    assert create(conn, "synthetic-worker", ["synthetic-plugin:worker-only-skill"])


def test_context_is_restored_after_refusal(board):
    from agent.runtime_cwd import scoped_session_cwd, set_session_cwd, reset_session_cwd
    from hermes_constants import get_hermes_home
    conn, _ = board
    home = get_hermes_home()
    token = set_session_cwd("prior-logical-cwd")
    try:
        with pytest.raises(ValueError):
            create(conn, "synthetic-worker", ["missing-synthetic"])
        assert get_hermes_home() == home
        assert scoped_session_cwd() == "prior-logical-cwd"
    finally:
        reset_session_cwd(token)


@pytest.mark.parametrize("trusted", [True, False])
def test_explicit_task_project_uses_assigned_profile_trust(board, tmp_path, trusted):
    conn, profile = board
    repo = tmp_path / "project"
    (repo / ".git").mkdir(parents=True)
    skill_at(repo / ".agents" / "skills")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "trusted_project_dirs": [str(repo)] if trusted else []}})
    kwargs = dict(title="Project skill", assignee="synthetic-worker", skills=["synthetic-shared"],
                  workspace_kind="dir", workspace_path=str(repo), board="skills-probe", initial_status="todo")
    if trusted:
        assert kb.create_task(conn, **kwargs)
    else:
        with pytest.raises(ValueError, match="unavailable"):
            kb.create_task(conn, **kwargs)


def test_unmaterialized_project_worktree_uses_its_recorded_anchor(board, tmp_path):
    from hermes_cli import projects_db
    conn, profile = board
    repo = tmp_path / "project"
    (repo / ".git").mkdir(parents=True)
    skill_at(repo / ".agents" / "skills")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {"trusted_project_dirs": [str(repo)]}})
    with projects_db.connect_closing() as registry:
        project = projects_db.create_project(registry, name="Fixture", primary_path=str(repo))
    tid = kb.create_task(conn, title="Project skill", assignee="synthetic-worker",
                         skills=["synthetic-shared"], project_id=project,
                         board="skills-probe", initial_status="todo")
    row = kb.get_task(conn, tid)
    assert not Path(row.workspace_path).exists()
    assert kb.assign_task(conn, tid, "synthetic-worker")


def test_ambient_trusted_project_is_not_used_for_scratch_task(board, tmp_path):
    from agent.runtime_cwd import set_session_cwd, reset_session_cwd
    conn, profile = board
    repo = tmp_path / "ambient"
    (repo / ".git").mkdir(parents=True)
    skill_at(repo / ".agents" / "skills")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {"trusted_project_dirs": [str(repo)]}})
    token = set_session_cwd(str(repo))
    try:
        with pytest.raises(ValueError, match="unavailable"):
            create(conn, "synthetic-worker", ["synthetic-shared"])
    finally:
        reset_session_cwd(token)


def test_same_tier_collision_and_disabled_skill_refuse(board):
    conn, profile = board
    skill_at(profile / "skills" / "one", "same", "first")
    skill_at(profile / "skills" / "two", "same", "second")
    with pytest.raises(ValueError, match="unavailable"):
        create(conn, "synthetic-worker", ["same"])
    skill_at(profile / "skills", "disabled-synthetic")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {"disabled": ["disabled-synthetic"]}})
    with pytest.raises(ValueError, match="unavailable"):
        create(conn, "synthetic-worker", ["disabled-synthetic"])


def test_worker_preload_and_installation_are_not_called(board, monkeypatch):
    from tools import skills_tool
    conn, profile = board
    skill_at(profile / "skills", "plain-synthetic")
    monkeypatch.setattr(skills_tool, "skill_view", lambda *a, **k: pytest.fail("no instruction loading"))
    assert create(conn, "synthetic-worker", ["plain-synthetic"])
    assert json.loads(conn.execute("SELECT skills FROM tasks ORDER BY created_at LIMIT 1").fetchone()[0]) == ["plain-synthetic"]


@pytest.mark.parametrize("tilde", [True, False])
def test_future_directory_retains_project_skill_context(board, tmp_path, monkeypatch, tilde):
    conn, profile = board
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "project"
    (repo / ".git").mkdir(parents=True)
    skill_at(repo / ".agents" / "skills")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "trusted_project_dirs": [str(repo)]}})
    target = "~/project/new-directory" if tilde else str(repo / "new-directory")
    assert kb.create_task(conn, title="Future directory", assignee="synthetic-worker",
                          skills=["synthetic-shared"], workspace_kind="dir", workspace_path=target,
                          board="skills-probe", initial_status="todo")
    assert not (repo / "new-directory").exists()


def test_disabled_declared_name_cannot_be_loaded_through_directory_alias(board):
    conn, profile = board
    path = skill_at(profile / "skills", "alias")
    path.write_text("---\nname: disabled-synthetic\ndescription: fixture\n---\nSynthetic instructions.\n")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "disabled": ["disabled-synthetic"]}})
    with pytest.raises(ValueError, match="unavailable"):
        create(conn, "synthetic-worker", ["alias"])


def test_unsupported_platform_refuses_before_creation(board):
    conn, profile = board
    path = skill_at(profile / "skills", "unsupported")
    path.write_text("---\nname: unsupported\nplatforms: [synthetic-no-such-platform]\n---\nFixture.\n")
    with pytest.raises(ValueError, match="unavailable"):
        create(conn, "synthetic-worker", ["unsupported"])


def test_materialized_worktree_uses_branch_skills_not_primary_checkout(board, tmp_path):
    from hermes_cli import projects_db
    conn, profile = board
    repo = tmp_path / "project"
    (repo / ".git").mkdir(parents=True)
    skill_at(repo / ".agents" / "skills", "main-only")
    with projects_db.connect_closing() as registry:
        project = projects_db.create_project(registry, name="Fixture", primary_path=str(repo))
    tid = kb.create_task(conn, title="Branch skill", assignee="external-control-plane",
                         skills=["branch-only"], project_id=project,
                         board="skills-probe", initial_status="todo")
    workspace = Path(kb.get_task(conn, tid).workspace_path)
    workspace.mkdir(parents=True)
    (workspace / ".git").write_text("gitdir: synthetic-not-invoked\n")
    skill_at(workspace / ".agents" / "skills", "branch-only")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "trusted_project_dirs": [str(repo), str(workspace)]}})
    assert kb.assign_task(conn, tid, "synthetic-worker")


def test_existing_relative_directory_does_not_use_creator_cwd(board, tmp_path, monkeypatch):
    conn, profile = board
    repo = tmp_path / "creator-repo"
    (repo / ".git").mkdir(parents=True)
    skill_at(repo / ".agents" / "skills")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "trusted_project_dirs": [str(repo)]}})
    monkeypatch.chdir(repo)
    with pytest.raises(ValueError, match="absolute"):
        kb.create_task(conn, title="Relative directory", assignee="synthetic-worker",
                       skills=["synthetic-shared"], workspace_kind="dir", workspace_path=".",
                       board="skills-probe", initial_status="todo")
