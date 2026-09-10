"""Bind explicit adoption to a reviewed proposal and unchanged live skill."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def receipt_path(cfg):
    return Path(cfg.state_dir) / "hermes-proposal.json"


def record_proposal(cfg, outcome, before_hash):
    staging = Path(outcome.staging_dir).resolve()
    candidate = staging / "proposed_SKILL.md"
    report = outcome.report
    payload = dict(project=cfg.invoked_project, target=cfg.target_skill_path,
                   before_sha256=before_hash, proposed_sha256=digest(candidate),
                   staging=str(staging), backend=cfg.backend, accepted=report.accepted,
                   baseline=report.baseline_score, candidate=report.candidate_score,
                   adopted=False)
    path = receipt_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return payload


def read_proposal(cfg):
    path = receipt_path(cfg)
    return json.loads(path.read_text()) if path.is_file() else None


def adopt(cfg):
    from filelock import FileLock, Timeout

    target = Path(cfg.target_skill_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Different projects and profiles may explicitly select the same live skill.
    # Its sibling lock gives every adopter the same ownership boundary.
    lock_path = target.parent / f".{target.name}.skillopt.lock"
    try:
        with FileLock(str(lock_path), timeout=0):
            return _adopt_locked(cfg, target)
    except Timeout:
        raise RuntimeError("Another adoption owns the selected live skill.") from None


def _adopt_locked(cfg, target):
    receipt = read_proposal(cfg)
    if not receipt:
        raise ValueError("No Hermes proposal exists for this profile, project, and target.")
    if receipt.get("project") != cfg.invoked_project or receipt.get("target") != cfg.target_skill_path:
        raise ValueError("Proposal identity does not match the selected project and target.")
    if receipt.get("adopted"):
        return {"status": "already_adopted", "target": receipt["target"]}
    if receipt.get("backend") == "mock":
        raise ValueError("Mock scores are diagnostic only; mock proposals cannot be adopted.")
    scores = (receipt.get("baseline"), receipt.get("candidate"))
    try:
        finite_scores = all(type(score) in (int, float) and math.isfinite(score) for score in scores)
    except OverflowError:
        finite_scores = False
    if receipt.get("accepted") is not True or not finite_scores or scores[1] <= scores[0]:
        raise ValueError("Proposal did not pass the held-out improvement gate.")
    staging = Path(receipt["staging"])
    candidate = staging / "proposed_SKILL.md"
    if not candidate.is_file():
        raise ValueError("The staged candidate changed or is missing; rerun validation.")
    # Validate and install one byte snapshot; later changes to the staging file
    # must not replace the exact proposal that passed the hash/frontmatter checks.
    candidate_bytes = candidate.read_bytes()
    if hashlib.sha256(candidate_bytes).hexdigest() != receipt.get("proposed_sha256"):
        raise ValueError("The staged candidate changed or is missing; rerun validation.")
    previous_bytes = target.read_bytes() if target.exists() else None
    previous_hash = hashlib.sha256(previous_bytes).hexdigest() if previous_bytes is not None else None
    if previous_hash != receipt["before_sha256"]:
        raise ValueError("The live skill changed after evaluation; rerun against its current contents.")
    import yaml
    text = candidate_bytes.decode("utf-8")
    header = re.match(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", text, re.MULTILINE | re.DOTALL)
    try:
        front = yaml.safe_load(header.group(1)) if header else None
    except yaml.YAMLError:
        raise ValueError("Candidate contains invalid skill frontmatter.") from None
    if not isinstance(front, dict) or not all(isinstance(front.get(k), str) and front[k].strip() for k in ("name", "description")):
        raise ValueError("Candidate requires valid skill name and description frontmatter.")
    # Only the bound skill can change. Never trust the engine's editable manifest
    # to redirect adoption to another skill or CLAUDE.md.
    backup = staging / "hermes-backup-SKILL.md"
    if backup.exists():
        raise ValueError("A previous adoption backup exists; inspect its incomplete result first.")
    if previous_bytes is not None:
        backup.write_bytes(previous_bytes)
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix=".skillopt-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(candidate_bytes)
        if target.exists():
            os.chmod(temporary, target.stat().st_mode & 0o777)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    receipt["adopted"] = True
    receipt_path(cfg).write_text(json.dumps(receipt, indent=2))
    return {"status": "adopted", "target": str(target), "backup": str(backup) if backup.exists() else None,
            "effective": "next_session"}
