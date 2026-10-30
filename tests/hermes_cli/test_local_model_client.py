"""Native Local Models client uses one-use tickets without proxying or following redirects."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('named', [False, True])
def test_profile_bound_job_transport_uses_new_ticket_per_request(tmp_path, monkeypatch, named):
    from hermes_cli import gateway_client
    from hermes_cli.local_model_client import LocalModelClient

    home = tmp_path / 'root'
    if named:
        home = home / 'profiles' / 'named'
    home.mkdir(parents=True)
    grants, received, issued = {}, [], []

    def ticket(request_home, endpoint, *, purpose):
        assert request_home == home and endpoint.profile_id == str(home)
        assert purpose == 'native-http'
        value = f'one-use-test-{len(issued)}'
        grants[value] = str(home)
        issued.append(value)
        return value

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.answer()

        def do_POST(self):
            self.answer()

        def answer(self):
            grant = grants.pop(self.headers['x-hermes-gateway-ticket'])
            assert grant == str(home)
            assert not self.headers.get('Origin')
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            received.append((self.command, self.path, body, grant))
            payload = json.dumps({'job_id': 'owner-job', 'status': 'done'}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    monkeypatch.setattr(gateway_client, '_session_ticket', ticket)
    # A broken proxy must not affect a discovered private loopback gateway.
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    monkeypatch.setenv('no_proxy', '')
    with HTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = SimpleNamespace(profile_id=str(home), api_origin=f'http://127.0.0.1:{server.server_port}')
            client = LocalModelClient(home, endpoint)
            assert client.post('activate', {'model_id': 'staged'})['job_id'] == 'owner-job'
            assert client.get('jobs/owner-job')['status'] == 'done'
        finally:
            server.shutdown()
            thread.join(5)
    assert len(issued) == 2 and not grants
    assert received == [
        ('POST', '/api/local-models/activate', {'model_id': 'staged'}, str(home)),
        ('GET', '/api/local-models/jobs/owner-job', {}, str(home)),
    ]
    assert not list(home.iterdir())


def test_failed_post_is_not_retried(monkeypatch, tmp_path):
    from urllib.error import URLError
    from hermes_cli import gateway_client
    from hermes_cli.local_model_client import LocalModelClient, LocalModelClientError

    calls = []
    monkeypatch.setattr(gateway_client, '_session_ticket', lambda *a, **k: 'unused-test-ticket')
    client = LocalModelClient(tmp_path, SimpleNamespace(api_origin='http://127.0.0.1:1'))

    def failed_open(*args, **kwargs):
        calls.append(args[0].method)
        raise URLError('connection lost after write')

    monkeypatch.setattr(client._http, 'open', failed_open)
    with pytest.raises(LocalModelClientError, match='outcome is unknown'):
        client.post('quickstart', {'model_id': 'tiny'})
    assert calls == ['POST']


@pytest.mark.parametrize('failure', ['ticket', 'response'])
def test_bootstrap_or_truncated_response_is_reported_without_traceback(monkeypatch, tmp_path, failure):
    from http.client import IncompleteRead
    from hermes_cli import gateway_client
    from hermes_cli.local_model_client import LocalModelClient, LocalModelClientError

    def ticket(*args, **kwargs):
        if failure == 'ticket':
            raise gateway_client.GatewayClientError('Gateway bootstrap identity changed; retry launch')
        return 'test-ticket'

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            raise IncompleteRead(b'partial')

    monkeypatch.setattr(gateway_client, '_session_ticket', ticket)
    client = LocalModelClient(tmp_path, SimpleNamespace(api_origin='http://127.0.0.1:1'))
    monkeypatch.setattr(client._http, 'open', lambda *a, **k: Response())
    with pytest.raises(LocalModelClientError, match='outcome is unknown'):
        client.get('jobs/owner-job')


def test_truncated_refusal_reports_http_status(monkeypatch, tmp_path):
    from http.client import IncompleteRead
    from urllib.error import HTTPError
    from hermes_cli import gateway_client
    from hermes_cli.local_model_client import LocalModelClient, LocalModelClientError

    class TruncatedBody:
        def read(self, limit):
            raise IncompleteRead(b'{')

        def close(self):
            pass

    monkeypatch.setattr(gateway_client, '_session_ticket', lambda *a, **k: 'unused-test-ticket')
    client = LocalModelClient(tmp_path, SimpleNamespace(api_origin='http://127.0.0.1:1'))

    def refusal(*args, **kwargs):
        raise HTTPError('http://127.0.0.1:1', 409, 'Conflict', {}, TruncatedBody())

    monkeypatch.setattr(client._http, 'open', refusal)
    with pytest.raises(LocalModelClientError, match='HTTP 409'):
        client.post('activate', {'model_id': 'tiny'})
