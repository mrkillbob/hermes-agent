"""Real native sockets distinguish event-loop wakeup IPC from network activity."""
import asyncio
from contextlib import contextmanager
import socket
import sys

import pytest

from tests.gateway.fixtures.startup_hold_peer import (
    _self_pipe_connect_allowed, _socketpair_endpoint_matches,
)

pytestmark = pytest.mark.platforms('any')


class ForbiddenConnect(Exception):
    pass


@contextmanager
def decisions():
    active = [True]
    observed = []

    def audit(event, args):
        if active[0] and event == 'socket.connect':
            permitted = _self_pipe_connect_allowed(args)
            observed.append(permitted)
            if not permitted:
                raise ForbiddenConnect('ordinary connection denied before its system call')

    sys.addaudithook(audit)
    try:
        yield observed
    finally:
        active[0] = False  # audit hooks live for this isolated test-file process


def test_real_asyncio_selfpipe_uses_only_its_exact_internal_connection(monkeypatch):
    # Exercise CPython's real portable fallback on this native host; no OS identity is mocked.
    monkeypatch.setattr(socket, 'socketpair', socket._fallback_socketpair)
    with decisions() as observed:
        loop = asyncio.SelectorEventLoop()
        try:
            assert observed == [True]
        finally:
            loop.close()


@pytest.mark.parametrize('method', ['connect', 'connect_ex'])
@pytest.mark.parametrize('address', [('127.0.0.1', 443), ('192.0.2.1', 443)])
def test_ordinary_loopback_and_remote_connects_remain_forbidden(method, address):
    with socket.socket() as client, decisions() as observed:
        with pytest.raises(ForbiddenConnect):
            getattr(client, method)(address)
        assert observed == [False]


def test_direct_fallback_is_not_an_asyncio_selfpipe():
    with decisions() as observed:
        with pytest.raises(ForbiddenConnect):
            socket._fallback_socketpair()
        assert observed == [False]


def test_matching_caller_name_cannot_substitute_for_stdlib_code_identity():
    def _make_self_pipe():
        return socket._fallback_socketpair()

    with decisions() as observed:
        with pytest.raises(ForbiddenConnect):
            _make_self_pipe()
        assert observed == [False]


@pytest.mark.parametrize('damage', ['none', 'socket', 'address', 'port'])
def test_endpoint_proof_uses_real_live_socket_identity_and_exact_bound_address(damage):
    with socket.socket() as client, socket.socket() as listener, socket.socket() as other:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        bound = listener.getsockname()
        candidate, address = {
            'none': (client, bound),
            'socket': (other, bound),
            'address': (client, ('127.0.0.2', bound[1])),
            'port': (client, (bound[0], 1 if bound[1] == 65535 else bound[1] + 1)),
        }[damage]
        values = {'csock': client, 'lsock': listener}
        assert _socketpair_endpoint_matches((candidate, address), values) is (damage == 'none')


def test_closed_listener_cannot_prove_an_endpoint():
    with socket.socket() as client, socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        address = listener.getsockname()
        listener.close()
        assert not _socketpair_endpoint_matches((client, address), {'csock': client, 'lsock': listener})


def test_datagram_cannot_substitute_for_a_stream_listener():
    with socket.socket() as client, socket.socket(type=socket.SOCK_DGRAM) as listener:
        assert not _socketpair_endpoint_matches((client, ('127.0.0.1', 443)),
                                                {'csock': client, 'lsock': listener})
