"""Shared control-plane policy for project worktree locations and remote bases."""
from pathlib import Path


def project_worktree_path(repo_root: Path, task_id: str) -> Path:
    from hermes_cli.config import read_user_config_raw
    from hermes_cli.kanban_db import kanban_home

    config = read_user_config_raw(kanban_home() / "config.yaml") or {}
    roots = (config.get("kanban") or {}).get("worktree_roots", {})
    if not isinstance(roots, dict):
        raise ValueError("kanban.worktree_roots must map repository paths to directories")
    root = Path(roots.get(str(repo_root.resolve()), str(repo_root / ".worktrees"))).expanduser()
    if not root.is_absolute() or root.resolve() == repo_root.resolve():
        raise ValueError("Kanban worktree root must be an absolute separate directory")
    return root / task_id


def refresh_configured_remote_base(repo_root, ref, git):
    remote_ref = ref.removeprefix("refs/remotes/")
    remote, separator, branch = remote_ref.partition("/")
    if not separator:
        return
    remotes = git(repo_root, "remote", timeout=20)
    if remotes.returncode or remote not in remotes.stdout.splitlines():
        return
    valid = git(repo_root, "check-ref-format", "refs/heads/" + branch, timeout=20)
    if valid.returncode:
        raise ValueError("configured Kanban remote base is not a branch ref")
    fetched = git(repo_root, "fetch", "--no-tags", remote,
                  f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}", timeout=60)
    if fetched.returncode:
        raise ValueError(f"could not refresh configured Kanban base {ref}: {fetched.stderr.strip()}")
