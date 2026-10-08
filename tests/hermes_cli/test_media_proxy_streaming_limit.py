"""Fork-only regression (see FORK_PATCHES.md): /api/media/proxy enforces its size cap while
streaming, never buffering an oversized upstream body."""

import httpx
from fastapi.testclient import TestClient

import hermes_cli.web_routers.files as files_router


def test_oversized_body_stops_reading_at_the_cap(monkeypatch):
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    read = []

    class _Resp:
        status_code = 200
        headers = {"content-type": "image/png"}

        async def aiter_bytes(self):
            for _ in range(1000):
                read.append(1)
                yield b"0" * files_router._MEDIA_MAX_BYTES

    class _Stream:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *a):
            return False

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url):
            return _Stream()

    monkeypatch.setattr(files_router, "_require_token", lambda request: None, raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", _Client, raising=False)

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    resp = client.get("/api/media/proxy", params={"url": "https://v3.fal.media/media/abc123"})

    assert resp.status_code == 413
    assert len(read) <= 2
