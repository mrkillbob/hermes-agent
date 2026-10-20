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


def _retain_grant_timings(tmp_path):
    """Keep synthetic timing evidence after runner cleanup, without changing lock behavior."""
    import json
    import os
    import time
    from pathlib import Path

    destination = os.environ.get('HERMES_E2E_ARTIFACTS')
    if not destination:
        return
    children = []
    for prefix in ('a', 'b'):
        try:
            with (tmp_path / f'grant-trace-{prefix}.jsonl').open('rb') as stream:
                body = stream.read(128 * 1024 + 1)
            if len(body) > 128 * 1024:
                raise ValueError('trace exceeds byte bound')
            rows = [json.loads(line) for line in body.splitlines()]
            if len(rows) > 512:
                raise ValueError('trace exceeds event bound')
            waits, holds = [], []
            waiting = held = None
            for row in rows:
                phase, timestamp = row['phase'], row['monotonic_ns']
                if phase == 'lock-attempt' and waiting is None:
                    waiting = timestamp
                elif phase == 'lock-acquired':
                    if waiting is not None:
                        waits.append(timestamp - waiting)
                    waiting, held = None, timestamp
                elif phase == 'lock-released' and held is not None:
                    holds.append(timestamp - held)
                    held = None
            children.append({'prefix': prefix, 'events': rows, 'retained_events': len(rows),
                             'completed_grants': sum(row['phase'] == 'grant-end' for row in rows),
                             'lock_errors': sum(row['phase'] == 'lock-error' for row in rows),
                             'max_acquired_wait_ns': max(waits, default=None),
                             'max_released_hold_ns': max(holds, default=None),
                             'open_wait_since_ns': waiting, 'open_hold_since_ns': held})
        except (OSError, ValueError, KeyError, TypeError) as error:
            children.append({'prefix': prefix, 'unavailable': type(error).__name__})
    report = {'schema': 1, 'phase': 'after-owned-child-cleanup', 'children': children,
              'timing_scope': 'Completed spans in the retained 512-event tail; earlier spans may be absent.',
              'observer_effect': 'Instrumented timings; event collection adds work inside the lock. '
                                 'Retention happens after cleanup. Success does not establish a fix.'}
    payload = json.dumps(report, separators=(',', ':')).encode('utf-8')
    if len(payload) > 256 * 1024:
        payload = json.dumps({'schema': 1, 'unavailable': 'summary exceeds byte bound'}).encode('utf-8')
    root = Path(destination) / 'grant-lock-diagnostics'
    root.mkdir(parents=True, exist_ok=True)
    (root / f'grant-lock-timing-{os.getpid()}-{time.monotonic_ns()}.json').write_bytes(payload)


@pytest.mark.platforms("any")
def test_concurrent_process_grants_preserve_every_model(tmp_path):
    import errno
    import json
    import os
    import subprocess
    import sys
    import time

    path = tmp_path / 'windows.json'
    program = '''
import json,os,sys,time
from collections import deque
from pathlib import Path
from hermes_cli import auth
from hermes_cli.local_runtime import growth
path=Path(sys.argv[1]);prefix=sys.argv[2]
growth.window_overrides_path=lambda:path
events=deque(maxlen=512)
index=None
def event(phase,error=None):
    events.append(dict(phase=phase,monotonic_ns=time.monotonic_ns(),pid=os.getpid(),
                       prefix=prefix,index=index,error=type(error).__name__ if error else None,
                       errno=getattr(error,'errno',None),winerror=getattr(error,'winerror',None)))
real_lock=auth._kernel_lock
def traced_lock(lock_file,acquire):
    event('lock-attempt' if acquire else 'unlock-attempt')
    try:
        result=real_lock(lock_file,acquire)
    except BaseException as error:
        event('lock-error' if acquire else 'unlock-error',error)
        raise
    event('lock-acquired' if acquire else 'lock-released')
    return result
auth._kernel_lock=traced_lock
real=growth.load_window_overrides
def slow(*args,**kwargs):
    event('critical-read-start')
    result=real(*args,**kwargs)
    time.sleep(.003)
    event('critical-read-end')
    return result
growth.load_window_overrides=slow
real_write=growth._write_overrides
def traced_write(overrides):
    event('critical-write-start')
    try:
        result=real_write(overrides)
    except BaseException as error:
        event('critical-write-error',error)
        raise
    event('critical-write-end')
    return result
growth._write_overrides=traced_write
try:
    for index in range(20):
        event('grant-start')
        try:
            growth.save_window_override(prefix+str(index),98304)
        except BaseException as error:
            event('grant-error',error)
            raise
        event('grant-end')
finally:
    # No trace I/O while holding the grant lock. Hard-killed children may leave no trace.
    trace=path.with_name('grant-trace-'+prefix+'.jsonl')
    try:
        with trace.open('w',encoding='utf-8') as stream:
            for row in events:
                stream.write(json.dumps(row)+'\\n')
    except OSError as error:
        print('grant trace unavailable:',error,file=sys.stderr)
'''
    def diagnostics():
        traces = []
        for prefix in ('a', 'b'):
            trace = tmp_path / f'grant-trace-{prefix}.jsonl'
            try:
                body = trace.read_text(encoding='utf-8')
            except OSError as error:
                body = f'trace unavailable: {error}'
            traces.append(f'child {prefix} (last 512 events):\n{body}')
        return '\n'.join(traces)

    children = [subprocess.Popen([sys.executable, '-c', program, str(path), prefix],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for prefix in ('a', 'b')]
    try:
        reads = 0
        deadline = time.monotonic() + 20
        while any(child.poll() is None for child in children):
            assert time.monotonic() < deadline, 'pending-grant writers timed out'
            try:
                assert isinstance(json.loads(path.read_text(encoding='utf-8')), dict)
                reads += 1
            except FileNotFoundError:
                pass  # first atomic publication has not happened yet
            except PermissionError as exc:
                # Windows can refuse a reader while rename is pending. This is not
                # a partial JSON read. Bound contention by the same deadline; still
                # require successful concurrent reads, every writer and the final map.
                # io.open uses CRT errno and can omit winerror on native Windows.
                winerror = getattr(exc, 'winerror', None)
                if os.name != 'nt' or not (winerror in (5, 32, 33) or
                                          (winerror is None and exc.errno == errno.EACCES)):
                    raise
            time.sleep(.001)
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            assert child.returncode == 0, (stdout, stderr)
        assert reads > 0, 'no complete pending-grant map observed during concurrent writes'
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        try:
            _retain_grant_timings(tmp_path)
        except Exception as error:
            # Diagnostics must not replace the original assertion or cleanup outcome.
            print('grant timing retention unavailable:', type(error).__name__)
        if sys.exc_info()[0] is not None:
            print(diagnostics())
    assert json.loads(path.read_text(encoding='utf-8')) == {
        prefix + str(index): 98304 for prefix in ('a', 'b') for index in range(20)}, diagnostics()


def test_failed_atomic_publication_preserves_existing_grants(tmp_path, monkeypatch):
    import errno
    import os
    from hermes_cli.local_runtime import growth

    path = tmp_path / 'windows.json'
    monkeypatch.setattr(growth, 'window_overrides_path', lambda: path)
    growth.save_window_override('existing', 98304)
    original = path.read_bytes()

    def unavailable(*args, **kwargs):
        raise OSError(errno.EXDEV, 'atomic rename unavailable')

    monkeypatch.setattr(os, 'replace', unavailable)
    with pytest.raises(OSError):
        growth.save_window_override('new', 98304)
    assert path.read_bytes() == original


@pytest.mark.platforms('windows')
def test_held_grant_file_is_never_rewritten_in_place(tmp_path, monkeypatch):
    from hermes_cli.local_runtime import growth

    path = tmp_path / 'windows.json'
    monkeypatch.setattr(growth, 'window_overrides_path', lambda: path)
    growth.save_window_override('existing', 98304)
    original = path.read_bytes()
    with path.open('rb'):
        with pytest.raises(PermissionError):
            growth.save_window_override('new', 98304)
        assert path.read_bytes() == original
    growth.save_window_override('new', 98304)
    assert growth.load_window_overrides() == {'existing': 98304, 'new': 98304}
