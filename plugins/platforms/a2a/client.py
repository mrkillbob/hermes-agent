"""Shared stdlib A2A JSON transport; importing this module opens no connection."""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request

from . import protocol

MAX_BYTES = 49152  # Leave room for the binding/envelope in a 64 KiB Kanban event.


def validate_url(url):
    if not isinstance(url, str):
        raise ValueError('Peer URL must be text')
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError('Explicit HTTP(S) peer URL without userinfo or fragment required')
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('A2A endpoint-changing redirects are not permitted')


def _bounded_read(response, limit, deadline):
    result = bytearray()
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError('A2A response deadline')
        chunk = response.read1(min(8192, limit + 1 - len(result)))
        if time.monotonic() >= deadline:
            raise TimeoutError('A2A response deadline')
        if not chunk:
            return bytes(result)
        result.extend(chunk)
        if len(result) > limit:
            raise ValueError('A2A response exceeds bound')


def _invalid_constant(value):
    raise ValueError('Nonfinite JSON number')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON member')
        result[key] = value
    return result


def decode_json(raw):
    return json.loads(raw, parse_constant=_invalid_constant, object_pairs_hook=_unique_object)


def http_json(url, headers, timeout, method, data=None, *, max_bytes=None, opener=None):
    """Legacy tools keep their existing behavior; bounded callers opt in explicitly.

    Budgets are cooperative around blocking OS I/O; no hard real-time guarantee.
    """
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    deadline = time.monotonic() + timeout
    with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
        if max_bytes is None:
            return json.loads(response.read().decode('utf-8'))
        if response.geturl() != url:
            raise ValueError('Peer response changed endpoint')
        raw = _bounded_read(response, max_bytes, deadline)
        return decode_json(raw.decode('utf-8'))


class PeerRPCError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('A2A peer returned a protocol error')


class JSONRPCClient:
    def __init__(self, url, *, version=protocol.PROTOCOL_VERSION, headers=None, opener=None):
        self.url = validate_url(url)
        if version != protocol.PROTOCOL_VERSION:
            raise ValueError('Unqualified A2A version')
        self.version = version
        self._headers = dict(headers or {})
        self._opener = opener or urllib.request.build_opener(NoRedirect()).open

    def rpc(self, method, params, request_id, *, timeout=2.0):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 2:
            raise ValueError('Finite request budget at most two seconds required')
        body = {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}
        data = json.dumps(body, sort_keys=True, allow_nan=False).encode()
        if len(data) > MAX_BYTES:
            raise ValueError('A2A request exceeds bound')
        headers = {**self._headers, 'Content-Type': 'application/json', 'A2A-Version': self.version}
        response = http_json(self.url, headers, timeout, 'POST', data, max_bytes=MAX_BYTES, opener=self._opener)
        if (not isinstance(response, dict) or response.get('jsonrpc') != '2.0' or type(response.get('id')) is not type(request_id) or response.get('id') != request_id
                or ('error' in response) == ('result' in response)):
            raise ValueError('Unbound JSON-RPC response')
        if 'error' in response:
            error = response['error']
            raise PeerRPCError(error.get('code') if isinstance(error, dict) else None)
        return response
