"""Strict writer inventory: actual references, failed census and unknown departures hold maintenance."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    return tmp_path


@pytest.mark.platforms('posix')
def test_strict_census_does_not_treat_failed_process_probe_as_empty(monkeypatch):
    from hermes_cli import dashboard_procs
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess([], 1, '', 'unavailable'))
    with pytest.raises(RuntimeError, match='startup_hold_writers_unknown'):
        dashboard_procs._iter_process_table(strict=True)


@pytest.mark.parametrize('content', ['{broken', '{}', '[null]'])
def test_legacy_inventory_rejects_corrupt_ledger(home, monkeypatch, content):
    from hermes_cli import startup_hold_writers, dashboard_procs
    monkeypatch.setattr(dashboard_procs, '_iter_process_table', lambda **kwargs: [])
    (home / 'spawn-ledger.json').write_text(content)
    with pytest.raises(RuntimeError, match='startup_hold_writers_unknown'):
        startup_hold_writers.refuse_legacy_writers()


def test_unproven_helper_identity_retains_hold(home, monkeypatch):
    from hermes_cli import startup_hold_writers, dashboard_procs, process_identity
    monkeypatch.setattr(dashboard_procs, '_iter_process_table', lambda **kwargs: [])
    monkeypatch.setattr(process_identity, '_pid_alive_matches', lambda *args, **kwargs: None)
    (home / 'spawn-ledger.json').write_text(json.dumps([{'pid': 123, 'create_time': None, 'purpose': 'mcp-helper'}]))
    with pytest.raises(RuntimeError, match='startup_hold_writers_unknown'):
        startup_hold_writers.refuse_legacy_writers()


@pytest.mark.platforms('any')
def test_abrupt_writer_exit_leaves_uncertain_reservation(home):
    from hermes_cli import startup_hold
    from pm.lifetime_launch import popen
    code = "import os; from hermes_cli.startup_hold import writer_scope; s=writer_scope(); s.__enter__(); os._exit(0)"
    with popen([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[2],
               env={**os.environ, 'HERMES_HOME': str(home)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        out, err = child.communicate(timeout=10)
    assert child.returncode == 0, (out, err)
    with startup_hold.admission_gate():
        with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
            with startup_hold.writer_exclusion():
                pytest.fail('a dead PID is not proof its writers exited')


def test_unsettled_child_factory_cannot_be_automatically_retired(home):
    from hermes_cli import startup_hold
    with startup_hold.writer_scope():
        startup_hold.mark_uncertain_children()
    with startup_hold.admission_gate():
        with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
            with startup_hold.writer_exclusion():
                pytest.fail('opaque child lifetime was treated as quiescent')
