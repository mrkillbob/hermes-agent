"""Other hosts retain their existing subprocess worker launch contract."""
import subprocess

import pytest


@pytest.mark.platforms("linux", "windows")
def test_worker_spawn_delegates_unchanged_to_popen(monkeypatch):
    from hermes_cli.kanban_worker_spawn import spawn_worker

    captured, process = {}, object()

    def popen(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    argv = ["absolute-worker", "-m", "harmless-test"]
    options = dict(cwd="workspace", stdin=subprocess.DEVNULL, stdout=object(),
                   stderr=subprocess.STDOUT, env={"SYNTHETIC": "value"},
                   start_new_session=True, creationflags=0)
    assert spawn_worker(argv, **options) is process
    assert captured == dict(argv=argv, **options)
