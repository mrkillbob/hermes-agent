"""SSE framing and dormant inbound route use only fake byte streams."""
import io
import json
import time

import pytest

from plugins.platforms.a2a.client_stream import frames, StreamClient
from plugins.platforms.a2a import protocol
from plugins.platforms.a2a.adapter import A2AAdapter
from gateway.config import PlatformConfig


def test_inbound_event_mode_is_explicitly_disabled():
    assert A2AAdapter(PlatformConfig(enabled=True))._event_returns is False
    assert A2AAdapter(PlatformConfig(enabled=True, extra={'reliable_events': True}))._event_returns is True
    with pytest.raises(ValueError):
        A2AAdapter(PlatformConfig(enabled=True, extra={'reliable_events': 'true'}))


@pytest.mark.parametrize('fault', ['id', 'duplicate', 'incomplete', 'oversize', 'nan'])
def test_stream_framing_rejects_unbound_or_unbounded_event(fault):
    data = protocol.sse_data({'task': {'id': 'task'}}, 'request').encode()
    data = {
        'id': data.replace(b'request', b'wrong'),
        'duplicate': b'data: {"jsonrpc":"2.0","id":"request","id":"request","result":{}}\n\n',
        'incomplete': data.rstrip(),
        'oversize': b'data: ' + b'x' * 50000,
        'nan': b'data: {"jsonrpc":"2.0","id":"request","result":{"v":NaN}}\n\n',
    }[fault]
    with pytest.raises(ValueError):
        list(frames(io.BytesIO(data), 'request', time.monotonic() + 5))


def test_stream_handles_split_multibyte_and_keepalive():
    raw = (': keepalive\n\n' + protocol.sse_data({'task': {'text': 'é'}}, 'request')).encode()
    class Split(io.BytesIO):
        def read1(self, n):
            return self.read(1)
    result = list(frames(Split(raw), 'request', time.monotonic() + 5))
    assert result[0]['result']['task']['text'] == 'é'


def test_stream_rejects_changed_endpoint_without_request_replay():
    class Response(io.BytesIO):
        headers = {'Content-Type': 'text/event-stream'}
        def geturl(self):
            return 'https://other.invalid'
    calls = []
    def opened(request, timeout):
        calls.append(json.loads(request.data)['method'])
        return Response(b'')
    client = StreamClient('https://fixture.invalid', opener=opened)
    with pytest.raises(ValueError):
        list(client.stream('SendStreamingMessage', {}, 'request'))
    assert calls == ['SendStreamingMessage']


def test_event_owner_captures_custom_home_before_callback_scope_changes(tmp_path, monkeypatch):
    first = tmp_path / 'custom-owner'
    monkeypatch.setenv('HERMES_HOME', str(first))
    adapter = A2AAdapter(PlatformConfig(enabled=True, extra={'reliable_events': True}))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'other-owner'))
    assert adapter._event_owner_home == str(first.resolve())


def test_saved_delivery_ack_is_visible_on_requested_result_replay():
    from plugins.platforms.a2a.event_return import wire_task
    task = {'id': 'task', 'contextId': 'context', 'status': {'state': 'TASK_STATE_COMPLETED'}}
    value = {'task': task, 'event': {'id': 'event', 'sha256': 'hash'}, 'acked': True}
    assert wire_task(value)['metadata']['hermes.delivery.v1']['acknowledged'] is True
