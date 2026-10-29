"""Serialized claim/crash transactions may share a second without overlapping."""
import copy
import json

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_dispatch as dispatch
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.kanban_source_resolution import _require_settled_source
from hermes_cli.kanban_transfer import _selected_task_rows


BOARD = 'equal-second-source-review'


@pytest.fixture
def sequential_crashes(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path / 'home'))
    for key in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(key, raising=False)
    clock = [1]
    monkeypatch.setattr(kb.time, 'time', lambda: clock[0])
    kb.create_board(BOARD)
    with connect_closing(board=BOARD) as conn:
        def create(count=2):
            tid = kb.create_task(conn, title='Synthetic finite source finding',
                                 initial_status='todo', board=BOARD, workspace_kind='scratch')
            for index in range(count):
                start = 10 + index * 5
                clock[0] = start
                with kb.write_txn(conn):
                    rid = kb._claim_and_open_run(conn, tid, 'todo', 'synthetic-reusable-claimer', 100, start)
                assert rid is not None
                clock[0] = start + 2
                assert dispatch.heartbeat_worker(conn, tid, expected_run_id=rid)
                clock[0] = start + 5
                # Persist only canonical crash CAS/end/event relations. Never
                # invoke recovery, process discovery, signalling or death probes.
                with kb.write_txn(conn):
                    changed = conn.execute(
                        "UPDATE tasks SET status='todo',claim_lock=NULL,claim_expires=NULL "
                        "WHERE id=? AND status='running' AND claim_lock=?",
                        (tid, 'synthetic-reusable-claimer'))
                    assert changed.rowcount == 1
                    assert kb._end_run(conn, tid, outcome='crashed', status='crashed') == rid
                    kb._append_event(conn, tid, 'crashed', {}, run_id=rid)
            clock[0] += 1
            with kb.write_txn(conn):
                conn.execute("UPDATE tasks SET status='blocked',consecutive_failures=2 WHERE id=?", (tid,))
                kb._append_event(conn, tid, 'gave_up', {'sticky': False})
            clock[0] += 1
            return conn, tid
        yield create


def test_serialized_crashes_with_equal_second_boundary_are_settled(sequential_crashes):
    conn, tid = sequential_crashes()
    rows = _selected_task_rows(conn, [tid])
    assert rows['task_runs'][0]['ended_at'] == rows['task_runs'][1]['started_at'] == 15
    prior_close = next(e for e in rows['task_events'] if e['kind'] == 'crashed')
    next_claim = [e for e in rows['task_events'] if e['kind'] == 'claimed'][1]
    assert prior_close['id'] < next_claim['id']
    before = copy.deepcopy(rows)
    _require_settled_source(rows)
    assert rows == before
    assert _selected_task_rows(conn, [tid]) == before


def test_every_equal_second_boundary_in_three_run_chain_is_proven(sequential_crashes):
    conn, tid = sequential_crashes(3)
    rows = _selected_task_rows(conn, [tid])
    before = copy.deepcopy(rows)
    _require_settled_source(rows)
    assert rows == before


def _duplicate_event(rows, original, *, kind=None):
    duplicate = copy.deepcopy(original)
    for event in rows['task_events']:
        if event['id'] > original['id']:
            event['id'] += 1
    duplicate['id'] += 1
    if kind:
        duplicate['kind'] = kind
    rows['task_events'].append(duplicate)


def _swap_ids(left, right):
    left['id'], right['id'] = right['id'], left['id']


def _remove_events(rows, removed):
    rows['task_events'] = [event for event in rows['task_events'] if event not in removed]


def _claim_after_heartbeat(claim, heartbeat):
    claim['created_at'] = heartbeat['created_at']
    _swap_ids(claim, heartbeat)


def _claim_after_terminal(rows, run, claim, heartbeat, close):
    claim['created_at'] = close['created_at']
    heartbeat['created_at'] = close['created_at']
    run['last_heartbeat_at'] = heartbeat['created_at']
    rows['tasks'][0]['last_heartbeat_at'] = heartbeat['created_at']
    _swap_ids(claim, close)


def _extra_unbound_claim(rows, claim):
    _duplicate_event(rows, claim)
    rows['task_events'][-1]['run_id'] = None


def _invalidate(rows, change):
    first, second = rows['task_runs']
    claims = [e for e in rows['task_events'] if e['kind'] == 'claimed']
    closes = [e for e in rows['task_events'] if e['kind'] == 'crashed']
    heartbeat = next(e for e in rows['task_events'] if e['kind'] == 'heartbeat' and e['run_id'] == second['id'])
    claim, close = claims[1], closes[0]
    payload = json.loads(claim['payload'])
    actions = {
        'reversed_boundary_events': lambda: _swap_ids(claim, close),
        'genuine_overlap': lambda: first.update(ended_at=first['ended_at'] + 1),
        'missing_prior_claim': lambda: _remove_events(rows, [claims[0]]),
        'missing_next_claim': lambda: _remove_events(rows, [claim]),
        'no_claim_authority': lambda: _remove_events(rows, claims),
        'missing_close': lambda: _remove_events(rows, [close]),
        'duplicate_prior_claim': lambda: _duplicate_event(rows, claims[0]),
        'duplicate_next_claim': lambda: _duplicate_event(rows, claim),
        'duplicate_close': lambda: _duplicate_event(rows, close),
        'second_terminal_kind': lambda: _duplicate_event(rows, close, kind='released'),
        'wrong_claim_event_run': lambda: claim.update(run_id=first['id']),
        'unbound_claim_event_run': lambda: claim.update(run_id=None),
        'foreign_event_task': lambda: claim.update(task_id='t_deadbeef'),
        'claim_after_own_heartbeat': lambda: _claim_after_heartbeat(claim, heartbeat),
        'claim_after_own_terminal': lambda: _claim_after_terminal(rows, second, claim, heartbeat, closes[1]),
        'wrong_terminal_kind': lambda: close.update(kind='released'),
        'terminal_outcome_mismatch': lambda: first.update(outcome='done'),
        'missing_run_lock': lambda: second.update(claim_lock=None),
        'empty_run_lock': lambda: second.update(claim_lock=''),
        'numeric_run_lock': lambda: second.update(claim_lock=7),
        'malformed_claim_json': lambda: claim.update(payload='{'),
        'nonobject_claim_payload': lambda: claim.update(payload='[]'),
        'stale_task_heartbeat': lambda: rows['tasks'][0].update(last_heartbeat_at=first['last_heartbeat_at']),
        'contradictory_extra_claim': lambda: _extra_unbound_claim(rows, claim),
        'boolean_prior_payload_run': lambda: claims[0].update(
            payload=json.dumps({**json.loads(claims[0]['payload']), 'run_id': True})),
        'float_prior_payload_run': lambda: claims[0].update(
            payload=json.dumps({**json.loads(claims[0]['payload']), 'run_id': float(first['id'])})),
    }
    edits = {'wrong_payload_run': ('run_id', first['id']), 'boolean_payload_run': ('run_id', True),
             'float_payload_run': ('run_id', float(second['id'])), 'missing_payload_run': ('run_id', None),
             'wrong_payload_lock': ('lock', 'other-claim'), 'empty_payload_lock': ('lock', ''),
             'numeric_payload_lock': ('lock', 7), 'missing_payload_lock': ('lock', None)}
    actions.update({name: lambda key=key, value=value: claim.update(payload=json.dumps({**payload, key: value}))
                    for name, (key, value) in edits.items()})
    actions[change]()


@pytest.mark.parametrize('change', [
    'reversed_boundary_events', 'genuine_overlap', 'missing_prior_claim', 'missing_next_claim',
    'no_claim_authority', 'missing_close', 'duplicate_prior_claim', 'duplicate_next_claim',
    'duplicate_close', 'second_terminal_kind', 'wrong_claim_event_run', 'unbound_claim_event_run',
    'foreign_event_task', 'claim_after_own_heartbeat', 'claim_after_own_terminal', 'wrong_terminal_kind',
    'terminal_outcome_mismatch', 'missing_run_lock', 'empty_run_lock', 'numeric_run_lock',
    'malformed_claim_json', 'nonobject_claim_payload', 'stale_task_heartbeat', 'contradictory_extra_claim',
    'wrong_payload_run', 'boolean_payload_run', 'float_payload_run', 'missing_payload_run',
    'wrong_payload_lock', 'empty_payload_lock', 'numeric_payload_lock', 'missing_payload_lock',
    'boolean_prior_payload_run', 'float_prior_payload_run',
])
def test_equal_second_boundary_needs_complete_unambiguous_causal_evidence(sequential_crashes, change):
    conn, tid = sequential_crashes()
    stored = _selected_task_rows(conn, [tid])
    rows = copy.deepcopy(stored)
    _invalidate(rows, change)
    before = copy.deepcopy(rows)
    with pytest.raises(ValueError):
        _require_settled_source(rows)
    assert rows == before
    assert _selected_task_rows(conn, [tid]) == stored


@pytest.mark.parametrize('field,value', [
    ('claim_lock', 'current-owner'), ('claim_expires', 99), ('worker_pid', 123),
    ('worker_started_at', 'fingerprint'), ('current_run_id', 2), ('status', 'running'),
    ('workflow_template_id', 'workflow'), ('current_step_key', 'step'), ('completion_contract', 'external'),
    ('block_kind', 'policy'), ('block_recurrences', 1),
])
def test_equal_second_evidence_does_not_discharge_other_guards(sequential_crashes, field, value):
    conn, tid = sequential_crashes()
    rows = _selected_task_rows(conn, [tid])
    rows['tasks'][0][field] = value
    before = copy.deepcopy(rows)
    with pytest.raises(ValueError):
        _require_settled_source(rows)
    assert rows == before


@pytest.mark.parametrize('payload', [{'sticky': True}, {'local_privacy_denial': True},
                                     {'failure_class': 'provider_egress_blocked'}])
def test_equal_second_evidence_preserves_policy_holds(sequential_crashes, payload):
    conn, tid = sequential_crashes()
    rows = _selected_task_rows(conn, [tid])
    event = rows['task_events'][-1]
    event.update(kind='gave_up', payload=json.dumps(payload))
    before = copy.deepcopy(rows)
    with pytest.raises(ValueError):
        _require_settled_source(rows)
    assert rows == before


def test_equal_second_evidence_preserves_dependencies(sequential_crashes):
    conn, tid = sequential_crashes()
    rows = _selected_task_rows(conn, [tid])
    rows['task_links'].append({'parent_id': 't_deadbeef', 'child_id': tid})
    with pytest.raises(ValueError):
        _require_settled_source(rows)
