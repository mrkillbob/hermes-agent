"""An operator pause survives automatic dependency-update collection."""
from contextlib import nullcontext
from pathlib import Path
import sys

import pytest

from agent import estop
from hermes_cli import venv_sync


@pytest.mark.parametrize("entry", ["collector", "sync", "source_sync", "completion"])
@pytest.mark.parametrize("sentinel", ["none", "profile", "fleet"])
def test_automatic_collection_honors_pause(tmp_path, monkeypatch, entry, sentinel):
    import pm
    from pm import client, environments, receipt, runtime
    from hermes_cli import gitlock, runtime_state, update_completion, update_lock

    fleet = tmp_path / ".hermes"
    home = fleet / "profiles" / "worker"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "checkout"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    marker = (home if sentinel == "profile" else fleet) / "ESTOP"
    if sentinel != "none":
        marker.write_text("paused\n")  # body validity must not affect the pause
    calls = []
    monkeypatch.setattr(runtime_state, "collect_generations", lambda path: calls.append("app") or [])
    monkeypatch.setattr(runtime, "collect_runtime_generations", lambda path: calls.append("runtime") or [])
    monkeypatch.setattr(environments, "install_state_dir", lambda path: tmp_path / "state")
    monkeypatch.setattr(environments, "runtime_facts_path", lambda path: tmp_path / "facts")
    (tmp_path / "facts").touch()
    monkeypatch.setattr(pm, "venv_is_current", lambda **kw: False)
    monkeypatch.setattr(pm, "sync_venv", lambda *a, **kw: None)
    monkeypatch.setattr(client, "ensure_tools_for_sync", lambda: None)
    monkeypatch.setattr(venv_sync, "refuse_foreign_owned_venv", lambda root: None)
    monkeypatch.setattr(venv_sync, "arm_completion", lambda root: None)
    monkeypatch.setattr(venv_sync, "publish_launchers", lambda root: None)
    monkeypatch.setattr(gitlock, "convert_treeless_checkout_first", lambda root: None)
    monkeypatch.setattr(receipt, "worker_context", lambda ident: nullcontext())
    monkeypatch.setattr(receipt, "last_for_update", lambda ident: None)
    monkeypatch.setattr(environments, "project_python", lambda root: Path(sys.executable))
    monkeypatch.setattr(environments, "activation_environment", lambda root: {})
    result = tmp_path / "result.json"

    def prepared_child(*args, **kwargs):
        result.write_text("{}")
        return 0

    monkeypatch.setattr(update_completion.subprocess, "call", prepared_child)
    monkeypatch.setattr(update_lock, "_acquire_checkout", lambda root: None)
    monkeypatch.setattr(update_lock, "checkout_lock_fds", lambda root: ())

    def run():
        if entry == "collector":
            venv_sync.collect_superseded_generations(root)
        elif entry == "sync":
            assert venv_sync.sync(root) == {"state": "synced", "ok": True}
        elif entry == "source_sync":
            venv_sync._sync_source_dependencies(root, arm=False)
        else:
            request = {"source": str(root), "receipt": {"update_id": "fixture"},
                       "bytecode_cache": str(tmp_path / "bytecode")}
            update_completion._prepare(request, tmp_path / "request.json", result)

    run()
    assert calls == (["app", "runtime"] if sentinel == "none" else [])
    if sentinel != "none":
        assert marker.read_text() == "paused\n"
        marker.unlink()  # explicit resume restores normal automatic cleanup
        run()
        assert calls == ["app", "runtime"]


def test_paused_collection_needs_no_installed_dependencies(tmp_path):
    import os
    import subprocess

    home = tmp_path / "home"
    home.mkdir()
    (home / "ESTOP").touch()
    source = Path(__file__).resolve().parents[2]
    program = f"""
import sys
sys.path.insert(0, {str(source)!r})
class NoCollectors:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('hermes_cli.runtime_state', 'pm.runtime'):
            raise AssertionError('paused cleanup must not import a collector')
sys.meta_path.insert(0, NoCollectors())
from hermes_cli.venv_sync import collect_superseded_generations
collect_superseded_generations({str(tmp_path / 'checkout')!r})
assert 'hermes_cli.runtime_state' not in sys.modules
assert 'pm.runtime' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-c", program],
                            env={**os.environ, "HERMES_HOME": str(home)},
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
