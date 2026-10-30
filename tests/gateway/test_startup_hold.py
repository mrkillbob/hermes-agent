"""Targetless startup holds carry no dispatch permission and restore only quiescent state."""
from pathlib import Path
import os
import importlib.util
from types import SimpleNamespace

import pytest

from hermes_state_runtime import RuntimeStoreError


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    if importlib.util.find_spec('gateway.drain_startup') is not None:
        from gateway import drain_startup
        monkeypatch.setattr(drain_startup, '_refuse_legacy_writers', lambda *args: None)
    monkeypatch.setattr(drain_startup, '_checkout_root', lambda: Path(os.environ['HERMES_HOME']))
    (tmp_path / 'ESTOP').write_text('retained pause')
    return tmp_path


def test_empty_home_hold_restores_exact_previous_marker(home):
    from gateway import drain_startup, drain_control, drain_kanban
    marker = home / '.drain_request.json'
    prior = '{ "action": "drain", "principal": "operator" }\n'
    marker.write_text(prior)
    value = drain_startup.begin()
    assert set(value) == {'hold_id', 'previous_drain'}
    assert value['previous_drain'] == prior
    assert drain_startup.status() == {'held': True, 'mode': 'startup_hold', 'hold_id': value['hold_id'], 'armed': False}
    assert drain_kanban.held()
    assert not drain_control.clear_drain_request(home=home)
    with pytest.raises(RuntimeError):
        drain_control.write_drain_request(home=home)
    assert not (home / 'kanban.db').exists()
    drain_startup.restore(hold_id=value['hold_id'])
    assert marker.read_text() == prior
    assert (home / 'ESTOP').read_text() == 'retained pause'


def test_hold_requires_pause_and_restores_absence(home):
    from gateway import drain_startup
    pause = home / 'ESTOP'
    pause.unlink()
    with pytest.raises(RuntimeStoreError, match='startup_hold_requires_pause'):
        drain_startup.begin()
    pause.write_text('retained pause')
    value = drain_startup.begin()
    pause.unlink()
    with pytest.raises(RuntimeStoreError, match='startup_hold_requires_pause'):
        drain_startup.restore(hold_id=value['hold_id'])
    assert (home / '.drain_request.json').exists()
    pause.write_text('retained pause')
    drain_startup.restore(hold_id=value['hold_id'])
    assert not (home / '.drain_request.json').exists()


@pytest.mark.parametrize('kind', ['startup', 'one-card'])
def test_protected_holds_cannot_nest_or_change_kind(home, kind):
    from gateway import drain_startup, drain_kanban
    target = dict(board='default', db=str(home / 'kanban.db'), task_id='t_fixture', profile_id=str(home))
    if kind == 'startup':
        drain_startup.begin()
    else:
        drain_kanban.begin(target)
    before = (home / '.drain_request.json').read_bytes()
    with pytest.raises(RuntimeStoreError):
        drain_startup.begin()
    with pytest.raises(RuntimeStoreError):
        drain_kanban.begin(target)
    assert (home / '.drain_request.json').read_bytes() == before


def test_startup_hold_cannot_arm_claim_or_admit_methods(home):
    from gateway import drain_startup, drain_kanban
    value = drain_startup.begin()
    with pytest.raises(RuntimeStoreError):
        drain_kanban.arm('owner', window_id=value['hold_id'])
    claim = dict(board='default', db=str(home / 'kanban.db'), task_id='t_fixture', profile_id=str(home),
                 run_id=1, claim_lock='fixture', execution_budget={'wall_seconds': 2, 'output_bytes': 1024, 'requests': 1})
    with pytest.raises(RuntimeStoreError):
        drain_kanban.bind_claim(claim)
    assert not drain_kanban.permitted(claim, instance_id='owner')
    assert not drain_kanban.dispatch_allowed(None, board='default', task_ids=('t_fixture',), budget=claim['execution_budget'], max_spawn=1)
    runner = SimpleNamespace()
    drain_kanban.latch(runner)
    connection = SimpleNamespace(authority=SimpleNamespace(runner=runner), native_owner=True)
    for method in ['kanban.run', 'prompt.submit', 'worker.adopt', 'worker.persist', 'session.interrupt']:
        assert not drain_kanban.method_allowed(connection, method, {}), method
    for mode in ('safe_mode', 'ignore_user_config'):
        assert not drain_kanban.method_allowed(connection, 'session.create', {mode: True})
    for method in ['ping', 'runtime.describe']:
        assert drain_kanban.method_allowed(connection, method, {})


@pytest.mark.parametrize('replacement', [None, '{}', '{broken', '{"action":"drain"}'])
def test_booted_hold_survives_marker_loss_or_replacement(home, replacement):
    from gateway import drain_startup, drain_kanban
    from hermes_cli import startup_hold
    value = drain_startup.begin()
    runner = SimpleNamespace()
    drain_kanban.latch(runner)
    assert startup_hold.enter_boot(home / 'source', ['gateway', 'run'])
    marker = home / '.drain_request.json'
    if replacement is None:
        marker.unlink()
    else:
        marker.write_text(replacement)
    (home / 'ESTOP').unlink()
    assert drain_kanban.held(runner)
    assert startup_hold.active()
    with pytest.raises(RuntimeStoreError):
        drain_startup.restore(hold_id=value['hold_id'])


def test_hold_does_not_expire_and_stale_restore_does_not_write(home, monkeypatch):
    from gateway import drain_startup, drain_control
    from datetime import datetime, UTC
    monkeypatch.setattr(drain_startup, 'datetime', SimpleNamespace(now=lambda *_: datetime(2000, 1, 1, tzinfo=UTC)))
    value = drain_startup.begin()
    marker = home / '.drain_request.json'
    monkeypatch.setattr(drain_control, 'current_instantiation_epoch', lambda: 'new-boot')
    assert drain_control.drain_requested(home=home)
    before = marker.read_bytes()
    with pytest.raises(RuntimeStoreError, match='startup_hold_unavailable'):
        drain_startup.restore(hold_id='different')
    assert marker.read_bytes() == before
    drain_startup.restore(hold_id=value['hold_id'])


def test_native_owner_reservation_prevents_create_and_restore(home):
    from gateway import drain_startup
    from gateway.runtime_ownership import ProfileOwnership, OwnershipConflict
    owner = ProfileOwnership()
    owner.reserve([home])
    try:
        with pytest.raises(OwnershipConflict):
            drain_startup.begin()
        assert not (home / '.drain_request.json').exists()
    finally:
        owner.close()
    value = drain_startup.begin()
    owner.reserve([home])
    try:
        with pytest.raises(OwnershipConflict):
            drain_startup.restore(hold_id=value['hold_id'])
    finally:
        owner.close()


def test_standalone_writer_reference_prevents_create_and_restore(home, monkeypatch):
    from gateway import drain_startup
    from hermes_cli import startup_hold
    with startup_hold.writer_scope():
        with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
            drain_startup.begin()
    with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
        drain_startup.begin()  # even a closed clean reference cannot retire this live process
    next_home = home / 'next-install'
    next_home.mkdir()
    (next_home / 'ESTOP').touch()
    monkeypatch.setenv('HERMES_HOME', str(next_home))
    value = drain_startup.begin()
    with pytest.raises(RuntimeError):
        with startup_hold.writer_scope():
            pytest.fail('held standalone writer admitted')
    drain_startup.restore(hold_id=value['hold_id'])
