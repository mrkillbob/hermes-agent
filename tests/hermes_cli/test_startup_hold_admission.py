"""Native pre-owner, exact departure, updater custody and bounded publication contracts."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hermes_state_runtime import RuntimeStoreError


@pytest.fixture
def home(tmp_path, monkeypatch):
    from gateway import drain_startup
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(drain_startup, '_checkout_root', lambda: tmp_path, raising=False)
    monkeypatch.setattr(drain_startup, '_refuse_legacy_writers', lambda *args: None)
    (tmp_path / 'ESTOP').touch()
    return tmp_path


@pytest.mark.platforms('any')
def test_native_boot_reservation_precedes_profile_ownership(home):
    from gateway import drain_startup
    from pm.lifetime_launch import popen
    value = drain_startup.begin()
    code = ('import os,sys; sys.argv=["hermes","gateway","run"]; '
            'import hermes_bootstrap; print("before-profile-owner",flush=True); '
            'sys.stdin.readline(); os._exit(0)')
    with popen([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[2],
               env={**os.environ, 'HERMES_HOME': str(home)}, stdin=subprocess.PIPE,
               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        try:
            assert child.stdout.readline().strip() == 'before-profile-owner'
            with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
                drain_startup.restore(hold_id=value['hold_id'])
        finally:
            child.communicate('\n', timeout=10)
    assert child.returncode == 0


@pytest.mark.platforms('any')
def test_closed_clean_reference_waits_for_actual_incarnation_departure(home):
    from hermes_cli import startup_hold
    from pm.lifetime_launch import popen
    code = ('from hermes_cli.startup_hold import writer_scope\nimport sys\n'
            'with writer_scope():\n    pass\n'
            'print("closed-before-exit",flush=True)\nsys.stdin.readline()\n')
    with popen([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[2],
               env={**os.environ, 'HERMES_HOME': str(home)}, stdin=subprocess.PIPE,
               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        try:
            assert child.stdout.readline().strip() == 'closed-before-exit'
            with startup_hold.admission_gate():
                with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
                    with startup_hold.writer_exclusion():
                        pytest.fail('a live incarnation was retired before actual exit')
        finally:
            child.communicate('\n', timeout=10)
    with startup_hold.admission_gate(), startup_hold.writer_exclusion():
        pass


def test_ensure_transport_defers_parent_maintenance_without_writer_debt(home, monkeypatch):
    from hermes_cli import startup_hold
    monkeypatch.setattr(startup_hold, '_process_writers', [])
    assert startup_hold.defer_maintenance(['gateway', 'ensure', '--json'])
    startup_hold.register_process_writer(['gateway', 'ensure', '--json'])
    assert not startup_hold._process_writers
    assert not (home / '.startup-writers').exists()


def test_existing_checkout_custody_refuses_hold_creation(home):
    from gateway import drain_startup
    from hermes_cli import update_lock
    assert update_lock._acquire_checkout(home) is None
    try:
        with pytest.raises(RuntimeStoreError, match='startup_hold_checkout_busy'):
            drain_startup.begin()
    finally:
        update_lock._release_checkout()
    assert not (home / '.startup-hold.json').exists()


def test_broken_checkout_lock_never_becomes_unguarded_permission(home, monkeypatch):
    from gateway import drain_startup
    from hermes_cli import update_lock
    monkeypatch.setattr(update_lock, '_acquire_checkout', lambda root: SimpleNamespace(reason='unsupported lock'))
    with pytest.raises(RuntimeStoreError, match='startup_hold_checkout_busy'):
        drain_startup.begin()
    assert not (home / '.startup-hold.json').exists()


def test_pending_recovery_is_rechecked_under_checkout_custody(home):
    from gateway import drain_startup
    (home / '.update-incomplete').touch()
    with pytest.raises(RuntimeStoreError, match='startup_hold_recovery_pending'):
        drain_startup.begin()
    assert not (home / '.startup-hold.json').exists()


def test_expanded_prior_marker_refuses_before_publishing_either_file(home):
    from gateway import drain_startup
    marker = home / '.drain_request.json'
    prior = json.dumps({'action': 'drain', 'note': '"' * 350_000}).encode()
    assert len(prior) < 1_048_576
    marker.write_bytes(prior)
    with pytest.raises(RuntimeStoreError, match='startup_hold_state_too_large'):
        drain_startup.begin()
    assert marker.read_bytes() == prior
    assert not (home / '.startup-hold.json').exists()


@pytest.mark.platforms('any')
@pytest.mark.parametrize('action,purpose',[('release','gateway'),('timeout','gateway'),
                                         ('hold','serve'),('hold','gateway')])
def test_parallel_startup_waits_only_for_bounded_admission(tmp_path,monkeypatch,action,purpose):
    from hermes_cli import startup_hold
    from pm.lifetime_launch import popen
    from tests.waiting import wait_until
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    code = r"""
import os
from pathlib import Path
from pm import filesystem
from hermes_cli import startup_hold
home=Path(os.environ['HERMES_HOME'])
real_lock=filesystem.lock_fd
seen=[]
def observed(fd,*,wait,timeout=None):
    if not seen:
        result=real_lock(fd,wait=False)
        if not result:
            seen.append(True)
            (home/'contended').write_text('real native lock contention')
            if wait:
                return real_lock(fd,wait=True,timeout=timeout)
        return result
    return real_lock(fd,wait=wait,timeout=timeout)
filesystem.lock_fd=observed
arguments=['gateway','run'] if os.environ['TEST_STARTUP_PURPOSE']=='gateway' else ['serve']
startup_hold.register_process_writer(arguments)
(home/'registered').write_text(str(startup_hold.active()))
"""
    gate=startup_hold.admission_gate()
    gate.__enter__()
    process=None
    try:
        process=popen([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[2],
                      env=dict(os.environ,HERMES_HOME=str(tmp_path),TEST_STARTUP_PURPOSE=purpose),
                      stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        assert wait_until(lambda:(tmp_path/'contended').exists() or process.poll() is not None,timeout=10)
        assert (tmp_path/'contended').exists()
        assert not (tmp_path/'registered').exists()
        if action == 'hold':
            # Any durable journal, including preparing/corrupt state, must fail closed.
            (tmp_path/startup_hold.JOURNAL_FILENAME).write_text('{}')
        if action != 'timeout':
            gate.__exit__(None,None,None)
            gate=None
        out,err=process.communicate(timeout=10)
        if action != 'timeout' and not (action=='hold' and purpose=='serve'):
            assert process.returncode==0,(out,err)
            assert (tmp_path/'registered').read_text()==str(action=='hold')
            assert len(list((tmp_path/'.startup-writers').glob('*.hold')))==1
        else:
            assert process.returncode != 0,(out,err)
            assert 'startup_hold_writers_busy' in err
            assert not (tmp_path/'.startup-writers').exists()
    finally:
        if gate is not None:
            gate.__exit__(None,None,None)
        if process is not None and process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)
