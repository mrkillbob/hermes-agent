"""Normal dispatch integrates explicit owner-configured peers; all transport is fake."""
from pathlib import Path

import pytest

from hermes_cli import config, kanban_db as kb, kanban_db_connect as kbc, kanban_db_dispatch as dispatch
from plugins.platforms.a2a import client
from tests.hermes_cli.fixtures.a2a_peer import FakePeer


@pytest.fixture
def configured(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    cfg = {'kanban': {'external_lanes': {'enabled': True, 'lanes': {
        'external-house': {'peer': 'house', 'executor_id': 'house-v1', 'authority_id': 'owner-v1'}}}},
        'a2a_agents': {'house': {'url': 'https://fixture.invalid/rpc', 'auth': {'type': 'none'}}}}
    monkeypatch.setattr(config, 'load_config_readonly', lambda: cfg)
    peer = FakePeer()
    original = client.JSONRPCClient
    monkeypatch.setattr(client, 'JSONRPCClient', lambda *a, **kw: original(*a, **kw, opener=peer.open))
    kb.init_db()
    with kbc.connect_closing() as conn:
        yield conn, cfg, peer


def task(conn, assignee='external-house'):
    return kb.create_task(conn, title='Synthetic arithmetic', body='Calculate two plus two', assignee=assignee)


def test_normal_dispatch_configured_send_then_return_without_resubmit(configured):
    conn, _, peer = configured
    tid = task(conn)
    assert dispatch.dispatch_once(conn, task_ids=[tid]).spawned[0][0] == tid
    assert kb.get_task(conn, tid).status == 'running'
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert kb.get_task(conn, tid).status == 'done'
    assert [v['method'] for v in peer.calls] == ['SendMessage', 'GetTask']
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert len(peer.calls) == 2


@pytest.mark.parametrize('setting', [None, {}, {'enabled': False}, {'enabled': False, 'lanes': 'ignored'}])
def test_disabled_default_and_dry_run_make_no_peer_calls(configured, setting):
    conn, cfg, peer = configured
    cfg['kanban']['external_lanes'] = setting
    tid = task(conn)
    assert dispatch.dispatch_once(conn, task_ids=[tid]).skipped_nonspawnable == [tid]
    assert kb.get_task(conn, tid).status == 'ready'
    assert not peer.calls


def test_dry_run_configured_lane_is_visible_but_inert(configured):
    conn, _, peer = configured
    tid = task(conn)
    assert dispatch.dispatch_once(conn, task_ids=[tid], dry_run=True).spawned[0][0] == tid
    assert kb.get_task(conn, tid).status == 'ready'
    assert not peer.calls
    assert not any(e.kind == 'external_intent' for e in kb.list_events(conn, tid))


def test_lost_ack_unknown_id_stays_fenced_and_never_replays(configured):
    conn, _, peer = configured
    peer.lost_reply = True
    tid = task(conn)
    dispatch.dispatch_once(conn, task_ids=[tid])
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert kb.get_task(conn, tid).status == 'running'
    assert [v['method'] for v in peer.calls] == ['SendMessage']


def test_unknown_lane_and_explicit_spawn_do_not_gain_peer_authority(configured):
    conn, _, peer = configured
    tid = task(conn, 'external-other')
    assert dispatch.dispatch_once(conn, task_ids=[tid]).skipped_nonspawnable == [tid]
    second = task(conn)
    assert dispatch.dispatch_once(conn, task_ids=[second], spawn_fn=lambda *a: None).skipped_nonspawnable == [second]
    assert not peer.calls


def test_disable_after_start_preserves_unresolved_run_without_polling(configured):
    conn, cfg, peer = configured
    tid = task(conn)
    dispatch.dispatch_once(conn, task_ids=[tid])
    cfg['kanban']['external_lanes']['enabled'] = False
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert len(peer.calls) == 1
    assert kb.get_task(conn, tid).status == 'running'


@pytest.mark.parametrize('field,value', [('enabled', 'true'), ('lanes', {'external-house': {'peer': 'missing'}})])
def test_malformed_enabled_config_fails_before_claim_or_network(configured, field, value):
    conn, cfg, peer = configured
    cfg['kanban']['external_lanes'][field] = value
    tid = task(conn)
    with pytest.raises(ValueError):
        dispatch.dispatch_once(conn, task_ids=[tid])
    assert kb.get_task(conn, tid).status == 'ready'
    assert not peer.calls


def test_peer_authority_change_cannot_poll_old_binding(configured):
    conn, cfg, peer = configured
    tid = task(conn)
    dispatch.dispatch_once(conn, task_ids=[tid])
    cfg['kanban']['external_lanes']['lanes']['external-house']['authority_id'] = 'different-owner'
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert len(peer.calls) == 1
    assert kb.get_task(conn, tid).status == 'running'


@pytest.mark.parametrize('surface', ['cli', 'gateway', 'dashboard'])
@pytest.mark.parametrize('enabled', [False, True])
def test_normal_surfaces_reach_configured_registry(configured, monkeypatch, surface, enabled):
    conn, cfg, peer = configured
    cfg['kanban']['external_lanes']['enabled'] = enabled
    monkeypatch.setattr(config, 'load_config', lambda: cfg)
    tid = task(conn)
    if surface == 'cli':
        import argparse
        from hermes_cli import kanban_ops, kanban_parser
        parser = argparse.ArgumentParser()
        kanban_parser.build_parser(parser.add_subparsers(dest='command'))
        args = parser.parse_args(['kanban', 'dispatch', '--task-id', tid, '--json'])
        assert kanban_ops._cmd_dispatch(args) == 0
    elif surface == 'gateway':
        from gateway.kanban_watchers_dispatcher import _KanbanDispatcher, _resolve_dispatcher_settings
        runner = _KanbanDispatcher(kb, _resolve_dispatcher_settings(cfg['kanban'], kb))
        assert runner.tick_once_for_board(None) is not None
    else:
        from plugins.kanban.dashboard import plugin_api
        assert isinstance(plugin_api.dispatch(dry_run=False, max_n=1, board=None), dict)
    assert kb.get_task(conn, tid).status == ('running' if enabled else 'ready')
    assert len(peer.calls) == int(enabled)


def test_poll_cooldown_and_exact_selection_preserve_other_runs(configured):
    conn, _, peer = configured
    peer.states = ['SUBMITTED', 'WORKING', 'COMPLETED']
    tid = task(conn)
    dispatch.dispatch_once(conn, task_ids=[tid])
    other = task(conn)
    dispatch.dispatch_once(conn, task_ids=[other], dry_run=True)
    assert len(peer.calls) == 1
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert len(peer.calls) == 2
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert len(peer.calls) == 2
    conn.execute("UPDATE task_events SET created_at=0 WHERE kind='external_a2a_poll'")
    conn.commit()
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert kb.get_task(conn, tid).status == 'done'
    assert len(peer.calls) == 3


@pytest.mark.parametrize('peer_update', [
    {'url': 'http://public.invalid/rpc'},
    {'url': 'https://fixture.invalid/rpc?token=private'},
    {'url': 'https://user:private@fixture.invalid/rpc'},
    {'url': 'http://127.0.0.1/rpc', 'auth': {'type': 'bearer', 'token': 'synthetic'}},
    {'auth': {'type': 'unknown', 'token': 'synthetic'}},
])
def test_unqualified_transport_config_refused_before_claim(configured, peer_update):
    conn, cfg, peer = configured
    cfg['a2a_agents']['house'].update(peer_update)
    tid = task(conn)
    with pytest.raises(ValueError):
        dispatch.dispatch_once(conn, task_ids=[tid])
    assert kb.get_task(conn, tid).status == 'ready'
    assert not peer.calls


def test_cached_terminal_applies_without_another_peer_call_after_crash(configured, monkeypatch):
    conn, _, peer = configured
    tid = task(conn)
    dispatch.dispatch_once(conn, task_ids=[tid])
    complete = kb.complete_task
    def crash(*args, **kwargs):
        raise OSError('synthetic crash after terminal persistence')
    monkeypatch.setattr(kb, 'complete_task', crash)
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert kb.get_task(conn, tid).status == 'running'
    assert len(peer.calls) == 2
    monkeypatch.setattr(kb, 'complete_task', complete)
    conn.execute("UPDATE task_events SET created_at=0 WHERE kind='external_a2a_poll'")
    conn.commit()
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert kb.get_task(conn, tid).status == 'done'
    assert len(peer.calls) == 2


def test_configured_registry_preserves_profile_dispatch_and_unassigned_cards(configured, monkeypatch):
    conn, _, peer = configured
    seen = []
    monkeypatch.setattr(dispatch, '_default_spawn', lambda t, workspace, board=None: seen.append(t.id))
    normal = task(conn, 'default')
    unassigned = kb.create_task(conn, title='Unassigned synthetic card')
    result = dispatch.dispatch_once(conn, task_ids=[normal, unassigned])
    assert [v[0] for v in result.spawned] == [normal]
    assert seen == [normal]
    assert kb.get_task(conn, unassigned).assignee is None
    assert not peer.calls


def test_refresh_is_limited_and_rotates_to_previously_unpolled_runs(configured):
    conn, _, peer = configured
    peer.states = ['WORKING']
    ids = [task(conn) for _ in range(6)]
    dispatch.dispatch_once(conn, task_ids=ids, max_spawn=8, max_in_progress=100)
    assert len(peer.calls) == 6
    dispatch.dispatch_once(conn, task_ids=ids, max_in_progress=100)
    assert len([v for v in peer.calls if v['method'] == 'GetTask']) == 4
    dispatch.dispatch_once(conn, task_ids=ids, max_in_progress=100)
    assert len([v for v in peer.calls if v['method'] == 'GetTask']) == 6


def test_changed_claim_does_not_query_peer(configured):
    conn, _, peer = configured
    tid = task(conn)
    dispatch.dispatch_once(conn, task_ids=[tid])
    conn.execute('UPDATE tasks SET claim_lock=? WHERE id=?', ('different-owner', tid))
    conn.commit()
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert len(peer.calls) == 1
    assert kb.get_task(conn, tid).status == 'running'


@pytest.mark.parametrize('peer_update', [
    {'url': 'https://fixture.invalid:bad/rpc'},
    {'url': 'https://fixture.invalid:0/rpc'},
    {'auth': []},
    {'auth': {'type': 'bearer', 'token': 'invalid token'}},
])
def test_malformed_peer_values_fail_before_claim(configured, peer_update):
    conn, cfg, peer = configured
    cfg['a2a_agents']['house'].update(peer_update)
    tid = task(conn)
    with pytest.raises(ValueError):
        dispatch.dispatch_once(conn, task_ids=[tid])
    assert kb.get_task(conn, tid).status == 'ready'
    assert not peer.calls


def test_existing_bearer_header_is_used_without_event_or_config_disclosure(configured, monkeypatch):
    import copy
    conn, cfg, peer = configured
    cfg['a2a_agents']['house']['auth'] = {'type': 'bearer', 'token': 'synthetic-secret-fixture'}
    before = copy.deepcopy(cfg)
    headers = []
    original = peer.open
    def capture(request, timeout):
        headers.append(request.get_header('Authorization'))
        return original(request, timeout)
    monkeypatch.setattr(peer, 'open', capture)
    tid = task(conn)
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert headers == ['Bearer synthetic-secret-fixture']
    assert 'synthetic-secret-fixture' not in str(kb.list_events(conn, tid))
    assert cfg == before


def test_root_policy_does_not_supply_missing_profile_peer_authority(configured, monkeypatch):
    import copy
    conn, cfg, peer = configured
    root = copy.deepcopy(cfg)
    cfg.clear()
    monkeypatch.setattr(config, 'read_user_config_raw', lambda *a, **kw: root)
    tid = task(conn)
    assert dispatch.dispatch_once(conn, task_ids=[tid]).skipped_nonspawnable == [tid]
    assert kb.get_task(conn, tid).status == 'ready'
    assert not peer.calls
