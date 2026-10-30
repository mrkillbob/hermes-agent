"""Startup shares one acquisition budget without skipping lifetime registration."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.waiting import wait_until

_COLLECTOR = '''
from pathlib import Path
import sys, threading, time, json
from hermes_cli import runtime_state
project, ready, release, expected = map(Path, sys.argv[1:])
assert runtime_state.install_state_dir(project).resolve() == expected.resolve(), 'fixture state binding differs'
def removing(path):
    ready.write_text('collector removing synthetic generation')
    deadline = time.monotonic() + 20
    while not release.exists():
        if time.monotonic() > deadline:
            raise RuntimeError('fixture release did not arrive')
        threading.Event().wait(.01)
runtime_state.shutil.rmtree = removing
runtime_state.collect_generations(project, min_age_seconds=0)
'''
_PROBE = '''
from pathlib import Path
import json, sys
from hermes_cli.runtime_state import runtime_lock
from pm.lifetime import LifetimeUnavailable
try:
    with runtime_lock(Path(sys.argv[1]), timeout=float(sys.argv[2])):
        print(json.dumps({'entered': True}))
except LifetimeUnavailable:
    print(json.dumps({'entered': False, 'registration_timed_out': True}))
'''


def _state(project):
    from pm.environments import install_state_dir
    from pm.lifetime import activity

    state = install_state_dir(project)
    with activity(state, create=True):
        for name in ('old', 'selected'):
            generation = state / 'environments' / name
            (generation / 'venv').mkdir(parents=True)
            (generation / 'venv' / 'pyvenv.cfg').write_text('home = fixture\n')
            (generation / '.lease-managed').touch()
        (state / 'facts.json').write_text(json.dumps({'packages': {'venv': {
            'environment': str(state / 'environments' / 'selected' / 'venv')}}}))
    return state


@pytest.mark.platforms('windows', 'posix')
@pytest.mark.parametrize('timeout', [0, .2])
def test_collector_removal_cannot_extend_startup_budget(tmp_path, timeout):
    project = tmp_path / 'project'
    project.mkdir()
    state = _state(project)
    ready, release = tmp_path / 'ready', tmp_path / 'release'
    root = Path(__file__).resolve().parents[2]
    collector = subprocess.Popen([sys.executable, '-c', _COLLECTOR, str(project), str(ready), str(release), str(state)],
                                 cwd=root, env=dict(os.environ), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    probe = None
    try:
        wait_until(lambda: ready.exists() or collector.poll() is not None, timeout=10)
        assert ready.exists(), collector.communicate(timeout=5)
        probe = subprocess.Popen([sys.executable, '-c', _PROBE, str(project), str(timeout)],
                                 cwd=root, env=dict(os.environ), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            output, error = probe.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            probe.kill()  # this fixture's blocked probe only
            probe.communicate(timeout=5)
            pytest.fail('startup acquisition ignored its timeout while collector held the gate')
        assert probe.returncode == 0, error
        assert json.loads(output) == {'entered': False, 'registration_timed_out': True}
    finally:
        release.touch()
        collector.communicate(timeout=10)
        if probe is not None and probe.poll() is None:
            probe.kill()
            probe.communicate(timeout=5)
    assert collector.returncode == 0


@pytest.mark.parametrize(('timeout', 'remaining'), [(10, 6.5), (0, 0), (None, None)])
def test_registration_and_internal_mutex_share_one_deadline(tmp_path, monkeypatch, timeout, remaining):
    from hermes_cli import runtime_state
    from pm import lifetime

    clock, seen = [100.0], []
    @contextmanager
    def registration(*args, deadline=None, **kwargs):
        seen.append(('registration', deadline))
        clock[0] += 3.5
        yield
    @contextmanager
    def mutex(*args, timeout=None, **kwargs):
        seen.append(('mutex', timeout))
        yield True
    monkeypatch.setattr(runtime_state, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(lifetime, 'activity', registration)
    monkeypatch.setattr(runtime_state, '_install_mutex', mutex)
    with runtime_state.runtime_lock(tmp_path / 'project', timeout=timeout) as held:
        assert held
    assert seen == [('registration', None if timeout is None else 100.0 + timeout), ('mutex', remaining)]


@pytest.mark.platforms('windows', 'posix')
def test_busy_install_mutex_still_yields_a_protected_fallback(tmp_path):
    from hermes_cli import runtime_state
    from pm.lifetime import gate

    project = tmp_path / 'project'
    project.mkdir()
    state = _state(project)
    ready, release = tmp_path / 'ready', tmp_path / 'release'
    code = '''
from pathlib import Path
import sys, threading, time
from hermes_cli.runtime_state import _install_mutex
state, ready, release = map(Path, sys.argv[1:])
with _install_mutex(state, timeout=None):
    ready.write_text('install mutex held')
    deadline = time.monotonic() + 20
    while not release.exists():
        if time.monotonic() > deadline:
            raise RuntimeError('fixture release did not arrive')
        threading.Event().wait(.01)
'''
    holder = subprocess.Popen([sys.executable, '-c', code, str(state), str(ready), str(release)],
                              cwd=Path(__file__).resolve().parents[2], env=dict(os.environ),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_until(ready.exists, timeout=10)
        with runtime_state.runtime_lock(project, timeout=0) as held:
            assert not held
            with gate(state) as guard:
                assert guard.held(writers_only=True)
            assert runtime_state.collect_generations(project, min_age_seconds=0) == []
            release_reader = runtime_state.lease_directory(state / 'environments' / 'selected')
        try:
            with gate(state) as guard:
                assert not guard.held(writers_only=True)
                assert guard.held(generation=state / 'environments' / 'selected')
        finally:
            release_reader()
    finally:
        release.touch()
        holder.communicate(timeout=10)
    assert holder.returncode == 0
