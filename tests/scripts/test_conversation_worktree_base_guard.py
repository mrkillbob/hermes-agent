"""Real manager/Git/bootstrap contracts; Luna admission remains project-owned."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys

import pytest

from agent.conversation_worktree import ConversationWorktreeManager
from agent.secret_scope import is_multiplex_active, set_multiplex_active
from agent.conversation_worktree_policy import ConversationCreationBasePolicy, ConversationWorktreePolicy
from hermes_state import SessionDB
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from scripts import conversation_worktree_base_guard as guard


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def metadata(root: Path, name: str) -> Path:
    return Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", name))


@contextmanager
def profile_home(home):
    home.mkdir(exist_ok=True)
    token = set_hermes_home_override(home)
    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        yield
    finally:
        reset_hermes_home_override(token)
        set_multiplex_active(previous)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global.config"))
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "config", "user.name", "Fixture")
    git(repository, "config", "user.email", "fixture@example.invalid")
    git(repository, "config", "extensions.worktreeConfig", "true")
    hooks = repository / ".githooks"
    hooks.mkdir()
    (hooks / "pre-push").write_text("#!/bin/sh\nexit 0\n")
    (hooks / "pre-push").chmod(0o755)
    (repository / ".gitignore").write_text(".venv/\n")
    scripts = repository / "scripts"
    scripts.mkdir()
    trace = '''from pathlib import Path
import json, subprocess, sys
root = Path.cwd()
path = Path(subprocess.check_output(['git', 'rev-parse', '--absolute-git-dir'], text=True).strip()) / 'calls.json'
calls = json.loads(path.read_text()) if path.exists() else []
calls.append([Path(__file__).name, *sys.argv[1:]])
path.write_text(json.dumps(calls))
'''
    (scripts / "check_agent_workspace.py").write_text(trace + '''if '--ensure-new' in sys.argv:
    (path.parent / 'agent-workspace-receipt.json').touch(exist_ok=True)
''')
    launcher = f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n"
    (scripts / "bootstrap_agent_workspace.py").write_text(trace + f'''
venv = root / '.venv' / 'bin'
venv.mkdir(parents=True, exist_ok=True)
python = venv / 'python'
python.write_text({launcher!r})
python.chmod(0o755)
''')
    (scripts / "worktree_environment.py").write_text('''def validate_environment(root, environment=None):
    venv = root / '.venv'
    return None if venv.is_dir() and not venv.is_symlink() else 'not child-owned'
''')
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "governed project fixture")
    source = tmp_path / "source"
    git(repository, "worktree", "add", "-qb", "source", str(source))
    git(source, "config", "--worktree", "core.hooksPath", str(source / ".githooks"))
    remote = tmp_path / "remote.git"
    git(repository, "clone", "--bare", "-q", str(repository), str(remote))
    git(repository, "remote", "add", "origin", str(remote))
    git(repository, "push", "-q", "origin", "HEAD:refs/heads/stable")
    return repository, source, remote, tmp_path / "conversations"


def advance(repository: Path) -> str:
    git(repository, "commit", "--allow-empty", "-qm", "advance governed branch")
    git(repository, "push", "-q", "origin", "HEAD:refs/heads/stable")
    return git(repository, "rev-parse", "HEAD")


def arguments(mode, remote, destination):
    return ["--mode", mode, "--remote", "origin", "--branch", "stable",
            "--expected-remote", str(remote), "--worktree-root", str(destination)]


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("mode", ["hermes-agent", "lunabot"])
@pytest.mark.parametrize("existing_branch", [False, True])
def test_real_manager_bootstrap_pins_claim_and_repairs_only_proven_copied_hooks(workspace, tmp_path, mode, existing_branch):
    repository, source, remote, destination = workspace
    source_head = git(source, "rev-parse", "HEAD")
    source_config = metadata(source, "config.worktree").read_bytes()
    selected = advance(repository)
    transport = (
        "import json, os, runpy, subprocess, sys; from pathlib import Path; "
        "path = Path(subprocess.check_output(['git', 'rev-parse', '--absolute-git-dir'], text=True).strip()); "
        "(path / 'bootstrap-transport.json').write_text(json.dumps({'home': os.environ['HERMES_HOME'], "
        "'base': os.environ['HERMES_CONVERSATION_BASE_COMMIT']})); "
        "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
    )
    home_a, home_b = tmp_path / "profile-a", tmp_path / "profile-b"
    home_a.mkdir()
    home_b.mkdir()
    policy = ConversationWorktreePolicy(
        enabled=True, source_worktree=source, worktree_root=destination,
        bootstrap=True, bootstrap_command=(sys.executable, "-c", transport, guard.__file__, *arguments(mode, remote, destination)),
        create_timeout=10, bootstrap_timeout=10,
        creation_base=ConversationCreationBasePolicy("origin", "stable", str(remote)),
    )
    with SessionDB(home_a / "state.db") as db:
        manager = ConversationWorktreeManager(policy, db)
        if existing_branch:
            _, branch = manager._expected_identity("pinned")
            git(source, "branch", branch, selected)
        bootstrap = manager._run_bootstrap
        later = []

        def remote_moves_after_claim(record):
            assert record.base_commit == selected
            later.append(advance(repository))
            if mode == "lunabot":
                root = Path(record.worktree_path)
                common = Path(record.repo_common_dir)
                import hashlib
                digest = hashlib.sha256(str(root).encode()).hexdigest()
                for path in (
                    metadata(root, "hermes-conversation-config-inheritance-v1.json"),
                    metadata(root, "hermes-conversation-owner-v1"),
                    common / "hermes-conversation-owner-claims-v1" / f"{digest}.json",
                ):
                    path.write_text("\ufeff" + path.read_text(encoding="utf-8-sig"), encoding="utf-8")
            bootstrap(record)

        manager._run_bootstrap = remote_moves_after_claim
        with profile_home(home_a):
            first = manager.bind_new_root_session("pinned", conversation_kind="interactive")
        assert json.loads(metadata(first.path, "bootstrap-transport.json").read_text()) == {
            "home": str(home_a), "base": selected,
        }
        assert first.base_commit == selected == git(first.path, "rev-parse", "HEAD")
        assert db.get_conversation_worktree("pinned").state == "ready"
        proof = json.loads(metadata(first.path, "hermes-conversation-config-inheritance-v1.json").read_text(encoding="utf-8-sig"))
        assert proof["source_head"] == source_head and proof["base_commit"] == selected
        if mode == "lunabot":
            assert git(first.path, "config", "--worktree", "core.hooksPath") == ".githooks"
            assert (first.path / ".githooks" / "pre-push").resolve() == (first.path / git(first.path, "rev-parse", "--git-path", "hooks/pre-push")).resolve()
            audit_path, = metadata(first.path, "config.worktree").parent.glob("hermes-hook-repair-*.json")
            audit = json.loads(audit_path.read_text())
            assert Path(audit["backup"]).read_bytes() == source_config
            assert json.loads(metadata(first.path, "calls.json").read_text()) == [
                ["check_agent_workspace.py", "--ensure-new", "--agent", "hermes"],
                ["bootstrap_agent_workspace.py"], ["check_agent_workspace.py"],
            ]
        else:
            receipt = json.loads(metadata(first.path, guard.RECEIPT).read_text())
            assert receipt["base_sha"] == selected
            receipt_path = metadata(first.path, guard.RECEIPT)
            receipt_path.write_text("\ufeff" + receipt_path.read_text(encoding="utf-8-sig"), encoding="utf-8")
            with profile_home(home_a):
                bootstrap(db.get_conversation_worktree("pinned"))
            assert metadata(first.path, "config.worktree").read_bytes() == source_config
        manager._run_bootstrap = bootstrap
        with SessionDB(home_b / "state.db") as db_b, profile_home(home_b):
            second = ConversationWorktreeManager(policy, db_b).bind_new_root_session("next", conversation_kind="interactive")
        with profile_home(home_a):
            returned = manager.bind_new_root_session("return-a", conversation_kind="interactive")
        for binding, home in ((second, home_b), (returned, home_a)):
            assert json.loads(metadata(binding.path, "bootstrap-transport.json").read_text()) == {
                "home": str(home), "base": later[0],
            }
        assert second.base_commit == later[0] == git(second.path, "rev-parse", "HEAD")
        git(first.path, "commit", "--allow-empty", "-qm", "normal ready conversation work")
        assert manager.bind_new_root_session("pinned", conversation_kind="interactive").base_commit == selected
    assert git(source, "rev-parse", "HEAD") == source_head
    assert metadata(source, "config.worktree").read_bytes() == source_config


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("fault", [
    "missing-pin", "wrong-pin", "foreign-source", "missing-proof", "source-drift",
    "config-drift", "custom-hook", "global-hook", "hook-bytes", "hook-symlink",
    "missing-source-hook", "missing-claim", "config-lock", "cas-drift", "skip-other-repository",
    "creation-head-drift", "creation-config-drift", "local-hook", "config-symlink", "command-hook", "post-replace-lock",
])
def test_guard_preserves_identity_and_concurrent_or_unproven_hook_settings(workspace, tmp_path, monkeypatch, fault):
    repository, source, remote, destination = workspace
    with SessionDB(tmp_path / "state.db") as db:
        manager = ConversationWorktreeManager(ConversationWorktreePolicy(
            enabled=True, source_worktree=source, worktree_root=destination, create_timeout=10,
        ), db)
        if fault in {"creation-head-drift", "creation-config-drift"}:
            original_git = manager._run_git

            def drift_at_creation(cwd, argv, timeout, phase):
                result = original_git(cwd, argv, timeout, phase)
                if argv[:2] == ["worktree", "add"]:
                    actions = {
                        "creation-head-drift": lambda: git(source, "commit", "--allow-empty", "-qm", "source drift"),
                        "creation-config-drift": lambda: git(source, "config", "--worktree", "fixture.changed", "true"),
                    }
                    actions[fault]()
                return result

            manager._run_git = drift_at_creation
        binding = manager.bind_new_root_session("retained", conversation_kind="interactive")
    root = binding.path
    config = metadata(root, "config.worktree")
    monkeypatch.chdir(root)
    monkeypatch.setenv("HERMES_CONVERSATION_BASE_COMMIT", binding.base_commit)
    monkeypatch.setenv("HERMES_CONVERSATION_SOURCE_WORKTREE", str(source))
    args = arguments("lunabot", remote, destination)
    def foreign_source():
        foreign = tmp_path / "foreign"
        git(repository, "clone", "-q", str(repository), str(foreign))
        monkeypatch.setenv("HERMES_CONVERSATION_SOURCE_WORKTREE", str(foreign))

    def symlink_hook():
        hook = root / ".githooks" / "pre-push"
        hook.unlink()
        hook.symlink_to(source / ".githooks" / "pre-push")

    def symlink_config():
        original = tmp_path / "unowned.config"
        original.write_bytes(config.read_bytes())
        config.unlink()
        config.symlink_to(original)

    def cas_drift():
        command = guard._command

        def conflicting_writer(checkout, argv):
            result = command(checkout, argv)
            if "--replace-all" in argv:
                config.write_bytes(config.read_bytes() + b"\n[fixture]\n\tchanged = true\n")
            return result

        monkeypatch.setattr(guard, "_command", conflicting_writer)

    def post_replace_lock():
        replace = guard.os.replace

        def next_writer(source_path, destination_path):
            replace(source_path, destination_path)
            Path(source_path).write_bytes(b"next writer")

        monkeypatch.setattr(guard.os, "replace", next_writer)

    def command_hook():
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "command-hooks"))

    def skip_other():
        args[args.index("--expected-remote") + 1] = str(tmp_path / "other.git")
        args.append("--skip-non-target-repo")

    actions = {
        "missing-pin": lambda: monkeypatch.delenv("HERMES_CONVERSATION_BASE_COMMIT"),
        "wrong-pin": lambda: monkeypatch.setenv("HERMES_CONVERSATION_BASE_COMMIT", advance(repository)),
        "foreign-source": foreign_source,
        "missing-proof": lambda: metadata(root, "hermes-conversation-config-inheritance-v1.json").unlink(),
        "source-drift": lambda: git(source, "commit", "--allow-empty", "-qm", "source moved after creation"),
        "config-drift": lambda: git(root, "config", "--worktree", "fixture.changed", "true"),
        "custom-hook": lambda: git(root, "config", "--worktree", "core.hooksPath", str(tmp_path / "custom-hooks")),
        "global-hook": lambda: git(root, "config", "--global", "core.hooksPath", str(tmp_path / "global-hooks")),
        "local-hook": lambda: git(root, "config", "--local", "core.hooksPath", str(tmp_path / "local-hooks")),
        "hook-bytes": lambda: (root / ".githooks" / "pre-push").write_text("#!/bin/sh\nexit 2\n"),
        "hook-symlink": symlink_hook,
        "config-symlink": symlink_config,
        "missing-source-hook": lambda: (source / ".githooks" / "pre-push").unlink(),
        "missing-claim": lambda: metadata(root, "hermes-conversation-owner-v1").unlink(),
        "config-lock": lambda: config.with_name(config.name + ".lock").write_bytes(b"other writer"),
        "cas-drift": cas_drift,
        "post-replace-lock": post_replace_lock,
        "command-hook": command_hook,
        "skip-other-repository": skip_other,
    }
    if fault in actions:
        actions[fault]()
    else:
        assert not metadata(root, "hermes-conversation-config-inheritance-v1.json").exists()
    before = config.read_bytes()
    head, branch = git(root, "rev-parse", "HEAD"), git(root, "branch", "--show-current")
    if fault in {"skip-other-repository", "post-replace-lock"}:
        assert guard.main(args) == 0
    else:
        with pytest.raises((RuntimeError, OSError)):
            guard.main(args)
    assert git(root, "rev-parse", "HEAD") == head
    assert git(root, "branch", "--show-current") == branch
    assert metadata(root, "calls.json").exists() == (fault == "post-replace-lock")
    if fault == "cas-drift":
        assert config.read_bytes() == before + b"\n[fixture]\n\tchanged = true\n"
    elif fault == "post-replace-lock":
        assert git(root, "config", "--worktree", "core.hooksPath") == ".githooks"
        assert config.with_name(config.name + ".lock").read_bytes() == b"next writer"
    else:
        assert config.read_bytes() == before
    if fault == "config-lock":
        assert config.with_name(config.name + ".lock").read_bytes() == b"other writer"
