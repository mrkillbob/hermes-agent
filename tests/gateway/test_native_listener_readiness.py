"""Native transport dependencies must finish loading before the listener is ready."""

import asyncio
import importlib
import sys
import threading
from types import SimpleNamespace

import pytest

from gateway import run_api
from tests.waiting import async_wait_until


@pytest.fixture
def native_listener(monkeypatch):
    loaded = threading.Event()
    stopped = asyncio.Event()

    class Listener:
        bound = False
        closed = False

        def bind(self, address):
            self.bound = True

        def setblocking(self, value):
            pass

        def getsockname(self):
            return ("127.0.0.1", 12345)

        def close(self):
            self.closed = True

    class Server:
        started = False
        _exit = False

        @property
        def should_exit(self):
            return self._exit

        @should_exit.setter
        def should_exit(self, value):
            self._exit = value
            if value:
                stopped.set()

        async def startup(self, sockets):
            assert loaded.is_set(), "listener became ready before the native handler loaded"
            self.started = True

        async def main_loop(self):
            await stopped.wait()

        async def shutdown(self, sockets):
            pass

    listener, server = Listener(), Server()
    config = SimpleNamespace(loaded=True, loaded_app=None, lifespan_class=lambda value: None)
    boundary = SimpleNamespace(_dashboard_public_hosts=lambda: (), listener_auth_required=lambda *args: False,
                               public_hosts_engage_gate=lambda hosts: False, public_url_hosts=lambda url: frozenset(),
                               should_require_auth=lambda host: False)
    lifecycle = SimpleNamespace(build_listener_server=lambda *args, **kwargs: (config, server))
    monkeypatch.delitem(sys.modules, "hermes_cli.web_server", raising=False)
    monkeypatch.setitem(sys.modules, "hermes_cli.web_server_boundary", boundary)
    monkeypatch.setitem(sys.modules, "hermes_cli.web_server_lifecycle", lifecycle)
    monkeypatch.setattr(run_api, "socket", SimpleNamespace(
        AF_INET6=run_api.socket.AF_INET6, SOCK_STREAM=1, getaddrinfo=lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 0))],
        socket=lambda *args: listener))
    monkeypatch.setattr(run_api, "_listener_socket", lambda *args, **kwargs: listener)
    monkeypatch.setattr(run_api, "_ACTIVE_RUNNER", None)
    return SimpleNamespace(loaded=loaded, listener=listener, server=server, runner=SimpleNamespace())


@pytest.mark.asyncio
async def test_native_handler_finishes_off_loop_before_listener_readiness(native_listener, monkeypatch):
    state = native_listener
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    original_import = importlib.import_module

    def load(name, *args, **kwargs):
        if name != "tui_gateway.ws":
            return original_import(name, *args, **kwargs)
        entered.set()
        assert threading.get_ident() != loop_thread, "native import blocked the owner loop"
        assert release.wait(5), "native import release was not delivered"
        state.loaded.set()
        return SimpleNamespace()

    monkeypatch.setattr(importlib, "import_module", load)
    boot = asyncio.create_task(run_api.start_gateway_api(state.runner))
    handle = None
    try:
        assert await async_wait_until(lambda: entered.is_set() or boot.done(), timeout=5)
        if boot.done():
            await boot  # Surface an early listener/startup failure instead of a wait symptom.
        assert entered.is_set() and not state.listener.bound and not state.server.started
        with pytest.raises(RuntimeError, match="gateway API already started"):
            await run_api.start_gateway_api(SimpleNamespace())
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), 5)
        assert not state.loaded.is_set()
        release.set()
        handle = await asyncio.wait_for(boot, 5)
        assert state.listener.bound and state.server.started
    finally:
        release.set()
        if handle is not None:
            await run_api.stop_gateway_api(handle)
        elif not boot.done():
            boot.cancel()
            await asyncio.gather(boot, return_exceptions=True)
    assert state.listener.closed and run_api._ACTIVE_RUNNER is None


@pytest.mark.asyncio
async def test_native_import_failure_closes_listener_and_preserves_cause(native_listener, monkeypatch):
    state = native_listener
    original = ImportError("synthetic native dependency failure")
    original_import = importlib.import_module

    def load(name, *args, **kwargs):
        if name == "tui_gateway.ws":
            raise original
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", load)
    with pytest.raises(ImportError) as caught:
        await run_api.start_gateway_api(state.runner)
    assert caught.value is original
    assert state.listener.closed and not state.listener.bound and not state.server.started
    assert run_api._ACTIVE_RUNNER is None
