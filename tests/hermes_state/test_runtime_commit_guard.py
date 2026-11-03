"""Real admission/claim/settlement rollback; synthetic guards are not native authority."""
from contextlib import contextmanager

import pytest

from hermes_state import SessionDB
import hermes_state_runtime as rt


@pytest.fixture
def state(tmp_path):
    db = SessionDB(tmp_path / 'state.db')
    db.create_session('session', source='test')
    epoch = rt.begin_runtime_epoch(db, instance_id='synthetic-instance')
    args = dict(epoch=epoch, principal_id='synthetic-user', session_id='session',
                request_id='request', payload={'text': 'synthetic non-secret input'})
    try:
        yield db, args
    finally:
        db.close()


@pytest.mark.parametrize('phase', ['admit', 'claim', 'settle'])
def test_refused_commit_leaves_actual_runtime_phase_unchanged(state, phase):
    db, args = state
    before = None
    if phase != 'admit':
        before = rt.admit_session_input(db, **args)
    if phase == 'settle':
        before = rt.claim_session_input(db, epoch=args['epoch'], session_id='session')
    if before is not None:
        # Claim's return also carries a transient transcript mark. Compare the
        # persisted row to its own pre-write snapshot, not that runtime sidecar.
        before = rt.get_session_admission(db, admission_id=before['admission_id'])

    @contextmanager
    def expired(connection):
        # Actual SQL has run. Refusal must still roll back this exact transaction.
        assert connection.in_transaction
        raise rt.RuntimeStoreError('synthetic_lease_expired')
        yield

    with pytest.raises(rt.RuntimeStoreError, match='synthetic_lease_expired'):
        if phase == 'admit':
            rt.admit_session_input(db, **args, _commit_guard=expired)
        elif phase == 'claim':
            rt.claim_session_input(db, epoch=args['epoch'], session_id='session', _commit_guard=expired)
        else:
            rt.settle_session_input(db, epoch=args['epoch'], admission_id=before['admission_id'],
                generation=before['generation'], outcome='completed',
                result={'result': {'final_response': 'synthetic'}, 'usage': {}}, _commit_guard=expired)
    if phase == 'admit':
        assert rt.list_session_admissions(db, session_id='session') == []
    else:
        assert rt.get_session_admission(db, admission_id=before['admission_id']) == before
        assert db.get_session('session')['runtime_generation'] == (0 if phase == 'claim' else before['generation'])
        if phase == 'settle':
            from gateway.session_results import admission_result
            assert admission_result(db, before['admission_id']) is None


def test_live_replay_cannot_bypass_commit_guard_or_create_duplicate(state):
    db, args = state
    admitted = rt.admit_session_input(db, **args)
    calls = []

    @contextmanager
    def revoked(connection):
        calls.append(True)
        raise rt.RuntimeStoreError('synthetic_lease_revoked')
        yield

    with pytest.raises(rt.RuntimeStoreError, match='synthetic_lease_revoked'):
        rt.admit_session_input(db, **args, _commit_guard=revoked)
    assert calls == [True]
    assert rt.list_session_admissions(db, session_id='session') == [admitted]
