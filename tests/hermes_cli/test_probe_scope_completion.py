"""Completion and shutdown share one cleanup attempt for each owned probe."""
import signal
import subprocess
import sys
import threading

import pytest

from hermes_cli import _subprocess_compat as compat
from hermes_cli.probe_lifecycle import ProbeScope, bind_probe_scope
from hermes_cli.source_check import _git_run

pytestmark = pytest.mark.platforms("posix")


def test_completed_group_is_not_signalled_again_before_scope_release(monkeypatch):
    scope = ProbeScope()
    at_release = threading.Event()
    finish_release = threading.Event()
    calls, errors = [], []
    original_release = scope.release

    def observe_group(pgid, sig):
        assert sig == signal.SIGKILL
        calls.append(pgid)  # Observer only: never signal a potentially recycled group.

    def delayed_release(proc):
        assert proc.returncode == 0
        at_release.set()
        assert finish_release.wait(5)
        original_release(proc)

    def worker():
        try:
            with bind_probe_scope(scope):
                assert _git_run(['-c', "print('complete')"], git=sys.executable).returncode == 0
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(compat.os, 'killpg', observe_group)
    monkeypatch.setattr(scope, 'release', delayed_release)
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert at_release.wait(5)
        completed_calls = list(calls)
        assert len(completed_calls) == 1
        scope.close()
        scope.close()
        assert calls == completed_calls
    finally:
        finish_release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors


def test_group_cleanup_oserror_preserves_successful_source_result(monkeypatch):
    def denied_group(*_args):
        raise PermissionError('synthetic owned group denial')

    monkeypatch.setattr(compat.os, 'killpg', denied_group)
    result = _git_run(['-c', "print('complete')"], git=sys.executable)
    assert result.returncode == 0
    assert result.stdout == 'complete\n'
    assert result.stderr == ''


def test_cleanup_denial_does_not_stop_other_owned_probes(tmp_path, monkeypatch):
    scope = ProbeScope()
    processes = []

    def denied_group(*_args):
        raise PermissionError('synthetic owned group denial')

    monkeypatch.setattr(compat.os, 'killpg', denied_group)
    try:
        for _ in range(2):
            proc = subprocess.Popen([sys.executable, '-c', 'import threading; threading.Event().wait(300)'],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, process_group=0)
            processes.append(proc)
            scope.spawn(lambda proc=proc: (proc, None))
        scope.close()
        for proc in processes:
            assert proc.wait(timeout=5) != 0
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
