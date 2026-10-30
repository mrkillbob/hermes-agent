"""Passive scoped history and configured bounded triage create; no broad client."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .intake_context import read_snapshot, require_context
from . import intake_history
from .intake_only import ASSIGNEE, KEY_PREFIX, SCOPES, IntakeError

MAX_BYTES = 1_048_576


def _run(argv):
    # File-backed bounded result inspection avoids retaining arbitrary task output
    # in memory. No shell, stdin, retry, model override, or command fallback.
    with tempfile.TemporaryFile() as out:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
            shell=False,
        )
        if completed.returncode:
            raise IntakeError("existing Kanban host capability unavailable")
        out.seek(0)
        raw = out.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise IntakeError("Kanban response exceeded output bound")
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise IntakeError("Kanban acknowledgment malformed") from None


class IntakeKanban:
    def __init__(self, runner=_run, *, expected_root=None, context=None):
        if expected_root is None or not Path(expected_root).expanduser().is_absolute():
            raise IntakeError("explicit registered intake control root required")
        if context is None:
            raise IntakeError("registered passive Kanban context required")
        self._context = context
        self._hermes = self._configured_executable()
        self._expected_root = Path(expected_root).expanduser().resolve()
        self._run = runner

    @staticmethod
    def _configured_executable():
        configured = os.environ.get("HERMES_EXECUTABLE", "").strip()
        executable = Path(configured)
        try:
            valid = executable.is_absolute() and executable.is_file() and os.access(executable, os.X_OK)
        except OSError:
            valid = False
        if not valid:
            raise IntakeError("HERMES_EXECUTABLE must name an executable absolute path")
        return str(executable)

    def prepare_create(self, card):
        """Reject known local launch failures before a durable pending receipt."""
        require_context(self._context, self._expected_root, card.board)
        if self._configured_executable() != self._hermes:
            raise IntakeError("HERMES_EXECUTABLE changed during intake; held")

    def lookup(self, board, key):
        if board not in set(SCOPES.values()) or not re.fullmatch(
            re.escape(KEY_PREFIX) + r"[0-9a-f]{64}", key
        ):
            raise IntakeError("intake lookup outside approved scope")
        rows = read_snapshot(self._context, board, expected_root=self._expected_root,
                             idempotency_key=key)["tasks"]
        if not isinstance(rows, list) or len(rows) > 1:
            raise IntakeError("exact complete Kanban lookup unavailable")
        for row in rows:
            required = {
                "id",
                "title",
                "body",
                "assignee",
                "status",
                "workspace_kind",
                "workspace_path",
                "idempotency_key",
                "model_override",
                "provider_override",
                "reasoning_effort",
                "branch_name",
                "current_run_id",
                "worker_pid",
                "claim_lock",
                "skills",
                "goal_mode",
            }
            if (
                not isinstance(row, dict)
                or not required <= set(row)
                or row["idempotency_key"] != key
            ):
                raise IntakeError("exact stored Kanban row unavailable")
        return rows

    def legacy_feedback_lookup(self, board, identity):
        if (
            board not in set(SCOPES.values())
            or not isinstance(identity, dict)
            or set(identity) != {"repository", "pr_number", "feedback_kind", "feedback_id"}
            or identity["repository"] not in SCOPES
            or SCOPES[identity["repository"]] != board
            or type(identity["pr_number"]) is not int
            or identity["pr_number"] < 1
            or identity["feedback_kind"] not in ("review_comment", "issue_comment", "review")
            or not isinstance(identity["feedback_id"], str)
            or not re.fullmatch(r"[1-9][0-9]*", identity["feedback_id"])
        ):
            raise IntakeError("legacy identity lookup outside approved scope")
        payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        if len(payload.encode("utf-8")) > 1024:
            raise IntakeError("legacy identity exceeds host request bound")
        deadline = intake_history._monotonic() + intake_history._LEGACY_INDEX_SECONDS
        rows = read_snapshot(self._context, board, expected_root=self._expected_root,
                             idempotency_prefix="github-pr-feedback:",
                             row_projector=intake_history.project_legacy_receipt)["tasks"]
        return intake_history.legacy_matches(rows, identity, deadline=deadline, projected=True)

    def create(self, card):
        self.prepare_create(card)
        if card.board not in set(SCOPES.values()) or not Path(card.workspace_path).is_dir():
            raise IntakeError("existing intake scope/workspace unavailable")
        try:
            body = json.loads(card.body)
        except (ValueError, TypeError):
            raise IntakeError("typed intake card required") from None
        if (
            not isinstance(body, dict)
            or body.get("repository") not in SCOPES
            or SCOPES[body["repository"]] != card.board
            or body.get("action") != "inspect"
            or not card.title.startswith("PR feedback intake:")
            or not re.fullmatch(re.escape(KEY_PREFIX) + r"[0-9a-f]{64}", card.idempotency_key)
        ):
            raise IntakeError("card outside pinned intake contract")
        result = self._run(
            [
                self._hermes,
                "kanban",
                "--board",
                card.board,
                "create",
                card.title,
                "--body",
                card.body,
                "--assignee",
                ASSIGNEE,
                "--workspace",
                "dir:" + card.workspace_path,
                "--triage",
                "--created-by",
                "github-pr-intake",
                "--idempotency-key",
                card.idempotency_key,
                "--json",
            ]
        )
        task_id = result.get("id") if isinstance(result, dict) else None
        if not isinstance(task_id, str) or not task_id:
            raise IntakeError("durable task ID unavailable")
        return task_id
