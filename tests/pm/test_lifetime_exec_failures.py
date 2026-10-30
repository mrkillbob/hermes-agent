"""The stdlib-only exec boundary preserves descriptor isolation and rolls failures back."""
import json
import os
import subprocess
import sys

import pytest

from tests.pm.test_lifetime_exec import ROOT, _fixture

pytestmark = pytest.mark.platforms('posix')
_DRIVER = '''
import errno, json, os, signal, sys, threading
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from pm import lifetime_handles, lifetime_launch
from pm.lifetime import acquire_activity, gate
root, state, generation, target, foreign_path, mode = sys.argv[1:7]
state, generation = Path(state), Path(generation)
borrowed = acquire_activity(state, generation=generation)
fd = os.open(foreign_path, os.O_CREAT | os.O_RDWR, 0o600)
os.set_inheritable(fd, True)
original_set = os.set_inheritable
original_list = os.listdir
previous_signal = signal.getsignal(signal.SIGPIPE)
before = {fd: True, borrowed.fd: os.get_inheritable(borrowed.fd)}
stdio = [os.get_inheritable(number) for number in (0,1,2)]
lifetime_launch.adopt_arguments(['--pm-lifetime-references='+str(borrowed.reference)])
seen = []
def execute(command, arguments):
    seen.append('exec')
    assert not os.get_inheritable(fd)
    assert os.get_inheritable(borrowed.fd)
    assert [os.get_inheritable(number) for number in (0,1,2)] == stdio
    assert signal.getsignal(signal.SIGPIPE) == signal.SIG_DFL
    if mode == 'interrupt':
        raise KeyboardInterrupt('synthetic setup interruption')
    raise FileNotFoundError('synthetic exec failure')
os.execvp = execute
if mode in ('inventory-missing', 'inventory-denied', 'transient'):
    def inventory(path):
        if path in ('/proc/self/fd','/dev/fd'):
            if mode == 'inventory-missing':
                raise FileNotFoundError('synthetic missing inventory')
            if mode == 'inventory-denied':
                raise PermissionError('synthetic inaccessible inventory')
            return [*original_list(path), '999999997']
        return original_list(path)
    os.listdir = inventory
if mode in ('partial-flags','rollback-error'):
    changes = []
    def change(number, inheritable):
        changes.append((number,inheritable))
        if mode == 'partial-flags' and len(changes) == 2:
            raise OSError(errno.EIO, 'synthetic partial setup failure')
        if mode == 'rollback-error' and number == fd and inheritable:
            raise PermissionError('synthetic rollback failure')
        return original_set(number,inheritable)
    os.set_inheritable = change
if mode == 'missing-borrowed':
    lifetime_launch._BORROWED = (999999998,)
if mode == 'reentrant':
    lifetime_launch._EXECUTING = True
errors = []
def invoke():
    try:
        lifetime_launch.main(['--managed', target, '-c', 'pass'])
    except BaseException as error:
        errors.append(error)
try:
    if mode == 'threaded':
        worker = threading.Thread(target=invoke)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
    else:
        invoke()
    assert len(errors) == 1
    error = errors[0]
    expected = {'inventory-missing': RuntimeError, 'inventory-denied': PermissionError,
                'partial-flags': OSError, 'rollback-error': PermissionError, 'missing-borrowed': OSError,
                'interrupt': KeyboardInterrupt, 'site-enabled': RuntimeError,
                'threaded': RuntimeError, 'reentrant': RuntimeError}.get(mode,FileNotFoundError)
    assert isinstance(error,expected), repr(error)
    if mode in ('exec-error','interrupt','transient','rollback-error'):
        assert seen == ['exec']
    else:
        assert not seen
    if mode == 'rollback-error':
        assert 'synthetic rollback failure' in str(error)
        assert error.__context__ is not None
        original_set(fd,True)  # repair only this synthetic descriptor after proving reported rollback failure
    for number, inherited in before.items():
        assert os.get_inheritable(number) == inherited
    assert [os.get_inheritable(number) for number in (0,1,2)] == stdio
    assert signal.getsignal(signal.SIGPIPE) == previous_signal
    assert len(list((state.parent/'.lifetimes'/state.name/'activities').iterdir())) == 1
    assert lifetime_launch._EXECUTING is (mode == 'reentrant')
finally:
    os.set_inheritable = original_set
    os.listdir = original_list
    os.close(fd)
    borrowed.close()
with gate(state) as guard:
    assert not guard.held()
print(json.dumps({'passed': mode}))
'''


@pytest.mark.parametrize('mode', ['exec-error', 'interrupt', 'inventory-missing', 'inventory-denied',
                                 'transient', 'partial-flags', 'rollback-error', 'missing-borrowed',
                                 'site-enabled', 'threaded', 'reentrant'])
def test_exec_failure_restores_owned_and_borrowed_state(tmp_path, mode):
    fixture = _fixture(tmp_path)
    command = [sys._base_executable, '-I', '-B']
    if mode != 'site-enabled':
        command.append('-S')
    command.extend(['-c', _DRIVER, str(ROOT), str(fixture.state), str(fixture.generation),
                    str(fixture.python), str(tmp_path/'foreign'), mode])
    result = subprocess.run(command, cwd=ROOT, env=dict(os.environ), capture_output=True,
                            text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {'passed': mode}
