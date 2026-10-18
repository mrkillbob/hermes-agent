"""Real owner admissions and event RPC, without gateway sockets or model calls."""
import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from gateway.session_controls import AuthorityConnection
from gateway.session_a2a_events import observations
from hermes_state_runtime import claim_session_input, settle_session_input


async def create_owner(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway import run
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'model': {'default': 'fixture'}, 'platform_toolsets': {'cli': []}})
    monkeypatch.setattr(run, '_resolve_gateway_model', lambda config: 'fixture')
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = run.GatewayRunner.__new__(run.GatewayRunner)
    runner.adapters, runner.session_store, runner._session_db, runner._draining = {}, store, store._db, False
    authority = await initialize_session_authority(runner, profile_id='target', instance_id='test')
    authority._schedule = lambda ref: None
    conn = AuthorityConnection(authority, SimpleNamespace(write=lambda frame: None), {'user_id': 'producer'})
    return conn, authority


@pytest_asyncio.fixture
async def owner(tmp_path, monkeypatch):
    conn, authority = await create_owner(tmp_path, monkeypatch)
    yield conn, authority
    await conn.close()
    authority.db.close()


def request():
    assignment = {'schema': 'hermes.a2a.assignment.v1', 'expected_result_schema': 'hermes.a2a.receipt.v1',
        'correlation': {'task_id': 'card', 'run_id': 1, 'key': 'key', 'payload_sha256': 'hash'},
        'assignment': {'title': 'Synthetic', 'body': 'Two plus two'}}
    return dict(op='accept', agent='dev', tenant='team', peer='alice', context_id='', message_id='message', text=json.dumps(assignment))


async def call(conn, params):
    response = await conn.dispatch({'id': 1, 'method': 'a2a.event', 'params': {'event_task': params}})
    assert 'result' in response, response
    return response['result']


@pytest.mark.asyncio
@pytest.mark.parametrize('reply,outcome,state', [('4', 'completed', 'COMPLETED'),
    ('synthetic failure', 'failed', 'FAILED'), ('[INPUT_REQUIRED] Which input?', 'completed', 'INPUT_REQUIRED')])
async def test_owner_duplicate_result_question_failure_and_ack_survive_reconnect(owner, reply, outcome, state):
    conn, authority = owner
    params = request()
    first = await call(conn, params)
    again = await call(conn, params)
    assert again['admission_id'] == first['admission_id']
    task = first['task']
    from hermes_state_runtime import get_session_admission
    admitted = get_session_admission(authority.db, admission_id=first['admission_id'])
    running = claim_session_input(authority.db, epoch=authority.epoch, session_id=admitted['target_session_id'])
    settle_session_input(authority.db, epoch=authority.epoch, admission_id=running['admission_id'],
        generation=running['generation'], outcome=outcome, result={'result': {'final_response': reply, 'messages': []}, 'usage': {}})
    read = {k: params[k] for k in ('agent', 'tenant', 'peer')} | {'op': 'observe', 'task_id': task['id']}
    terminal = await call(conn, read)
    assert terminal['task']['status']['state'] == 'TASK_STATE_' + state
    reconnect = AuthorityConnection(authority, SimpleNamespace(write=lambda frame: None), {'user_id': 'producer'})
    assert (await call(reconnect, read))['event'] == terminal['event']
    event = terminal['event']
    ack = await call(reconnect, read | {'op': 'ack', 'event_id': event['id'], 'sha256': event['sha256']})
    assert ack['acknowledged'] == event['id']
    assert (await call(reconnect, read))['acked'] is True
    foreign = await reconnect.dispatch({'id': 2, 'method': 'a2a.event', 'params': {'event_task': read | {'peer': 'bob'}}})
    assert 'error' in foreign
    await reconnect.close()


@pytest.mark.asyncio
async def test_observations_read_only_on_completion_or_one_replay_gap():
    class Client:
        def __init__(self):
            self.events, self.calls = asyncio.Queue(), []
        async def rpc(self, method, **params):
            self.calls.append(params['event_task']['op'])
            return {'task': {'id': 'task'}, 'admission_id': 'admission', 'event': None if len(self.calls) == 1 else {'saved': True}}
    client = Client()
    stream = observations(client, {'op': 'accept', 'agent': 'dev', 'tenant': '', 'peer': 'alice'})
    assert (await anext(stream))['event'] is None
    await client.events.put({'params': {'type': 'unrelated'}})
    await client.events.put({'params': {'type': 'message.complete', 'payload': {'admission_id': 'admission'}}})
    assert (await anext(stream))['event'] == {'saved': True}
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert client.calls == ['accept', 'observe']


@pytest.mark.asyncio
@pytest.mark.parametrize('disconnect', [False, True])
async def test_real_owner_stream_and_disconnect_without_cancelling_work(owner, monkeypatch, disconnect):
    from contextlib import asynccontextmanager
    from plugins.platforms.a2a.event_return import serve
    from plugins.platforms.a2a import adapter as module
    from plugins.platforms.a2a.client import decode_json
    conn, authority = owner
    client = SimpleNamespace(events=asyncio.Queue(), calls=[], latest=None)
    async def rpc(method, **params):
        assert method == 'a2a.event'
        client.calls.append(params['event_task']['op'])
        client.latest = await call(conn, params['event_task'])
        return client.latest
    client.rpc = rpc
    @asynccontextmanager
    async def factory(home):
        yield client
    monkeypatch.setattr(module, '_profile_home', lambda profile: 'synthetic-home')
    class Handler:
        close_connection = False
        def __init__(self):
            self.frames = []
    handler = Handler()
    def write(handler, payload):
        handler.frames.append(payload)
        if len(handler.frames) == 1:
            if disconnect:
                raise BrokenPipeError('synthetic disconnect after task ACK')
            from hermes_state_runtime import get_session_admission
            row = get_session_admission(authority.db, admission_id=client.latest['admission_id'])
            running = claim_session_input(authority.db, epoch=authority.epoch, session_id=row['target_session_id'])
            settle_session_input(authority.db, epoch=authority.epoch, admission_id=running['admission_id'],
                generation=running['generation'], outcome='completed',
                result={'result': {'final_response': '4', 'messages': []}, 'usage': {}})
            client.events.put_nowait({'params': {'type': 'message.complete', 'payload': {'admission_id': running['admission_id']}}})
    adapter = SimpleNamespace(_active_profile='default', _event_owner_home='synthetic-home', _sse_headers=lambda h: None, _sse_write=write)
    params = request()
    message = {'message': {'messageId': params['message_id'], 'parts': [{'text': params['text']}]}}
    operation = serve(adapter, handler, '_rpc_message_stream', 'r', message, 'alice', {'slug': 'dev', 'local': True}, client_factory=factory)
    if disconnect:
        with pytest.raises(BrokenPipeError):
            await operation
        from hermes_state_runtime import get_session_admission
        row = get_session_admission(authority.db, admission_id=client.latest['admission_id'])
        assert row['status'] == 'queued'
        assert client.calls == ['accept']
        return
    await operation
    assert client.calls == ['accept', 'observe']
    first = decode_json(handler.frames[0].split('data: ', 1)[1])
    last = decode_json(handler.frames[1].split('data: ', 1)[1])
    assert first['result']['task']['status']['state'] == 'TASK_STATE_SUBMITTED'
    assert last['result']['task']['status']['state'] == 'TASK_STATE_COMPLETED'
    assert 'hermes.delivery.v1' in last['result']['task']['metadata']


@pytest.mark.asyncio
async def test_crash_after_canonical_admission_before_projection_binding_deduplicates(owner, monkeypatch):
    from hermes_state_a2a import EventTasks
    from hermes_state_runtime import RuntimeStoreError
    conn, authority = owner
    original = EventTasks.bind
    def fail(*args, **kwargs):
        raise RuntimeStoreError('storage_unavailable')
    monkeypatch.setattr(EventTasks, 'bind', fail)
    response = await conn.dispatch({'id': 1, 'method': 'a2a.event', 'params': {'event_task': request()}})
    assert 'error' in response
    monkeypatch.setattr(EventTasks, 'bind', original)
    restored = await call(conn, request())
    with authority.db._read_ctx() as sql:
        assert sql.execute('SELECT COUNT(*) FROM session_admissions').fetchone()[0] == 1
    assert restored['task']['id'].startswith('task-')


@pytest.mark.asyncio
@pytest.mark.parametrize('project_before_restart', [False, True])
async def test_canonical_result_projection_and_ack_survive_owner_database_restart(tmp_path, monkeypatch, project_before_restart):
    from hermes_state_runtime import get_session_admission
    conn, authority = await create_owner(tmp_path, monkeypatch)
    params = request()
    accepted = await call(conn, params)
    row = get_session_admission(authority.db, admission_id=accepted['admission_id'])
    running = claim_session_input(authority.db, epoch=authority.epoch, session_id=row['target_session_id'])
    settle_session_input(authority.db, epoch=authority.epoch, admission_id=running['admission_id'],
        generation=running['generation'], outcome='completed',
        result={'result': {'final_response': '4', 'messages': []}, 'usage': {}})
    read = {k: params[k] for k in ('agent', 'tenant', 'peer')} | {'op': 'observe', 'task_id': accepted['task']['id']}
    final = await call(conn, read) if project_before_restart else None
    await conn.close()
    authority.db.close()
    restored, replacement = await create_owner(tmp_path, monkeypatch)
    recovered = await call(restored, read)
    if final is not None:
        assert recovered['event'] == final['event']
    final = recovered
    assert final['task']['status']['state'] == 'TASK_STATE_COMPLETED'
    assert (await call(restored, params))['admission_id'] == accepted['admission_id']
    event = final['event']
    await call(restored, read | {'op': 'ack', 'event_id': event['id'], 'sha256': event['sha256']})
    assert (await call(restored, read))['acked']
    await restored.close()
    replacement.db.close()


@pytest.mark.asyncio
async def test_concurrent_duplicate_acceptance_and_bound_lost_ack_lookup(owner):
    from hermes_state_a2a import digest
    conn, authority = owner
    params = request()
    first, second = await asyncio.gather(call(conn, params), call(conn, params))
    assert first['admission_id'] == second['admission_id']
    corr = json.loads(params['text'])['correlation']
    lookup = {k: params[k] for k in ('agent', 'tenant', 'peer')} | {'op': 'find', 'message_id': params['message_id'],
        'request_sha256': digest([{k: params[k] for k in ('context_id', 'text')}, corr])}
    assert (await call(conn, lookup))['task']['id'] == first['task']['id']
    bad = await conn.dispatch({'id': 2, 'method': 'a2a.event', 'params': {'event_task': lookup | {'request_sha256': 'wrong'}}})
    assert 'error' in bad
    with authority.db._read_ctx() as sql:
        assert sql.execute('SELECT COUNT(*) FROM session_admissions').fetchone()[0] == 1


@pytest.mark.asyncio
async def test_started_owner_restart_stays_unknown_without_second_admission(tmp_path, monkeypatch):
    from hermes_state_runtime import get_session_admission
    conn, authority = await create_owner(tmp_path, monkeypatch)
    params = request()
    accepted = await call(conn, params)
    row = get_session_admission(authority.db, admission_id=accepted['admission_id'])
    claim_session_input(authority.db, epoch=authority.epoch, session_id=row['target_session_id'])
    await conn.close()
    authority.db.close()
    restored, replacement = await create_owner(tmp_path, monkeypatch)
    read = {k: params[k] for k in ('agent', 'tenant', 'peer')} | {'op': 'observe', 'task_id': accepted['task']['id']}
    response = await restored.dispatch({'id': 2, 'method': 'a2a.event', 'params': {'event_task': read}})
    assert response['error']['message'] == 'unknown_execution'
    with replacement.db._read_ctx() as sql:
        assert sql.execute('SELECT COUNT(*) FROM session_admissions').fetchone()[0] == 1
    await restored.close()
    replacement.db.close()
