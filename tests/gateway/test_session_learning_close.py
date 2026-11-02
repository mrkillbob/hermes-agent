"""Actual close/reader lifecycle with synthetic native registrations, never a live socket."""
import asyncio
from contextlib import suppress
from types import SimpleNamespace
from threading import Event

import pytest

from agent.learning_vault import ExactVaultNoteRequest, VaultLearningNode
from gateway.config import Platform
from gateway.session_contract import SessionRef
from gateway.session_controls import AuthorityConnection
from gateway.session_learning import LearningBroker
from hermes_state_runtime import RuntimeStoreError


@pytest.fixture
def connections(tmp_path, monkeypatch):
    # The real constructor creates Principal and registers the exact transport;
    # the synthetic identity is not proof of a redeemed native ticket.
    authority = SimpleNamespace(profile_id=str(tmp_path / 'profile'), instance_id='instance', epoch=1,
        sessions={}, events={}, retired=False,
        runner=SimpleNamespace(config=SimpleNamespace(multiplex_profiles=False)),
        _require_admission_open=lambda **kwargs: None)
    made = []
    for index in range(3):
        connection = AuthorityConnection(authority, object(), {
            'provider': 'local', 'user_id': 'synthetic-user', 'native_bootstrap': True,
            'profile_id': authority.profile_id, 'instance_id': authority.instance_id})
        sid, sub = f'session-{index}', f'subscription-{index}'
        live = SimpleNamespace(source=SimpleNamespace(platform=Platform.LOCAL,
            user_id=connection.actor.subject), subscribers={sub: connection.actor})
        authority.sessions[sid] = live
        connection.subscriptions[sid] = sub
        broker = LearningBroker(authority)
        monkeypatch.setattr(broker, '_source_sha', lambda: 'c' * 40)
        made.append(SimpleNamespace(connection=connection, broker=broker, live=live,
                                   ref=SessionRef(authority.profile_id, sid)))
    made[0].connection._siblings = {'first': made[1].connection, 'later': made[2].connection}
    authority._handle = lambda ref: SimpleNamespace(execution_state='idle', revision=1,
                                                   execution_generation=0)
    authority._pending_rows = lambda ref: []
    authority.logical_owner = lambda sid: sid
    authority.physical_target = lambda ref: ref.session_id
    return authority, made


def select(item):
    exact = ExactVaultNoteRequest('/synthetic/vault', 1, 2,
        'Memories/Codex/reference.md', 'alr-synthetic', 'a' * 64)
    decision = item.broker.select(item.connection, item.ref, request_id='request', exact=exact)
    item.broker.decide(item.connection, decision_id=decision, request_id='request', choice='once')
    return decision


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['finish', 'cancel', 'error'])
async def test_tree_refuses_capture_before_first_teardown_await(connections, monkeypatch, outcome):
    authority, made = connections
    decisions = [select(item) for item in made]
    entered, release = asyncio.Event(), asyncio.Event()
    detached, reads = [], []

    async def detach(actor, subscription, **kwargs):
        detached.append(subscription)
        if subscription == 'subscription-1':
            entered.set()
            await release.wait()
            if outcome == 'error':
                raise RuntimeError('synthetic teardown failure')
        for live in authority.sessions.values():
            live.subscribers.pop(subscription, None)

    authority.detach = detach
    monkeypatch.setattr('gateway.session_learning.read_exact_vault_learning',
                        lambda *args, **kwargs: reads.append(args))
    closing = asyncio.create_task(made[0].connection.close())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        # These registrations have not yet been removed. Closure itself must fence
        # the parent and the not-yet-visited sibling, including retained decisions.
        for item, decision in zip(made, decisions):
            assert authority.events[item.connection.actor.transport_id] is item.connection.transport
            with pytest.raises(RuntimeStoreError, match='permission_denied'):
                item.broker.capture(item.connection, decision_id=decision, request_id='request')
        assert reads == []
        assert detached == ['subscription-1']
    finally:
        if outcome == 'cancel':
            closing.cancel()
        release.set()
        with suppress(asyncio.CancelledError, RuntimeError):
            await closing
    if outcome == 'finish':
        assert set(detached) == {'subscription-0', 'subscription-1', 'subscription-2'}
        assert authority.events == {}
    else:
        # Failed/cancelled cleanup must never reopen the native read gate.
        for item in made:
            with pytest.raises(RuntimeStoreError, match='permission_denied'):
                item.broker._native_live(item.connection, item.ref)


@pytest.mark.asyncio
async def test_close_during_capture_cannot_return_a_captured_reference(connections, monkeypatch):
    authority, made = connections
    item = made[0]
    decision = select(item)
    reading, release_read = Event(), Event()
    detaching, release_detach = asyncio.Event(), asyncio.Event()
    node = VaultLearningNode('alr-synthetic', 'Synthetic', 'shared-memory', 'codex', 'General',
                            'verified', None, (), 'inert synthetic reference', 'not-applicable')

    def read(exact, *, authorize):
        assert authorize(exact) is True
        reading.set()
        assert release_read.wait(timeout=5)
        return node, None

    async def detach(*args, **kwargs):
        detaching.set()
        await release_detach.wait()

    authority.detach = detach
    monkeypatch.setattr('gateway.session_learning.read_exact_vault_learning', read)
    capture = asyncio.create_task(asyncio.to_thread(item.broker.capture, item.connection,
                                 decision_id=decision, request_id='request'))
    closing = None
    try:
        assert await asyncio.to_thread(reading.wait, 5)
        closing = asyncio.create_task(item.connection.close())
        await asyncio.wait_for(detaching.wait(), timeout=5)
        release_read.set()
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            await capture
        assert item.broker._decisions[decision].reference is None
    finally:
        release_read.set()
        release_detach.set()
        with suppress(RuntimeStoreError):
            await capture
        if closing is not None:
            await closing


@pytest.mark.asyncio
@pytest.mark.parametrize('replaced', [False, True])
async def test_closing_host_cannot_publish_a_fresh_sibling(connections, monkeypatch, tmp_path, replaced):
    from gateway.session_authorities import SessionAuthorities
    authority, made = connections
    root = made[0].connection
    root._identity['profile_scope'] = 'host'
    home = tmp_path / 'late'
    registry = SessionAuthorities(authority.profile_id)
    registry.add(authority.profile_id, authority)
    authority.runner.session_authorities = registry
    target = SimpleNamespace(profile_id=str(home), instance_id='later-instance',
        events={}, sessions={}, retired=False, runner=authority.runner)
    registry.add(home, target, name='late')
    monkeypatch.setattr('hermes_cli.profiles.get_profile_dir',
                        lambda name: home if name == 'late' else authority.profile_id)
    if replaced:
        previous = root._sibling_for('late')
        registry.remove(home)
        target = SimpleNamespace(profile_id=str(home), instance_id='replacement-instance',
            events={}, sessions={}, retired=False, runner=authority.runner)
        registry.add(home, target, name='late')
        assert previous.authority is not target
    entered, release = asyncio.Event(), asyncio.Event()

    async def detach(*args, **kwargs):
        entered.set()
        await release.wait()

    authority.detach = detach
    closing = asyncio.create_task(root.close())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            root._sibling_for('late')
        assert target.events == {}
    finally:
        release.set()
        await closing
