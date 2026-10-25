"""Regressions found by independent startup-hold review."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'ESTOP').touch()
    from gateway import drain_startup
    monkeypatch.setattr(drain_startup, '_refuse_legacy_writers', lambda *args: None)
    monkeypatch.setattr(drain_startup, '_checkout_root', lambda: Path(os.environ['HERMES_HOME']))
    return home


def test_exact_prior_marker_keeps_crlf_bytes(home):
    from gateway import drain_startup
    marker = home / '.drain_request.json'
    prior = b'{"action":"drain"}\r\n'
    marker.write_bytes(prior)
    value = drain_startup.begin()
    drain_startup.restore(hold_id=value['hold_id'])
    assert marker.read_bytes() == prior


@pytest.mark.parametrize('flag', ['--repl', '--for', '--replace=true'])
def test_abbreviated_replacement_flags_refuse_before_boot(home, flag):
    from gateway import drain_startup
    from hermes_cli import startup_hold
    drain_startup.begin()
    with pytest.raises(SystemExit, match='startup_hold'):
        startup_hold.enter_boot(home / 'source', ['gateway', 'run', flag])


def test_discovered_native_hold_keeps_its_actual_reason(home):
    from hermes_cli.gateway_runtime import _endpoint
    from tests.hermes_cli.test_gateway_runtime_discovery import _ready_owner_payload
    payload = _ready_owner_payload(home)
    payload.update(state='draining', drain_reason='startup_hold')
    result = _endpoint(payload, home)
    assert result.state == 'draining'
    assert result.reason_code == 'startup_hold'


def test_unheld_torn_tree_recovery_precedes_new_hold_import(tmp_path):
    """Only bootstrap is guaranteed present during a ZIP swap; repair gets first chance."""
    root = tmp_path / 'source'
    root.mkdir()
    shutil.copy2(Path(__file__).resolve().parents[2] / 'hermes_bootstrap.py', root / 'hermes_bootstrap.py')
    package = root / 'hermes_cli'
    package.mkdir()
    (package / '__init__.py').touch()
    witness = tmp_path / 'recovery-entered'
    (package / '_early_recovery.py').write_text(
        'from pathlib import Path\n'
        'def restore_interrupted_pull(*args):\n'
        f'    Path({str(witness)!r}).touch()\n'
        '    return False\n')
    subprocess.run([sys.executable, '-S', '-c', 'import hermes_bootstrap'], cwd=root,
        env={**os.environ, 'PYTHONPATH': str(root), 'HERMES_HOME': str(tmp_path / 'empty-home')},
        capture_output=True, timeout=10, check=False)
    assert witness.exists()


@pytest.mark.parametrize('damage', [b'', b'not-sqlite-private-fixture'])
def test_held_database_corruption_is_retained_without_quarantine(home, damage):
    from gateway import drain_startup
    from hermes_state import SessionDB
    drain_startup.begin()
    path = home / 'state.db'
    path.write_bytes(damage)
    before = set(home.iterdir())
    with pytest.raises(RuntimeError, match='startup_hold'):
        SessionDB(path)
    assert path.read_bytes() == damage
    assert set(home.iterdir()) == before


def test_clean_exact_incarnation_receipt_covers_its_ledger_row(home, monkeypatch):
    from hermes_cli import startup_hold, startup_hold_writers, dashboard_procs
    import subprocess
    import sys
    from pm.lifetime_launch import popen
    monkeypatch.setattr(dashboard_procs, '_iter_process_table', lambda **kwargs: [])
    code = 'from hermes_cli.startup_hold import writer_scope\nwith writer_scope():\n    pass\n'
    with popen([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[2],
               env={**os.environ, 'HERMES_HOME': str(home)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        out, err = child.communicate(timeout=10)
    assert child.returncode == 0, (out, err)
    receipt = json.loads(next((home / '.startup-writers').iterdir()).read_text())
    (home / 'spawn-ledger.json').write_text(json.dumps([dict(purpose='serve', hermes_home=str(home),
        pid=receipt['pid'], create_time=receipt['create_time'])]))
    with startup_hold.admission_gate(), startup_hold.writer_exclusion() as covered:
        startup_hold_writers.refuse_legacy_writers(covered=covered)


def test_changed_previous_state_cannot_restore_under_same_id(home):
    from gateway import drain_startup
    from hermes_state_runtime import RuntimeStoreError
    value = drain_startup.begin()
    marker = home / '.drain_request.json'
    body = json.loads(marker.read_bytes())
    body['startup_hold']['previous_drain'] = '{"action":"drain"}'
    marker.write_text(json.dumps(body))
    before = marker.read_bytes()
    with pytest.raises(RuntimeStoreError, match='startup_hold_unavailable'):
        drain_startup.restore(hold_id=value['hold_id'])
    assert marker.read_bytes() == before
