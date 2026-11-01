"""Darwin liveness observes synthetic states without spawning a secondary ps."""
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.platforms("macos")


@pytest.mark.parametrize('state,exists,expected', [
    ('zombie', True, False), ('sleeping', True, True),
    ('sleeping', False, False), ('unreadable', True, True),
    ('unreadable', False, False), ('gone', True, False),
])
def test_darwin_status_never_spawns(monkeypatch, state, exists, expected):
    from gateway import status
    from hermes_cli import kanban_db_dispatch as dispatch

    class Gone(Exception):
        pass

    def read_status():
        if state == 'gone':
            raise Gone()
        if state == 'unreadable':
            raise PermissionError('fixture')
        return state

    fake = SimpleNamespace(Process=lambda _: SimpleNamespace(status=read_status),
                           STATUS_ZOMBIE='zombie', NoSuchProcess=Gone,
                           pid_exists=lambda _: exists)
    monkeypatch.setitem(sys.modules, 'psutil', fake)
    monkeypatch.setattr(dispatch.sys, 'platform', 'darwin')
    monkeypatch.setattr(dispatch.subprocess, 'run', lambda *a, **k: pytest.fail('spawn attempted'))
    monkeypatch.setattr(status, 'pid_exists_stdlib', lambda _: pytest.fail('unexpected native fallback'))
    assert dispatch._pid_alive(123456789) is expected


@pytest.mark.parametrize('exists', [False, True])
def test_missing_psutil_keeps_conservative_native_answer(monkeypatch, exists):
    from gateway import status
    from hermes_cli import kanban_db_dispatch as dispatch
    monkeypatch.setitem(sys.modules, 'psutil', None)
    monkeypatch.setattr(dispatch.sys, 'platform', 'darwin')
    monkeypatch.setattr(status, 'pid_exists_stdlib', lambda _: exists)
    monkeypatch.setattr(dispatch.subprocess, 'run', lambda *a, **k: pytest.fail('spawn attempted'))
    assert dispatch._pid_alive(123456789) is exists
