"""Configured event returns exercise real journal/lifecycle with fake SSE, no peers."""
import io
import json
from pathlib import Path

import pytest

from hermes_cli import config, kanban_db as kb, kanban_db_connect as kbc, kanban_db_dispatch as dispatch
from hermes_cli.kanban_external import Delivery
from hermes_cli.kanban_external_config import configured_lanes
from hermes_state_a2a import digest
from plugins.platforms.a2a import protocol, client_stream
from plugins.platforms.a2a.event_return import wire_task
from tests.hermes_cli.fixtures.a2a_peer import Response



@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    cfg = {'kanban': {'external_lanes': {'enabled': True, 'lanes': {
        'external-house': {'peer': 'house', 'executor_id': 'house-v1', 'authority_id': 'owner-v1'}}}},
        'a2a_agents': {'house': {'url': 'https://fixture.invalid/rpc', 'auth': {'type': 'none'}}}}
    monkeypatch.setattr(config, 'load_config_readonly', lambda: cfg)
    kb.init_db()
    with kbc.connect_closing() as conn:
        yield conn, cfg, None


def task(conn):
    return kb.create_task(conn, title='Synthetic arithmetic', body='Calculate two plus two', assignee='external-house')


class SSE(io.BytesIO):
    headers = {'Content-Type': 'text/event-stream'}
    def geturl(self):
        return 'https://fixture.invalid/rpc'


class Peer:
    def __init__(self, state):
        self.state, self.calls, self.saved = state, [], None
        self.drop_result = self.drop_ack = self.drop_task_ack = False
        self.change = lambda value: value
    def open(self, request, timeout):
        rpc = json.loads(request.data)
        self.calls.append(rpc['method'])
        assert rpc['params'].get('tenant') == 'team'
        if rpc['method'] == 'SendMessage':
            data = rpc['params']['message']['parts'][0]['data']
            if self.drop_ack:
                raise TimeoutError('lost synthetic acknowledgment')
            return Response({'jsonrpc': '2.0', 'id': rpc['id'], 'result': {'message': {'parts': [
                {'data': {'acknowledged': data['event_id'], 'sha256': data['sha256']}}]}}}, request.full_url)
        if rpc['method'] == 'GetTask':
            assert 'hermes.a2a.lookup.v1' in rpc['params']['metadata']
            return Response({'jsonrpc': '2.0', 'id': rpc['id'], 'result': wire_task(self.saved)}, request.full_url)
        if rpc['method'] == 'SendStreamingMessage':
            corr = json.loads(rpc['params']['message']['parts'][0]['text'])['correlation']
            kind = {'COMPLETED': 'completed', 'FAILED': 'error', 'INPUT_REQUIRED': 'question'}[self.state]
            receipt = {'schema': 'hermes.a2a.receipt.v1', 'correlation': corr, 'kind': kind,
                       'summary': 'Synthetic result', 'findings': [], 'questions': ['Which input?'] if kind == 'question' else []}
            result = protocol.build_task('remote', 'context', 'TASK_STATE_' + self.state)
            if kind == 'question':
                result['status']['message'] = protocol.text_message(protocol.ROLE_AGENT, json.dumps(receipt))
            else:
                result['artifacts'] = [{'artifactId': 'stable', 'parts': [protocol.text_part(json.dumps(receipt))]}]
            self.saved = {'task': result, 'event': {'id': 'event-' + digest(result), 'sha256': digest(result), 'task': result}}
        else:
            assert rpc['method'] == 'SubscribeToTask'
        initial = {'task': protocol.build_task('remote', 'context', protocol.STATE_SUBMITTED), 'event': None}
        frames = [initial] if self.drop_result and rpc['method'] == 'SendStreamingMessage' else [initial, self.saved]
        if self.drop_task_ack and rpc['method'] == 'SendStreamingMessage':
            raise TimeoutError('lost initial task ACK')
        return SSE(''.join(protocol.sse_data(protocol.stream_task(self.change(wire_task(v))), rpc['id']) for v in frames).encode())


def enable(configured, monkeypatch, state='COMPLETED'):
    conn, cfg, _ = configured
    cfg['kanban']['external_lanes']['lanes']['external-house']['return_mode'] = 'events'
    cfg['a2a_agents']['house']['tenant'] = 'team'
    peer = Peer(state)
    original = client_stream.StreamClient
    monkeypatch.setattr(client_stream, 'StreamClient', lambda *a, **kw: original(*a, **kw, opener=peer.open))
    return conn, peer


@pytest.mark.parametrize('state,expected', [('COMPLETED', 'done'), ('FAILED', 'blocked'), ('INPUT_REQUIRED', 'blocked')])
def test_configured_event_return_and_no_periodic_peer_requests(configured, monkeypatch, state, expected):
    conn, peer = enable(configured, monkeypatch, state)
    tid = task(conn)
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert kb.get_task(conn, tid).status == expected
    assert peer.calls == ['SendStreamingMessage', 'SendMessage']
    for _ in range(3):
        dispatch.dispatch_once(conn, task_ids=[tid])
    assert peer.calls == ['SendStreamingMessage', 'SendMessage']
    assert any(e.kind == 'external_a2a_delivery_ack' for e in kb.list_events(conn, tid))


def delivery(conn, tid):
    return Delivery(**next(e.payload for e in kb.list_events(conn, tid) if e.kind == 'external_intent'))


def test_disconnect_has_one_explicit_reattach_without_resubmission(configured, monkeypatch):
    conn, peer = enable(configured, monkeypatch)
    peer.drop_result = True
    tid = task(conn)
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert kb.get_task(conn, tid).status == 'running'
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert peer.calls == ['SendStreamingMessage']
    ex = configured_lanes(conn)._lanes['external-house'].executor
    d = delivery(conn, tid)
    assert ex.recover_events_once(d).kind == 'completed'
    assert ex.recover_events_once(d).kind == 'completed'
    assert peer.calls == ['SendStreamingMessage', 'SubscribeToTask', 'SendMessage']
    assert configured_lanes(conn).reconcile(conn, d)
    assert kb.get_task(conn, tid).status == 'done'


def test_lost_delivery_ack_retains_terminal_and_recovers_without_execution(configured, monkeypatch):
    conn, peer = enable(configured, monkeypatch)
    peer.drop_ack = True
    tid = task(conn)
    dispatch.dispatch_once(conn, task_ids=[tid])
    # The locally durable result remains authoritative despite the lost peer ACK.
    assert kb.get_task(conn, tid).status == 'done'
    peer.drop_ack = False
    ex = configured_lanes(conn)._lanes['external-house'].executor
    d = delivery(conn, tid)
    ex.recover_events_once(d)
    assert peer.calls == ['SendStreamingMessage', 'SendMessage', 'SendMessage']


def test_lost_initial_ack_uses_one_bound_lookup_without_new_send(configured, monkeypatch):
    conn, peer = enable(configured, monkeypatch)
    peer.drop_task_ack = True
    tid = task(conn)
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert kb.get_task(conn, tid).status == 'running'
    ex = configured_lanes(conn)._lanes['external-house'].executor
    d = delivery(conn, tid)
    assert ex.recover_events_once(d).kind == 'completed'
    assert configured_lanes(conn).reconcile(conn, d)
    assert ex.recover_events_once(d).kind == 'completed'
    assert peer.calls == ['SendStreamingMessage', 'GetTask', 'SendMessage']


@pytest.mark.parametrize('fault', ['negotiation', 'context', 'event_hash', 'result_binding'])
def test_unbound_event_is_held_without_ack_or_resubmission(configured, monkeypatch, fault):
    conn, peer = enable(configured, monkeypatch)
    def change(value):
        if fault == 'negotiation':
            value['metadata'] = {}
        elif 'hermes.delivery.v1' in value['metadata']:
            changes = {
                'context': lambda: value.update(contextId='foreign'),
                'event_hash': lambda: value['metadata']['hermes.delivery.v1'].update(sha256='wrong'),
                'result_binding': lambda: value['artifacts'][0]['parts'][0].update(text='unbound prose'),
            }
            changes[fault]()
        return value
    peer.change = change
    tid = task(conn)
    dispatch.dispatch_once(conn, task_ids=[tid])
    dispatch.dispatch_once(conn, task_ids=[tid])
    assert kb.get_task(conn, tid).status == 'running'
    assert peer.calls == ['SendStreamingMessage']
