"""Operator-reviewed archival of accepted artifact-only, never-dispatched intake.

The operator must independently establish that the complete brief is fulfilled.
Hashes bind that acceptance to exact bytes and a complete card revision; they do
not establish scientific validity, execution, runtime acceptance or permission.
No worker, HTTP endpoint or automatic classifier exposes this operation.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_source_supersession import _require_inactive_intake
from hermes_cli.kanban_task_revision import require_task_revision
from hermes_cli.kanban_transfer import _selected_task_rows

_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
_MAX_BYTES = 1024 * 1024


@contextlib.contextmanager
def existing_artifact_connection(*, board):
    """Open a canonical existing board with normal tracked connection settings.

    No creation, migration, quarantine, schema repair or initialized-cache
    assertion occurs. The operator must qualify the full schema, triggers and
    quiescence under its guarded writer transaction before calling archival.
    Ordinary SQLite WAL/SHM bookkeeping remains possible.
    """
    board = kb._require_slug(board)
    path = kb.kanban_db_path(board=board)
    kb._assert_not_delegated_child_mutation(path)
    if not path.is_file():
        raise ValueError('an existing initialized Kanban board is required')
    conn, _ = kbc._open_configured(path, kbc._schema_is_present,
                                  existing_only=True, require_schema=True)
    with contextlib.closing(conn):
        yield conn


@dataclass(frozen=True)
class ArtifactAcceptance:
    board: str
    task_id: str
    reviewed_revision: str
    artifact_sha256: str
    provenance_sha256: str
    accepted_by: str
    acceptance_kind: str = 'deliverable_accepted'

    def payload(self):
        digests = (self.reviewed_revision, self.artifact_sha256, self.provenance_sha256)
        if (any(not isinstance(value, str) or _DIGEST.fullmatch(value) is None for value in digests)
                or not isinstance(self.task_id, str)
                or re.fullmatch(r't_[0-9a-f]{8}', self.task_id) is None
                or not isinstance(self.accepted_by, str)
                or _IDENTIFIER.fullmatch(self.accepted_by) is None
                or self.acceptance_kind != 'deliverable_accepted'):
            raise ValueError('artifact disposition requires explicit bounded deliverable acceptance')
        payload = dict(schema='kanban-artifact-acceptance-v1', board=kb._require_slug(self.board),
                       task_id=self.task_id, reviewed_revision=self.reviewed_revision,
                       artifact_sha256=self.artifact_sha256, provenance_sha256=self.provenance_sha256,
                       accepted_by=self.accepted_by, acceptance_kind=self.acceptance_kind)
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return dict(payload, acceptance_sha256=hashlib.sha256(encoded).hexdigest())


def _require_bytes(value, expected):
    if (type(value) is not bytes or not 0 < len(value) <= _MAX_BYTES
            or hashlib.sha256(value).hexdigest() != expected):
        raise ValueError('artifact disposition requires exact immutable bounded accepted bytes')


def archive_accepted_artifact(conn, task_id, *, board, expected_revision, evidence,
                              artifact: bytes, provenance: bytes, allow_nested: bool = False):
    """Archive accepted intake with one digest-only event and no completion hooks.

    Missing-input stops do not qualify as completed deliverables here. This path
    refuses links, runs, active ownership and holds rather than releasing them.
    The caller retains the accepted artifact and provenance; neither body nor
    filesystem path is copied into the board's audit event.

    An operator may explicitly compose this side-effect-free primitive inside
    its own guarded transaction to keep store-wide preflight atomic. Its success
    then releases only a savepoint; durability requires the outer commit.
    """
    if type(evidence) is not ArtifactAcceptance or type(allow_nested) is not bool:
        raise ValueError('explicit artifact acceptance is required')
    payload = evidence.payload()
    board = kb._require_slug(board)
    if (evidence.board, evidence.task_id, evidence.reviewed_revision) != (board, task_id, expected_revision):
        raise ValueError('artifact acceptance does not match reviewed board/card/revision')
    _require_bytes(artifact, evidence.artifact_sha256)
    _require_bytes(provenance, evidence.provenance_sha256)
    actual = kbc._main_db_file(conn)
    if actual is None or Path(actual).resolve() != kb.kanban_db_path(board=board).resolve():
        raise ValueError('artifact disposition connection does not match canonical board')
    with kb.write_txn(conn, allow_nested=allow_nested):
        rows = _selected_task_rows(conn, [task_id])
        require_task_revision(expected_revision, board, task_id, rows)
        _require_inactive_intake(rows)
        if kb._has_sticky_block(conn, task_id):
            raise ValueError('artifact disposition preserves explicit policy holds')
        changed = conn.execute("UPDATE tasks SET status='archived' WHERE id=? AND status='triage'", (task_id,))
        if changed.rowcount != 1:
            raise ValueError('artifact disposition task changed')
        kb._append_event(conn, task_id, 'archived', {
            **payload, 'scope': 'artifact_only', 'runtime_acceptance_claimed': False,
            'experiment_execution_claimed': False, 'workspace_retained': True,
            'failure_state_preserved': True, 'readiness_recomputed': False,
        })
    return True
