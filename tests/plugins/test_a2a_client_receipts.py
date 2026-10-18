"""Bounded raw A2A client behavior using in-memory responses only."""
import json
import urllib.request

import pytest

from plugins.platforms.a2a import client, tools
from tests.hermes_cli.fixtures.a2a_peer import Response


def test_legacy_tool_http_delegation_keeps_public_patch_seam(monkeypatch):
    seen = []
    def opened(request, timeout):
        seen.append((request.get_method(), timeout))
        return Response({'legacy': True}, request.full_url)
    monkeypatch.setattr(tools.urllib.request, 'urlopen', opened)
    assert tools._http_json('https://fixture.invalid', {}, 7, 'GET') == {'legacy': True}
    assert seen == [('GET', 7)]


@pytest.mark.parametrize('fault', ['id', 'version', 'both', 'neither', 'oversize', 'redirect', 'nan', 'duplicate_key'])
def test_raw_rpc_rejects_unbound_or_unbounded_response(fault):
    def opened(request, timeout):
        value = {'jsonrpc': '2.0', 'id': 'request', 'result': {}}
        changes = {
            'id': lambda: value.update(id='wrong'),
            'version': lambda: value.update(jsonrpc='1.0'),
            'both': lambda: value.update(error={'code': -1}),
            'neither': lambda: value.pop('result'),
            'oversize': lambda: value.update(result='x' * (client.MAX_BYTES + 1)),
        }
        changes.get(fault, lambda: None)()
        response = Response(value, 'https://elsewhere.invalid' if fault == 'redirect' else request.full_url)
        if fault == 'nan':
            response.data = b'{"jsonrpc":"2.0","id":"request","result":NaN}'
        if fault == 'duplicate_key':
            response.data = b'{"jsonrpc":"2.0","id":"wrong","id":"request","result":{}}'
        return response
    rpc = client.JSONRPCClient('https://fixture.invalid', opener=opened)
    with pytest.raises(ValueError):
        rpc.rpc('GetTask', {'id': 'task'}, 'request')


def test_redirect_handler_never_replays_request():
    with pytest.raises(ValueError):
        client.NoRedirect().redirect_request(urllib.request.Request('https://fixture.invalid'), None, 302,
                                             'redirect', {}, 'https://elsewhere.invalid')


def test_rpc_errors_do_not_expose_peer_detail():
    def opened(request, timeout):
        return Response({'jsonrpc': '2.0', 'id': 'request', 'error': {'code': -32001, 'message': 'sensitive raw detail'}}, request.full_url)
    with pytest.raises(client.PeerRPCError) as error:
        client.JSONRPCClient('https://fixture.invalid', opener=opened).rpc('GetTask', {'id': 'task'}, 'request')
    assert error.value.code == -32001
    assert 'sensitive' not in str(error.value)


def test_slow_chunks_cannot_extend_cooperative_deadline(monkeypatch):
    ticks = iter([0, 0, .5, 1.1])
    monkeypatch.setattr(client.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(TimeoutError):
        client.http_json('https://fixture.invalid', {}, 1, 'GET', max_bytes=100,
                         opener=lambda request, timeout: Response({'small': 'response'}, request.full_url))


@pytest.mark.parametrize('url', ['file:///tmp/data', 'https://user:secret@fixture.invalid', 'https://fixture.invalid/#fragment'])
def test_owner_peer_must_be_an_explicit_http_endpoint(url):
    with pytest.raises(ValueError):
        client.JSONRPCClient(url)


def test_request_size_is_checked_before_transport():
    calls = []
    rpc = client.JSONRPCClient('https://fixture.invalid', opener=lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError):
        rpc.rpc('SendMessage', {'text': 'x' * client.MAX_BYTES}, 'request')
    assert not calls
