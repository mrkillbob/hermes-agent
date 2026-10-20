"""The existing supervisor readiness POST shares owned request cancellation."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from hermes_cli.local_runtime.supervisor import LlamaServerSupervisor


@pytest.fixture
def readiness_server(tmp_path):
    posted, release = threading.Event(), threading.Event()
    state = {'block': False, 'posts': 0, 'bad_metrics': False, 'queries': [], 'generation': ('fixture-owner', 1)}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, content_type='application/json'):
            data = json.dumps(body).encode() if content_type == 'application/json' else body.encode()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            state['queries'].append(self.path)
            if self.path == '/models':
                self.reply({'data': [{'id': 'fixture', 'status': {'value': 'loaded'}}]})
            elif self.path.startswith('/slots?'):
                self.reply([{'id': 0, 'is_processing': False}])
            elif self.path.startswith('/metrics?'):
                if state.get('replace_on_metrics'):
                    state['generation'] = ('fixture-owner', 2)
                text = 'llamacpp:requests_processing 0\n'
                if not state['bad_metrics']:
                    text += 'llamacpp:requests_deferred 0\n'
                self.reply(text, 'text/plain')

        def do_POST(self):
            state['posts'] += 1
            self.rfile.read(int(self.headers['Content-Length']))
            posted.set()
            if state['block']:
                release.wait(5)
            self.reply({'choices': [{'message': {'content': 'Paris'}}]})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    supervisor = LlamaServerSupervisor(tmp_path / 'fixture-engine', tmp_path,
                                      port=server.server_address[1])
    supervisor._readiness_identity = lambda: state['generation']
    try:
        yield supervisor, state, posted, release
    finally:
        release.set()
        supervisor.stop()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_existing_readiness_post_uses_non_autoload_telemetry(readiness_server):
    supervisor, state, _, _ = readiness_server
    assert supervisor.touch_generate('fixture', timeout_s=2)
    assert state['posts'] == 1
    telemetry = [path for path in state['queries'] if path.startswith(('/slots?', '/metrics?'))]
    assert telemetry
    assert all('autoload=false' in path and 'model=fixture' in path for path in telemetry)


def test_owner_stop_aborts_only_its_pending_readiness_request(readiness_server):
    supervisor, state, posted, release = readiness_server
    state['block'] = True
    result, done = [], threading.Event()

    def touch():
        try:
            result.append(supervisor.touch_generate('fixture', timeout_s=5))
        finally:
            done.set()

    thread = threading.Thread(target=touch, daemon=True)
    thread.start()
    assert posted.wait(2)
    supervisor.stop()
    assert done.wait(2)
    release.set()
    thread.join(2)
    assert result == [False]
    assert state['posts'] == 1
    assert supervisor.touch_generate('fixture', timeout_s=1) is False
    assert state['posts'] == 1


def test_unverifiable_idle_telemetry_never_dispatches_readiness(readiness_server):
    supervisor, state, _, _ = readiness_server
    state['bad_metrics'] = True
    assert supervisor.touch_generate('fixture', timeout_s=1) is False
    assert state['posts'] == 0


def test_failed_readiness_stays_fenced_until_owner_incarnation_changes(readiness_server):
    supervisor, state, _, release = readiness_server
    state['block'] = True
    assert supervisor.touch_generate('fixture', timeout_s=0.2) is False
    state['block'] = False
    release.set()
    # Passive idle telemetry does not resolve an attempted request's quarantine.
    assert supervisor.touch_generate('fixture', timeout_s=1) is False
    assert state['posts'] == 1
    state['generation'] = ('fixture-owner', 2)
    assert supervisor.touch_generate('fixture', timeout_s=2) is True
    assert state['posts'] == 2


def test_owner_replacement_during_preflight_prevents_post(readiness_server):
    supervisor, state, _, _ = readiness_server
    state['replace_on_metrics'] = True
    assert supervisor.touch_generate('fixture', timeout_s=1) is False
    assert state['posts'] == 0
    state['replace_on_metrics'] = False
    assert supervisor.touch_generate('fixture', timeout_s=2) is True
    assert state['posts'] == 1


def test_real_identity_reader_refuses_missing_foreign_or_unverified_owner(readiness_server, monkeypatch):
    import os
    from hermes_cli.local_runtime import recovery

    supervisor, state, _, _ = readiness_server
    supervisor._readiness_identity = lambda: LlamaServerSupervisor._readiness_identity(supervisor)
    assert supervisor.touch_generate('fixture', timeout_s=1) is False
    supervisor._state = {'owner_pid': os.getpid() + 1, 'base_url': supervisor.base_url}
    assert supervisor.touch_generate('fixture', timeout_s=1) is False
    supervisor._state['owner_pid'] = os.getpid()
    monkeypatch.setattr(recovery, 'recorded_process', lambda record: None)
    assert supervisor.touch_generate('fixture', timeout_s=1) is False
    assert state['posts'] == 0
