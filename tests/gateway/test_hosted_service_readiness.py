"""Prepared hosted services must not re-read profile scopes on every watcher tick."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from gateway import session_hosted_service as hosted


class Service:
    def __init__(self, installed=True):
        self._transport_installed = installed
        self.starts = 0

    def start(self):
        self.starts += 1


def bind_authorities(monkeypatch, authorities):
    monkeypatch.setattr(hosted, 'active_authority', lambda runner: authorities[0])
    monkeypatch.setattr(hosted, 'all_authorities', lambda runner: authorities)
    monkeypatch.setattr(hosted, 'serving_authorities', lambda runner: authorities)


@pytest.mark.asyncio
async def test_prepared_watcher_keeps_readiness_without_entering_profile_scope(monkeypatch):
    authorities = [SimpleNamespace(hosted_room_service=Service()) for _ in range(3)]
    bind_authorities(monkeypatch, authorities)

    def forbidden_scope(authority):
        raise AssertionError('prepared watcher must not load profile credentials')

    monkeypatch.setattr(hosted, 'owner_scope', forbidden_scope)
    runner = SimpleNamespace(session_runtime_descriptor={'state': 'starting'}, _draining=False)
    for _ in range(2):
        assert await hosted.ensure_hosted_service(runner) is authorities[0].hosted_room_service
    assert all(a.hosted_room_service.starts == 0 for a in authorities)
    runner.session_runtime_descriptor['state'] = 'ready'
    await hosted.ensure_hosted_service(runner)
    assert all(a.hosted_room_service.starts == 1 for a in authorities)
    runner._draining = True
    await hosted.ensure_hosted_service(runner)
    assert all(a.hosted_room_service.starts == 1 for a in authorities)


@pytest.mark.asyncio
async def test_late_authority_and_missing_transport_prepare_in_exact_owner_scope(monkeypatch):
    from gateway import session_hosted_transport

    ready = SimpleNamespace(profile_id='ready', hosted_room_service=Service())
    missing = SimpleNamespace(profile_id='missing')
    transport_only = SimpleNamespace(profile_id='transport', hosted_room_service=Service(False))
    authorities = [ready, missing, transport_only]
    bind_authorities(monkeypatch, authorities)
    current = []
    entries = []

    @contextmanager
    def scope(authority):
        entries.append(authority.profile_id)
        current.append(authority)
        try:
            yield
        finally:
            current.pop()

    def create(authority, loop):
        assert current == [authority]
        service = Service(False)
        service.attest = object()
        return service

    transport_only.hosted_room_service.attest = object()
    installs = []

    def install(server, authority, loop, *, attest):
        assert current == [authority]
        assert attest is authority.hosted_room_service.attest
        installs.append(authority.profile_id)

    monkeypatch.setattr(hosted, 'owner_scope', scope)
    monkeypatch.setattr(hosted, 'CanonicalHostedRoomService', create)
    monkeypatch.setattr(session_hosted_transport, 'install_hosted_transport', install)
    runner = SimpleNamespace(session_runtime_descriptor={'state': 'ready'}, _draining=False,
                             session_control_server=object())
    await hosted.ensure_hosted_service(runner)
    assert entries == ['missing', 'transport']
    assert installs == ['missing', 'transport']
    assert all(a.hosted_room_service.starts == 1 for a in authorities)
    await hosted.ensure_hosted_service(runner)
    assert entries == ['missing', 'transport']
    assert installs == ['missing', 'transport']
    assert all(a.hosted_room_service.starts == 2 for a in authorities)
