"""Profile/project-scoped configuration for the pinned SkillOpt engine."""
from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ENGINE_VERSION = "0.2.0"


def configuration(args):
    try:
        installed = version("skillopt")
    except PackageNotFoundError as exc:
        raise RuntimeError("Install skillopt==0.2.0 in the active Hermes Python environment.") from exc
    if installed != ENGINE_VERSION:
        raise RuntimeError(f"Expected skillopt=={ENGINE_VERSION}; found {installed}.")
    from hermes_constants import get_hermes_home
    from skillopt_sleep.config import DEFAULTS, SleepConfig

    project = Path(args.project or Path.cwd()).expanduser().resolve()
    if not project.is_dir():
        raise ValueError("The selected project must be an existing directory.")
    home = get_hermes_home()
    target = Path(args.target_skill_path).expanduser() if args.target_skill_path else home / "skills/skillopt-sleep-learned/SKILL.md"
    target = (project / target).resolve() if not target.is_absolute() else target.resolve()
    if target.name != "SKILL.md":
        raise ValueError("The target must be a SKILL.md file.")
    key = hashlib.sha256(f"{project}\0{target}".encode()).hexdigest()[:24]
    state_dir = home / "plugin-data/skillopt-sleep" / key
    # Do not inherit ~/.skillopt-sleep/config: it can disable validation, adopt
    # automatically, evolve CLAUDE.md, or select a different provider/profile.
    data = dict(DEFAULTS)
    data.update(invoked_project=str(project), projects="invoked", transcript_source="hermes",
                backend=args.backend, model=args.model, target_skill_path=str(target),
                max_tasks_per_night=args.max_tasks, max_sessions_per_night=args.max_sessions,
                lookback_hours=args.lookback_hours, edit_budget=args.edit_budget,
                auto_adopt=False, gate_mode="on", evolve_memory=False, evolve_skill=True,
                state_dir=str(state_dir), progress=args.progress)
    return SleepConfig(data)
