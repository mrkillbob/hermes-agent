"""Strict configuration admission for manual dispatcher entry points."""
from collections.abc import Mapping
from pathlib import Path

_CAPS = ("max_spawn", "max_in_progress", "max_in_progress_per_profile", "max_in_progress_per_model")
_CAP_MAPS = ("max_in_progress_by_profile", "max_in_progress_by_model")


def _positive_cap(value, key):
    if isinstance(value, str) and value.strip().isdecimal():
        value = int(value.strip())
    if type(value) is not int or value <= 0:
        raise ValueError(f"kanban.{key} must be a positive integer or null")
    return value


def _validated_guard(guard, *, final):
    if not isinstance(guard, Mapping):
        raise ValueError("kanban.priority_runtime_guard must be a mapping")
    guard = dict(guard)
    for key in ("enabled", "include_linked_worktrees"):
        if key in guard and type(guard[key]) is not bool:
            raise ValueError(f"priority_runtime_guard.{key} must be a boolean")
    for key in ("max_in_progress", "normal_max_in_progress"):
        if key in guard:
            guard[key] = _positive_cap(guard[key], "priority_runtime_guard." + key)
    for key in ("project_roots", "entrypoints"):
        if key in guard:
            values = guard[key]
            if not isinstance(values, (list, tuple)) or any(not isinstance(v, str) or not v.strip() or "\0" in v for v in values):
                raise ValueError(f"priority_runtime_guard.{key} must be a list of paths")
            if key == "project_roots" and any(not Path(v).expanduser().is_absolute() for v in values):
                raise ValueError("priority_runtime_guard.project_roots must be absolute paths")
            if key == "entrypoints" and any(Path(v).is_absolute() or ".." in Path(v).parts or not Path(v).parts for v in values):
                raise ValueError("priority_runtime_guard.entrypoints must remain relative to their roots")
    if final and guard.get("enabled") and (not guard.get("project_roots") or not guard.get("entrypoints", ("main.py",))):
        raise ValueError("enabled priority_runtime_guard requires roots and entrypoints")
    return guard


def validated_section(config, *, required, final=False):
    from hermes_cli.config import FailedConfigRead

    if isinstance(config, FailedConfigRead):
        raise ValueError("dispatcher configuration is a failed-read fallback") from config.read_error
    if not isinstance(config, Mapping) or (required and "kanban" not in config):
        raise ValueError("dispatcher configuration must contain a kanban mapping")
    section = config.get("kanban", {})
    if not isinstance(section, Mapping):
        raise ValueError("kanban configuration must be a mapping")
    section = dict(section)
    for key in _CAPS:
        if section.get(key) is not None:
            section[key] = _positive_cap(section[key], key)
    for key in _CAP_MAPS:
        if section.get(key) is not None:
            caps = section[key]
            if not isinstance(caps, Mapping) or any(not isinstance(k, str) or not k.strip() for k in caps):
                raise ValueError(f"kanban.{key} must map names to positive integers")
            section[key] = {name: _positive_cap(cap, key) for name, cap in caps.items()}
    if "priority_runtime_guard" in section:
        section["priority_runtime_guard"] = _validated_guard(section["priority_runtime_guard"], final=final)
    assignee = section.get("default_assignee")
    if assignee is not None and not isinstance(assignee, str):
        raise ValueError("kanban.default_assignee must be a string or null")
    return section


def strict_shared_config(profile_section):
    from hermes_cli.config import validate_user_config_file
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_constants import get_default_hermes_root

    path = get_default_hermes_root() / "config.yaml"
    validate_user_config_file(path)
    root = validated_section(load_user_config_effective(path, fail_closed=True), required=False)
    for key, value in validated_section({"kanban": profile_section}, required=True).items():
        if isinstance(value, Mapping) and isinstance(root.get(key), Mapping):
            root[key] = {**root[key], **value}
        else:
            root[key] = value
    return root


def load_dispatch_section():
    from hermes_cli.config import get_config_path, load_config, validate_user_config_file
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.managed_scope import get_managed_dir
    from hermes_constants import get_default_hermes_root

    managed = get_managed_dir()
    if managed is not None:
        validate_user_config_file(managed / "config.yaml")
    section = validated_section(load_config(), required=True)
    if get_config_path().resolve() != (get_default_hermes_root() / "config.yaml").resolve():
        # Defaults are not profile overrides of an installation-root host cap.
        explicit = validated_section(load_user_config_effective(fail_closed=True), required=False)
        section = {**section, **strict_shared_config(explicit)}
    else:
        section = strict_shared_config(section)
    return validated_section({"kanban": section}, required=True, final=True)
