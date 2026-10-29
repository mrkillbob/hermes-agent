"""One-use state tests; synthetic host selection is NOT native transport qualification.

The real host/fence adapter is tested separately. These tests deliberately isolate
the decision machine and exact-reader call, not the native authentication gate.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.learning_vault import ExactVaultNoteRequest, VaultLearningDiagnostic, VaultLearningNode
from gateway.session_contract import SessionRef
from gateway.session_learning import HostFence, LearningBroker
from hermes_state_runtime import RuntimeStoreError


@pytest.fixture
def state(monkeypatch):
    import gateway.session_learning as module
    clock = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    live, connection = object(), object()
    fence = HostFence('user-a', 'transport-a', '/fixture/profile', 'instance-a', 'c' * 40,
                      7, 'logical-a', 'physical-a', 3, 9)
    broker = LearningBroker(object())
    # State-machine isolation only; no authentication claim is made by this stub.
    monkeypatch.setattr(broker, '_fence', lambda conn, ref: (live, fence))
    monkeypatch.setattr(broker, '_native_live', lambda conn, ref, **kwargs: live)
    exact = ExactVaultNoteRequest('/synthetic/vault', 1, 2, 'Memories/Codex/one.md',
                                  'alr-fixture', 'a' * 64)
    ref = SessionRef('/fixture/profile', 'logical-a')
    identifier = broker.select(connection, ref, request_id='request-a', exact=exact)
    calls = []
    node = VaultLearningNode('alr-fixture', 'Title', 'shared-memory', 'codex', 'General',
                             'verified', None, (), 'inert diagnostic', 'not-applicable')
    def read(request, *, authorize):
        assert authorize(replace(request)) is True
        calls.append(request)
        return node, None
    monkeypatch.setattr(module, 'read_exact_vault_learning', read)
    return SimpleNamespace(broker=broker, connection=connection, identifier=identifier,
                           exact=exact, ref=ref, clock=clock, calls=calls, node=node,
                           module=module, live=live, fence=fence)


def decide(state, choice='once'):
    return state.broker.decide(state.connection, decision_id=state.identifier,
                              request_id='request-a', choice=choice)


def capture(state, **overrides):
    params = {'decision_id': state.identifier, 'request_id': 'request-a', **overrides}
    return state.broker.capture(state.connection, **params)


def test_host_selection_needs_explicit_decision_before_one_capture(state):
    with pytest.raises(RuntimeStoreError):
        capture(state)
    assert state.calls == []
    assert decide(state)['status'] == 'approved'
    receipt = capture(state)
    assert receipt['record_id'] == state.exact.expected_record_id
    assert receipt['raw_sha256'] == state.exact.expected_sha256
    assert receipt['session_delivery'] is False
    assert receipt['session_acknowledged'] is False
    assert receipt['codex_conversation_binding'] == 'unavailable'
    assert receipt['activity_authority'] == 'unavailable'
    assert len(state.calls) == 1
    with pytest.raises(RuntimeStoreError):
        capture(state)
    assert len(state.calls) == 1


@pytest.mark.parametrize('mutation', ['expired', 'deny', 'wrong-request', 'wrong-user'])
def test_refusal_does_not_call_reader(state, mutation):
    decide(state, 'deny' if mutation == 'deny' else 'once')
    params = {}
    if mutation == 'expired':
        state.clock[0] = 160.0
    elif mutation == 'wrong-request':
        params['request_id'] = 'another-request'
    elif mutation == 'wrong-user':
        state.connection = object()
    with pytest.raises(RuntimeStoreError):
        capture(state, **params)
    assert state.calls == []


@pytest.mark.parametrize('field,value', [
    ('source_sha', 'd' * 40), ('authority_epoch', 8), ('physical_target', 'compressed-b'),
    ('principal_id', 'user-b'), ('revision', 4), ('selection_generation', 10),
])
def test_owner_lineage_or_generation_move_refuses_before_capture(state, monkeypatch, field, value):
    decide(state)
    moved = replace(state.fence, **{field: value})
    monkeypatch.setattr(state.broker, '_fence', lambda conn, ref: (state.live, moved))
    with pytest.raises(RuntimeStoreError, match='stale_generation'):
        capture(state)
    assert state.calls == []


def test_live_object_replacement_refuses_even_with_equal_fields(state, monkeypatch):
    decide(state)
    monkeypatch.setattr(state.broker, '_fence', lambda conn, ref: (object(), state.fence))
    with pytest.raises(RuntimeStoreError):
        capture(state)
    assert state.calls == []


def test_reader_refusal_is_one_consumed_attempt(state, monkeypatch):
    decide(state)
    def refused(request, *, authorize):
        state.calls.append(request)
        return None, VaultLearningDiagnostic('note_hash_mismatch')
    monkeypatch.setattr(state.module, 'read_exact_vault_learning', refused)
    with pytest.raises(RuntimeStoreError, match='reference_refused'):
        capture(state)
    with pytest.raises(RuntimeStoreError):
        capture(state)
    assert len(state.calls) == 1


def test_expiry_during_capture_never_returns_success(state, monkeypatch):
    decide(state)
    def expiring(request, *, authorize):
        assert authorize(request)
        state.clock[0] = 161.0
        state.calls.append(request)
        return state.node, None
    monkeypatch.setattr(state.module, 'read_exact_vault_learning', expiring)
    with pytest.raises(RuntimeStoreError):
        capture(state)
    assert state.broker._decisions[state.identifier].status == 'expired'


def test_original_request_mutation_does_not_change_private_selection(state):
    object.__setattr__(state.exact, 'relative_path', 'Memories/Codex/changed.md')
    decide(state)
    capture(state)
    assert state.calls[0].relative_path == 'Memories/Codex/one.md'


def test_revoke_prevents_capture_without_deleting_original(state):
    decide(state)
    state.broker.revoke(state.connection, decision_id=state.identifier, request_id='request-a')
    with pytest.raises(RuntimeStoreError):
        capture(state)
    assert state.calls == []
    assert state.identifier in state.broker._decisions


def test_unknown_outcome_never_retries_read(state, monkeypatch):
    decide(state)
    def uncertain(request, *, authorize):
        state.calls.append(request)
        raise OSError('synthetic fixture failure')
    monkeypatch.setattr(state.module, 'read_exact_vault_learning', uncertain)
    with pytest.raises(RuntimeStoreError, match='reference_unavailable') as failure:
        capture(state)
    assert 'synthetic fixture failure' not in str(failure.value)
    with pytest.raises(RuntimeStoreError):
        capture(state)
    assert state.broker._decisions[state.identifier].status == 'uncertain'
    assert len(state.calls) == 1


def test_expiry_during_canonicalization_cannot_commit_capture(state, monkeypatch):
    decide(state)
    original = state.module.CapturedReference.from_node
    def expiring(*args, **kwargs):
        value = original(*args, **kwargs)
        state.clock[0] = 161.0
        return value
    monkeypatch.setattr(state.module.CapturedReference, 'from_node', expiring)
    with pytest.raises(RuntimeStoreError):
        capture(state)
    assert state.broker._decisions[state.identifier].reference is None


def test_expired_registry_entries_release_capacity_without_revalidating_old_id(state):
    identifiers = [state.identifier]
    for index in range(63):
        identifiers.append(state.broker.select(state.connection, state.ref,
                           request_id=f'request-{index}', exact=state.exact))
    with pytest.raises(RuntimeStoreError, match='session_busy'):
        state.broker.select(state.connection, state.ref, request_id='full', exact=state.exact)
    state.clock[0] = 161.0
    new_id = state.broker.select(state.connection, state.ref, request_id='fresh', exact=state.exact)
    assert new_id not in identifiers
    assert len(state.broker._decisions) == 1
    with pytest.raises(RuntimeStoreError):
        capture(state)


def test_revoke_authentication_is_separate_from_admission_open(state, monkeypatch):
    decide(state)
    calls = []
    def native(conn, ref, *, admission=True):
        calls.append(admission)
        if admission:
            raise RuntimeStoreError('runtime_draining')
        return state.live
    monkeypatch.setattr(state.broker, '_native_live', native)
    state.broker.revoke(state.connection, decision_id=state.identifier, request_id='request-a')
    assert calls == [False]
    assert state.broker._decisions[state.identifier].status == 'revoked'
