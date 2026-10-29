"""Conservative recorded heartbeat lineage for source-only archival.

This establishes a relation between retained rows, not process death or runtime
acceptance. Current ownership and policy holds remain the caller's responsibility.
"""
from __future__ import annotations

import json
from itertools import pairwise


def _positive(value):
    return type(value) is int and 0 < value < 2**63


def _settled_runs(rows, terminal_runs, now):
    task = rows['tasks'][0]
    runs = rows['task_runs']
    if not runs or len(runs) > 4096:
        raise ValueError('source resolution requires bounded historical run lineage')
    seen = set()
    for run in runs:
        rid, start, end = run.get('id'), run.get('started_at'), run.get('ended_at')
        if (run.get('task_id') != task['id'] or not _positive(rid) or rid in seen
                or run.get('status') not in terminal_runs or not _positive(start)
                or not _positive(end) or not start <= end <= now):
            raise ValueError('source resolution refuses ambiguous historical run lineage')
        heartbeat = run.get('last_heartbeat_at')
        if heartbeat is not None and (not _positive(heartbeat) or not start <= heartbeat <= end):
            raise ValueError('source resolution refuses unbound historical run heartbeat')
        seen.add(rid)
    ordered = sorted(runs, key=lambda run: run['id'])
    if any(left['ended_at'] > right['started_at'] for left, right in pairwise(ordered)):
        raise ValueError('source resolution refuses ambiguous historical run ordering')
    if not _positive(task.get('started_at')) or task['started_at'] != ordered[0]['started_at']:
        raise ValueError('source resolution requires bound historical task start')
    return ordered


def _historical_events(rows, runs, now):
    events = rows['task_events']
    if len(events) > 4096:
        raise ValueError('source resolution requires bounded historical event lineage')
    by_run = {run['id']: run for run in runs}
    seen = set()
    for event in events:
        eid, at, rid = event.get('id'), event.get('created_at'), event.get('run_id')
        if (event.get('task_id') != rows['tasks'][0]['id'] or not _positive(eid) or eid in seen
                or not _positive(at) or at > now or not isinstance(event.get('kind'), str)
                or not event['kind'] or (rid is not None and (not _positive(rid) or rid not in by_run))):
            raise ValueError('source resolution refuses ambiguous historical event lineage')
        if event['kind'] == 'heartbeat':
            run = by_run.get(rid)
            if run is None or not run['started_at'] <= at <= run['ended_at']:
                raise ValueError('source resolution refuses unbound historical heartbeat event')
        seen.add(eid)
    ordered = sorted(events, key=lambda event: event['id'])
    if any(left['created_at'] > right['created_at'] for left, right in pairwise(ordered)):
        raise ValueError('source resolution refuses inconsistent historical event ordering')
    return ordered


def _claim_payload(event):
    try:
        payload = json.loads(event.get('payload') or '{}')
    except (TypeError, ValueError):
        raise ValueError('source resolution refuses malformed historical claim evidence') from None
    if not isinstance(payload, dict):
        raise ValueError('source resolution refuses malformed historical claim evidence')
    return payload


def _claimed_boundary(run, events, terminal_runs):
    """Bind an ordinary crash attempt to its unique claim and closing events."""
    claims = []
    rid, lock = run['id'], run.get('claim_lock')
    for event in events:
        if event['kind'] != 'claimed':
            continue
        payload = _claim_payload(event)
        if event['run_id'] != rid and payload.get('run_id') != rid:
            continue
        if (event['run_id'] != rid or not _positive(payload.get('run_id')) or payload['run_id'] != rid
                or not isinstance(lock, str) or not lock or not isinstance(payload.get('lock'), str)
                or payload['lock'] != lock):
            raise ValueError('source resolution refuses unbound historical claim evidence')
        claims.append(event)
    closing = [event for event in events if event['run_id'] == rid and event['kind'] in terminal_runs]
    if (len(claims) != 1 or len(closing) != 1 or closing[0]['kind'] != 'crashed'
            or run['status'] != 'crashed' or run.get('outcome') != 'crashed'):
        raise ValueError('source resolution requires unique ordinary crash boundary evidence')
    claim, close = claims[0], closing[0]
    if (not run['started_at'] <= claim['created_at'] <= run['ended_at']
            or close['created_at'] < run['ended_at']
            or any(event['id'] <= claim['id'] for event in events
                   if event['run_id'] == rid and (event['kind'] == 'heartbeat' or event['kind'] in terminal_runs))):
        raise ValueError('source resolution refuses inconsistent historical claim causality')
    return claim, close


def _require_recorded_run_order(runs, events, terminal_runs):
    # Integer wall-clock stamps can tie across serialized transactions. Only
    # exact claim/crash ledger bindings justify that boundary; run ID magnitude
    # or a reusable host:pid claim string is never the tie-breaking authority.
    boundaries = {}
    for left, right in pairwise(runs):
        if left['ended_at'] != right['started_at']:
            continue
        for run in (left, right):
            if run['id'] not in boundaries:
                boundaries[run['id']] = _claimed_boundary(run, events, terminal_runs)
        left_close = boundaries[left['id']][1]
        right_claim = boundaries[right['id']][0]
        if left_close['id'] >= right_claim['id']:
            raise ValueError('source resolution refuses ambiguous historical transaction order')


def require_historical_heartbeat(rows, terminal_runs, now):
    """Preserve only a uniquely linked heartbeat preceding the latest crash close.

    The crash writer retains the heartbeat; timeout/reclaim writers clear it and
    are intentionally not alternate ways to qualify this source-only operation.
    All validation uses the caller's revision-checked transaction snapshot.
    """
    heartbeat = rows['tasks'][0].get('last_heartbeat_at')
    if not _positive(now) or not _positive(heartbeat) or heartbeat > now:
        raise ValueError('source resolution refuses unqualified historical heartbeat time')
    runs = _settled_runs(rows, terminal_runs, now)
    latest = runs[-1]
    if (latest['status'] != 'crashed' or latest.get('outcome') != 'crashed'
            or latest.get('last_heartbeat_at') != heartbeat):
        raise ValueError('source resolution requires exact latest crashed heartbeat lineage')
    events = _historical_events(rows, runs, now)
    _require_recorded_run_order(runs, events, terminal_runs)
    closing = [event for event in events if event['run_id'] == latest['id']
               and event['kind'] in terminal_runs]
    if (len(closing) != 1 or closing[0]['kind'] != 'crashed'
            or closing[0]['created_at'] < latest['ended_at']):
        raise ValueError('source resolution requires unique historical crash closing event')
    heartbeats = [event for event in events if event['kind'] == 'heartbeat'
                  and event['run_id'] == latest['id']]
    matching = [event for event in heartbeats if event['created_at'] == heartbeat]
    if (len(matching) != 1 or matching[0]['id'] != heartbeats[-1]['id']
            or matching[0]['id'] >= closing[0]['id']):
        raise ValueError('source resolution requires unique historical heartbeat event')
    if any(event['kind'] == 'heartbeat' and
           (event['id'] >= closing[0]['id'] or event['created_at'] > latest['ended_at'])
           for event in events):
        raise ValueError('source resolution preserves heartbeat after terminal history')
