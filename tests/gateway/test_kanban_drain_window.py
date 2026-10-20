"""An external hold cannot admit unrelated work or disappear on expiry/restart."""
from datetime import datetime, timedelta, UTC
import json
from types import SimpleNamespace

import pytest

from hermes_state_runtime import RuntimeStoreError


def test_external_drain_refuses_durable_admission():
    from gateway.session_authority import SessionAuthority
    authority = object.__new__(SessionAuthority)
    authority.retiring = False
    authority.runner = SimpleNamespace(_draining=False, _running=True, _external_drain_active=True)
    with pytest.raises(RuntimeStoreError, match='runtime_draining'):
        authority._require_admission_open()


def test_external_drain_refuses_interactive_writes():
    from gateway.drain_control import ws_method_refused_while_draining
    runner = SimpleNamespace(_draining=False, _running=True, _external_drain_active=True)
    assert ws_method_refused_while_draining(runner, 'prompt.submit')


@pytest.mark.parametrize('reason', ['expired', 'restarted'])
def test_one_card_hold_remains_after_permit_expiry_or_restart(tmp_path, monkeypatch, reason):
    from gateway import drain_control as dc
    monkeypatch.setattr(dc, 'current_instantiation_epoch', lambda: 'new-boot')
    body = {'action': 'drain', 'epoch': 'old-boot' if reason == 'restarted' else 'new-boot',
            'requested_at': (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            'kanban_once': {'window_id': 'test-window'}}
    dc.drain_request_path(tmp_path).write_text(json.dumps(body))
    assert dc.drain_requested(home=tmp_path), 'expired/restarted one-shot permission must leave other work held'


def test_permission_is_one_attempt_owner_bound_and_hold_restores(tmp_path, monkeypatch):
    from gateway import drain_kanban as window
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    target = {'board': 'default', 'db': str(tmp_path / 'kanban.db'),
              'task_id': 't_one', 'profile_id': str(tmp_path)}
    prior = '{"action":"drain","principal":"prior"}\n'
    (tmp_path / '.drain_request.json').write_text(prior)
    (tmp_path / 'ESTOP').write_text('hold')
    state = window.begin(target, seconds=900)
    assert window.held()
    window.arm('owner-one', window_id=state['window_id'])
    budget = {'wall_seconds': 2, 'output_bytes': 1024, 'requests': 1}
    claim = dict(target, run_id=7, claim_lock='claim', execution_budget=budget)
    window.bind_claim(claim)
    assert window.permitted(claim, instance_id='owner-one')
    assert not window.permitted(claim, instance_id='owner-two')
    assert not window.permitted(dict(claim, run_id=8), instance_id='owner-one')
    with pytest.raises(RuntimeStoreError):
        window.bind_claim(dict(claim, run_id=8))
    window.restore(window_id=state['window_id'], owner_stopped=True)
    assert (tmp_path / '.drain_request.json').read_text() == prior
    assert (tmp_path / 'ESTOP').read_text() == 'hold'


def test_window_cannot_restore_while_unpaused_or_owner_alive(tmp_path, monkeypatch):
    from gateway import drain_kanban as window
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'ESTOP').write_text('hold')
    target = {'board': 'default', 'db': str(tmp_path / 'kanban.db'),
              'task_id': 't_one', 'profile_id': str(tmp_path)}
    state = window.begin(target, seconds=900)
    with pytest.raises(RuntimeStoreError):
        window.restore(window_id=state['window_id'], owner_stopped=False)
    (tmp_path / 'ESTOP').unlink()
    with pytest.raises(RuntimeStoreError):
        window.restore(window_id=state['window_id'], owner_stopped=True)
    assert window.held()


def test_only_bound_session_can_cross_hold_and_estop_still_wins(tmp_path, monkeypatch):
    from gateway import drain_kanban as window
    from gateway.session_authority import SessionAuthority
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'ESTOP').write_text('hold')
    target = dict(board='default', db=str(tmp_path / 'kanban.db'), task_id='t_one', profile_id=str(tmp_path))
    value = window.begin(target)
    runner = SimpleNamespace(_draining=False, _running=True, _external_drain_active=False)
    window.latch(runner)
    window.arm('owner', window_id=value['window_id'])
    claim = dict(target, run_id=1, claim_lock='claim', execution_budget={
        'wall_seconds': 2, 'output_bytes': 1024, 'requests': 1})
    window.bind_claim(claim)
    authority = object.__new__(SessionAuthority)
    authority.runner, authority.profile_id, authority.instance_id, authority.retiring = runner, str(tmp_path), 'owner', False
    from gateway.session_contract import Principal
    connection = SimpleNamespace(authority=authority, native_owner=True, actor=Principal('owner', str(tmp_path), frozenset({'session:create', 'session:control'}), 'transport'))
    with window.claim_scope(connection, claim, 'one-session'):
        with pytest.raises(RuntimeStoreError):
            authority._require_admission_open(session_id='one-session')
        (tmp_path / 'ESTOP').unlink()
        authority._require_admission_open(session_id='one-session')
        with pytest.raises(RuntimeStoreError):
            authority._require_admission_open(session_id='other-session')
    with pytest.raises(RuntimeStoreError):
        authority._require_admission_open(session_id='one-session')


@pytest.mark.asyncio
async def test_held_owner_start_does_not_recover_or_connect(tmp_path, monkeypatch):
    from gateway import drain_kanban as window
    from gateway.run import GatewayRunner
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'ESTOP').write_text('hold')
    window.begin(dict(board='default', db=str(tmp_path / 'kanban.db'), task_id='t_one', profile_id=str(tmp_path)))
    runner = object.__new__(GatewayRunner)
    window.latch(runner)
    runner._start_install_faulthandler = lambda: None
    runner._start_check_access_policy = lambda: False
    async def no_op():
        return False
    async def forbidden():
        pytest.fail('held startup recovered prior work')
    runner._start_log_startup_environment = no_op
    runner._abort_startup_if_shutdown_requested = no_op
    runner._start_recover_previous_run = forbidden
    runner._spawn_supervised = lambda *a, **k: None
    assert await runner._start_impl()
    assert runner._running and runner._external_drain_active


def test_generic_drain_cancel_cannot_remove_one_card_hold(tmp_path, monkeypatch):
    from gateway import drain_kanban as window, drain_control as dc
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'ESTOP').write_text('hold')
    window.begin(dict(board='default', db=str(tmp_path / 'kanban.db'), task_id='t_one', profile_id=str(tmp_path)))
    assert not dc.clear_drain_request(home=tmp_path)
    assert window.held()


@pytest.mark.parametrize('bad', [None, 'bad', {'window_id': 'bad'}, {'window_id': 'bad', 'expires_at': float('nan')}])
def test_malformed_window_latches_hold_but_never_grants(tmp_path, monkeypatch, bad):
    from gateway import drain_kanban as window
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / '.drain_request.json').write_text(json.dumps({'action': 'drain', 'kanban_once': bad}))
    runner = SimpleNamespace()
    window.latch(runner)
    assert window.held(runner)
    assert not window.permitted({}, instance_id='owner')
    with pytest.raises(RuntimeStoreError):
        window.arm('owner', window_id='bad')


def test_manual_cron_claim_does_not_change_schedules_under_hold(tmp_path, monkeypatch):
    from gateway import drain_kanban as window
    from cron.jobs import create_job, claim_job_for_fire
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    job = create_job(prompt='untouched', schedule='every 5m')
    path = tmp_path / 'cron' / 'jobs.json'
    original = path.read_bytes()
    (tmp_path / 'ESTOP').write_text('hold')
    window.begin(dict(board='default', db=str(tmp_path / 'kanban.db'), task_id='t_one', profile_id=str(tmp_path)))
    for options in ({}, {'manual': True}, {'force': True}):
        assert claim_job_for_fire(job['id'], **options) is False
        assert path.read_bytes() == original


def test_cancel_revokes_claim_but_preserves_hold(tmp_path, monkeypatch):
    from gateway import drain_kanban as window
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'ESTOP').write_text('hold')
    target = dict(board='default', db=str(tmp_path / 'kanban.db'), task_id='t_one', profile_id=str(tmp_path))
    value = window.begin(target)
    window.arm('owner', window_id=value['window_id'])
    claim = dict(target, run_id=1, claim_lock='c', execution_budget={'wall_seconds': 2, 'output_bytes': 1024, 'requests': 1})
    window.bind_claim(claim)
    assert window.permitted(claim, instance_id='owner')
    window.cancel(window_id=value['window_id'])
    assert window.held() and not window.permitted(claim, instance_id='owner')
    with pytest.raises(RuntimeStoreError):
        window.arm('other', window_id=value['window_id'])


@pytest.mark.parametrize('mode', ['budget', 'cancel', 'expiry'])
def test_real_owner_window_holds_unrelated_work(tmp_path, mode):
    import os
    from pathlib import Path
    import subprocess
    import sys
    from tests.gateway.fixtures.local_recovery_probe import child_env
    repo = Path(__file__).resolve().parents[2]
    env = child_env()
    env.update(HOME=str(tmp_path / 'user'), USERPROFILE=str(tmp_path / 'user'), HERMES_HOME=str(tmp_path / 'state'),
               PYTHONPATH=str(repo), WINDOW_PROBE_MODE=mode)
    result = subprocess.run([sys.executable, str(repo / 'tests/gateway/fixtures/kanban_window_probe.py')],
                            cwd=repo, env=env, capture_output=True, text=True, timeout=150, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads((tmp_path / 'state/receipt.json').read_text())
    assert receipt['physical_requests'] == 1
    assert receipt['cron_unchanged'] and receipt['restored'] and receipt['restart_fenced']


@pytest.mark.asyncio
async def test_direct_no_agent_cron_cannot_execute_during_hold(tmp_path, monkeypatch):
    from gateway import drain_kanban as window
    from cron.jobs import create_job
    from cron.scheduler import run_job
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    script = tmp_path / 'scripts' / 'must_not_run.py'
    script.parent.mkdir()
    script.write_text('from pathlib import Path\nPath(' + repr(str(tmp_path / 'effect')) + ').touch()\n')
    job = create_job(prompt='', schedule='every 5m', script=str(script), no_agent=True)
    (tmp_path / 'ESTOP').write_text('hold')
    window.begin(dict(board='default', db=str(tmp_path / 'kanban.db'), task_id='t_one', profile_id=str(tmp_path)))
    outcome = run_job(job)
    assert not outcome[0] and not (tmp_path / 'effect').exists()


def test_restore_refuses_owner_lock_even_with_caller_assertion(tmp_path, monkeypatch):
    from gateway import drain_kanban as window
    from gateway.runtime_ownership import ProfileOwnership
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'ESTOP').write_text('hold')
    value = window.begin(dict(board='default', db=str(tmp_path / 'kanban.db'), task_id='t_one', profile_id=str(tmp_path)))
    owner = ProfileOwnership()
    owner.reserve([tmp_path])
    try:
        with pytest.raises(RuntimeError):
            window.restore(window_id=value['window_id'], owner_stopped=True)
        assert window.held()
    finally:
        owner.close()


def test_generic_marker_writers_share_window_transaction_lock(tmp_path, monkeypatch):
    from gateway import drain_kanban as window, drain_control as dc
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    dc.write_drain_request()
    before = dc.drain_request_path().read_bytes()
    with window._marker_lock():
        assert not dc.clear_drain_request()
        with pytest.raises(RuntimeError):
            dc.write_drain_request(principal='concurrent')
        assert dc.drain_request_path().read_bytes() == before


@pytest.mark.parametrize('contents', ['{broken', 'null', '[]'])
def test_generic_drain_cannot_remove_or_replace_corrupt_hold(tmp_path, monkeypatch, contents):
    from gateway import drain_kanban as window, drain_control as dc
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    dc.drain_request_path().write_text(contents)
    assert window.held()
    assert not dc.clear_drain_request()
    with pytest.raises(RuntimeError):
        dc.write_drain_request()
    assert dc.drain_request_path().read_text() == contents


@pytest.mark.asyncio
async def test_held_authority_defers_unrelated_recovery(tmp_path, monkeypatch):
    from gateway import drain_kanban as window
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    import hermes_state_runtime as rt
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store._db
    db.create_session('unrelated', source='test')
    epoch = rt.begin_runtime_epoch(db, instance_id='old')
    rt.admit_session_input(db, epoch=epoch, principal_id='automation:old', session_id='unrelated', request_id='old', payload={'text': 'old'})
    rt.claim_session_input(db, epoch=epoch, session_id='unrelated')
    before = tuple(db._conn.execute('SELECT * FROM session_admissions').fetchone())
    (tmp_path / 'ESTOP').write_text('hold')
    window.begin(dict(board='default', db=str(tmp_path / 'kanban.db'), task_id='t_one', profile_id=str(tmp_path)))
    runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False)
    window.latch(runner)
    try:
        authority = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='held')
        assert tuple(db._conn.execute('SELECT * FROM session_admissions').fetchone()) == before
        assert 'unrelated' not in authority.sessions
    finally:
        db.close()


def test_refused_retarget_cannot_replace_original_cancellation_scope(tmp_path, monkeypatch):
    from gateway import drain_kanban as window
    from gateway.session_contract import Principal
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'ESTOP').write_text('hold')
    target = dict(board='default', db=str(tmp_path / 'kanban.db'), task_id='t_one', profile_id=str(tmp_path))
    value = window.begin(target)
    window.arm('owner', window_id=value['window_id'])
    claim = dict(target, run_id=1, claim_lock='c', execution_budget={'wall_seconds': 2, 'output_bytes': 1024, 'requests': 1})
    window.bind_claim(claim)
    runner = SimpleNamespace()
    window.latch(runner)
    authority = SimpleNamespace(runner=runner, profile_id=str(tmp_path), instance_id='owner')
    connection = SimpleNamespace(authority=authority, native_owner=True, actor=Principal('original', str(tmp_path), frozenset(), 't'))
    with window.claim_scope(connection, claim, 'original-session'):
        pass
    first = runner._kanban_drain_control
    connection.actor = Principal('other', str(tmp_path), frozenset(), 'u')
    with pytest.raises(RuntimeStoreError):
        with window.claim_scope(connection, claim, 'other-session'):
            pass
    assert runner._kanban_drain_control == first


def test_held_boot_preserves_unknown_turn_marker(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig, Platform
    from gateway.session import SessionStore, SessionSource
    from gateway.run_runtime import release_unknown_turn_markers
    import hermes_state_runtime as rt
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    source = SessionSource(platform=Platform.DISCORD, user_id='human', chat_id='legacy', chat_type='dm')
    entry = store.get_or_create_session(source)
    token = store.mark_turn_active(entry.session_key)
    db = store._db
    try:
        epoch = rt.begin_runtime_epoch(db, instance_id='before')
        rt.admit_session_input(db, epoch=epoch, principal_id='human', session_id=entry.session_id,
                               request_id='old', payload={'text': 'old'})
        rt.claim_session_input(db, epoch=epoch, session_id=entry.session_id)
        epoch = rt.begin_runtime_epoch(db, instance_id='replacement')
        rt.recover_session_inputs(db, epoch=epoch)
        from gateway.session_authority import SessionAuthority
        runner = SimpleNamespace(_kanban_drain=True, session_store=store, _draining=False)
        runner.session_authority = SessionAuthority(runner, profile_id=str(tmp_path), db=db, epoch=epoch, instance_id='held')
        release_unknown_turn_markers(runner)
        assert store.list_sessions()[0].active_turn_token == token
    finally:
        db.close()


@pytest.mark.asyncio
async def test_held_boot_defers_mcp_discovery(monkeypatch):
    from gateway import run, run_bootstrap
    async def forbidden(config):
        pytest.fail('held owner tried to discover MCP servers before its selected claim')
    async def started():
        return True
    runner = SimpleNamespace(_kanban_drain=True, config=object(), start=started, should_exit_cleanly=False, _running=True)
    monkeypatch.setattr(run, '_discover_gateway_mcp_tools', forbidden)
    assert await run_bootstrap._start_gateway_run_runner(runner, [False]) is None
