"""Owner-scoped HTTP connection discovery, dead-pool recovery, and safe TCP shutdown."""

from __future__ import annotations

import contextlib
from typing import Any


def _ra():
    """Read the public facade lazily so its logger patch seam remains live."""
    import run_agent
    return run_agent


def _iter_httpx_pools_with_owner(http_client: Any):
    """Yield ``(pool, owner)`` pairs reachable from an httpx client, including mounted transports:
    keepalive and proxy configs put live connections on ``client._mounts``, which a
    ``_transport``-only walk misses.

    ``owner`` is ``None`` for a pool this client owns outright, or the ``_SharedTransport`` view
    id when the pool is process-shared with other clients
    (``process_bootstrap.build_keepalive_http_client``). Callers must then touch only the
    in-flight requests stamped with that owner.

    Walking the default transport alone makes ``force_close_tcp_sockets`` return 0 while a stream is still
    mid-recv — the interrupt logs success and the provider keeps burning the slot (#72975).
    """
    seen_pools: set[int] = set()
    try:
        transports = [getattr(http_client, "_transport", None)]
        transports += list((getattr(http_client, "_mounts", None) or {}).values())
        for transport in transports:
            if transport is None:
                continue
            # Connections live under ``_pool``; a directly mounted HTTPProxy *is* a ConnectionPool,
            # so ``_connections`` may sit on the transport itself.
            pool = getattr(transport, "_pool", None)
            if pool is None and getattr(transport, "_connections", None) is not None:
                pool = transport
            if pool is not None and id(pool) not in seen_pools:
                seen_pools.add(id(pool))
                owner = id(transport) if type(transport).__name__ == "_SharedTransport" else None
                yield pool, owner
    except Exception:
        return


def _iter_httpx_pool_objects(http_client: Any):
    """Yield httpcore pool objects reachable from an httpx client."""
    for pool, _owner in _iter_httpx_pools_with_owner(http_client):
        yield pool


def _connection_candidates(conn: Any):
    """Walk nested wrappers: proxy tunnels (``_connection``) plus httpx/httpcore
    stream envelopes (``_stream``/``_httpcore_stream``: BoundSyncStream →
    ResponseStream → connection byte stream → HTTP11/2 connection)."""
    seen: set[int] = set()
    stack = [conn]
    while stack:
        obj = stack.pop()
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        yield obj
        for attr in ("_connection", "_stream", "_httpcore_stream"):
            nxt = getattr(obj, attr, None)
            if nxt is not None:
                stack.append(nxt)


def _socket_from_candidate(candidate: Any):
    """Raw socket behind a connection/stream wrapper yielded by ``_connection_candidates``."""
    stream = getattr(candidate, "_network_stream", None) or getattr(candidate, "_stream", None)
    sock = _socket_from_stream(stream) if stream is not None else None
    return sock if sock is not None else _socket_from_stream(candidate)


def _socket_from_response(response: Any):
    """Raw socket behind an httpx response's network stream (``extensions["network_stream"]``
    first, then ``response.stream``), or None. Callers own their error handling."""
    exts = getattr(response, "extensions", None) or {}
    direct = exts.get("network_stream") if isinstance(exts, dict) else None
    for start in (direct, getattr(response, "stream", None)):
        if start is None:
            continue
        for candidate in _connection_candidates(start):
            sock = _socket_from_candidate(candidate)
            if sock is not None:
                return sock
    return None


def _socket_from_stream(stream: Any):
    """Raw socket behind an httpcore network stream (several backends), or None."""
    sock = getattr(stream, "_sock", None)
    if sock is None and callable(getattr(stream, "get_extra_info", None)):
        with contextlib.suppress(Exception):
            sock = stream.get_extra_info("socket")
    if sock is None:
        sock = getattr(getattr(stream, "stream", None), "_sock", None)
    if sock is None and callable(getattr(getattr(stream, "_stream", None), "extra", None)):
        # anyio-backed streams expose the raw socket through SocketAttribute.raw_socket.
        with contextlib.suppress(Exception):
            from anyio.abc import SocketAttribute
            sock = stream._stream.extra(SocketAttribute.raw_socket)
    return sock


def _iter_pool_sockets(client: Any):
    """Yield raw sockets reachable from an OpenAI/httpx client pool. Defensive over private
    httpcore internals (``conn._connection``, proxy tunnel wrappers) that vary by release; also
    walks mount transports and in-flight ``PoolRequest.connection`` objects (``_connections``
    is empty during checkout)."""
    try:
        # Some SDK wrappers *are* the httpx client; fall through so mount-aware discovery runs.
        http_client = getattr(client, "_client", None)
        pools = list(_iter_httpx_pools_with_owner(client if http_client is None else http_client))
    except Exception:
        return
    if not pools:
        return
    from agent.process_bootstrap import HERMES_TRANSPORT_OWNER_EXT
    seen: set[int] = set()
    for pool, owner in pools:
        # ``is None``, not falsiness: an empty ``_connections`` must still let us walk in-flight ``_requests``.
        raw_conns = getattr(pool, "_connections", None)
        if raw_conns is None:
            raw_conns = getattr(pool, "_pool", None)
        # A process-shared pool carries other clients' idle + in-flight connections: only this
        # client's own in-flight requests (stamped by ``_SharedTransport.handle_request``) may be
        # shut down.
        connections = [] if owner is not None else list(raw_conns or [])
        for pool_req in list(getattr(pool, "_requests", None) or []):
            if owner is not None:
                exts = getattr(getattr(pool_req, "request", None), "extensions", None) or {}
                if exts.get(HERMES_TRANSPORT_OWNER_EXT) != owner:
                    continue
            conn = getattr(pool_req, "connection", None)
            if conn is not None:
                connections.append(conn)
        for conn in connections:
            for candidate in _connection_candidates(conn):
                sock = _socket_from_candidate(candidate)
                if sock is not None and id(sock) not in seen:
                    seen.add(id(sock))
                    yield sock


def _socket_is_dead(sock) -> bool:
    """Probe socket health with a non-blocking recv peek."""
    import socket as _socket
    try:
        sock.setblocking(False)
        return sock.recv(1, _socket.MSG_PEEK | _socket.MSG_DONTWAIT) == b""
    except BlockingIOError:
        return False  # no data available: socket is healthy
    except OSError:
        return True
    finally:
        with contextlib.suppress(OSError):
            sock.setblocking(True)


def cleanup_dead_connections(agent) -> bool:
    """Force-close and rebuild the primary client if its pool has dead sockets (CLOSE-WAIT, errors); returns True if cleaned."""
    client = getattr(agent, "client", None)
    if client is None:
        return False
    try:
        dead_count = sum(1 for sock in _iter_pool_sockets(client) if _socket_is_dead(sock))
        if dead_count > 0:
            _ra().logger.warning("Found %d dead connection(s) in client pool — rebuilding client", dead_count)
            agent._replace_primary_openai_client(reason="dead_connection_cleanup")
            return True
    except Exception as exc:
        _ra().logger.debug("Dead connection check error: %s", exc)
    return False


def _shutdown_socket(sock: Any) -> None:
    """``shutdown(SHUT_RDWR)`` WITHOUT closing the FD. ``close()`` from a non-owner thread is
    unsafe: the SSL BIO caches the raw FD, the kernel recycles it, and a flushed TLS record lands
    in the wrong file (once clobbered a SQLite header). ``shutdown()`` is FD-safe from any thread.
    Already shut down / not connected / FD invalid are all benign."""
    import socket as _socket
    try:
        # Clear a blocking timeout so a hung SSL_read notices the shutdown. Still no close().
        settimeout = getattr(sock, "settimeout", None)
        if callable(settimeout):
            with contextlib.suppress(OSError):
                settimeout(0)
        sock.shutdown(_socket.SHUT_RDWR)
    except OSError:
        pass


def force_close_tcp_sockets(client: Any) -> int:
    """Abort in-flight TCP I/O on every pool socket via ``_shutdown_socket``. Returns the count
    (logged as ``tcp_force_closed=N``)."""
    shutdown_count = 0
    try:
        for sock in _iter_pool_sockets(client):
            _shutdown_socket(sock)
            shutdown_count += 1
    except Exception as exc:
        _ra().logger.debug("Force-close TCP sockets sweep error: %s", exc)
    return shutdown_count
