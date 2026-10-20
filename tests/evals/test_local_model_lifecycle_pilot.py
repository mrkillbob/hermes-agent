"""The native lifecycle pilot refuses without a coordinated window and spawns nothing.

Offline: `run`, process creation and the supervisor are replaced with tripwires, so a regression
that would reach a native model fails here instead of loading one.
"""

from __future__ import annotations

import subprocess

import pytest

from evals.local_model_lifecycle import pilot


@pytest.fixture
def tripwires(monkeypatch):
    reached = []

    def trip(name):
        def fail(*args, **kwargs):
            reached.append(name)
            raise AssertionError(f"{name} reached")
        return fail

    monkeypatch.setattr(pilot, "run", trip("run"))
    monkeypatch.setattr(subprocess, "Popen", trip("Popen"))
    monkeypatch.setattr("hermes_cli.local_runtime.supervisor.LlamaServerSupervisor", trip("supervisor"))
    return reached


def _plan(blockers):
    return lambda args: {"blockers": list(blockers), "would_download": False}


@pytest.mark.parametrize("argv, blockers", [
    (["--run"], []),                                  # missing window acknowledgement
    (["--run", "--window-ack", "w"], ["active CI job"]),  # acknowledged but the host is busy
    (["--run"], ["active CI job"]),
])
def test_run_refuses_with_exit_2_and_spawns_nothing(tmp_path, monkeypatch, tripwires, capsys, argv, blockers):
    monkeypatch.setattr(pilot, "plan", _plan(blockers))
    work = tmp_path / "work"
    assert pilot.main([*argv, "--work-root", str(work)]) == 2
    assert tripwires == []
    assert not work.exists(), "a refused run still created its work root"
    assert "refusing to run" in capsys.readouterr().err


def test_default_mode_is_plan_only(tmp_path, monkeypatch, tripwires):
    monkeypatch.setattr(pilot, "plan", _plan([]))
    assert pilot.main(["--work-root", str(tmp_path / "work")]) == 0
    assert tripwires == []
    assert not (tmp_path / "work").exists()


def test_real_plan_never_downloads_or_spawns(tmp_path, tripwires):
    missing = tmp_path / "absent.gguf"
    report = pilot.plan(pilot.argparse.Namespace(model=str(missing), engine=str(tmp_path / "no-engine")))
    assert report["would_download"] is False
    assert any("model missing" in b for b in report["blockers"])
    assert tripwires == []


@pytest.mark.platforms("posix")
def test_native_crash_pilot_refuses_even_with_window_and_files(tmp_path, monkeypatch, tripwires):
    model, engine = tmp_path / "staged.gguf", tmp_path / "staged-engine"
    model.write_bytes(b"fixture")
    engine.write_bytes(b"fixture")
    monkeypatch.setattr(pilot, "other_activity", list)
    assert pilot.main(["--run", "--window-ack", "admitted-window", "--model", str(model),
                       "--engine", str(engine), "--work-root", str(tmp_path / "work")]) == 2
    assert tripwires == []
    assert not (tmp_path / "work").exists()
