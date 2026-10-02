"""Concurrent readers must not observe an incompletely imported MCP transport."""

import threading

from tools import mcp_tool


def test_sdk_readiness_waits_for_transport_import(monkeypatch):
    published_session = threading.Event()
    finish_import = threading.Event()
    reader_waiting = threading.Event()
    real_lock = threading.Lock()
    results = {}
    errors = []
    transport = object()

    class ImportLock:
        def locked(self):
            return real_lock.locked()

        def __enter__(self):
            if threading.current_thread().name == "sdk-reader":
                reader_waiting.set()
            real_lock.acquire()

        def __exit__(self, *_args):
            real_lock.release()

    def import_names(module, names, *_args):
        for name in names:
            monkeypatch.setitem(
                vars(mcp_tool), name, transport if name == "stdio_client" else object(),
            )
        if module == "mcp":
            published_session.set()
            if not finish_import.wait(6):
                raise RuntimeError("test did not release SDK import")
        return True

    monkeypatch.delattr(mcp_tool, "stdio_client", raising=False)
    monkeypatch.setattr(mcp_tool, "ClientSession", None)
    monkeypatch.setattr(mcp_tool, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(mcp_tool, "_MCP_SDK_IMPORT_ATTEMPTED", False)
    monkeypatch.setattr(mcp_tool, "_MCP_SDK_IMPORT_LOCK", ImportLock())
    monkeypatch.setattr(mcp_tool, "_import_sdk_names", import_names)
    monkeypatch.setattr(mcp_tool, "_client_session_accepts", lambda _name: False)

    def load(label):
        try:
            results[label] = (
                mcp_tool._ensure_mcp_sdk(),
                vars(mcp_tool).get("stdio_client") is transport,
            )
        except BaseException as exc:
            errors.append(exc)

    publisher = threading.Thread(target=load, args=("publisher",), name="sdk-publisher", daemon=True)
    reader = threading.Thread(target=load, args=("reader",), name="sdk-reader", daemon=True)
    publisher.start()
    try:
        assert published_session.wait(3), "publisher never reached the SDK transport import"
        reader.start()
        assert reader_waiting.wait(3), (
            f"reader bypassed the in-progress SDK import: {results.get('reader')}"
        )
    finally:
        finish_import.set()
        publisher.join(timeout=3)
        if reader.ident is not None:
            reader.join(timeout=3)
    assert not publisher.is_alive() and not reader.is_alive()
    assert not errors
    assert results == {"publisher": (True, True), "reader": (True, True)}
