"""Record reviewed source acceptance independently of an execution hold.

Operator-only composition API: no CLI, worker or HTTP exposure. The caller must
verify the public source and the acceptance document before supplying its digest.
This is a nonterminal receipt, never an upstream-merge or execution permission.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_source_history import require_historical_heartbeat
from hermes_cli.kanban_source_resolution import _TERMINAL_RUNS
from hermes_cli.kanban_source_supersession import SourceSupersessionEvidence
from hermes_cli.kanban_task_revision import require_task_revision
from hermes_cli.kanban_transfer import _selected_task_rows

_HOLD_KINDS = frozenset(('blocked', 'unblocked', 'gave_up', 'needs_attention', 'block_loop_detected'))


@dataclass(frozen=True)
class SourceAcceptance:
    source: SourceSupersessionEvidence
    acceptance_sha256: str
    accepted_by: str
    scope_decision: str
    upstream_milestone_fulfilled: bool

    def payload(self):
        if not isinstance(self.source, SourceSupersessionEvidence):
            raise ValueError('reviewed public source evidence is required')
        source = self.source.payload()
        if (source['reason_code'] != 'source_finding_resolved'
                or not isinstance(self.acceptance_sha256, str)
                or re.fullmatch(r'[0-9a-f]{64}', self.acceptance_sha256) is None
                or not isinstance(self.accepted_by, str)
                or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', self.accepted_by) is None
                or self.scope_decision not in ('local_equivalent_upstream_pending',
                                               'original_source_contract_satisfied')
                or type(self.upstream_milestone_fulfilled) is not bool
                or (self.scope_decision == 'local_equivalent_upstream_pending'
                    and self.upstream_milestone_fulfilled)):
            raise ValueError('explicit source scope and upstream milestone attestation required')
        return {**source, 'acceptance_sha256': self.acceptance_sha256,
                'accepted_by': self.accepted_by, 'scope_decision': self.scope_decision,
                'upstream_milestone_fulfilled': self.upstream_milestone_fulfilled}


def source_hold_bindings(rows):
    """Bind the full retained hold events; do not classify a hold as discharged."""
    bindings = []
    for event in rows['task_events']:
        if event['kind'] not in _HOLD_KINDS:
            continue
        try:
            payload = json.loads(event['payload']) if event['payload'] is not None else {}
            if not isinstance(payload, dict):
                raise ValueError('non-object hold payload')
            encoded = json.dumps(event, sort_keys=True, ensure_ascii=False,
                                 allow_nan=False, separators=(',', ':')).encode('utf-8')
        except (TypeError, ValueError, UnicodeError):
            raise ValueError('source acceptance refuses ambiguous hold history') from None
        bindings.append((event['id'], hashlib.sha256(encoded).hexdigest()))
    return tuple(bindings)


def _require_inactive(rows):
    task = rows['tasks'][0]
    ownership = ('claim_lock', 'claim_expires', 'worker_pid', 'worker_started_at', 'current_run_id')
    contracts = ('workflow_template_id', 'current_step_key', 'block_kind')
    if (task['status'] != 'blocked' or any(task.get(key) is not None for key in ownership)
            or any(task.get(key) for key in contracts) or rows['task_links']
            or rows['kanban_notify_subs'] or task.get('block_recurrences', 0) != 0
            or task.get('completion_contract') not in (None, 'local-only')):
        raise ValueError('source receipt requires inactive unlinked Blocked without external contracts')
    for run in rows['task_runs']:
        ended = run.get('ended_at')
        if run.get('status') not in _TERMINAL_RUNS or type(ended) is not int or ended <= 0:
            raise ValueError('source receipt preserves unfinished or unknown run history')
    if task.get('last_heartbeat_at') is not None:
        require_historical_heartbeat(rows, _TERMINAL_RUNS, int(kb.time.time()))


def record_source_acceptance(conn, task_id, *, board, expected_revision, evidence,
                             acceptance: bytes, reviewed_hold_events):
    """Append one receipt; preserve status, readiness, holds, failures and all runs.

    Requires a standalone normal writer transaction. Repeated source/document
    receipts refuse, even with a freshly reviewed revision; no silent re-attestation.
    Source acceptance and upstream fulfillment are explicit caller attestations,
    not conclusions from a GitHub URL or from the existence of this receipt.
    """
    if not isinstance(evidence, SourceAcceptance):
        raise ValueError('explicit source acceptance is required')
    payload = evidence.payload()
    if (not isinstance(acceptance, bytes) or not 0 < len(acceptance) <= 1024 * 1024
            or hashlib.sha256(acceptance).hexdigest() != evidence.acceptance_sha256):
        raise ValueError('reviewed source acceptance bytes changed')
    board = kb._require_slug(board)
    actual = kbc._main_db_file(conn)
    if actual is None or Path(actual).resolve() != kb.kanban_db_path(board=board).resolve():
        raise ValueError('source acceptance connection does not match canonical board')
    with kb.write_txn(conn):
        rows = _selected_task_rows(conn, [task_id])
        require_task_revision(expected_revision, board, task_id, rows)
        _require_inactive(rows)
        holds = source_hold_bindings(rows)
        if type(reviewed_hold_events) is not tuple or reviewed_hold_events != holds:
            raise ValueError('reviewed hold event bindings changed')
        if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='trigger' "
                        "AND tbl_name IN ('tasks','task_events')").fetchone():
            raise ValueError('source acceptance refuses task/event triggers')
        _refuse_duplicate(rows, payload)
        kb._append_event(conn, task_id, 'source_acceptance', {
            **payload, 'scope': 'source_only', 'reviewed_revision': expected_revision,
            'reviewed_hold_events': holds, 'status_unchanged': True, 'holds_preserved': True,
            'runtime_acceptance_claimed': False, 'worker_admission_granted': False,
            'readiness_recomputed': False, 'failure_state_preserved': True,
            'workspace_retained': True,
        })
    return True


def _refuse_duplicate(rows, payload):
    for event in rows['task_events']:
        if event['kind'] != 'source_acceptance':
            continue
        try:
            previous = json.loads(event['payload'])
            if not isinstance(previous, dict):
                raise ValueError('non-object acceptance payload')
        except (TypeError, ValueError):
            raise ValueError('source acceptance refuses ambiguous prior receipts') from None
        keys = ('repository', 'pull_request', 'merge_sha', 'acceptance_sha256')
        if all(previous.get(key) == payload[key] for key in keys):
            raise ValueError('source acceptance receipt already recorded')
