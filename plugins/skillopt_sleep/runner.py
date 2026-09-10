"""Run the pinned engine through one Hermes-scoped state and adoption path."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .configuration import configuration
from .adoption import adopt, digest, read_proposal, record_proposal


def _execute(args, cfg):
    from skillopt_sleep.cycle import run_sleep_cycle
    from skillopt_sleep.mine import assign_splits, mine
    from skillopt_sleep.state import SleepState
    from .hermes_source import harvest_hermes

    action = args.skillopt_sleep_action or "status"
    if action == "status":
        state = SleepState.load(cfg.state_path)
        return {"status": "ready", "engine_version": "0.2.0", "project": cfg.invoked_project,
                "target": cfg.target_skill_path, "state_path": cfg.state_path,
                "night": state.night, "proposal": read_proposal(cfg)}
    if action == "adopt":
        return adopt(cfg)
    if cfg.backend != "mock":
        # A subprocess backend bypasses Hermes' protected egress/provider gates.
        # Keep it unavailable until an adapter enforces that same boundary.
        raise ValueError("Live SkillOpt subprocess backends are not admitted through Hermes egress. Use mock for local diagnostics.")
    digests = harvest_hermes(project=cfg.invoked_project, limit=cfg.max_sessions_per_night,
                             lookback_hours=cfg.lookback_hours)
    tasks = mine(digests, max_tasks=cfg.max_tasks_per_night, candidate_limit=cfg.max_sessions_per_night,
                 holdout_fraction=cfg.val_fraction, seed=cfg.seed)
    tasks = assign_splits(tasks, val_fraction=cfg.val_fraction, test_fraction=cfg.test_fraction, seed=cfg.seed)
    # Conversational feedback is a mining hint, never a verified reference.
    for task in tasks:
        if task.reference_kind == "none":
            task.outcome = "unknown"
    if action == "harvest":
        return {"status": "harvested", "project": cfg.invoked_project, "source": "hermes",
                "n_sessions": len(digests), "n_tasks": len(tasks),
                "tasks": [task.to_dict() for task in tasks], "reviewed": False}
    before = digest(Path(cfg.target_skill_path))
    outcome = run_sleep_cycle(cfg, seed_tasks=tasks, dry_run=action == "dry-run")
    report = outcome.report
    if outcome.staging_dir:
        # The upstream timestamp has one-second precision. Move our completed
        # proposal to a unique path while holding the project lock.
        import uuid
        old = Path(outcome.staging_dir)
        new = old.with_name(old.name + "-" + uuid.uuid4().hex[:12])
        old.rename(new)
        outcome.staging_dir = str(new)
        state = SleepState.load(cfg.state_path)
        if state.data.get("history"):
            state.data["history"][-1]["staging"] = str(new)
            state.save()
        record_proposal(cfg, outcome, before)
    return {"status": "diagnostic", "source": "hermes", "backend": cfg.backend,
            "night": report.night, "n_sessions": len(digests), "n_tasks": report.n_tasks,
            "baseline": report.baseline_score, "candidate": report.candidate_score,
            "gate_action": report.gate_action, "accepted": report.accepted,
            "staging_dir": outcome.staging_dir, "adopted": False,
            "adoption_eligible": False, "reason": "mock_backend"}


def run(args):
    try:
        cfg = configuration(args)
        action = args.skillopt_sleep_action or "status"
        if action in {"run", "adopt"}:
            from filelock import FileLock, Timeout
            lock_path = Path(cfg.invoked_project) / ".skillopt-sleep/hermes.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with FileLock(str(lock_path), timeout=0):
                    payload = _execute(args, cfg)
            except Timeout as exc:
                raise RuntimeError("A SkillOpt run or adoption already owns this project.") from exc
        else:
            payload = _execute(args, cfg)
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"[skillopt-sleep] {json.dumps(payload, ensure_ascii=False)}")
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        from .hermes_source import _safe_text
        try:
            message = _safe_text(str(exc))
        except RuntimeError:
            message = "Operation failed; error details withheld because redaction failed."
        print(f"[skillopt-sleep] {message}", file=sys.stderr)
        return 1
