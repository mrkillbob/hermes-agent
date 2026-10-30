"""Explicit bounded foreground SSE connection; no watcher or retry loop."""
import json
import math
import time
import urllib.request

from .client import JSONRPCClient, MAX_BYTES, decode_json


class StreamClient(JSONRPCClient):
    def stream(self, method, params, request_id, *, seconds=120):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 120:
            raise ValueError('Bounded stream lifetime required')
        body = json.dumps({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}, allow_nan=False).encode()
        if len(body) > MAX_BYTES:
            raise ValueError('A2A request exceeds bound')
        headers = {**self._headers, 'Content-Type': 'application/json', 'Accept': 'text/event-stream', 'A2A-Version': self.version}
        request = urllib.request.Request(self.url, data=body, headers=headers, method='POST')
        deadline = time.monotonic() + seconds
        with self._opener(request, timeout=seconds) as response:
            if response.geturl() != self.url or response.headers.get('Content-Type', '').split(';')[0] != 'text/event-stream':
                raise ValueError('Exact endpoint SSE response required')
            yield from frames(response, request_id, deadline)


def frames(response, request_id, deadline):
    pending, total, count = b'', 0, 0
    while time.monotonic() < deadline:
        chunk = response.read1(8192)
        if time.monotonic() >= deadline:
            raise TimeoutError('A2A stream deadline')
        if not chunk:
            if pending.strip():
                raise ValueError('Incomplete SSE frame')
            return
        total += len(chunk)
        if total > 4 * MAX_BYTES:
            raise ValueError('A2A stream exceeds bound')
        pending += chunk
        # CRLF and LF framing, without text decoding until a complete bounded frame.
        pending = pending.replace(b'\r\n', b'\n')
        while b'\n\n' in pending:
            raw, pending = pending.split(b'\n\n', 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('A2A frame exceeds bound')
            data = [line[5:].lstrip() for line in raw.split(b'\n') if line.startswith(b'data:')]
            if not data:
                continue
            count += 1
            if count > 16:
                raise ValueError('A2A frame count exceeds bound')
            value = decode_json(b'\n'.join(data).decode('utf-8'))
            if (not isinstance(value, dict) or set(value) != {'jsonrpc', 'id', 'result'}
                    or value['jsonrpc'] != '2.0' or type(value['id']) is not type(request_id)
                    or value['id'] != request_id or not isinstance(value['result'], dict)):
                raise ValueError('Unbound SSE response')
            yield value
        if len(pending) > MAX_BYTES:
            raise ValueError('A2A frame exceeds bound')
    raise TimeoutError('A2A stream deadline')
