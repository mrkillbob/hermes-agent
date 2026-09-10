"""Isolated real-engine diagnostics and hash-bound explicit adoption contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("skillopt_sleep")

from filelock import FileLock

from hermes_state import SessionDB
from plugins.skillopt_sleep.adoption import adopt, digest, read_proposal, record_proposal
from plugins.skillopt_sleep.cli import register_cli
from plugins.skillopt_sleep.configuration import configuration
from plugins.skillopt_sleep.runner import run


def _arguments(project, action, *extra):
    parser = argparse.ArgumentParser()
    register_cli(parser)
    return parser.parse_args([action, "--project", str(project), "--json", *extra])


@pytest.mark.parametrize("action", ["harvest", "dry-run", "run"])
def test_real_mock_workflow_is_profile_scoped_and_never_adopts(tmp_path, monkeypatch, capsys, action):
    home = tmp_path / "hermes-profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    memory = project / "CLAUDE.md"
    memory.write_text("User-owned memory: preserve this file.\n")
    args = _arguments(project, action)
    cfg = configuration(args)
    target = Path(cfg.target_skill_path)
    assert not target.exists()
    assert cfg.backend == "mock" and cfg.auto_adopt is False
    assert cfg.evolve_memory is False and cfg.gate_mode == "on"
    assert Path(cfg.state_dir).is_relative_to(home)
    db = SessionDB(db_path=home / "state.db")
    prompts = ["Fix parser tokenization", "Improve parser diagnostics", "Test parser recovery",
               "Document parser grammar", "Explain parser precedence", "Review parser inputs"]
    try:
        for index, prompt in enumerate(prompts):
            session = f"session-{index}"
            db.create_session(session, "cli", cwd=str(project), git_repo_root=str(project))
            db.append_message(session, "user", prompt)
            db.append_message(session, "assistant", "A proposed parser solution")
            db.append_message(session, "user", "Thanks, looks good")
    finally:
        db.close()
    before = (home / "state.db").read_bytes()

    assert run(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["source"] == "hermes" and payload["n_sessions"] == len(prompts)
    assert payload["n_tasks"] > 0
    assert not target.exists()
    assert memory.read_text() == "User-owned memory: preserve this file.\n"
    assert (home / "state.db").read_bytes() == before
    if action == "harvest":
        assert payload["reviewed"] is False
        assert all(task["outcome"] == "unknown" for task in payload["tasks"])
    else:
        assert payload["status"] == "diagnostic"
        assert payload["adoption_eligible"] is False and payload["adopted"] is False
        if action == "run":
            assert Path(payload["staging_dir"]).is_dir()
            assert read_proposal(cfg)["backend"] == "mock"
            with pytest.raises(ValueError, match="Mock scores"):
                adopt(cfg)
        else:
            assert not payload["staging_dir"]
    other_home = tmp_path / "other-profile"
    other_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(other_home))
    assert read_proposal(configuration(args)) is None


@pytest.mark.parametrize("case", [
    "success", "candidate-changed", "target-changed", "mock", "nan-candidate",
    "nan-baseline", "infinite-candidate", "no-improvement", "other-project-lock", "candidate-replaced-during-read",
    "quoted-separator", "invalid-yaml",
])
def test_adoption_uses_reviewed_bytes_finite_scores_and_shared_target_lock(tmp_path, monkeypatch, case):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    project = tmp_path / "first-project"
    project.mkdir()
    cfg = configuration(_arguments(project, "adopt"))
    target = Path(cfg.target_skill_path)
    target.parent.mkdir(parents=True)
    original = b"---\nname: parser\ndescription: Review parser behavior.\n---\nOriginal guidance.\n"
    proposed = original.replace(b"Original guidance.", b"Reviewed improvement.")
    if case == "quoted-separator":
        proposed = proposed.replace(b"description: Review parser behavior.", b"description: 'Handle --- separators.'")
    if case == "invalid-yaml":
        proposed = proposed.replace(b"description: Review parser behavior.", b"description: [unterminated")
    replacement = original.replace(b"Original guidance.", b"Unreviewed replacement.")
    target.write_bytes(original)
    staging = tmp_path / "staging"
    staging.mkdir()
    candidate = staging / "proposed_SKILL.md"
    candidate.write_bytes(proposed)
    # Synthetic evaluation evidence tests the adoption boundary; no live backend
    # is invoked or claimed qualified by this fixture.
    cfg.data["backend"] = "mock" if case == "mock" else "codex"
    baseline, score = 0.4, 0.8
    if case == "nan-candidate":
        score = float("nan")
    if case == "nan-baseline":
        baseline = float("nan")
    if case == "infinite-candidate":
        score = float("inf")
    if case == "no-improvement":
        score = baseline
    outcome = SimpleNamespace(staging_dir=str(staging), report=SimpleNamespace(
        accepted=True, baseline_score=baseline, candidate_score=score))
    record_proposal(cfg, outcome, digest(target))
    if case == "candidate-changed":
        candidate.write_bytes(replacement)
    if case == "target-changed":
        target.write_bytes(replacement)
    if case == "candidate-replaced-during-read":
        read_bytes = Path.read_bytes

        def replace_after_read(path):
            content = read_bytes(path)
            if path == candidate:
                path.write_bytes(replacement)
            return content

        monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    before_adoption = target.read_bytes()
    if case == "other-project-lock":
        second = tmp_path / "second-project"
        second.mkdir()
        second_cfg = configuration(_arguments(second, "adopt"))
        second_cfg.data["backend"] = "codex"
        record_proposal(second_cfg, outcome, digest(target))
        assert second_cfg.state_dir != cfg.state_dir
        assert second_cfg.target_skill_path == cfg.target_skill_path
        # A first project's in-flight adopter owns the target lock; a second
        # project's independent state must not grant a second writer.
        with FileLock(str(target.parent / f".{target.name}.skillopt.lock"), timeout=0):
            with pytest.raises(RuntimeError, match="Another adoption"):
                adopt(second_cfg)
    elif case in {"success", "candidate-replaced-during-read", "quoted-separator"}:
        result = adopt(cfg)
        assert result["effective"] == "next_session"
        assert target.read_bytes() == proposed
        assert Path(result["backup"]).read_bytes() == original
        assert adopt(cfg)["status"] == "already_adopted"
    else:
        with pytest.raises(ValueError):
            adopt(cfg)
    if case not in {"success", "candidate-replaced-during-read", "quoted-separator"}:
        assert target.read_bytes() == before_adoption
        assert read_proposal(cfg)["adopted"] is False
