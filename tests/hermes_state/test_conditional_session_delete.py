"""Empty housekeeping cannot expand to a populated conversation or bypass guards."""
import os

import pytest

from hermes_state import SessionDB
import hermes_state_runtime as runtime
from hermes_state_errors import SessionTurnLeaseLostError


@pytest.fixture
def owner(tmp_path):
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        db.create_session('empty', source='test')
        epoch = runtime.begin_runtime_epoch(db, instance_id='fixture')
        args = dict(epoch=epoch, principal_id='human', session_id='empty', request_id='discard',
                    expected_revision=0, expected_generation=0, operation='delete', payload={'only_if_empty': True})
        yield db, args


def test_empty_delete_commits_and_exact_receipt_replays(owner):
    db, args = owner
    result = runtime.mutate_runtime_session(db, **args)
    assert result['deleted_ids'] == ['empty']
    assert db.get_session('empty') is None
    assert runtime.mutate_runtime_session(db, **args) == result


@pytest.mark.parametrize('value', [False, 1, 'true', None])
def test_condition_requires_exact_true(owner, value):
    db, args = owner
    with pytest.raises(runtime.RuntimeStoreError, match='invalid_params'):
        runtime.mutate_runtime_session(db, **(args | {'payload': {'only_if_empty': value}}))
    assert db.get_session('empty') is not None


def test_empty_compression_tip_cannot_delete_populated_ancestor(owner):
    db, args = owner
    db.append_message('empty', 'user', 'retained history')
    db.end_session('empty', 'compression')
    db.create_session('tip', source='test', parent_session_id='empty')
    with pytest.raises(runtime.RuntimeStoreError, match='revision_conflict'):
        runtime.mutate_runtime_session(db, **(args | {'session_id': 'tip'}))
    assert db.get_session('tip') is not None
    assert db.get_messages('empty')[0]['content'] == 'retained history'


def test_active_turn_lease_remains_protected(owner):
    db, args = owner
    holder = f'pid={os.getpid()}:turn=fixture'
    assert db.try_acquire_session_turn_lease('empty', holder, ttl_seconds=300)
    try:
        with pytest.raises((runtime.RuntimeStoreError, SessionTurnLeaseLostError)):
            runtime.mutate_runtime_session(db, **args)
        assert db.get_session('empty') is not None
    finally:
        db.release_session_turn_lease('empty', holder)
