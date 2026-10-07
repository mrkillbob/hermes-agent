"""Isolated server, stdout and frame-capture fixtures for gateway protocol tests."""

import io
import sys
from unittest.mock import MagicMock, patch

import pytest

_original_stdout = sys.stdout


@pytest.fixture(autouse=True)
def _restore_stdout():
    yield
    sys.stdout = _original_stdout


@pytest.fixture()
def server():
    # The sys.modules mocks only need to cover the *initial* import — once
    # tui_gateway.server is cached, they are inert. Keeping them active for
    # the whole test poisons any module first imported inside a test body:
    # e.g. hermes_cli.active_sessions would bind the mocked get_hermes_home
    # (a fixed shared path) forever, leaking active-session registry entries
    # across every later test in the process. Scope the patch to the import.
    #
    # Import server_requests (pure stdlib) and transport BEFORE the window: the patch drops every module first
    # imported inside it, so otherwise the module server.py binds its sinks on (write/emit/answerable) would vanish
    # from sys.modules and a test's own ``from tui_gateway import server_requests`` would get a fresh,
    # unbound copy whose default sinks drop frames and treat every client as answerable. Likewise a test's
    # ``from tui_gateway.transport import bind_transport`` would bind a fresh module's ContextVar that the
    # server's ``current_transport()`` never reads (first-in-process test sees ``_stdio_transport`` as caller).
    import tui_gateway.server_requests  # noqa: F401
    import tui_gateway.transport  # noqa: F401
    with patch.dict("sys.modules", {
        "hermes_constants": MagicMock(get_hermes_home=MagicMock(return_value="/tmp/hermes_test")),
        "hermes_cli.env_loader": MagicMock(),
        "hermes_cli.banner": MagicMock(),
        "hermes_state": MagicMock(),
    }):
        import importlib
        mod = importlib.import_module("tui_gateway.server")

    # Snapshot the RPC registry: several tests below stub handlers
    # ("slash.exec", "fast.ping", ...) directly in the module-level dict,
    # which is shared with every other test file in the process.
    methods = dict(mod._methods)
    real_stdout = mod._real_stdout
    yield mod
    # Reset module-level state without re-importing. importlib.reload
    # would re-register the module's atexit hooks (ThreadPoolExecutor
    # shutdown, _shutdown_sessions); the duplicates race the stderr
    # buffer at interpreter shutdown and surface as Fatal Python error:
    # _enter_buffered_busy. Restoring the dicts in place gives the next
    # test a clean slate.
    mod._methods.clear()
    mod._methods.update(methods)
    mod._real_stdout = real_stdout
    for sid in list(mod._sessions):
        mod._close_session_by_id(sid, end_reason="test_cleanup")
    from tui_gateway import server_requests
    server_requests.reset_for_tests()
    mod._live_transports.clear()


@pytest.fixture()
def capture(server):
    """Redirect server's real stdout to a StringIO and return (server, buf)."""
    buf = io.StringIO()
    server._real_stdout = buf
    return server, buf
