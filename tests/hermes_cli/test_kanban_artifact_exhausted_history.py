"""Explicit artifact-only acceptance after a recorded terminal retry-budget stop."""
import json

import pytest

from hermes_cli import kanban_artifact_disposition as api, kanban_db as kb
from hermes_cli.kanban_source_resolution import _require_settled_source
from tests.hermes_cli.test_kanban_artifact_settled_history import (
    ARTIFACT, BOARD, PEER, PROVENANCE, TASK, apply, evidence, rows,
)

from tests.hermes_cli import test_kanban_artifact_settled_history as fixtures

store = fixtures.store

ERROR = 'Tool guardrail halted terminal: identical_call_streak_halt'
ACCOUNTING = dict(failures=2, effective_limit=2, limit_source='task',
                  trigger_outcome='crashed', retry_status='ready')


@pytest.fixture
def exhausted(store):
    with kb.write_txn(store):
        store.execute('UPDATE task_events SET id=id+10')
        store.execute("INSERT INTO task_events(id,task_id,run_id,kind,payload,created_at) "
                      "VALUES(10,?,1,'claimed',?,10)",
                      (TASK, json.dumps(dict(run_id=1, lock='synthetic-lock'))))
        store.execute('UPDATE tasks SET last_heartbeat_at=18,consecutive_failures=2,max_retries=2,'
                      'last_failure_error=? WHERE id=?', (ERROR, TASK))
        store.execute("INSERT INTO task_runs(id,task_id,status,outcome,claim_lock,started_at,ended_at,"
                      "last_heartbeat_at,metadata,error) VALUES(2,?,'gave_up','gave_up','fixture-2',14,20,18,?,?)",
                      (TASK, json.dumps(ACCOUNTING), ERROR))
        events = [(13, 'claimed', dict(run_id=2, lock='fixture-2'), 14),
                  (14, 'heartbeat', None, 16), (15, 'heartbeat', None, 18),
                  (16, 'gave_up', dict(ACCOUNTING, error=ERROR, guardrail='identical_call_streak_halt',
                                      tool_name='terminal'), 20),
                  (17, 'worker_result', dict(exit_code=0), 20)]
        for eid, kind, payload, at in events:
            store.execute('INSERT INTO task_events(id,task_id,run_id,kind,payload,created_at) VALUES(?,?,2,?,?,?)',
                          (eid, TASK, kind, json.dumps(payload) if payload is not None else None, at))
    return store


def test_explicit_artifact_acceptance_preserves_terminal_failure_and_history(exhausted, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('artifact acceptance invoked a runtime/completion effect')
    for name in ('recompute_ready', '_cleanup_workspace', '_clear_failure_counter',
                 'notify_task_updated', '_fire_kanban_lifecycle_hook'):
        monkeypatch.setattr(kb, name, forbidden)
    before, peer = rows(exhausted), rows(exhausted, PEER)
    accepted = evidence(exhausted)
    preview = api.preview_accepted_artifact(
        exhausted, TASK, board=BOARD, expected_revision=accepted.reviewed_revision, evidence=accepted,
        artifact=ARTIFACT, provenance=PROVENANCE, allow_settled_history=True, allow_exhausted_history=True)
    assert preview['exhausted_history_reviewed'] and not preview['admission_granted']
    assert rows(exhausted) == before
    assert apply(exhausted, accepted, allow_settled_history=True, allow_exhausted_history=True)
    after = rows(exhausted)
    assert after['tasks'] == [dict(before['tasks'][0], status='archived')]
    assert after['task_events'][:-1] == before['task_events']
    for table in before.keys() - {'tasks', 'task_events'}:
        assert after[table] == before[table]
    payload = json.loads(after['task_events'][-1]['payload'])
    assert payload['terminal_budget_history_preserved'] is True
    assert payload['scope'] == 'artifact_only' and not payload['runtime_acceptance_claimed']
    assert rows(exhausted, PEER) == peer


def test_default_artifact_and_source_resolution_still_refuse(exhausted):
    before = rows(exhausted)
    with pytest.raises(ValueError, match='latest crashed'):
        _require_settled_source(before)
    with pytest.raises(ValueError, match='latest crashed'):
        apply(exhausted, evidence(exhausted), allow_settled_history=True)
    with pytest.raises(ValueError, match='explicit artifact'):
        apply(exhausted, evidence(exhausted), allow_exhausted_history=True)
    assert rows(exhausted) == before


@pytest.mark.parametrize('field,value', [
    ('failures', 3), ('effective_limit', 3), ('limit_source', 'unknown'),
    ('trigger_outcome', 'done'), ('retry_status', 'blocked'), ('failures', True),
    ('sticky', True), ('local_privacy_denial', True), ('force_trip', True),
    ('failure_class', 'provider_egress_blocked'), ('guardrail', 'unknown'),
    ('tool_name', 'unknown'), ('error', 'different failure'),
])
def test_conflicting_or_unsupported_closure_refuses(exhausted, field, value):
    payload = json.loads(exhausted.execute('SELECT payload FROM task_events WHERE id=16').fetchone()[0])
    payload[field] = value
    exhausted.execute('UPDATE task_events SET payload=? WHERE id=16', (json.dumps(payload),))
    before = rows(exhausted)
    with pytest.raises(ValueError):
        apply(exhausted, evidence(exhausted), allow_settled_history=True, allow_exhausted_history=True)
    assert rows(exhausted) == before


@pytest.mark.parametrize('sql', [
    "UPDATE tasks SET claim_lock='active'", "UPDATE tasks SET block_kind='needs_input'",
    "UPDATE tasks SET consecutive_failures=3", "UPDATE tasks SET max_retries=3",
    "UPDATE task_runs SET metadata='{}' WHERE id=2",
    "UPDATE task_runs SET status='running' WHERE id=2",
    "UPDATE task_runs SET last_heartbeat_at=17 WHERE id=2",
    "UPDATE task_runs SET claim_expires=30 WHERE id=2",
    "UPDATE task_runs SET worker_pid=999 WHERE id=2",
    "UPDATE task_events SET id=19 WHERE id=15",
    "UPDATE task_events SET id=19 WHERE id=12",
    "UPDATE task_events SET created_at=21 WHERE id=16",
    "UPDATE task_events SET payload='{}' WHERE id=13",
    "UPDATE task_events SET run_id=NULL WHERE id=16",
    "INSERT INTO task_events(task_id,run_id,kind,payload,created_at) "
    "SELECT task_id,run_id,kind,payload,created_at FROM task_events WHERE id=16",
    "INSERT INTO task_events(task_id,run_id,kind,payload,created_at) "
    "SELECT task_id,run_id,kind,payload,created_at FROM task_events WHERE id=13",
    "INSERT INTO task_links(parent_id,child_id) VALUES('t_5678abcd','t_1234abcd')",
])
def test_ambiguous_or_active_history_refuses(exhausted, sql):
    exhausted.execute(sql)
    before = rows(exhausted)
    with pytest.raises(ValueError):
        apply(exhausted, evidence(exhausted), allow_settled_history=True, allow_exhausted_history=True)
    assert rows(exhausted) == before


def test_outer_rollback_preserves_every_row(exhausted):
    before = rows(exhausted)
    accepted = evidence(exhausted)
    with pytest.raises(RuntimeError):
        with kb.write_txn(exhausted):
            apply(exhausted, accepted, allow_settled_history=True, allow_exhausted_history=True, allow_nested=True)
            raise RuntimeError('operator stop before commit')
    assert rows(exhausted) == before


@pytest.mark.parametrize('target,payload', [
    ('claim', '{"run_id":99,"run_id":2,"lock":"fixture-2"}'),
    ('claim', '{"run_id":2,"lock":"foreign","lock":"fixture-2"}'),
    ('hold', '{"sticky":true,"sticky":false}'),
    ('hold', '{"local_privacy_denial":true,"local_privacy_denial":false}'),
    ('metadata', '{"failures":9,"failures":2,"effective_limit":2,"limit_source":"task",'
                 '"trigger_outcome":"crashed","retry_status":"ready"}'),
])
def test_duplicate_claim_hold_or_accounting_keys_refuse(exhausted, target, payload):
    if target == 'claim':
        exhausted.execute('UPDATE task_events SET payload=? WHERE id=13', (payload,))
    elif target == 'metadata':
        exhausted.execute('UPDATE task_runs SET metadata=? WHERE id=2', (payload,))
    else:
        exhausted.execute("INSERT INTO task_events(id,task_id,kind,payload,created_at) "
                          "VALUES(9,?,'needs_attention',?,9)", (TASK, payload))
    before = rows(exhausted)
    with pytest.raises(ValueError):
        apply(exhausted, evidence(exhausted), allow_settled_history=True, allow_exhausted_history=True)
    assert rows(exhausted) == before


@pytest.mark.parametrize('value', [None, 1, 'true'])
def test_exhausted_opt_in_requires_exact_boolean(exhausted, value):
    before = rows(exhausted)
    with pytest.raises(ValueError, match='explicit artifact'):
        apply(exhausted, evidence(exhausted), allow_settled_history=True, allow_exhausted_history=value)
    assert rows(exhausted) == before
