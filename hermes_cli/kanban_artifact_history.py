"""Explicit artifact-only acceptance of a recorded, exhausted tool-loop attempt.

This preserves failure history; it neither repairs the guardrail nor establishes
process death, runtime success, permission to retry, or artifact acceptance.
"""
from __future__ import annotations

import json

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_source_history import (
    _claimed_boundary, _historical_events, _positive, _require_recorded_run_order, _settled_runs,
)
from hermes_cli.kanban_source_resolution import _require_inactive_settled_history, _TERMINAL_RUNS

_ACCOUNTING_KEYS = frozenset(('failures', 'trigger_outcome', 'effective_limit', 'limit_source', 'retry_status'))
_EVENT_KEYS = _ACCOUNTING_KEYS | {'error', 'guardrail', 'tool_name'}
_GUARDRAIL = 'identical_call_streak_halt'


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('artifact history refuses duplicate accounting fields')
        result[key] = value
    return result


def _object(value):
    try:
        parsed = json.loads(value, object_pairs_hook=_unique_object)
    except (TypeError, ValueError):
        raise ValueError('artifact history requires unambiguous failure accounting') from None
    if not isinstance(parsed, dict):
        raise ValueError('artifact history requires an object payload')
    return parsed


def _accounting(value, keys):
    parsed = _object(value)
    if parsed.keys() != keys:
        raise ValueError('artifact history refuses unsupported failure accounting')
    return parsed


def require_exhausted_artifact_history(rows):
    """Require exact task-limit exhaustion by the supported terminal tool-loop guard.

    Call only inside revision-checked artifact acceptance, with explicit opt-in.
    Existing ownership/hold checks and the caller's sticky-block check still apply.
    No row is rewritten to resemble ordinary source/crash history.
    """
    for event in rows['task_events']:
        if event['kind'] in _TERMINAL_RUNS and event.get('run_id') is None:
            raise ValueError('artifact history refuses unbound terminal events')
        if event['kind'] in ('claimed', 'blocked', 'gave_up', 'needs_attention'):
            _object(event.get('payload') or '{}')
    _require_inactive_settled_history(rows)
    now = int(kb.time.time())
    task = rows['tasks'][0]
    runs = _settled_runs(rows, _TERMINAL_RUNS, now)
    events = _historical_events(rows, runs, now)
    latest = runs[-1]
    if latest['status'] != 'gave_up' or latest.get('outcome') != 'gave_up':
        raise ValueError('artifact history requires a latest exhausted attempt')
    for run in runs:
        allowed = ('gave_up',) if run is latest else ('crashed',)
        _claimed_boundary(run, events, _TERMINAL_RUNS, boundary_outcomes=allowed)
        if any(run.get(key) is not None for key in ('claim_expires', 'worker_pid', 'worker_started_at')):
            raise ValueError('artifact history preserves recorded run ownership')
    _require_recorded_run_order(runs, events, _TERMINAL_RUNS,
                                boundary_outcomes=('crashed', 'gave_up'))
    _, closing = _claimed_boundary(latest, events, _TERMINAL_RUNS, boundary_outcomes=('gave_up',))
    if closing['created_at'] != latest['ended_at']:
        raise ValueError('artifact history requires exact exhausted closing time')
    metadata = _accounting(latest.get('metadata'), _ACCOUNTING_KEYS)
    payload = _accounting(closing.get('payload'), _EVENT_KEYS)
    if any(metadata[key] != payload[key] for key in _ACCOUNTING_KEYS):
        raise ValueError('artifact history requires matching failure accounting')
    values = (metadata['failures'], metadata['effective_limit'], payload['failures'],
              payload['effective_limit'], task.get('consecutive_failures'), task.get('max_retries'))
    if not all(_positive(value) and value == values[0] for value in values):
        raise ValueError('artifact history requires exact task-specific retry exhaustion')
    error = f'Tool guardrail halted terminal: {_GUARDRAIL}'
    if (metadata['trigger_outcome'] != 'crashed' or metadata['limit_source'] != 'task'
            or metadata['retry_status'] != 'ready' or payload['guardrail'] != _GUARDRAIL
            or payload['tool_name'] != 'terminal' or payload['error'] != error
            or latest.get('error') != error or task.get('last_failure_error') != error):
        raise ValueError('artifact history refuses unsupported guardrail or failure lineage')
    heartbeat = task.get('last_heartbeat_at')
    if not _positive(heartbeat) or latest.get('last_heartbeat_at') != heartbeat:
        raise ValueError('artifact history requires exact exhausted heartbeat lineage')
    heartbeats = [event for event in events if event['kind'] == 'heartbeat'
                  and event['run_id'] == latest['id']]
    matching = [event for event in heartbeats if event['created_at'] == heartbeat]
    if (len(matching) != 1 or matching[0]['id'] != heartbeats[-1]['id']
            or matching[0]['id'] >= closing['id']):
        raise ValueError('artifact history requires a unique pre-exhaustion heartbeat')
    if any(event['kind'] in ('heartbeat', 'claimed', 'spawned', 'worker_bound', 'owner_admitted')
           and (event['id'] >= closing['id'] or event['created_at'] > latest['ended_at'])
           for event in events):
        raise ValueError('artifact history preserves activity after terminal history')
