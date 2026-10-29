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
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(tmp_path / "no-managed-scope"))
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


@pytest.mark.parametrize("tier", ["external_dirs", "create_dir", "trusted_project_dirs"])
def test_assignee_settings_do_not_inherit_creator_worker_snapshot(board, tmp_path, monkeypatch, tier):
    from agent import safe_worker_policy
    conn, profile = board
    root = tmp_path / "assignee-only"
    workspace = None
    if tier == "trusted_project_dirs":
        (root / ".git").mkdir(parents=True)
        skill_at(root / ".agents" / "skills")
        workspace = str(root)
    else:
        skill_at(root)
    value = str(root) if tier == "create_dir" else [str(root)]
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {tier: value}})
    snapshot = {"skills": {}, "agent": {"max_turns": 3}}
    monkeypatch.setattr(safe_worker_policy, "worker_config_snapshot", lambda: snapshot.copy())
    assert kb.create_task(conn, title="Cross-profile metadata", assignee="synthetic-worker",
                          skills=["synthetic-shared"], workspace_kind="dir" if workspace else "scratch",
                          workspace_path=workspace, board="skills-probe", initial_status="todo")
    assert safe_worker_policy.worker_config_snapshot() == snapshot


@pytest.mark.parametrize("assignee_disabled", [True, False])
def test_disabled_settings_belong_to_assignee_not_creator_snapshot(board, monkeypatch, assignee_disabled):
    from agent import safe_worker_policy
    conn, profile = board
    skill_at(profile / "skills", "plain-synthetic")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "disabled": ["plain-synthetic"] if assignee_disabled else []}})
    creator = {"skills": {"disabled": [] if assignee_disabled else ["plain-synthetic"]}}
    monkeypatch.setattr(safe_worker_policy, "worker_config_snapshot", lambda: creator.copy())
    if assignee_disabled:
        with pytest.raises(ValueError, match="unavailable"):
            create(conn, "synthetic-worker", ["plain-synthetic"])
    else:
        assert create(conn, "synthetic-worker", ["plain-synthetic"])
    assert safe_worker_policy.worker_config_snapshot() == creator


@pytest.mark.parametrize("disabled_platform", ["cli", "telegram"])
def test_platform_disabled_is_evaluated_for_destination_cli_worker(board, monkeypatch, disabled_platform):
    conn, profile = board
    skill_at(profile / "skills", "plain-synthetic")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "platform_disabled": {disabled_platform: ["plain-synthetic"]}}})
    monkeypatch.setenv("HERMES_PLATFORM", "telegram")
    if disabled_platform == "cli":
        with pytest.raises(ValueError, match="unavailable"):
            create(conn, "synthetic-worker", ["plain-synthetic"])
    else:
        assert create(conn, "synthetic-worker", ["plain-synthetic"])


def test_explicit_assignee_metadata_does_not_use_frozen_external_root_cache(board, tmp_path, monkeypatch):
    from agent import skill_utils
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    conn, profile = board
    target_root, stale_root = tmp_path / "target", tmp_path / "stale"
    skill_at(target_root)
    stale_root.mkdir()
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "external_dirs": [str(target_root)]}})
    token = set_hermes_home_override(profile)
    try:
        key = skill_utils._config_cache_key(profile / "config.yaml")
    finally:
        reset_hermes_home_override(token)
    monkeypatch.setattr(skill_utils, "_EXTERNAL_DIRS_CACHE", {key: [stale_root]})
    assert create(conn, "synthetic-worker", ["synthetic-shared"])
    assert skill_utils._EXTERNAL_DIRS_CACHE == {key: [stale_root]}


def test_nested_empty_metadata_restores_scope_and_never_replaces_worker_config(board, tmp_path, monkeypatch):
    from agent import skill_utils, safe_worker_policy
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_cli.config import load_config_readonly
    _, profile = board
    root = tmp_path / "scoped"
    root.mkdir()
    creator = {"agent": {"max_turns": 3}, "skills": {"external_dirs": [str(root)]}}
    monkeypatch.setattr(safe_worker_policy, "worker_config_snapshot", lambda: creator.copy())
    monkeypatch.setattr(skill_utils, "_EXTERNAL_DIRS_CACHE", {})
    home = set_hermes_home_override(profile)
    try:
        with skill_utils.skill_metadata_config({"skills": {"external_dirs": [str(root)]}}):
            assert skill_utils.get_external_skills_dirs() == [root]
            with pytest.raises(RuntimeError):
                with skill_utils.skill_metadata_config({"skills": {}}):
                    assert skill_utils.get_external_skills_dirs() == []
                    assert load_config_readonly()["agent"]["max_turns"] == 3
                    raise RuntimeError("synthetic refusal")
            assert skill_utils.get_external_skills_dirs() == [root]
        assert skill_utils._SKILL_METADATA_CONFIG.get() is None
        assert safe_worker_policy.worker_config_snapshot() == creator
    finally:
        reset_hermes_home_override(home)


def test_restricted_worker_does_not_read_an_assignee_config(board, monkeypatch):
    from agent import safe_worker_policy
    from hermes_cli import config_backend
    from hermes_cli.kanban_db_skills import validate_local_task_skills
    monkeypatch.setattr(safe_worker_policy, "safe_worker_enabled", lambda: True)
    monkeypatch.setattr(config_backend, "read_config_doc", lambda *a: pytest.fail("restricted config read"))
    with pytest.raises(ValueError, match="restricted worker"):
        validate_local_task_skills(["synthetic"], "synthetic-worker")


@pytest.mark.parametrize("honors_managed", [True, False])
def test_managed_cli_disabled_rule_respects_destination_backend(board, tmp_path, monkeypatch, honors_managed):
    from hermes_cli import config_backend, managed_scope
    conn, profile = board
    skill_at(profile / "skills", "plain-synthetic")
    managed = tmp_path / "managed"
    managed.mkdir()
    atomic_config_write(managed / "config.yaml", {"skills": {"platform_disabled": {"cli": ["plain-synthetic"]}}})
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    managed_scope.invalidate_managed_cache()
    monkeypatch.setattr(config_backend.FileBackend, "honors_managed_config", lambda self: honors_managed)
    if honors_managed:
        with pytest.raises(ValueError, match="unavailable"):
            create(conn, "synthetic-worker", ["plain-synthetic"])
    else:
        assert create(conn, "synthetic-worker", ["plain-synthetic"])


def test_creator_worker_workspace_fence_does_not_replace_target_metadata_context(board, tmp_path, monkeypatch):
    from agent.runtime_cwd import resolve_kanban_worker_cwd
    conn, profile = board
    target, creator = tmp_path / "target", tmp_path / "creator"
    (target / ".git").mkdir(parents=True)
    creator.mkdir()
    skill_at(target / ".agents" / "skills")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "trusted_project_dirs": [str(target)]}})
    monkeypatch.setenv("HERMES_KANBAN_TASK", "creator-task")
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(creator))
    assert resolve_kanban_worker_cwd(str(target)) == str(creator)
    assert kb.create_task(conn, title="Target metadata", assignee="synthetic-worker",
                          skills=["synthetic-shared"], workspace_kind="dir", workspace_path=str(target),
                          board="skills-probe", initial_status="todo")
    assert resolve_kanban_worker_cwd(str(target)) == str(creator)


def test_new_card_does_not_inherit_creator_implicit_project_trust(board, monkeypatch):
    conn, profile = board
    root = kb.workspaces_root(board="skills-probe")
    creator = root / "creator-task"
    (creator / ".git").mkdir(parents=True)
    skill_at(creator / ".agents" / "skills")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "trusted_project_dirs": [str(root.parent)]}})
    monkeypatch.setenv("HERMES_KANBAN_TASK", "creator-task")
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(creator))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACES_ROOT", str(root))
    with pytest.raises(ValueError, match="unavailable"):
        kb.create_task(conn, title="Distinct target", assignee="synthetic-worker",
                       skills=["synthetic-shared"], workspace_kind="dir", workspace_path=str(creator),
                       board="skills-probe", initial_status="todo")


@pytest.mark.parametrize("tilde", [True, False])
def test_reassignment_retains_destination_owned_workspace_trust(board, tmp_path, tilde):
    conn, profile = board
    tid = create(conn, "external-control-plane", ["synthetic-shared"])
    root = kb.workspaces_root(board="skills-probe")
    workspace = root / tid
    (workspace / ".git").mkdir(parents=True)
    skill_at(workspace / ".agents" / "skills")
    spelling = "~/" + workspace.relative_to(tmp_path).as_posix() if tilde else str(workspace)
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET workspace_kind='dir', workspace_path=? WHERE id=?", (spelling, tid))
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "trusted_project_dirs": [str(root.parent)]}})
    assert kb.assign_task(conn, tid, "synthetic-worker")


@pytest.mark.parametrize("external_name", ["external-worktrees", ".worktrees"])
def test_unallocated_configured_external_project_worktree_keeps_repo_anchor(board, tmp_path, external_name):
    from hermes_cli import projects_db
    conn, profile = board
    repo, external = tmp_path / "project", tmp_path / external_name
    (repo / ".git").mkdir(parents=True)
    external.mkdir()
    skill_at(repo / ".agents" / "skills")
    atomic_config_write(profile.parent.parent / "config.yaml", {"kanban": {
        "worktree_roots": {str(repo): str(external)}}})
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "trusted_project_dirs": [str(repo)]}})
    with projects_db.connect_closing() as registry:
        project = projects_db.create_project(registry, name="Fixture", primary_path=str(repo))
    tid = kb.create_task(conn, title="External project skill", assignee="external-control-plane",
                         skills=["synthetic-shared"], project_id=project,
                         board="skills-probe", initial_status="todo")
    assert Path(kb.get_task(conn, tid).workspace_path) == external / tid
    assert not (external / tid).exists()
    assert kb.assign_task(conn, tid, "synthetic-worker")


@pytest.mark.parametrize("kind", ["dir", "worktree"])
@pytest.mark.parametrize("external_name", ["external", ".worktrees"])
def test_external_anchor_refuses_dir_or_ambiguous_mapping(board, tmp_path, kind, external_name):
    from hermes_cli import projects_db
    conn, profile = board
    repo, other, external = tmp_path / "project", tmp_path / "other", tmp_path / external_name
    for path in (repo, other):
        (path / ".git").mkdir(parents=True)
        skill_at(path / ".agents" / "skills")
    external.mkdir()
    roots = {str(repo): str(external)}
    if kind == "worktree":
        roots[str(other)] = str(external)
    atomic_config_write(profile.parent.parent / "config.yaml", {"kanban": {"worktree_roots": roots}})
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "trusted_project_dirs": [str(repo), str(other)]}})
    with projects_db.connect_closing() as registry:
        project = projects_db.create_project(registry, name="Fixture", primary_path=str(repo))
    tid = kb.create_task(conn, title="External anchor guard", assignee="external-control-plane",
                         skills=["synthetic-shared"], project_id=project, workspace_kind=kind,
                         board="skills-probe", initial_status="todo")
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET workspace_path=? WHERE id=?", (str(external / tid), tid))
    with pytest.raises(ValueError, match="unavailable" if kind == "dir" else "ambiguous"):
        kb.assign_task(conn, tid, "synthetic-worker")


@pytest.mark.parametrize("tier", ["create_dir", "external_dirs", "trusted_project_dirs"])
@pytest.mark.parametrize("reference", ["${HERMES_HOME}", "$HERMES_HOME", "${env:HERMES_HOME}"])
@pytest.mark.parametrize("managed", [False, True])
def test_skill_path_references_expand_for_destination_home(board, monkeypatch, tmp_path, tier, reference, managed):
    from hermes_cli import managed_scope
    conn, profile = board
    root = profile / "extra"
    workspace = None
    if tier == "trusted_project_dirs":
        (root / ".git").mkdir(parents=True)
        skill_at(root / ".agents" / "skills")
        workspace = str(root)
    else:
        skill_at(root)
    configured = reference + "/extra"
    value = configured if tier == "create_dir" else [configured]
    if managed:
        managed_dir = tmp_path / "managed"
        managed_dir.mkdir()
        atomic_config_write(managed_dir / "config.yaml", {"skills": {tier: value}})
        monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed_dir))
        managed_scope.invalidate_managed_cache()
    else:
        atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {tier: value}})
    creator_home = str(profile.parent.parent)
    assert __import__("os").environ["HERMES_HOME"] == creator_home
    assert kb.create_task(conn, title="Destination path expansion", assignee="synthetic-worker",
                          skills=["synthetic-shared"], workspace_kind="dir" if workspace else "scratch",
                          workspace_path=workspace, board="skills-probe", initial_status="todo")
    assert __import__("os").environ["HERMES_HOME"] == creator_home


@pytest.mark.parametrize("managed", [False, True])
def test_config_env_reference_preserves_literal_destination_value(board, tmp_path, monkeypatch, managed):
    from hermes_cli import managed_scope
    conn, profile = board
    root = profile / "extra-$SECOND"
    skill_at(root)
    monkeypatch.setenv("SHARED_SKILLS", str(root))
    monkeypatch.setenv("SECOND", "must-not-expand")
    (profile / ".env").write_text(f"SHARED_SKILLS={root}\n", encoding="utf-8")
    settings = {"model": "synthetic-unused", "skills": {"create_dir": "${ env:SHARED_SKILLS }"}}
    if managed:
        managed_dir = tmp_path / "managed"
        managed_dir.mkdir()
        atomic_config_write(managed_dir / "config.yaml", settings)
        monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed_dir))
        managed_scope.invalidate_managed_cache()
    else:
        atomic_config_write(profile / "config.yaml", settings)
    assert create(conn, "synthetic-worker", ["synthetic-shared"])


@pytest.mark.parametrize("present", [False, True])
def test_metadata_config_references_preserve_bound_scope_isolation(board, monkeypatch, present):
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    from agent.skill_utils import _expand_path, skill_metadata_config
    _, profile = board
    monkeypatch.setenv("SYNTHETIC_SCOPE_PATH", "/process-only")
    values = {"SYNTHETIC_SCOPE_PATH": "/bound-synthetic"} if present else {}
    token = set_secret_scope(values, profile_home=str(profile))
    try:
        with skill_metadata_config({}):
            expected = "/bound-synthetic" if present else "${env:SYNTHETIC_SCOPE_PATH}"
            assert _expand_path("${env:SYNTHETIC_SCOPE_PATH}") == Path(expected)
    finally:
        reset_secret_scope(token)


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("reference", ["${SYNTHETIC_DISABLED}", "${env:SYNTHETIC_DISABLED}"])
def test_config_references_in_disabled_rules_remain_effective(board, tmp_path, monkeypatch, managed, reference):
    from hermes_cli import managed_scope
    conn, profile = board
    skill_at(profile / "skills", "plain-synthetic")
    monkeypatch.setenv("SYNTHETIC_DISABLED", "plain-synthetic")
    (profile / ".env").write_text("SYNTHETIC_DISABLED=plain-synthetic\n", encoding="utf-8")
    settings = {"model": "synthetic-unused", "skills": {"disabled": [reference]}}
    if managed:
        managed_dir = tmp_path / "managed"
        managed_dir.mkdir()
        atomic_config_write(managed_dir / "config.yaml", settings)
        monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed_dir))
        managed_scope.invalidate_managed_cache()
    else:
        atomic_config_write(profile / "config.yaml", settings)
    with pytest.raises(ValueError, match="unavailable"):
        create(conn, "synthetic-worker", ["plain-synthetic"])


@pytest.mark.parametrize("operation", ["create", "assign"])
@pytest.mark.parametrize("reference", ["${SYNTHETIC_PATH}", "${env:SYNTHETIC_PATH}", "$SYNTHETIC_PATH"])
@pytest.mark.parametrize("creator_has_value", [False, True])
def test_destination_profile_references_do_not_borrow_creator_scope(
    board, monkeypatch, operation, reference, creator_has_value,
):
    from agent.secret_scope import (current_secret_scope, current_secret_scope_home,
                                    reset_multiplex_context, reset_secret_scope,
                                    set_multiplex_context, set_secret_scope)
    conn, profile = board
    root = profile / "owned-extra"
    skill_at(root)
    (profile / ".env").write_text(f"SYNTHETIC_PATH={root}\n", encoding="utf-8")
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "create_dir": reference}})
    monkeypatch.setenv("SYNTHETIC_PATH", str(profile.parent.parent / "wrong-process"))
    creator = {"SYNTHETIC_PATH": str(profile.parent.parent / "wrong-scope")} if creator_has_value else {}
    creator_home = str(profile.parent.parent)
    tid = create(conn, "external-control-plane", ["synthetic-shared"]) if operation == "assign" else None
    context_token = set_multiplex_context(True)
    scope_token = set_secret_scope(creator, profile_home=creator_home)
    try:
        if operation == "create":
            assert create(conn, "synthetic-worker", ["synthetic-shared"])
        else:
            assert kb.assign_task(conn, tid, "synthetic-worker")
    finally:
        assert current_secret_scope() == creator
        assert current_secret_scope_home() == creator_home
        reset_secret_scope(scope_token)
        reset_multiplex_context(context_token)


@pytest.mark.parametrize("operation", ["create", "assign"])
def test_missing_destination_reference_refuses_and_restores_all_scopes(board, monkeypatch, operation):
    from agent.runtime_cwd import scoped_session_cwd
    from agent.secret_scope import (current_secret_scope, reset_multiplex_context, reset_secret_scope,
                                    set_multiplex_context, set_secret_scope)
    from hermes_constants import get_hermes_home

    conn, profile = board
    root = profile / "creator-only"
    skill_at(root)
    atomic_config_write(profile / "config.yaml", {"model": "synthetic-unused", "skills": {
        "create_dir": "${env:SYNTHETIC_MISSING}"}})
    creator = {"SYNTHETIC_MISSING": str(root)}
    monkeypatch.setenv("SYNTHETIC_MISSING", str(root))
    tid = create(conn, "external-control-plane", ["synthetic-shared"]) if operation == "assign" else None
    before = [list(conn.execute(f"SELECT * FROM {table}")) for table in ("tasks", "task_events")]
    original_home, original_cwd = get_hermes_home(), scoped_session_cwd()
    context_token = set_multiplex_context(True)
    scope_token = set_secret_scope(creator, profile_home=str(profile.parent.parent))
    try:
        with pytest.raises(ValueError, match="unavailable"):
            if operation == "create":
                create(conn, "synthetic-worker", ["synthetic-shared"])
            else:
                kb.assign_task(conn, tid, "synthetic-worker")
        assert current_secret_scope() == creator
        assert get_hermes_home() == original_home
        assert scoped_session_cwd() == original_cwd
        assert [list(conn.execute(f"SELECT * FROM {table}")) for table in ("tasks", "task_events")] == before
    finally:
        reset_secret_scope(scope_token)
        reset_multiplex_context(context_token)
