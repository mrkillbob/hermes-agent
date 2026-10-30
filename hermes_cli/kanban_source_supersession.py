"""Operator-reviewed archival of fulfilled, never-dispatched source intake.

This records source disposition, not completion or runtime acceptance. External
merge/finding evidence must be verified by the operator before this mutation.
The normal writer guard, canonical board identity and complete revision remain
mandatory; no worker/tool/HTTP surface automatically exposes this operation.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_task_revision import require_task_revision
from hermes_cli.kanban_transfer import _selected_task_rows

_REASONS = frozenset(('source_finding_resolved', 'review_summary_superseded',
                      'informational_source_receipt', 'source_dependency_refresh_completed'))


@dataclass(frozen=True)
class SourceSupersessionEvidence:
    repository: str
    pull_request: int
    merge_sha: str
    reason_code: str
    finding_comment_id: int | None = None

    def payload(self):
        if (not isinstance(self.repository, str)
                or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}', self.repository) is None
                or type(self.pull_request) is not int or not 0 < self.pull_request < 2**31
                or not isinstance(self.merge_sha, str) or re.fullmatch(r'[0-9a-f]{40}', self.merge_sha) is None
                or not isinstance(self.reason_code, str) or self.reason_code not in _REASONS
                or (self.finding_comment_id is not None and
                    (type(self.finding_comment_id) is not int or not 0 < self.finding_comment_id < 2**63))):
            raise ValueError('source supersession requires bounded reviewed public source evidence')
        return {'repository': self.repository, 'pull_request': self.pull_request,
                'merge_sha': self.merge_sha, 'reason_code': self.reason_code,
                'finding_comment_id': self.finding_comment_id}


def _require_inactive_intake(rows):
    task = rows['tasks'][0]
    owner_fields = ('claim_lock', 'claim_expires', 'worker_pid', 'worker_started_at',
                    'current_run_id', 'started_at', 'last_heartbeat_at')
    contracts = ('workflow_template_id', 'current_step_key', 'block_kind')
    if (task['status'] != 'triage' or any(task.get(name) is not None for name in owner_fields)
            or any(task.get(name) for name in contracts) or rows['task_runs'] or rows['task_links']
            or task.get('completion_contract') not in (None, 'local-only')
            or task.get('block_recurrences', 0) != 0
            or task.get('consecutive_failures', 0) != 0 or task.get('last_failure_error')):
        raise ValueError('source supersession requires unclaimed never-dispatched unlinked Triage without holds/contracts')
    for event in rows['task_events']:
        if event['kind'] == 'block_loop_detected':
            raise ValueError('source supersession preserves block-loop holds')
        if event['kind'] in ('blocked', 'gave_up', 'needs_attention'):
            try:
                value = json.loads(event.get('payload') or '{}')
            except (TypeError, ValueError):
                raise ValueError('source supersession refuses ambiguous hold history') from None
            if (not isinstance(value, dict) or value.get('sticky') or value.get('local_privacy_denial')
                    or value.get('failure_class') == 'provider_egress_blocked'):
                raise ValueError('source supersession preserves existing policy holds')


def archive_source_supersession(conn, task_id, *, board, expected_revision, evidence):
    """Change only reviewed inactive intake status plus one bounded archived event.

    Refuses dependency edges and completion/workflow contracts, so archival cannot
    bypass their acceptance or release an existing dependent. No readiness,
    notification, counter clear, run synthesis, filesystem cleanup or hook call.
    """
    if not isinstance(evidence, SourceSupersessionEvidence):
        raise ValueError('explicit reviewed source evidence is required')
    payload = evidence.payload()
    board = kb._require_slug(board)
    actual = kbc._main_db_file(conn)
    if actual is None or Path(actual).resolve() != kb.kanban_db_path(board=board).resolve():
        raise ValueError('source supersession connection does not match canonical board')
    with kb.write_txn(conn):
        rows = _selected_task_rows(conn, [task_id])
        require_task_revision(expected_revision, board, task_id, rows)
        _require_inactive_intake(rows)
        if kb._has_sticky_block(conn, task_id):
            raise ValueError('source supersession preserves explicit blocked/unblocked hold lineage')
        changed = conn.execute("UPDATE tasks SET status='archived' WHERE id=? AND status='triage'", (task_id,))
        if changed.rowcount != 1:
            raise ValueError('source supersession task changed')
        kb._append_event(conn, task_id, 'archived', {
            **payload, 'scope': 'source_only', 'reviewed_revision': expected_revision,
            'runtime_acceptance_claimed': False, 'workspace_retained': True,
            'failure_state_preserved': True, 'readiness_recomputed': False,
        })
    return True
