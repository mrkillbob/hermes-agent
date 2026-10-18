"""Real response serialization against inert owner receipts; no sockets or peers."""
from contextlib import asynccontextmanager
from copy import deepcopy
import io
import json
import time
from types import SimpleNamespace

import pytest

from gateway.session_a2a_events import result_task
from hermes_state_a2a import digest
from plugins.platforms.a2a import protocol
from plugins.platforms.a2a.adapter import A2AAdapter, A2ARequestHandler
from plugins.platforms.a2a.client import MAX_BYTES
from plugins.platforms.a2a.client_stream import frames
from plugins.platforms.a2a.event_return import LOOKUP_SCHEMA, dispatch, serve


def terminal_value(context, reply='x' * 60_000):
    record = {'task_id': 'task-' + 'a' * 64, 'context_id': context,
        'scope': ['producer', 'agent', 'tenant', 'peer'], 'request_sha256': 'f' * 64,
        'correlation': {'task_id': 'card', 'run_id': 1, 'key': 'b' * 32, 'payload_sha256': 'c' * 64},
        'admission': {'session_id': 'session', 'admission_id': 'admission'}, 'event': None, 'acked': False}
    task = result_task(record, SimpleNamespace(outcome='completed'), {'final_response': reply})
    event = {'id': 'event-' + digest(task), 'sha256': digest(task), 'task': task}
    return {'task': task, 'event': event, 'acked': False}


class Handler:
    _json = A2ARequestHandler._json

    def __init__(self):
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.code = code

    def send_header(self, name, value):
        pass

    def end_headers(self):
        pass


async def response(value, method, request_id, handler=None):
    handler, calls = handler or Handler(), []
    async def rpc(name, **params):
        calls.append(params['event_task']['op'])
        return value
    @asynccontextmanager
    async def factory(home):
        yield SimpleNamespace(rpc=rpc)
    adapter = SimpleNamespace(_event_owner_home='fixture', _active_profile='default',
        _sse_headers=A2AAdapter._sse_headers, _sse_write=A2AAdapter._sse_write)
    params = {'id': value['task']['id']}
    if method == 'find':
        params = {'metadata': {LOOKUP_SCHEMA: {'message_id': 'b' * 32, 'request_sha256': 'f' * 64}}}
    await serve(adapter, handler, '_rpc_tasks_subscribe' if method == 'stream' else '_rpc_tasks_get',
        request_id, params, 'peer', {'slug': 'agent', 'local': True}, client_factory=factory)
    return handler.wfile.getvalue(), calls


@pytest.mark.asyncio
@pytest.mark.parametrize('context', ['context', '雪' * 1024], ids=['ascii-context', 'unicode-context'])
@pytest.mark.parametrize('method', ['stream', 'get', 'find'])
async def test_terminal_response_fits_real_delivery_envelope(context, method):
    value = terminal_value(context)
    request_id = 'b' * 32 + (':stream' if method == 'stream' else ':recover')
    raw, calls = await response(value, method, request_id)
    if method == 'stream':
        assert all(len(part) + 2 <= MAX_BYTES for part in raw.split(b'\n\n') if part)
        envelope, = frames(io.BytesIO(raw), request_id, time.monotonic() + 5)
        task = envelope['result']['task']
    else:
        assert len(raw) <= MAX_BYTES
        envelope = json.loads(raw)
        task = envelope['result']
    assert envelope['id'] == request_id
    assert task['contextId'] == context
    assert calls == ['find' if method == 'find' else 'observe']
    assert json.loads(task['artifacts'][0]['parts'][0]['text'])['summary'].endswith(
        '[result truncated to fit A2A receipt limits]')


@pytest.mark.asyncio
@pytest.mark.parametrize('context', ['context', '雪' * 1024], ids=['ascii-context', 'unicode-context'])
@pytest.mark.parametrize('method', ['stream', 'get'])
@pytest.mark.parametrize('request_id', ['x' * 1022, '\n' * 511, '雪' * 170 + 'xx'],
    ids=['ascii', 'escaped', 'unicode'])
async def test_terminal_response_fits_maximum_serialized_request_id(context, method, request_id):
    assert len(json.dumps(request_id).encode()) == 1024
    raw, _ = await response(terminal_value(context), method, request_id)
    if method == 'stream':
        assert all(len(part) + 2 <= MAX_BYTES for part in raw.split(b'\n\n') if part)
        envelope, = frames(io.BytesIO(raw), request_id, time.monotonic() + 5)
    else:
        assert len(raw) <= MAX_BYTES
        envelope = json.loads(raw)
    assert envelope['id'] == request_id


@pytest.mark.parametrize('request_id', ['x' * 1023, '\n' * 512, '雪' * 171, True, {}, []],
    ids=['ascii-too-long', 'escaped-too-long', 'unicode-too-long', 'bool', 'object', 'array'])
def test_invalid_event_request_id_is_refused_before_owner_access(request_id):
    handler = Handler()
    # No adapter/owner exists: invalid IDs must be rejected before either is read.
    dispatch(None, handler, '_rpc_tasks_get', request_id, {}, 'peer', {})
    raw = handler.wfile.getvalue()
    assert len(raw) < MAX_BYTES
    assert json.loads(raw)['id'] is None
    assert json.loads(raw)['error']['code'] == protocol.ERR_INVALID_PARAMS


def test_missing_event_request_id_notification_has_no_response():
    from plugins.platforms.a2a import event_return

    handler = Handler()
    assert dispatch(None, handler, '_rpc_tasks_get', event_return.MISSING_REQUEST_ID,
        {}, 'peer', {}) is None
    assert handler.code == 204
    assert handler.wfile.getvalue() == b''


@pytest.mark.asyncio
@pytest.mark.parametrize('request_id', ['short', 123, 1.25, None])
async def test_scalar_get_request_id_is_echoed_unchanged(request_id):
    raw, _ = await response(terminal_value('context', 'short result'), 'get', request_id)
    assert json.loads(raw)['id'] == request_id


@pytest.mark.asyncio
async def test_null_stream_request_id_keeps_jsonrpc_envelope():
    raw, _ = await response(terminal_value('context', 'short result'), 'stream', None)
    envelope, = frames(io.BytesIO(raw), None, time.monotonic() + 5)
    assert envelope['jsonrpc'] == '2.0'
    assert envelope['id'] is None
    assert envelope['result']['task']['id'].startswith('task-')


def test_event_post_distinguishes_missing_request_id_from_explicit_null(monkeypatch):
    from plugins.platforms.a2a.adapter import A2ARequestHandler
    from plugins.platforms.a2a import event_return

    seen = []
    monkeypatch.setattr(event_return, 'dispatch', lambda *args: seen.append(args[3]))

    class Security:
        def authenticate(self, _authorization, _client_ip):
            return 'peer'
        def is_trusted_peer(self, _identity):
            return True

    adapter = SimpleNamespace(_event_returns=True, _security_context=Security(),
        _rate_limiter=SimpleNamespace(allow=lambda _identity: True),
        _route_for_request=lambda _path, _params: {'agent': {'slug': 'agent', 'local': True}})

    def post(payload):
        raw = json.dumps(payload).encode()
        handler = object.__new__(A2ARequestHandler)
        handler.server = SimpleNamespace(adapter=adapter)
        handler.headers = {'Content-Length': str(len(raw))}
        handler.rfile, handler.path, handler.client_address = io.BytesIO(raw), '/', ('127.0.0.1', 1)
        handler.wfile = io.BytesIO()
        handler.send_response = lambda code: setattr(handler, 'code', code)
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None
        handler.do_POST()
        return handler

    notification = post({'jsonrpc': '2.0', 'method': 'not-a-method', 'params': {}})
    post({'jsonrpc': '2.0', 'id': None, 'method': 'tasks/get', 'params': {}})
    assert notification.code == 204
    assert notification.wfile.getvalue() == b''
    assert len(seen) == 1
    assert seen[0] is None


@pytest.mark.asyncio
async def test_unicode_summary_is_already_escaped_inside_receipt_json():
    raw, _ = await response(terminal_value('context', '🙂' * 10_000), 'get', 'size-check')
    assert len(raw) <= MAX_BYTES
    payload = json.loads(json.loads(raw)['result']['artifacts'][0]['parts'][0]['text'])
    assert payload['summary'].startswith('🙂')


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['stream', 'get'])
async def test_large_question_fits_without_changing_question_state(method):
    value = terminal_value('雪' * 1024, '[INPUT_REQUIRED] ' + 'x' * 60_000)
    request_id = 'x' * 1022
    raw, _ = await response(value, method, request_id)
    if method == 'stream':
        assert all(len(part) + 2 <= MAX_BYTES for part in raw.split(b'\n\n') if part)
        envelope, = frames(io.BytesIO(raw), request_id, time.monotonic() + 5)
        task = envelope['result']['task']
    else:
        assert len(raw) <= MAX_BYTES
        task = json.loads(raw)['result']
    assert task['status']['state'] == protocol.STATE_INPUT_REQUIRED
    payload = json.loads(task['status']['message']['parts'][0]['text'])
    assert payload['kind'] == 'question' and payload['questions'] == [payload['summary']]


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['stream', 'get'])
async def test_preexisting_oversized_receipt_is_retained_and_refused(method):
    value = terminal_value('context', 'small result')
    value['task']['artifacts'][0]['parts'][0]['text'] = 'x' * MAX_BYTES
    value['event'].update(id='event-' + digest(value['task']), sha256=digest(value['task']))
    original = deepcopy(value)
    handler = Handler()
    with pytest.raises(ValueError, match='exceeds'):
        await response(value, method, 'request', handler)
    assert handler.wfile.getvalue() == b''
    assert value == original
