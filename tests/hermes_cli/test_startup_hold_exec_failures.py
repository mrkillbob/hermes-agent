"""Native POSIX exec handoff failures retain ownership and reject foreign capabilities."""
import json
import os
import sys

import pytest

from tests.hermes_cli.test_startup_hold_exec import ROOT

pytestmark = pytest.mark.platforms('posix')
FAILURE_DRIVER = r"""
import json, os, sys, threading
from pathlib import Path
from hermes_cli import startup_hold as policy, startup_hold_exec as transfer
sys.argv = ['hermes', 'gateway', 'run']
policy.register_process_writer(sys.argv[1:])
fd = policy._process_writers[0][1]
path = next(policy._writer_directory().glob('*.hold'))
other = os.open(str(path.parent.parent/'unrelated'), os.O_CREAT|os.O_RDWR, 0o600)
os.set_inheritable(other, True)
before = (path.read_bytes(), os.get_inheritable(fd), os.lseek(fd,0,os.SEEK_CUR),
          os.fstat(fd).st_ino, tuple(policy._process_writers), list(sys.orig_argv))
mode = os.environ['TEST_EXEC_FAILURE']
original_set, original_write = os.set_inheritable, os.write
changed = []
def fail_flag(number, flag):
    if number == fd and flag and not changed:
        changed.append(True)
        if mode == 'after-flag':
            original_set(number, flag)
        raise OSError('fixture inherit failure')
    return original_set(number,flag)
def fail_write(number, data):
    if number == fd and not changed:
        changed.append(True)
        original_write(number,data[:8])
        raise OSError('fixture partial record write')
    return original_write(number,data)
release, ready = threading.Event(), threading.Event()
worker = None
if mode in ('before-flag','after-flag'):
    os.set_inheritable = fail_flag
if mode == 'partial-record':
    os.write = fail_write
if mode == 'threaded':
    def wait():
        ready.set()
        release.wait(15)
    worker = threading.Thread(target=wait)
    worker.start()
    assert ready.wait(10)
try:
    try:
        command = [sys.executable, '-m', 'fixture'] if mode == 'wrong-command' else [sys.executable,'-c','pass']
        with transfer.writer_exec_handoff(command) as command:
            assert os.get_inheritable(fd)
            assert os.get_inheritable(other)
            assert tuple(policy._process_writers) == before[4]
            if mode == 'interrupt':
                raise KeyboardInterrupt('fixture exec interruption')
            os.execv('/nonexistent/hermes-startup-exec-fixture',command)
    except (OSError, RuntimeError, KeyboardInterrupt):
        pass
    else:
        raise AssertionError('failed exec unexpectedly returned')
finally:
    os.set_inheritable, os.write = original_set, original_write
    release.set()
    if worker:
        worker.join(10)
assert (path.read_bytes(),os.get_inheritable(fd),os.lseek(fd,0,os.SEEK_CUR),
        os.fstat(fd).st_ino,tuple(policy._process_writers),list(sys.orig_argv)) == before
assert os.get_inheritable(other)
os.close(other)
print(json.dumps({'restored':mode}))
"""

TARGET = r"""
import json, os, sys
from pathlib import Path
from hermes_cli import startup_hold as policy, startup_hold_exec as transfer
sys.argv=['hermes','gateway','run']
capability = getattr(sys,'_hermes_startup_writer_handoff')
mode=os.environ['TEST_EXEC_REJECT']
foreign=None
saved=None
if mode == 'fork':
    pid=os.fork()
    if pid:
        _, status=os.waitpid(pid,0)
        assert status==0
        transfer.adopt_process_writer()
        print(json.dumps({'rejected':mode,'references':len(policy._process_writers)}))
        raise SystemExit(0)
if mode == 'stale':
    saved=dict(capability)
    transfer.adopt_process_writer()
    setattr(sys,'_hermes_startup_writer_handoff',saved)
elif mode == 'nonce': capability['nonce']='0'*32
elif mode == 'root': capability['root'] += '/foreign'
elif mode == 'pid': capability['pid'] += 1
elif mode == 'purpose': capability.update(purpose='serve',native=False)
elif mode == 'name': capability['name']='0'*32+'.hold'
elif mode == 'identity': capability['inode'] += 1
elif mode == 'extra': capability['extra']='untrusted'
elif mode == 'schema': capability['schema']=True
elif mode == 'closed': os.close(capability['fd'])
elif mode == 'foreign':
    foreign=os.open(str(Path(capability['root'])/'foreign'),os.O_CREAT|os.O_RDWR,0o600)
    capability['fd']=foreign
elif mode in ('unlocked','foreign-lock'):
    path=Path(capability['root'])/'.startup-writers'/capability['name']
    if mode == 'foreign-lock':
        saved=os.dup(capability['fd'])
    foreign=os.open(path,os.O_RDWR)
    os.dup2(foreign,capability['fd'])
    os.close(foreign)
    foreign=capability['fd']
    os.set_inheritable(foreign,False)
try:
    transfer.adopt_process_writer()
except (RuntimeError,OSError,ValueError):
    if foreign is not None:
        os.fstat(foreign)  # refusal must not close an arbitrary foreign descriptor
    assert len(list(policy._writer_directory().glob('*.hold')))==1
    assert len(policy._process_writers)==(1 if mode=='stale' else 0)
    if mode == 'fork':
        os._exit(0)
    print(json.dumps({'rejected':mode,'references':len(policy._process_writers)}),flush=True)
    if mode == 'stale':
        raise SystemExit(0)
    os._exit(0)
raise AssertionError('invalid exec capability adopted')
"""
ADOPTION_DRIVER = r"""
import os, sys
from hermes_cli import startup_hold as policy, startup_hold_exec as transfer
sys.argv=['hermes','gateway','run']
policy.register_process_writer(sys.argv[1:])
with transfer.writer_exec_handoff([sys.executable,'-c',os.environ['TEST_EXEC_TARGET']]) as command:
    os.execv(command[0],command)
"""


def child(home, program, **fields):
    from pm.lifetime_launch import run
    result=run([sys.executable,'-c',program],cwd=ROOT,
               env=dict(os.environ,HERMES_HOME=str(home),**fields),capture_output=True,text=True,timeout=25)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Exception ignored in atexit' not in result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('mode',['exec-error','interrupt','before-flag','after-flag',
                                 'partial-record','threaded','wrong-command'])
def test_failed_exec_keeps_original_reference_and_cleanup(tmp_path,monkeypatch,mode):
    from hermes_cli import startup_hold
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    assert child(tmp_path,FAILURE_DRIVER,TEST_EXEC_FAILURE=mode)=={'restored':mode}
    assert len(list((tmp_path/'.startup-writers').glob('*.hold')))==1
    with startup_hold.admission_gate(),startup_hold.writer_exclusion():
        pass


@pytest.mark.parametrize('mode',['nonce','root','pid','purpose','name','identity','extra','schema',
                                 'closed','foreign','unlocked','foreign-lock','stale','fork'])
def test_invalid_adoption_never_creates_replacement_or_closes_foreign_fd(tmp_path,monkeypatch,mode):
    from hermes_cli import startup_hold
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    result=child(tmp_path,ADOPTION_DRIVER,TEST_EXEC_TARGET=TARGET,TEST_EXEC_REJECT=mode)
    assert result=={'rejected':mode,'references':1 if mode in ('stale','fork') else 0}
    if mode in ('stale','fork'):
        with startup_hold.admission_gate(),startup_hold.writer_exclusion():
            pass
    else:
        with startup_hold.admission_gate():
            with pytest.raises(RuntimeError,match='startup_hold_writers_busy'):
                with startup_hold.writer_exclusion():
                    pytest.fail('rejected capability debt disappeared')


ROLLBACK_DRIVER = r"""
import atexit, json, os, sys
from pathlib import Path
from hermes_cli import startup_hold as policy, startup_hold_exec as transfer
sys.argv=['hermes','gateway','run']
foreign_path=Path(os.environ['HERMES_HOME'])/'foreign-data'
foreign_path.parent.mkdir(parents=True,exist_ok=True)
foreign_path.write_bytes(b'foreign data must survive')
watched_path=foreign_path
expected=b'foreign data must survive'
fd=None

def inspect():
    try:
        os.fstat(fd)
        still_open=True
    except OSError:
        still_open=False
    print(json.dumps({'foreign_still_open':still_open,
                      'foreign_unchanged':watched_path.read_bytes()==expected}),flush=True)
atexit.register(inspect)
policy.register_process_writer(sys.argv[1:])
fd=policy._process_writers[0][1]
foreign=os.open(foreign_path,os.O_RDWR)
with transfer.writer_exec_handoff([sys.executable,'-c','pass']) as command:
    if os.environ['TEST_EXEC_REUSE'] == 'same-inode':
        watched_path=next(policy._writer_directory().glob('*.hold'))
        expected=watched_path.read_bytes()
        os.close(foreign)
        foreign=os.open(watched_path,os.O_RDWR)
    os.dup2(foreign,fd)
    os.set_inheritable(fd,False)
    raise OSError('fixture failed exec after descriptor custody changed')
"""


@pytest.mark.parametrize('reuse',['foreign-file','same-inode'])
def test_failed_exec_cleanup_does_not_touch_reused_foreign_descriptor(tmp_path,monkeypatch,reuse):
    from hermes_cli import startup_hold
    from pm.lifetime_launch import run
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    result=run([sys.executable,'-c',ROLLBACK_DRIVER],cwd=ROOT,
               env=dict(os.environ,HERMES_HOME=str(tmp_path),TEST_EXEC_REUSE=reuse),capture_output=True,text=True,timeout=20)
    assert result.returncode != 0
    assert 'startup_writer_handoff_restore_failed' in result.stderr
    assert 'Exception ignored in atexit' not in result.stderr
    assert json.loads(result.stdout)=={'foreign_still_open':True,'foreign_unchanged':True}
    with startup_hold.admission_gate():
        with pytest.raises(RuntimeError,match='startup_hold_writers_busy'):
            with startup_hold.writer_exclusion():
                pytest.fail('uncertain failed-exec reservation was retired')


PRELUDE_DRIVER = r"""
import os, sys
from pathlib import Path
from hermes_cli import startup_hold as policy, startup_hold_exec as transfer
sys.argv=['hermes','gateway','run']
policy.register_process_writer(sys.argv[1:])
body="from pathlib import Path; import os; Path(os.environ['HERMES_HOME'],'body-ran').touch()"
with transfer.writer_exec_handoff([sys.executable,'-c',body]) as command:
    if os.environ['TEST_EXEC_PRELUDE']=='closed':
        os.close(policy._process_writers[0][1])
    else:
        command[-1]+='\n# changed logical body'
    os.execv(command[0],command)
"""


@pytest.mark.parametrize('mode',['closed','changed-body'])
def test_reexec_prelude_failure_preserves_uncertainty_before_body(tmp_path,monkeypatch,mode):
    from hermes_cli import startup_hold
    from pm.lifetime_launch import run
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    result=run([sys.executable,'-c',PRELUDE_DRIVER],cwd=ROOT,
               env=dict(os.environ,HERMES_HOME=str(tmp_path),TEST_EXEC_PRELUDE=mode),
               capture_output=True,text=True,timeout=20)
    assert result.returncode != 0
    assert not (tmp_path/'body-ran').exists()
    assert len(list((tmp_path/'.startup-writers').glob('*.hold')))==1
    with startup_hold.admission_gate():
        with pytest.raises(RuntimeError,match='startup_hold_writers_busy'):
            with startup_hold.writer_exclusion():
                pytest.fail('failed re-entry discarded uncertain reservation')


READY_DRIVER = r"""
import json,os,sys
from pathlib import Path
from hermes_cli import startup_hold as policy
sys.argv=['hermes','gateway','run']
policy.register_process_writer(sys.argv[1:])
fd=policy._process_writers[0][1]
path=Path(os.environ['HERMES_HOME'])/'foreign-ready-data'
path.write_bytes(b'foreign native-exit data')
if os.environ['TEST_READY_REUSE']=='same-inode':
    path=next(policy._writer_directory().glob('*.hold'))
expected=path.read_bytes()
foreign=os.open(path,os.O_RDWR)
os.dup2(foreign,fd)
os.close(foreign)
os.set_inheritable(fd,False)
policy.mark_native_exit_ready()
os.fstat(fd)
print(json.dumps({'unchanged':path.read_bytes()==expected,
                  'uncertain':str(policy.get_default_hermes_root().resolve()) in policy._uncertain_children}),flush=True)
os._exit(0)
"""


@pytest.mark.parametrize('reuse',['foreign-file','same-inode'])
def test_native_exit_ready_never_writes_a_reused_foreign_descriptor(tmp_path,monkeypatch,reuse):
    from hermes_cli import startup_hold
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    result=child(tmp_path,READY_DRIVER,TEST_READY_REUSE=reuse)
    assert result=={'unchanged':True,'uncertain':True}
    with startup_hold.admission_gate():
        with pytest.raises(RuntimeError,match='startup_hold_writers_busy'):
            with startup_hold.writer_exclusion():
                pytest.fail('native exit publication erased uncertain ownership')
