"""Operator-only artifact receipts; delivery acceptance never grants execution.

No worker, HTTP or mutation CLI exposure. The explicit-path inspector is the
read-only consumer. Existing status, retention and dependency policies are intact.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_source_acceptance import _require_inactive, source_hold_bindings
from hermes_cli.kanban_task_revision import require_task_revision, task_revision
from hermes_cli.kanban_transfer import _selected_task_rows

_KIND = 'artifact_acceptance'
_SCHEMA = 'restricted-artifact-acceptance-v1'


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


@dataclass(frozen=True)
class RestrictedArtifactAcceptance:
    artifact_sha256: str
    provenance_sha256: str
    criterion_sha256: str
    comment_id: int
    comment_sha256: str
    accepted_by: str

    def payload(self):
        digests = (self.artifact_sha256, self.provenance_sha256,
                   self.criterion_sha256, self.comment_sha256)
        if (any(not isinstance(v, str) or re.fullmatch(r'[0-9a-f]{64}', v) is None for v in digests)
                or type(self.comment_id) is not int or self.comment_id <= 0
                or not isinstance(self.accepted_by, str)
                or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', self.accepted_by) is None):
            raise ValueError('explicit artifact, criterion, comment and operator evidence required')
        return dict(vars(self))


def criterion_digest(rows):
    """Bind the complete original title/body, not an extracted acceptance phrase."""
    task = rows['tasks'][0]
    return _digest(_encoded({key: task[key] for key in ('title', 'body')}))


def comment_digest(comment):
    return _digest(_encoded(comment))


def _context_revision(board, task_id, rows):
    # Exclude only our receipts so a receipt does not invalidate itself. Any
    # subsequent task, history, comment, hold or relationship change invalidates it.
    context = {**rows, 'task_events': [e for e in rows['task_events'] if e['kind'] != _KIND]}
    return task_revision(board, task_id, context)


def _require_policy_history(rows):
    _require_inactive(rows)
    holds = source_hold_bindings(rows)
    latest = None
    for event in rows['task_events']:
        kind = event['kind']
        if kind not in ('blocked', 'unblocked', 'gave_up', 'needs_attention', 'block_loop_detected'):
            continue
        payload = json.loads(event['payload'] or '{}')
        if kind in ('blocked', 'block_loop_detected'):
            raise ValueError('artifact receipt refuses other operator/block-loop holds')
        if kind in ('gave_up', 'unblocked'):
            latest = (kind, payload)
        if ((kind == 'needs_attention' or payload.get('sticky'))
                and payload.get('failure_class') != 'provider_egress_blocked'):
            raise ValueError('artifact receipt requires recognized privacy hold lineage')
    if (latest is None or latest[0] != 'gave_up' or latest[1].get('sticky') is not True
            or latest[1].get('local_privacy_denial') is not True
            or latest[1].get('failure_class') != 'provider_egress_blocked'):
        raise ValueError('artifact receipt requires an effective local privacy hold')
    return holds


def record_restricted_artifact_acceptance(conn, task_id, *, board, expected_revision,
                                          evidence, artifact, provenance, reviewed_hold_events):
    """Append one receipt, in a standalone guarded transaction; never change status.

    Caller review establishes fulfillment; hashes establish only evidence identity.
    The accepted comment must already exist. No file paths or content enter the event.
    """
    if type(evidence) is not RestrictedArtifactAcceptance:
        raise ValueError('explicit restricted artifact acceptance required')
    payload = evidence.payload()
    for data, digest in ((artifact, evidence.artifact_sha256),
                         (provenance, evidence.provenance_sha256)):
        if type(data) is not bytes or not 0 < len(data) <= 1024 * 1024 or _digest(data) != digest:
            raise ValueError('reviewed artifact/provenance bytes changed')
    board = kb._require_slug(board)
    actual = kbc._main_db_file(conn)
    if actual is None or Path(actual).resolve() != kb.kanban_db_path(board=board).resolve():
        raise ValueError('artifact acceptance requires canonical board connection')
    with kb.write_txn(conn):
        rows = _selected_task_rows(conn, [task_id])
        require_task_revision(expected_revision, board, task_id, rows)
        holds = _require_policy_history(rows)
        if type(reviewed_hold_events) is not tuple or reviewed_hold_events != holds:
            raise ValueError('reviewed hold bindings changed')
        if not kb._has_sticky_block(conn, task_id):
            raise ValueError('execution hold must remain effective')
        comments = [c for c in rows['task_comments'] if c['id'] == evidence.comment_id]
        if (criterion_digest(rows) != evidence.criterion_sha256 or len(comments) != 1
                or comment_digest(comments[0]) != evidence.comment_sha256):
            raise ValueError('reviewed criterion/comment changed')
        if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='trigger' "
                        "AND tbl_name IN ('tasks','task_events','task_comments')").fetchone():
            raise ValueError('artifact acceptance refuses task/event/comment triggers')
        context = _context_revision(board, task_id, rows)
        for event in rows['task_events']:
            if event['kind'] == _KIND:
                previous = json.loads(event['payload'] or '{}')
                if not isinstance(previous, dict):
                    raise ValueError('ambiguous prior artifact receipt')
                if previous.get('context_revision') == context:
                    raise ValueError('artifact acceptance already recorded for this context')
        kb._append_event(conn, task_id, _KIND, {
            **payload, 'schema': _SCHEMA, 'board': board, 'task_id': task_id,
            'reviewed_revision': expected_revision, 'context_revision': context,
            'reviewed_hold_events': holds, 'deliverable_accepted': True,
            'execution_authorized': False, 'privacy_denial_resolved': False,
            'cleanup_authorized': False,
        })
    return True


def artifact_acceptance_view(board, task_id, rows):
    """Read-only projection of recorded acceptance, not fresh artifact validation.

    Never substitute for operational status or dependency/dispatch predicates.
    Malformed/stale receipts fail closed; ordinary operational readers are unchanged.
    """
    result = dict(deliverable_accepted=False, execution_status=rows['tasks'][0]['status'],
                  execution_authorized=False, receipt_id=None)
    try:
        holds = _require_policy_history(rows)
        context = _context_revision(board, task_id, rows)
        for event in reversed(rows['task_events']):
            if event['kind'] != _KIND:
                continue
            payload = json.loads(event['payload'] or '{}')
            if not isinstance(payload, dict):
                return result
            evidence = RestrictedArtifactAcceptance(**{k: payload[k] for k in (
                'artifact_sha256', 'provenance_sha256', 'criterion_sha256',
                'comment_id', 'comment_sha256', 'accepted_by')})
            evidence.payload()
            comments = [c for c in rows['task_comments'] if c['id'] == evidence.comment_id]
            if (payload.get('schema') == _SCHEMA and payload.get('board') == board
                    and payload.get('task_id') == task_id and event['run_id'] is None
                    and payload.get('context_revision') == context
                    and payload.get('reviewed_hold_events') == [list(h) for h in holds]
                    and payload.get('deliverable_accepted') is True
                    and all(payload.get(k) is False for k in (
                        'execution_authorized', 'privacy_denial_resolved', 'cleanup_authorized'))
                    and criterion_digest(rows) == evidence.criterion_sha256
                    and len(comments) == 1 and comment_digest(comments[0]) == evidence.comment_sha256):
                return {**result, 'deliverable_accepted': True, 'receipt_id': event['id'],
                        'label': 'Artifact accepted — execution restricted'}
            # A newer invalid receipt must not resurrect an older acceptance.
            return result
    except (KeyError, TypeError, ValueError, UnicodeError):
        return result
    return result
