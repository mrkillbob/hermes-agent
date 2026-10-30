"""Durable A2A projections reuse owner state and never execute a model."""
import pytest

from hermes_state import SessionDB
from hermes_state_runtime import begin_runtime_epoch, RuntimeStoreError
from hermes_state_a2a import EventTasks


def ledger(path, instance='one', peer='peer'):
    db = SessionDB(db_path=path)
    epoch = begin_runtime_epoch(db, instance_id=instance)
    return db, EventTasks(db, epoch, ('producer', 'agent', 'tenant', peer))


def test_duplicate_restart_and_conflict(tmp_path):
    path = tmp_path / 'state.db'
    db, tasks = ledger(path)
    first = tasks.reserve('message', {'context_id': '', 'text': 'synthetic'})
    assert tasks.reserve('message', {'context_id': '', 'text': 'synthetic'}) == first
    with pytest.raises(RuntimeStoreError):
        tasks.reserve('message', {'context_id': '', 'text': 'different'})
    tasks.bind(first['task_id'], {'session_id': 'session', 'admission_id': 'admission'})
    event = tasks.publish(first['task_id'], {'status': {'state': 'TASK_STATE_FAILED'}, 'id': first['task_id'], 'contextId': first['context_id']})
    db.close()
    db, tasks = ledger(path, 'two')
    saved = tasks.read(first['task_id'])
    assert saved['event'] == event
    assert tasks.publish(first['task_id'], event['task']) == event
    with pytest.raises(RuntimeStoreError):
        tasks.publish(first['task_id'], {**event['task'], 'contextId': 'other'})
    tasks.ack(first['task_id'], event['id'], event['sha256'])
    assert tasks.read(first['task_id'])['acked'] is True
    db.close()


def test_foreign_scope_and_stale_owner_fail_closed(tmp_path):
    db, tasks = ledger(tmp_path / 'state.db')
    first = tasks.reserve('message', {'context_id': '', 'text': 'synthetic'})
    foreign = EventTasks(db, tasks.epoch, ('producer', 'agent', 'tenant', 'other'))
    with pytest.raises(RuntimeStoreError):
        foreign.read(first['task_id'])
    begin_runtime_epoch(db, instance_id='replacement')
    with pytest.raises(RuntimeStoreError):
        tasks.reserve('second', {'context_id': '', 'text': 'synthetic'})
    db.close()
