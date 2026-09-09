"""Bootstrap repository-scoped Python environments into Git worktrees."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)
_GIT_TIMEOUT = 30


def _git_value(path: Path, args: list[str]) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_GIT_TIMEOUT, check=False,
        )
    except Exception:
        return None
    if result.returncode != 0 or not (result.stdout or "").strip():
        return None
    return Path(result.stdout.strip()).expanduser().resolve(strict=False)


def _same_repository_environment(
    source_root: Path, resolved_source: Path, *, governed_roots: tuple[Path, ...] = ()
) -> bool:
    try:
        if resolved_source.is_relative_to(source_root):
            return True
    except ValueError:
        pass
    if any(resolved_source == root or resolved_source.is_relative_to(root) for root in governed_roots):
        return True
    common_dir = _git_value(source_root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if common_dir is None:
        return False
    main_root = common_dir.parent
    return (
        _git_value(resolved_source, ["rev-parse", "--show-toplevel"]) == main_root
        and _git_value(main_root, ["rev-parse", "--path-format=absolute", "--git-common-dir"]) == common_dir
    )


def bootstrap_worktree_environments(
    repo_root: Path,
    target: Path,
    *,
    environment_names: tuple[str, ...] = (".venv", "venv"),
    require_python: bool = True,
    allow_venv_fallback: bool = True,
) -> tuple[str, ...]:
    """Link missing repository-owned environments into *target*."""
    try:
        source_root = repo_root.expanduser().resolve(strict=True)
        target_root = target.expanduser().resolve(strict=True)
    except OSError as exc:
        logger.warning("worktree environment bootstrap roots unavailable: %s", exc)
        return ()

    source_roots = [source_root]
    governed_roots: list[Path] = []
    common_dir = _git_value(source_root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if common_dir is not None and common_dir.name == ".git":
        main_root = common_dir.parent
        if main_root not in source_roots:
            source_roots.append(main_root)
        governed_roots.append(main_root.parent / "venvs")

    linked: list[str] = []
    for environment_name in environment_names:
        destination = target_root / environment_name
        if destination.exists() or destination.is_symlink():
            continue
        source_names = (
            (environment_name, "venv")
            if environment_name == ".venv" and allow_venv_fallback
            else (environment_name,)
        )
        for candidate_root in source_roots:
            for source_name in source_names:
                source = candidate_root / source_name
                try:
                    if not source.exists():
                        continue
                    resolved_source = source.resolve(strict=True)
                    if not resolved_source.is_dir() or not _same_repository_environment(
                        source_root, resolved_source, governed_roots=tuple(governed_roots)
                    ):
                        continue
                    if os.name == "nt":
                        python_candidates = (resolved_source / "Scripts" / "python.exe", resolved_source / "bin" / "python")
                    else:
                        python_candidates = (resolved_source / "bin" / "python",)
                    if require_python and not any(p.is_file() and os.access(p, os.X_OK) for p in python_candidates):
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(str(resolved_source), str(destination), target_is_directory=True)
                    linked.append(environment_name)
                    break
                except OSError:
                    logger.warning("worktree environment bootstrap could not link %s", environment_name)
            if linked and linked[-1] == environment_name:
                break
    return tuple(linked)
