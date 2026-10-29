"""Check local task skill availability before intake or reassignment is durable."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Sequence


def _configured_worktree_repo(workspace: Path) -> Optional[Path]:
    from hermes_cli.config_backend import config_exists, read_config_doc
    from hermes_cli.kanban_db import kanban_home

    path = kanban_home() / "config.yaml"
    config = read_config_doc(path) if config_exists(path) else {}
    roots = ((config or {}).get("kanban") or {}).get("worktree_roots", {})
    if not isinstance(roots, dict):
        raise ValueError("kanban.worktree_roots must map repository paths to directories")
    matches = set()
    for key, value in roots.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        repo, target = Path(key), Path(value).expanduser()
        # Forward allocation indexes by the resolved repository spelling.
        if (repo.is_absolute() and key == str(repo.resolve()) and target.is_absolute()
                and target.resolve() != repo.resolve() and target.resolve() == workspace.parent.resolve()):
            matches.add(repo)
    if len(matches) > 1:
        raise ValueError("project skill anchor is ambiguous")
    return next(iter(matches), None)


def _task_skill_cwd(home: Path, workspace_path: Optional[str], project_repo: Optional[str],
                    project_id: Optional[str], task_id: Optional[str], workspace_kind: Optional[str]) -> Path:
    target = Path(project_repo).expanduser() if project_repo else None
    if workspace_path:
        workspace = Path(workspace_path).expanduser()
        if not workspace.is_absolute():
            raise ValueError("task skill workspace must be absolute")
        if target is None and workspace.is_dir():
            return workspace
        # A project task's deterministic worktree may not have been allocated
        # yet. Its stored anchor, never the caller's cwd, owns project skills.
        if (target is None and project_id and task_id and workspace_kind == "worktree"
                and workspace.name == task_id):
            target = _configured_worktree_repo(workspace)
            if target is None:
                target = workspace.parent.parent if workspace.parent.name == ".worktrees" else workspace
        elif target is None:
            target = workspace
    if target is None:
        return home
    if not target.is_absolute():
        raise ValueError("task skill workspace must be absolute")
    # Workspace allocation may create missing directories later. Project
    # discovery is anchored at their existing ancestor, never caller cwd.
    while not target.is_dir() and target.parent != target:
        target = target.parent
    return target


def _filesystem_skill_available(name: str, roots, disabled: set[str]) -> bool:
    from tools import skills_tool

    error, directory, skill_md = skills_tool.locate_skill(name, None, roots)
    if error is not None or skill_md is None:
        return False
    try:
        metadata = skills_tool._safe_frontmatter(content=skills_tool._read_skill_text(skill_md))
    except OSError:
        return False
    if not skills_tool.skill_matches_platform(metadata):
        return False
    declared = metadata.get("name", skill_md.parent.name)
    if declared in disabled:
        return False
    relative = skills_tool._owned_relative(directory, skill_md, [root for _tier, root in roots])
    # Mirror skill_view's resolved-copy rule: a disabled relative selector
    # must own this copy's catalog load_name, not just share its spelling.
    if relative in disabled:
        return not any(entry["path"] == skill_md and entry["load_name"] == relative
                       for entry in skills_tool._skill_catalog(skip_disabled=True, include_hidden=True))
    return True


def _skill_metadata_layer(settings):
    from hermes_cli.config import _expand_env_vars

    if not isinstance(settings, dict):
        return _expand_env_vars(settings)
    # Paths expand once in the destination resolver; other skill settings use
    # the normal config reference parser, including disabled-name rules.
    paths = {key: value for key, value in settings.items()
             if key in ("create_dir", "external_dirs", "trusted_project_dirs")}
    expanded = _expand_env_vars({key: value for key, value in settings.items() if key not in paths})
    expanded.update(paths)
    return expanded


def _assigned_skill_config(home: Path) -> dict:
    from hermes_cli.config_backend import config_exists, read_config_doc
    from hermes_cli.config import _deep_merge
    from hermes_cli.managed_scope import load_managed_config

    path = home / "config.yaml"
    config = read_config_doc(path) if config_exists(path) else {}
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("assigned profile skill settings must be a mapping")
    # The active home is already the destination, including its backend's
    # honors_managed_config contract. This never reads a worker snapshot.
    if "skills" in config:
        config["skills"] = _skill_metadata_layer(config["skills"])
    managed = load_managed_config()
    if "skills" not in managed:
        return config
    return _deep_merge(config, {"skills": _skill_metadata_layer(managed["skills"])})


def validate_local_task_skills(
    skills: Optional[Sequence[str]], assignee: Optional[str], *,
    workspace_path: Optional[str] = None, project_repo: Optional[str] = None,
    project_id: Optional[str] = None, task_id: Optional[str] = None,
    workspaces_root: Optional[str] = None, workspace_kind: Optional[str] = None,
) -> None:
    """Refuse unavailable filesystem skills for a known local profile.

    External control-plane assignees remain valid; they resolve their own skills.
    Namespaced plugin selectors retain worker-side resolution: discovering or
    loading plugins at card creation would execute plugin code in the creator.
    This is an intake check, not a replacement for worker-time validation.
    """
    if not skills or not assignee:
        return
    from agent.safe_worker_policy import safe_worker_enabled

    if safe_worker_enabled():
        raise ValueError("task skill admission is unavailable in a restricted worker")
    from hermes_cli.profiles import get_profile_dir, profile_exists

    if not profile_exists(assignee):
        return
    from agent.runtime_cwd import reset_session_cwd, set_session_cwd
    from agent.skill_utils import get_disabled_skill_names, get_skill_search_roots, skill_metadata_config
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.skills_tool import skill_lookup_path_error

    home = get_profile_dir(assignee)
    cwd = _task_skill_cwd(home, workspace_path, project_repo, project_id, task_id, workspace_kind)
    home_token = set_hermes_home_override(home)
    cwd_token = set_session_cwd(str(cwd))
    try:
        config = _assigned_skill_config(home)
        worker_env = {"HERMES_KANBAN_TASK": task_id or "",
                      "HERMES_KANBAN_WORKSPACE": str(Path(workspace_path).expanduser()) if workspace_path else "",
                      "HERMES_KANBAN_WORKSPACES_ROOT": str(Path(workspaces_root).expanduser()) if workspaces_root else ""}
        with skill_metadata_config(config, project_start=cwd, worker_env=worker_env):
            roots = get_skill_search_roots()
            # Kanban workers run with platform='cli', regardless of the
            # messaging surface that created or reassigned this task.
            disabled = get_disabled_skill_names(platform="cli")
            unavailable = []
            for name in skills:
                if skill_lookup_path_error(name):
                    unavailable.append(name)
                elif ":" not in name:
                    if not _filesystem_skill_available(name, roots, disabled):
                        unavailable.append(name)
            if unavailable:
                raise ValueError("task skills unavailable to assigned profile: " + ", ".join(unavailable))
    finally:
        reset_session_cwd(cwd_token)
        reset_hermes_home_override(home_token)


def _normalize_task_skills(skills: Optional[Iterable[str]]) -> Optional[list[str]]:
    """Strip/dedupe a skills list. Commas are refused (a comma-joined string must
    not land in one argv slot); toolset names are rejected all at once because
    agents that confuse the two usually pass several."""
    from hermes_cli.kanban_db import KNOWN_TOOLSET_NAMES

    if skills is None:
        return None
    cleaned: list[str] = []
    seen: set[str] = set()
    toolset_typos: list[str] = []
    for s in skills:
        if not s:
            continue
        name = str(s).strip()
        if not name:
            continue
        if "," in name:
            raise ValueError(
                f"skill name cannot contain comma: {name!r} "
                f"(pass a list of separate names instead of a comma-joined string)"
            )
        if name.casefold() in KNOWN_TOOLSET_NAMES:
            toolset_typos.append(name)
            continue
        if name in seen:
            continue
        seen.add(name)
        cleaned.append(name)
    if toolset_typos:
        quoted = ", ".join(repr(n) for n in toolset_typos)
        noun = "is a toolset name" if len(toolset_typos) == 1 else "are toolset names"
        raise ValueError(
            f"{quoted} {noun}, not skill name(s). "
            "Enable toolsets for the assignee's platform with "
            "`hermes tools enable kanban --platform <platform>` or add "
            "`kanban` to the appropriate `platform_toolsets.<platform>` entry "
            "instead of putting toolsets in per-task skills. Skills are named skill bundles "
            "(e.g. `blogwatcher`, `github-code-review`); toolsets are runtime "
            "capabilities (e.g. `web`, `browser`, `terminal`)."
        )
    return cleaned

