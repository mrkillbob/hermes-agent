"""Check local task skill availability before intake or reassignment is durable."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Sequence


def _task_skill_cwd(home: Path, workspace_path: Optional[str], project_repo: Optional[str],
                    project_id: Optional[str], task_id: Optional[str]) -> Path:
    target = Path(project_repo).expanduser() if project_repo else None
    if workspace_path:
        workspace = Path(workspace_path).expanduser()
        if not workspace.is_absolute():
            raise ValueError("task skill workspace must be absolute")
        if target is None and workspace.is_dir():
            return workspace
        # A project task's deterministic worktree may not have been allocated
        # yet. Its stored anchor, never the caller's cwd, owns project skills.
        if (target is None and project_id and task_id and workspace.is_absolute()
                and workspace.name == task_id and workspace.parent.name == ".worktrees"):
            target = workspace.parent.parent
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


def _filesystem_skill_available(name: str, roots) -> bool:
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
    if skills_tool._is_skill_disabled(declared):
        return False
    relative = skills_tool._owned_relative(directory, skill_md, [root for _tier, root in roots])
    # Mirror skill_view's resolved-copy rule: a disabled relative selector
    # must own this copy's catalog load_name, not just share its spelling.
    if skills_tool._is_skill_disabled(relative):
        return not any(entry["path"] == skill_md and entry["load_name"] == relative
                       for entry in skills_tool._skill_catalog(skip_disabled=True, include_hidden=True))
    return True


def validate_local_task_skills(
    skills: Optional[Sequence[str]], assignee: Optional[str], *,
    workspace_path: Optional[str] = None, project_repo: Optional[str] = None,
    project_id: Optional[str] = None, task_id: Optional[str] = None,
) -> None:
    """Refuse unavailable filesystem skills for a known local profile.

    External control-plane assignees remain valid; they resolve their own skills.
    Namespaced plugin selectors retain worker-side resolution: discovering or
    loading plugins at card creation would execute plugin code in the creator.
    This is an intake check, not a replacement for worker-time validation.
    """
    if not skills or not assignee:
        return
    from hermes_cli.profiles import get_profile_dir, profile_exists

    if not profile_exists(assignee):
        return
    from agent.runtime_cwd import reset_session_cwd, set_session_cwd
    from agent.skill_utils import get_skill_search_roots
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.skills_tool import skill_lookup_path_error

    home = get_profile_dir(assignee)
    cwd = _task_skill_cwd(home, workspace_path, project_repo, project_id, task_id)
    home_token = set_hermes_home_override(home)
    cwd_token = set_session_cwd(str(cwd))
    try:
        roots = get_skill_search_roots()
        unavailable = []
        for name in skills:
            if skill_lookup_path_error(name):
                unavailable.append(name)
            elif ":" not in name:
                if not _filesystem_skill_available(name, roots):
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

