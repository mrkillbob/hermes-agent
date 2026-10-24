"""POSIX bootstrap replacement preserves signals, topology and exact lifetime custody."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
import venv

import pytest

from tests.waiting import wait_until

pytestmark = pytest.mark.platforms('posix')
ROOT = Path(__file__).resolve().parents[2]
_TARGET = '''
import errno, json, os, signal, subprocess, sys, threading, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from pm import lifetime_launch
lifetime_launch.adopt_arguments(sys.argv)
root, mode, ready, received, release, done, unrelated, identity = sys.argv[1:9]
ready, received, release, done = map(Path, (ready, received, release, done))
def publish(path, data):
    staged = path.with_name(path.name + '.tmp')
    staged.write_text(json.dumps(data))
    staged.replace(path)
seen = []
def stopped(number, frame):
    seen.append(number)
    publish(received, seen)
if mode == 'signal':
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, stopped)
if mode.startswith('descendant'):
    leaf = ready.with_name('leaf')
    command = [sys.executable, '-I', __file__, root, 'leaf', str(leaf), str(received), str(release), str(done), unrelated, identity]
    lifetime_launch.popen(command, managed=True, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 10
    while not leaf.exists() and time.monotonic() < deadline:
        threading.Event().wait(.01)
    assert leaf.exists()
try:
    info = os.fstat(int(unrelated))
    leaked = [info.st_dev, info.st_ino] == json.loads(identity)
except OSError as error:
    assert error.errno == errno.EBADF
    leaked = False
publish(ready, {'pid': os.getpid(), 'pgid': os.getpgrp(), 'sid': os.getsid(0),
                'refs': bool(lifetime_launch._BORROWED),
                'refs_open': all(os.fstat(fd) for fd in lifetime_launch._BORROWED),
                'leaked': leaked, 'ignored': signal.getsignal(signal.SIGTERM) == signal.SIG_IGN})
if mode == 'descendant-exit':
    os._exit(0)
deadline = time.monotonic() + 20
while not release.exists() and time.monotonic() < deadline:
    threading.Event().wait(.01)
publish(done if mode == 'leaf' else received, seen)
raise SystemExit(37)
'''
_DRIVER = '''
import runpy, signal, sys
from pathlib import Path
bootstrap, setup, ignored = sys.argv[1:4]
sys.argv = [bootstrap, *sys.argv[4:]]
if ignored == 'yes':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if setup != '-':
    Path(setup).write_text('starting bootstrap')
runpy.run_path(bootstrap, run_name='__main__')
'''


def _fixture(tmp_path):
    from pm.environments import venv_python
    from pm.lifetime import activity

    state = tmp_path / 'installs/fixture'
    generation = state / 'environments/gen-fixture'
    with activity(state, create=True):
        environment = generation / 'venv'
        venv.EnvBuilder(with_pip=False).create(environment)
        (generation / '.lease-managed').touch()
    script = tmp_path / 'target.py'
    script.write_text(_TARGET)
    return SimpleNamespace(state=state, generation=generation, python=venv_python(environment),
                           script=script, root=tmp_path, ready=tmp_path/'ready', received=tmp_path/'received',
                           release=tmp_path/'release', done=tmp_path/'done', setup=tmp_path/'setup')


def _start(fixture, mode='signal', *, ignored=False, setup=False):
    with (fixture.root / 'unrelated').open('w') as foreign:
        info = os.fstat(foreign.fileno())
        command = [sys._base_executable, '-I', '-S', '-B', '-c', _DRIVER,
                   str(ROOT/'scripts/lifetime_python.py'), str(fixture.setup) if setup else '-',
                   'yes' if ignored else 'no', '--managed', str(fixture.python), '-I', str(fixture.script),
                   str(ROOT), mode, str(fixture.ready), str(fixture.received), str(fixture.release),
                   str(fixture.done), str(foreign.fileno()), json.dumps([info.st_dev, info.st_ino])]
        return subprocess.Popen(command, cwd=ROOT, env=dict(os.environ), start_new_session=True,
                                pass_fds=(foreign.fileno(),), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _ready(fixture, process):
    wait_until(lambda: fixture.ready.exists() or process.poll() is not None, timeout=10)
    assert fixture.ready.exists(), process.communicate(timeout=5)
    return json.loads(fixture.ready.read_text())


def _released(fixture):
    from pm.lifetime import gate
    with gate(fixture.state) as guard:
        return not guard.held()


def _finish(fixture, process):
    fixture.release.touch()
    if process.poll() is None:
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()  # only this fixture's exact bootstrap/interpreter PID
            process.communicate(timeout=5)
    else:
        process.communicate(timeout=10)
    wait_until(lambda: _released(fixture), timeout=10)


@pytest.mark.parametrize('delivery', ['pid', 'group'])
@pytest.mark.parametrize('name', ['SIGTERM', 'SIGINT', 'SIGHUP'])
def test_one_cancellation_reaches_one_interpreter_once(tmp_path, delivery, name):
    fixture = _fixture(tmp_path)
    process = _start(fixture)
    try:
        record = _ready(fixture, process)
        assert record['pid'] == process.pid
        assert record['pgid'] == record['sid'] == process.pid
        number = getattr(signal, name)
        if delivery == 'group':
            os.killpg(process.pid, number)
        else:
            process.send_signal(number)
        wait_until(lambda: fixture.received.exists(), timeout=5)
        assert json.loads(fixture.received.read_text()) == [number]
        assert process.poll() is None and not _released(fixture)
        fixture.release.touch()
        _, error = process.communicate(timeout=10)
        assert process.returncode == 37, error
        assert json.loads(fixture.received.read_text()) == [number]
    finally:
        _finish(fixture, process)


def test_repeated_cancellation_is_not_suppressed(tmp_path):
    fixture = _fixture(tmp_path)
    process = _start(fixture)
    try:
        _ready(fixture, process)
        for count in (1, 2):
            process.send_signal(signal.SIGTERM)
            wait_until(lambda: fixture.received.exists() and
                       len(json.loads(fixture.received.read_text())) == count, timeout=5)
        fixture.release.touch()
        process.communicate(timeout=10)
        assert json.loads(fixture.received.read_text()) == [signal.SIGTERM, signal.SIGTERM]
    finally:
        _finish(fixture, process)


def test_default_signal_keeps_native_wait_status_and_releases_custody(tmp_path):
    fixture = _fixture(tmp_path)
    process = _start(fixture, 'default')
    try:
        _ready(fixture, process)
        process.terminate()
        process.communicate(timeout=5)
        assert process.returncode == -signal.SIGTERM
        assert _released(fixture)
    finally:
        _finish(fixture, process)


def test_ignored_signal_and_descriptor_boundary_survive_exec(tmp_path):
    fixture = _fixture(tmp_path)
    process = _start(fixture, 'default', ignored=True)
    try:
        record = _ready(fixture, process)
        assert record['pid'] == process.pid
        assert record['ignored'] and record['refs'] and record['refs_open'] and not record['leaked']
        process.terminate()
        fixture.release.touch()
        process.communicate(timeout=10)
        assert process.returncode == 37
    finally:
        _finish(fixture, process)


@pytest.mark.parametrize('mode', ['descendant-exit', 'descendant-term', 'descendant-group'])
def test_exact_descendant_custody_after_interpreter_exit(tmp_path, mode):
    fixture = _fixture(tmp_path)
    process = _start(fixture, mode)
    try:
        record = _ready(fixture, process)
        assert record['pid'] == process.pid
        if mode == 'descendant-term':
            process.terminate()
        elif mode == 'descendant-group':
            os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=5)
        if mode != 'descendant-group':
            assert not _released(fixture)
            fixture.release.touch()
            wait_until(lambda: fixture.done.exists(), timeout=10)
        wait_until(lambda: _released(fixture), timeout=10)
    finally:
        _finish(fixture, process)


def test_cancellation_during_registration_never_starts_an_unprotected_target(tmp_path):
    from pm.lifetime import gate
    fixture = _fixture(tmp_path)
    process = None
    try:
        with gate(fixture.state) as guard:
            process = _start(fixture, setup=True)
            wait_until(lambda: fixture.setup.exists(), timeout=10)
            process.terminate()
            process.communicate(timeout=5)
            assert process.returncode == -signal.SIGTERM
            assert not fixture.ready.exists() and not guard.held()
    finally:
        if process is not None:
            _finish(fixture, process)


def test_stop_continue_keeps_the_same_interpreter_and_custody(tmp_path):
    fixture = _fixture(tmp_path)
    process = _start(fixture)
    stopped = []
    try:
        assert _ready(fixture, process)['pid'] == process.pid
        process.send_signal(signal.SIGSTOP)
        def observed_stop():
            pid, status = os.waitpid(process.pid, os.WUNTRACED | os.WNOHANG)
            if pid:
                stopped.append(status)
                return True
            return False
        wait_until(observed_stop, timeout=5)
        assert os.WIFSTOPPED(stopped[0]) and not _released(fixture)
        process.send_signal(signal.SIGCONT)
        fixture.release.touch()
        _, error = process.communicate(timeout=10)
        assert process.returncode == 37, error
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGCONT)
        _finish(fixture, process)
