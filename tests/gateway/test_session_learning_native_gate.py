"""Native gate adapter refusal tests with explicit synthetic transport registrations.

These construct an in-process connection fixture, NOT a redeemed socket/ticket.
They qualify field/registration refusal logic only, not transport authentication,
atomic close, source adoption or a production issuer.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.session_contract import Principal, SessionRef
from gateway.session_controls import AuthorityConnection
from gateway.session_learning import LearningBroker
from hermes_state_runtime import RuntimeStoreError


@pytest.fixture
def registered():
    actor = Principal('auth:v1:local:user-a', '/fixture/profile',
                      frozenset({'session:read', 'session:submit', 'session:approve'}), 'transport-a')
    transport = object()
    live = SimpleNamespace(source=SimpleNamespace(platform=Platform.LOCAL, user_id=actor.subject),
                           subscribers={'sub-a': actor})
    held = [False]
    def admission_open(**kwargs):
        if held[0]:
            raise RuntimeStoreError('runtime_draining')
    authority = SimpleNamespace(profile_id=actor.profile_id, instance_id='instance-a',
                                sessions={'logical-a': live}, retired=False,
                                _native_legacy_transports={actor.transport_id: actor},
                                events={actor.transport_id: transport},
                                _require_admission_open=admission_open)
    # Explicit synthetic fixture: no claim that __new__ authenticates a transport.
    connection = AuthorityConnection.__new__(AuthorityConnection)
    connection.authority = authority
    connection.actor = actor
    connection.transport = transport
    connection.native_owner = True
    connection._identity = {'provider': 'local', 'instance_id': 'instance-a',
                            'profile_id': actor.profile_id}
    return SimpleNamespace(broker=LearningBroker(authority), authority=authority,
                           connection=connection, live=live, held=held,
                           ref=SessionRef(actor.profile_id, 'logical-a'))


def test_exact_registered_transport_and_current_user_are_required(registered):
    assert registered.broker._native_live(registered.connection, registered.ref) is registered.live
    registered.live.source.user_id = 'auth:v1:local:user-b'
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        registered.broker._native_live(registered.connection, registered.ref)


@pytest.mark.parametrize('mutation', [
    'native-flag', 'registration', 'equal-principal-copy', 'transport', 'subscriber',
    'profile', 'instance', 'api', 'capability', 'missing-live', 'retired',
])
def test_stale_or_wrong_scope_connection_refuses_without_restoring(registered, mutation):
    c, a = registered.connection, registered.authority
    actions = {
        'native-flag': lambda: setattr(c, 'native_owner', False),
        'registration': a._native_legacy_transports.clear,
        'equal-principal-copy': lambda: a._native_legacy_transports.update(
            {c.actor.transport_id: replace(c.actor)}),
        'transport': lambda: a.events.update({c.actor.transport_id: object()}),
        'subscriber': registered.live.subscribers.clear,
        'profile': lambda: c._identity.update(profile_id='/other/profile'),
        'instance': lambda: c._identity.update(instance_id='old-instance'),
        'api': lambda: c._identity.update(provider='api'),
        'capability': lambda: setattr(c, 'actor', replace(c.actor, capabilities=frozenset())),
        'missing-live': a.sessions.clear,
        'retired': lambda: setattr(a, 'retired', True),
    }
    actions[mutation]()
    # The fixture deliberately has no restore/create/catalog API to fall back to.
    with pytest.raises(RuntimeStoreError):
        registered.broker._native_live(c, registered.ref)


def test_hold_blocks_admission_but_does_not_block_authenticated_revocation(registered):
    registered.held[0] = True
    with pytest.raises(RuntimeStoreError, match='runtime_draining'):
        registered.broker._native_live(registered.connection, registered.ref)
    assert registered.broker._native_live(
        registered.connection, registered.ref, admission=False) is registered.live


def test_arbitrary_request_object_cannot_substitute_for_registered_connection(registered):
    forged = SimpleNamespace(**vars(registered.connection))
    with pytest.raises(RuntimeStoreError):
        registered.broker._native_live(forged, registered.ref)
