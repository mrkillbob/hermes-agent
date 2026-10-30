"""Initial control transactions keep version probes read-only before any hold exists."""
import subprocess
import sys

import pytest


@pytest.mark.parametrize('command', [
    ['gateway', 'drain', 'hold'],
    ['gateway', 'drain', 'reconcile'],
    ['gateway', 'drain', 'prepare-reboot-reconcile'],
    ['gateway', 'drain', 'restore', '--hold-id', 'fixture'],
    ['gateway', 'ensure', '--json'],
])
def test_initial_administrative_git_probe_disables_hooks_and_lazy_fetch(tmp_path, monkeypatch, command):
    from hermes_cli import version_info
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['hermes', *command])
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        kwargs['stdout'].write(b'fixture-head\n')
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(version_info.subprocess, 'run', run)
    assert version_info._run_git(tmp_path, 'rev-parse', 'HEAD') == 'fixture-head'
    argv, options = calls[0]
    assert argv == ['git', '-c', 'core.fsmonitor=false', 'rev-parse', 'HEAD']
    assert options['env']['GIT_NO_LAZY_FETCH'] == '1'
    assert options['env']['GIT_OPTIONAL_LOCKS'] == '0'
    assert options['timeout'] == 3
    assert not (tmp_path / '.startup-hold.json').exists()
    assert not (tmp_path / '.drain_request.json').exists()
