"""A2A task correlation and restart evidence use fake peers, never sockets/models."""
from dataclasses import replace
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_external import ExternalLanes, Lane, reclaim_paused
from tests.hermes_cli.fixtures.a2a_peer import FakePeer


def api():
    from hermes_cli import kanban_a2a_executor as adapter
    from plugins.platforms.a2a.client import JSONRPCClient
    return adapter, JSONRPCClient


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    kb.init_db()
    with kbc.connect_closing() as conn:
        yield conn, tmp_path


def prepared(context, peer=None, body="Synthetic arithmetic", title="fixture: A2A task"):
    conn, root = context
    adapter, client = api()
    peer = peer or FakePeer()
    plan = adapter.PeerPlan('fixture-peer', 'https://fixture.invalid/a2a', tenant='fixture')
    db = Path(conn.execute('PRAGMA database_list').fetchone()['file']).resolve()
    ex = adapter.A2AExecutor(db, plan, client(plan.url, version=plan.version, opener=peer.open))
    stub = SimpleNamespace(submit=lambda d: None, lookup=lambda d: None)
    lanes = ExternalLanes({'external-a2a': Lane(plan.executor_id, stub)})
    tid = kb.create_task(conn, title=title, body=body, assignee='external-a2a')
    task = kb.claim_task(conn, tid)
    delivery = lanes.deliver(conn, task, str(root / tid))
    return delivery, ex, peer, ExternalLanes({'external-a2a': Lane(plan.executor_id, ex)})


def test_remote_ack_result_and_local_lookup_are_distinct(context):
    conn, _ = context
    d, ex, peer, lanes = prepared(context)
    assert ex.submit(d).kind == 'ack'
    assert kb.get_task(conn, d.task_id).status == 'running'
    assert ex.submit(d).kind == 'ack'
    assert len(peer.calls) == 1
    before = conn.execute('SELECT COUNT(*) FROM task_events').fetchone()[0]
    assert ex.lookup(d).kind == 'ack'
    assert conn.execute('SELECT COUNT(*) FROM task_events').fetchone()[0] == before
    assert len(peer.calls) == 1
    assert ex.refresh_once(d).kind == 'completed'
    assert kb.get_task(conn, d.task_id).status == 'running'
    assert lanes.reconcile(conn, d)
    assert kb.get_task(conn, d.task_id).status == 'done'
    assert len(peer.calls) == 2
    assert ex.refresh_once(d).kind == 'completed'
    assert len(peer.calls) == 2


def test_lost_ack_without_remote_id_never_resends(context):
    conn, _ = context
    d, ex, peer, _ = prepared(context)
    peer.lost_reply = True
    assert ex.submit(d).kind == 'accepted'
    assert ex.submit(d).kind == 'accepted'
    assert ex.refresh_once(d) is None
    assert len(peer.calls) == 1
    assert reclaim_paused(conn, d.task_id)


def test_crash_after_intent_before_send_is_not_replayed(context, monkeypatch):
    d, ex, peer, _ = prepared(context)
    def interrupted(*args, **kwargs):
        raise SystemExit('fake crash')
    monkeypatch.setattr(ex.client, 'rpc', interrupted)
    with pytest.raises(SystemExit):
        ex.submit(d)
    assert ex.submit(d).kind == 'accepted'
    assert ex.refresh_once(d) is None
    assert not peer.calls


def test_concurrent_submit_has_one_network_winner(context):
    d, ex, peer, _ = prepared(context)
    peer.release = threading.Event()
    outcomes, errors = [], []
    def send():
        try:
            outcomes.append(ex.submit(d))
        except Exception as error:
            errors.append(error)
    first = threading.Thread(target=send)
    first.start()
    assert peer.entered.wait(timeout=5)
    second = threading.Thread(target=send)
    second.start()
    second.join(timeout=5)
    peer.release.set()
    first.join(timeout=5)
    assert not first.is_alive() and not second.is_alive() and not errors
    assert sorted(x.kind for x in outcomes) == ['accepted', 'ack']
    assert len(peer.calls) == 1


def test_restart_uses_saved_peer_task_and_memory_loss_holds(context):
    conn, _ = context
    d, ex, peer, _ = prepared(context)
    ex.submit(d)
    adapter, client = api()
    restarted = adapter.A2AExecutor(Path(d.database), ex.plan, client(ex.plan.url, opener=peer.open))
    peer.forgotten = True
    assert restarted.refresh_once(d) is None
    assert restarted.lookup(d) is None
    assert reclaim_paused(conn, d.task_id)
    assert [c['method'] for c in peer.calls] == ['SendMessage', 'GetTask']


@pytest.mark.parametrize('state', ['INPUT_REQUIRED', 'AUTH_REQUIRED', 'CANCELED', 'REJECTED', 'UNSPECIFIED', 'ALIEN'])
def test_unmapped_or_interrupted_states_preserve_typed_hold(context, state):
    conn, _ = context
    d, ex, peer, lanes = prepared(context, FakePeer(['SUBMITTED', state]))
    ex.submit(d)
    assert ex.refresh_once(d) is None
    assert ex.lookup(d) is None
    status = ex.status(d)
    assert status.state == 'TASK_STATE_' + state
    if state == 'INPUT_REQUIRED':
        assert status.questions == ('Which synthetic input?',)
    assert not lanes.reconcile(conn, d)
    assert kb.get_task(conn, d.task_id).status == 'running'
    assert reclaim_paused(conn, d.task_id)
    assert len(peer.calls) == 2


def test_bound_error_keeps_typed_receipt_and_blocks(context):
    conn, _ = context
    d, ex, _peer, lanes = prepared(context, FakePeer(['FAILED']))
    assert ex.submit(d).kind == 'error'
    assert lanes.reconcile(conn, d)
    assert kb.get_task(conn, d.task_id).status == 'blocked'
    assert ex.lookup(d).kind == 'error'


@pytest.mark.parametrize('fault', ['task', 'context', 'key', 'digest', 'no_artifact', 'message_only', 'duplicate_receipt'])
def test_foreign_or_missing_result_cannot_complete(context, fault):
    conn, _ = context
    d, ex, peer, lanes = prepared(context)
    ex.submit(d)
    def changed(task):
        if fault in {'task', 'context'}:
            task['id' if fault == 'task' else 'contextId'] = 'foreign'
        elif fault in {'key', 'digest'}:
            receipt = json.loads(task['artifacts'][0]['parts'][0]['text'])
            receipt['correlation']['key' if fault == 'key' else 'payload_sha256'] = 'foreign'
            task['artifacts'][0]['parts'][0]['text'] = json.dumps(receipt)
        elif fault == 'duplicate_receipt':
            text = task['artifacts'][0]['parts'][0]['text']
            task['artifacts'][0]['parts'][0]['text'] = text[:-1] + ', \"summary\": \"duplicate\"}'
        else:
            task.pop('artifacts', None)
            if fault == 'message_only':
                return {'role': 'ROLE_AGENT', 'parts': [{'text': '4'}]}
        return task
    peer.change = changed
    assert ex.refresh_once(d) is None
    assert ex.lookup(d) is None
    assert not lanes.reconcile(conn, d)
    assert kb.get_task(conn, d.task_id).status == 'running'


def test_saved_terminal_survives_lifecycle_crash_without_peer(context, monkeypatch):
    conn, _ = context
    d, ex, peer, lanes = prepared(context, FakePeer(['COMPLETED']))
    ex.submit(d)
    original = kb.complete_task
    def interrupted(*args, **kwargs):
        raise RuntimeError('fake interruption after receipt commit')
    monkeypatch.setattr(kb, 'complete_task', interrupted)
    with pytest.raises(RuntimeError):
        lanes.reconcile(conn, d)
    monkeypatch.setattr(kb, 'complete_task', original)
    assert lanes.reconcile(conn, d)
    assert kb.get_task(conn, d.task_id).status == 'done'
    assert len(peer.calls) == 1


def test_request_privacy_and_changed_peer_rejection(context):
    _conn, _ = context
    d, ex, peer, _ = prepared(context)
    ex.submit(d)
    body = json.dumps(peer.calls)
    assert all(value not in body for value in [d.claim, d.database, d.workspace])
    assert peer.calls[0]['params']['message']['messageId'] == d.key
    assert 'taskId' not in peer.calls[0]['params']['message']
    adapter, client = api()
    plan = replace(ex.plan, tenant='different')
    other = adapter.A2AExecutor(Path(d.database), plan, client(plan.url, opener=peer.open))
    with pytest.raises(ValueError):
        other.submit(d)
    assert len(peer.calls) == 1


def test_late_working_reply_cannot_replace_terminal_receipt(context, monkeypatch):
    d, ex, _peer, _ = prepared(context, FakePeer(['SUBMITTED', 'WORKING', 'COMPLETED']))
    ex.submit(d)
    original = ex.client.rpc
    ready, release = threading.Event(), threading.Event()
    first_reply, failures = [], []
    def delayed(method, params, request_id, **kwargs):
        response = original(method, params, request_id, **kwargs)
        if response['result']['status']['state'] == 'TASK_STATE_WORKING':
            ready.set()
            assert release.wait(timeout=5)
        return response
    monkeypatch.setattr(ex.client, 'rpc', delayed)
    def refresh():
        try:
            first_reply.append(ex.refresh_once(d))
        except Exception as error:
            failures.append(error)
    thread = threading.Thread(target=refresh)
    thread.start()
    try:
        assert ready.wait(timeout=5)
        assert ex.refresh_once(d).kind == 'completed'
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive() and not failures
    assert first_reply[0].kind == 'completed'
    assert ex.lookup(d).kind == 'completed'
    assert ex.status(d).state == 'TASK_STATE_COMPLETED'


def test_restart_can_refresh_saved_task_and_complete(context):
    conn, _ = context
    d, ex, peer, lanes = prepared(context)
    ex.submit(d)
    adapter, client = api()
    restarted = adapter.A2AExecutor(Path(d.database), ex.plan, client(ex.plan.url, opener=peer.open))
    assert restarted.refresh_once(d).kind == 'completed'
    assert lanes.reconcile(conn, d)
    assert kb.get_task(conn, d.task_id).status == 'done'
    assert [c['method'] for c in peer.calls] == ['SendMessage', 'GetTask']


def test_stale_local_ownership_refuses_remote_refresh(context):
    conn, _ = context
    d, ex, peer, _ = prepared(context)
    ex.submit(d)
    kb.block_task(conn, d.task_id, reason='operator hold')
    with pytest.raises(ValueError):
        ex.refresh_once(d)
    assert len(peer.calls) == 1


def test_transport_failure_detail_and_auth_headers_stay_out_of_journal(context):
    conn, _ = context
    d, ex, peer, _ = prepared(context)
    def failed(*args, **kwargs):
        raise OSError('secret-auth-value and arbitrary provider detail')
    ex.client._headers['Authorization'] = 'Bearer secret-auth-value'
    ex.client._opener = failed
    ex.submit(d)
    events = json.dumps([event.payload for event in kb.list_events(conn, d.task_id)])
    assert 'secret-auth-value' not in events
    assert 'provider detail' not in events
    assert ex.lookup(d) is None
    assert ex.refresh_once(d) is None
    assert not peer.calls



def test_saved_remote_id_survives_crash_before_observation(context, monkeypatch):
    d, ex, peer, _ = prepared(context)
    def interrupted(*args, **kwargs):
        raise SystemExit('fake crash after remote task receipt')
    original = ex._save
    monkeypatch.setattr(ex, '_save', interrupted)
    with pytest.raises(SystemExit):
        ex.submit(d)
    monkeypatch.setattr(ex, '_save', original)
    assert ex.refresh_once(d).kind == 'completed'
    assert [c['method'] for c in peer.calls] == ['SendMessage', 'GetTask']


def test_credential_shaped_assignment_requires_explicit_redaction(context):
    d, ex, peer, _ = prepared(context, body='API token sk-abcdefghij1234567890ABCD')
    with pytest.raises(ValueError):
        ex.submit(d)
    assert not peer.calls


def test_preexisting_conflicting_send_intent_fails_closed(context):
    d, ex, peer, _ = prepared(context)
    ex.journal.record(d, 'external_a2a_send_intent',
                      {'plan': ex.plan.__dict__, 'message_id': 'foreign', 'request_sha256': 'wrong'})
    with pytest.raises(ValueError):
        ex.submit(d)
    assert not peer.calls



@pytest.mark.parametrize('local_path', ['/private/synthetic/work/a.py', 'C:\\Synthetic\\work\\a.py',
                                       '\\\\fixture\\share\\work', '~/synthetic/a',
                                       'file:///private/synthetic/a', '../synthetic/a', './synthetic/a',
                                       'FILE:/private/synthetic/a', 'path:/private/synthetic/a',
                                       '//server/share/work', '\\Windows\\Synthetic'])
@pytest.mark.parametrize('field', ['title', 'body'])
def test_local_path_text_is_refused_before_transport(context, local_path, field):
    d, ex, peer, _ = prepared(context, **{field: 'Inspect ' + local_path})
    with pytest.raises(ValueError):
        ex.submit(d)
    assert not peer.calls
    assert local_path not in json.dumps(peer.calls)
    assert ex.journal.read(d, 'external_a2a_send_intent') is None


@pytest.mark.parametrize('text', [
    'input / output', 'request / response and input / output', 'Use input/output terminology',
    'Discuss / as a separator', 'A lone slash: /', "Quoted '/' is a separator",
    'The symbol \\ separates words', 'A // comment', 'Project-relative src/module.py',
    'Discuss ./ and ../ syntax', 'file: a label',
    'Read https://example.invalid/reference', 'Read https://example.invalid/~/reference',
    'Read HTTPS://example.invalid/reference',
])
def test_prose_separators_project_names_and_web_urls_are_preserved(context, text):
    d, ex, peer, _ = prepared(context, body=text)
    assert ex.submit(d).kind == 'ack'
    assignment = json.loads(peer.calls[0]['params']['message']['parts'][0]['text'])
    assert assignment['assignment']['body'] == text
