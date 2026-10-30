"""A managed child's kernel reference survives loss of its supervisor."""
from pathlib import Path
import subprocess
import sys

import pytest

from tests.waiting import wait_until


_CHILD = '''
from pathlib import Path
import threading, time, sys
from pm.lifetime_launch import adopt_arguments
adopt_arguments(sys.argv)
ready, release, done = map(Path, sys.argv[1:4])
ready.write_text('ready')
deadline=time.monotonic()+20
while not release.exists() and time.monotonic()<deadline:
    threading.Event().wait(.02)
done.write_text('done')
'''
_SUPERVISOR = f'''
from pathlib import Path
import os, subprocess, sys, threading
from pm.lifetime import activity
from pm.lifetime_launch import popen
state, ready, release, done, mode, seam = sys.argv[1:]
with activity(Path(state), generation=Path(state)/'generation') as owned:
    command = [sys.executable,'-c',{_CHILD!r},ready,release,done]
    if seam == 'spawn':
        popen(command, managed=True)
    else:
        Path(ready+'.supervisor').write_text('starting probe')
        if seam == 'marker':
            from pm import extras, runtime
            runtime.runtime_command = lambda *args, **kwargs: command
            extras._evaluate_in_runtime('fixture', {{}})
        else:
            from pm.recovery import validate_environment
            validate_environment(command, env=dict(os.environ), cwd=Path.cwd())
    if mode == 'exit':
        os._exit(0)
    Path(ready+'.supervisor').write_text('spawned')
    threading.Event().wait(20)
'''


def _surviving_child(tmp_path, mode, seam="spawn", *, stable_identity=False):
    from pm.lifetime import activity, gate

    domain = tmp_path / "state"
    with activity(domain, create=True, stable_identity=stable_identity):
        (domain / "generation").mkdir()
    ready, release, done = (tmp_path / name for name in ("ready", "release", "done"))
    supervisor = subprocess.Popen([sys.executable, "-c", _SUPERVISOR, str(domain),
                                   str(ready), str(release), str(done), mode, seam], cwd=Path(__file__).resolve().parents[2])
    try:
        wait_until(lambda: ready.exists(), timeout=10)
        if mode == "terminate":
            wait_until(lambda: Path(str(ready)+".supervisor").exists(), timeout=10)
            supervisor.terminate()  # this fixture's supervisor, never external work
        supervisor.wait(timeout=10)
        with gate(domain) as guard:
            assert guard.held(generation=domain / "generation")
        release.touch()
        wait_until(lambda: done.exists(), timeout=10)

        def released():
            with gate(domain) as guard:
                return not guard.held()
        wait_until(released, timeout=10)
    finally:
        release.touch()
        if supervisor.poll() is None:
            supervisor.terminate()
        supervisor.wait(timeout=10)
        wait_until(lambda: done.exists(), timeout=10)


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("mode", ["exit", "terminate"])
def test_posix_child_reference_survives_supervisor_loss(tmp_path, mode):
    _surviving_child(tmp_path, mode)


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('mode', ['exit', 'terminate'])
def test_native_volume_child_reference_survives_supervisor_loss(tmp_path, mode):
    _surviving_child(tmp_path, mode, stable_identity=True)


@pytest.mark.platforms("windows")
@pytest.mark.parametrize("mode", ["exit", "terminate"])
def test_windows_child_reference_survives_supervisor_loss(tmp_path, mode):
    _surviving_child(tmp_path, mode)


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("seam", ["marker", "recovery"])
def test_posix_pm_probe_survives_supervisor_loss(tmp_path, seam):
    _surviving_child(tmp_path, "terminate", seam)


@pytest.mark.platforms("windows")
@pytest.mark.parametrize("seam", ["marker", "recovery"])
def test_windows_pm_probe_survives_supervisor_loss(tmp_path, seam):
    _surviving_child(tmp_path, "terminate", seam)
