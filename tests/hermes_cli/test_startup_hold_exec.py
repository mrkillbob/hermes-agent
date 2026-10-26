"""Actual POSIX bootstrap re-exec keeps one continuous startup writer reservation."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys

import pytest

pytestmark = pytest.mark.platforms('posix')
ROOT = Path(__file__).resolve().parents[2]
DRIVER = r"""
import json, os, subprocess, sys
from pathlib import Path
def no_provider(event, arguments):
    if event in ('socket.connect', 'socket.connect_ex'):
        raise AssertionError('source bootstrap regression must remain provider-free')
sys.addaudithook(no_provider)
stage = Path(os.environ['TEST_EXEC_STAGE'])
count = int(stage.read_text()) if stage.exists() else 0
total = int(os.environ['TEST_EXEC_COUNT'])
capability = getattr(sys, '_hermes_startup_writer_handoff', None)
leaked = False
if capability:
    fd = capability['fd']
    info = os.fstat(fd)
    assert not os.get_inheritable(fd)
    probe = ('import json,os,sys\n'
             'try:\n s=os.fstat(int(sys.argv[1])); same=[s.st_dev,s.st_ino]==json.loads(sys.argv[2])\n'
             'except OSError: same=False\n'
             'print(json.dumps(same))')
    result = subprocess.run([sys.executable, '-c', probe, str(fd), json.dumps([info.st_dev,info.st_ino])],
                            close_fds=False, capture_output=True, text=True, check=True, timeout=10)
    leaked = json.loads(result.stdout)
sys.argv = ['-c', 'gateway', 'run', '--fixture-value', 'café space']
from pm import environments
from hermes_cli import _early_recovery, venv_sync
environments.activate_dependencies = lambda *a, **k: None
_early_recovery.recover_if_needed = lambda *a, **k: None

def report(phase):
    from hermes_cli import startup_hold
    fd = startup_hold._process_writers[0][1]
    info = os.fstat(fd)
    print(json.dumps(dict(phase=phase, pid=os.getpid(), fd=fd, device=info.st_dev,
                         inode=info.st_ino, inheritable=os.get_inheritable(fd),
                         references=len(list(startup_hold._writer_directory().glob('*.hold'))),
                         helper_leaked=leaked, argv=sys.argv, utf8=sys.flags.utf8_mode)), flush=True)

def select_interpreter(*a, **k):
    if count >= total:
        return None
    if count == 0 and os.environ.get('TEST_EXEC_OPAQUE') == '1':
        from hermes_cli.startup_hold import mark_uncertain_children
        mark_uncertain_children()
    stage.write_text(str(count + 1))
    report('before-exec')
    assert sys.stdin.readline().strip() == 'exec'
    return Path(sys.executable)  # prepare_launch also re-enters when PM is not current.
venv_sync.prepare_launch = select_interpreter
import hermes_bootstrap
report('after-exec')
assert sys.stdin.readline().strip() == 'exit'
"""


def message(child):
    readable, _, _ = select.select([child.stdout], [], [], 15)
    assert readable, 'bootstrap did not reach its bounded barrier'
    line = child.stdout.readline()
    assert line, child.stderr.read() if child.poll() is not None else 'bootstrap stopped publishing'
    return json.loads(line)


def send(child, value):
    child.stdin.write(value + '\n')
    child.stdin.flush()


def stop(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def exclusion_is_busy():
    from hermes_cli import startup_hold
    with startup_hold.admission_gate():
        with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
            with startup_hold.writer_exclusion():
                pytest.fail('live bootstrap reservation was lost')


def exercise_exec(tmp_path, monkeypatch, repeats, opaque, home):
    from hermes_cli import startup_hold
    from pm.lifetime_launch import popen
    monkeypatch.setenv('HERMES_HOME', str(home))
    env = dict(os.environ, HERMES_HOME=str(home), TEST_EXEC_STAGE=str(tmp_path/'phase'),
               TEST_EXEC_COUNT=str(repeats), TEST_EXEC_OPAQUE='1' if opaque else '0')
    with popen([sys.executable, '-X', 'utf8', '-c', DRIVER], cwd=ROOT, env=env, text=True,
               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        try:
            first = message(child)
            assert first['phase'] == 'before-exec'
            exclusion_is_busy()
            send(child, 'exec')
            for index in range(repeats):
                current = message(child)
                assert current['phase'] == ('after-exec' if index == repeats-1 else 'before-exec')
                assert {k: current[k] for k in ('pid','fd','device','inode')} == {
                    k: first[k] for k in ('pid','fd','device','inode')}
                assert current['references'] == 1
                assert current['argv'] == first['argv']
                assert current['utf8'] == first['utf8'] == 1
                assert not current['inheritable'] and not current['helper_leaked']
                exclusion_is_busy()
                if index < repeats-1:
                    send(child, 'exec')
            send(child, 'exit')
            _, err = child.communicate(timeout=15)
            assert child.returncode == 0, err
            assert 'Exception ignored in atexit' not in err
        finally:
            stop(child)
    assert len(list((home/'.startup-writers').glob('*.hold'))) == 1
    if opaque:
        exclusion_is_busy()
    else:
        with startup_hold.admission_gate(), startup_hold.writer_exclusion():
            pass


@pytest.mark.parametrize('repeats',[1,2,3])
@pytest.mark.parametrize('opaque',[False,True])
def test_bootstrap_reexec_preserves_one_reference_until_real_departure(tmp_path,monkeypatch,repeats,opaque):
    exercise_exec(tmp_path,monkeypatch,repeats,opaque,tmp_path/'hôme-雪')


def test_bootstrap_reexec_preserves_non_utf8_posix_home(tmp_path,monkeypatch):
    import errno
    home=Path(os.fsdecode(os.fsencode(tmp_path)+b'/home-\xff'))
    try:
        home.mkdir()
    except (OSError,UnicodeError) as error:
        if isinstance(error,UnicodeError) or error.errno==errno.EILSEQ:
            pytest.skip('native filesystem requires UTF-8 pathnames')
        raise
    exercise_exec(tmp_path,monkeypatch,2,False,home)
