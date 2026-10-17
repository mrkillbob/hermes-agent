"""Read-only adopted endpoint probes never autoload or acquire boot authority."""

import pytest


@pytest.mark.parametrize('case,expected', [
    ('idle', True), ('slot-busy', False), ('metric-busy', False),
    ('unknown-slots', False), ('unknown-metric', False), ('transport-error', False),
    ('truncated-response', False),
    ('foreign', False), ('no-state', False),
])
def test_adopted_idle_probe_fails_closed(monkeypatch, case, expected):
    from hermes_cli.local_runtime import endpoint

    base = 'http://127.0.0.1:18434/v1'
    monkeypatch.setattr(endpoint, '_state_endpoint', lambda: None if case == 'no-state' else
                        {'base_url': base, 'api_key': 'disposable-test-key'})
    reads = []

    def read(root, key, route, timeout_s):
        reads.append(route)
        assert root == base.removesuffix('/v1') and key == 'disposable-test-key'
        assert timeout_s == 2 and 'autoload=false' in route and 'model=model%20a' in route
        if case == 'transport-error':
            raise OSError('closed')
        if case == 'truncated-response':
            from http.client import IncompleteRead
            raise IncompleteRead(b'partial', 20)
        if route.startswith('/slots?'):
            return ('{}' if case == 'unknown-slots' else
                    '[{"is_processing":true}]' if case == 'slot-busy' else
                    '[{"is_processing":false}]')
        return ('llamacpp:requests_processing 1\n' if case == 'metric-busy' else
                '' if case == 'unknown-metric' else 'llamacpp:requests_processing 0\n')

    monkeypatch.setattr(endpoint, 'managed_get_text', read, raising=False)
    actual = endpoint.managed_model_idle('http://127.0.0.1:9999/v1' if case == 'foreign' else base,
                                         'model a')
    assert actual is expected
    if case in ('foreign', 'no-state'):
        assert reads == []


@pytest.fixture
def telemetry_server():
    import http.server
    import threading

    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/target')
                self.end_headers()
                return
            body = b'x' * ((1 << 20) + 2) if self.path == '/large' else b'llamacpp:requests_processing 0\n'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_telemetry_read_ignores_environment_proxy(telemetry_server, monkeypatch):
    from hermes_cli.local_runtime.endpoint import managed_get_text

    base, seen = telemetry_server
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('no_proxy', '')
    monkeypatch.setenv('NO_PROXY', '')
    assert managed_get_text(base, 'disposable-key', '/metrics', 2) == 'llamacpp:requests_processing 0\n'
    assert seen == ['/metrics']


def test_telemetry_read_refuses_redirect(telemetry_server):
    import urllib.error
    from hermes_cli.local_runtime.endpoint import managed_get_text

    base, seen = telemetry_server
    with pytest.raises(urllib.error.HTTPError) as error:
        managed_get_text(base, 'disposable-key', '/redirect', 2)
    assert error.value.code == 302
    assert seen == ['/redirect']


def test_telemetry_read_rejects_oversized_body(telemetry_server):
    from hermes_cli.local_runtime.endpoint import managed_get_text

    base, seen = telemetry_server
    with pytest.raises(ValueError, match='exceeds limit'):
        managed_get_text(base, 'disposable-key', '/large', 2)
    assert seen == ['/large']


def test_pending_growth_never_replaces_a_larger_grant(tmp_path, monkeypatch):
    from hermes_cli.local_runtime import growth

    monkeypatch.setattr(growth, 'window_overrides_path', lambda: tmp_path / 'windows.json')
    growth.save_window_override('model', 196608)
    growth.save_window_override('model', 98304, only_grow=True)
    assert growth.load_window_overrides() == {'model': 196608}


def test_corrupt_pending_grants_are_not_overwritten(tmp_path, monkeypatch):
    from hermes_cli.local_runtime import growth

    path = tmp_path / 'windows.json'
    path.write_text('{incomplete', encoding='utf-8')
    monkeypatch.setattr(growth, 'window_overrides_path', lambda: path)
    with pytest.raises(ValueError):
        growth.save_window_override('model', 98304)
    with pytest.raises(ValueError):
        growth.clear_window_override('model')
    assert path.read_text(encoding='utf-8') == '{incomplete'


def test_concurrent_process_grants_preserve_every_model(tmp_path):
    import json
    import subprocess
    import sys
    import time

    path = tmp_path / 'windows.json'
    program = '''
import sys,time
from pathlib import Path
from hermes_cli.local_runtime import growth
path=Path(sys.argv[1]);prefix=sys.argv[2]
growth.window_overrides_path=lambda:path
real=growth.load_window_overrides
def slow(*args,**kwargs):
    result=real(*args,**kwargs)
    time.sleep(.003)
    return result
growth.load_window_overrides=slow
for index in range(20):
    growth.save_window_override(prefix+str(index),98304)
'''
    children = [subprocess.Popen([sys.executable, '-c', program, str(path), prefix],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for prefix in ('a', 'b')]
    try:
        deadline = time.monotonic() + 20
        while any(child.poll() is None for child in children):
            assert time.monotonic() < deadline, 'pending-grant writers timed out'
            try:
                assert isinstance(json.loads(path.read_text(encoding='utf-8')), dict)
            except FileNotFoundError:
                pass  # first atomic publication has not happened yet
            time.sleep(.001)
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            assert child.returncode == 0, (stdout, stderr)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    assert json.loads(path.read_text(encoding='utf-8')) == {
        prefix + str(index): 98304 for prefix in ('a', 'b') for index in range(20)}
