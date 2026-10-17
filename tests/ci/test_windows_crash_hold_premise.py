"""The Windows crash harness must prove custody before injecting its fault."""

from contextlib import nullcontext
import json
import time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.platforms("windows")


def _fixture(tmp_path, monkeypatch):
    from tests.e2e.core.windows_update import test_crash_cells as cells

    install, logs, home = tmp_path / "checkout", tmp_path / "logs", tmp_path / "home"
    (install / ".git").mkdir(parents=True)
    logs.mkdir()
    home.mkdir()
    machine = SimpleNamespace(root=tmp_path, install_dir=install, logs=logs, hermes_home=home)
    hold = cells._GitHold(machine, "hermes_bootstrap.py", inject_missing=True)
    hold.flags.mkdir()
    target = install / hold.path
    target.write_text("original bootstrap", encoding="utf-8")
    lock = install / ".git" / "index.lock"
    lock.write_text("held", encoding="utf-8")
    updater = SimpleNamespace(pid=10)
    git = SimpleNamespace(pid=11, name=lambda: "git.exe", cwd=lambda: str(install),
                          cmdline=lambda: ["git", "merge", "--ff-only"])
    state = {"created": 2.0, "parents": [git, updater]}
    child = SimpleNamespace(pid=12, is_running=lambda: True, create_time=lambda: state["created"],
                            cmdline=lambda: ["python", str(hold.flags / "hold.py"), hold.token],
                            parents=lambda: state["parents"])
    ready = {"pid": 12, "created": 2.0, "state": "holding", "token": hold.token,
             "path": str(target.resolve()), "deadline": time.monotonic() + 1200}
    (hold.flags / "held").write_text(json.dumps(ready), encoding="utf-8")
    monkeypatch.setattr(cells.psutil, "Process", lambda pid: child)
    monkeypatch.setattr(cells, "fail_with", lambda machine, message: message)
    return cells, machine, hold, updater, target, lock, ready, state


def test_reused_filter_pid_cannot_authorize_missing_module_injection(tmp_path, monkeypatch):
    _cells, machine, hold, updater, target, lock, _ready, state = _fixture(tmp_path, monkeypatch)
    state["created"] = 3.0
    with pytest.raises(AssertionError, match="incarnation changed"):
        hold.point(updater, machine, "target")
    assert target.read_text(encoding="utf-8") == "original bootstrap"
    assert lock.exists()


def test_foreign_filter_tree_cannot_authorize_missing_module_injection(tmp_path, monkeypatch):
    _cells, machine, hold, updater, target, lock, _ready, state = _fixture(tmp_path, monkeypatch)
    state["parents"] = state["parents"][:1]
    with pytest.raises(AssertionError, match="outside this updater"):
        hold.point(updater, machine, "target")
    assert target.exists() and lock.exists()


def test_stale_cell_token_cannot_authorize_missing_module_injection(tmp_path, monkeypatch):
    _cells, machine, hold, updater, target, lock, ready, _state = _fixture(tmp_path, monkeypatch)
    ready["token"] = "another-cell"
    (hold.flags / "held").write_text(json.dumps(ready), encoding="utf-8")
    with pytest.raises(AssertionError, match="mismatched filter readiness"):
        hold.point(updater, machine, "target")
    assert target.exists() and lock.exists()


def test_verified_live_hold_injects_only_the_armed_missing_bootstrap(tmp_path, monkeypatch):
    _cells, machine, hold, updater, target, lock, ready, _state = _fixture(tmp_path, monkeypatch)
    sibling = machine.install_dir / "hermes_constants.py"
    sibling.write_text("keep", encoding="utf-8")
    assert hold.point(updater, machine, "target") is not None
    assert not target.exists()
    assert lock.exists() and sibling.read_text(encoding="utf-8") == "keep"
    receipt = json.loads((machine.logs / "git-hold-hermes_bootstrap.py.json").read_text())
    assert receipt["filter"]["pid"] == ready["pid"]
    assert receipt["git_pid"] == 11 and receipt["updater_pid"] == updater.pid
    assert receipt["path_existed_before_injection"] and receipt["missing_injected"]
    assert receipt["path_absent_at_kill_point"]


def test_crash_premise_is_captured_before_disarm_and_git_inspection(tmp_path, monkeypatch):
    cells, machine, hold, updater, target, lock, _ready, _state = _fixture(tmp_path, monkeypatch)
    lock.unlink()
    machine.mint = lambda *args: "target"
    machine.publish = lambda *args: None
    machine.gateway_phase = nullcontext
    machine.hermes = lambda *args, **kwargs: "follow-up"
    monkeypatch.setattr(cells, "_head", lambda machine: "before")
    monkeypatch.setattr(cells, "_tree", lambda *args: {})
    monkeypatch.setattr(cells, "one_shot_turn", lambda *args: "turn")

    def killed(*args):
        target.unlink()
        lock.write_text("left by killed git", encoding="utf-8")
        return "verified hold"

    def disarm():
        target.write_text("later state", encoding="utf-8")
        lock.unlink()

    hold.arm = lambda: None
    hold.disarm = disarm
    monkeypatch.setattr(cells, "_kill_when", killed)
    result = cells._crash(machine, None, "cell", lambda: updater, lambda *args: None, hold)
    assert result["index_lock_after_kill"] and result["held_missing_after_kill"]
    assert target.exists() and not lock.exists()
