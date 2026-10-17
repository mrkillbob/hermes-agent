"""Intake-only read/triage delivery; no maintenance controller or dispatcher."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .feedback_filters import non_actionable_feedback_reason
from .github_client import (
    MAX_DISCOVERED_PULL_REQUESTS,
    MAX_FEEDBACK_BODY_CHARS,
    Feedback,
)
from .policy import FeedbackReceipt, PluginPolicy, PullRequest

SCOPES = {
    "mrkillbob/luna-bot": "tradingbot-burndown",
    "mrkillbob/hermes-agent": "default",
    "NousResearch/hermes-agent": "default",
}
ASSIGNEE = "task-intake-router"
KEY_PREFIX = "github-pr-intake:v1:"


class IntakeError(RuntimeError):
    """Unavailable evidence or an uncertain delivery; never an automatic retry."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class IntakePolicy:
    enabled: bool
    broad_enabled: bool
    repository: str
    admission: PluginPolicy

    def check(self, now):
        if self.enabled is not True or self.broad_enabled is not False:
            raise IntakeError("intake capability is not independently enabled")
        if self.repository not in SCOPES or self.repository not in self.admission.targets:
            raise IntakeError("repository outside fixed intake scope")
        if not self.admission.enabled or self.admission.auto_dispatch:
            raise IntakeError("pure admission policy invalid")
        if self.admission.not_before is None:
            raise IntakeError("feedback time boundary unavailable")
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise IntakeError("timezone-aware time evidence required")
        if self.repository == "NousResearch/hermes-agent":
            local = now.astimezone(ZoneInfo("America/Los_Angeles"))
            if (local.hour, local.minute) != (3, 15):
                raise IntakeError("upstream intake outside approved Pacific window")


@dataclass(frozen=True)
class IntakeCard:
    board: str
    title: str
    body: str
    workspace_path: str
    idempotency_key: str

    def as_dict(self):
        return {
            "board": self.board,
            "title": self.title,
            "body": self.body,
            "workspace_kind": "dir",
            "workspace_path": self.workspace_path,
            "assignee": ASSIGNEE,
            "idempotency_key": self.idempotency_key,
        }


def render_card(policy, pull, feedback):
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", pull.head_sha):
        raise IntakeError("exact PR head unavailable")
    if not re.fullmatch(r"[1-9][0-9]*", feedback.feedback_id):
        raise IntakeError("canonical feedback ID unavailable")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*(?:\[bot\])?", feedback.reviewer.login):
        raise IntakeError("canonical reviewer identity unavailable")
    receipt = FeedbackReceipt(
        policy.repository, pull.number, feedback.kind, feedback.feedback_id, pull.head_sha
    )
    key = KEY_PREFIX + _sha(_json(receipt.key))
    fragment = {
        "review_comment": "discussion_r",
        "issue_comment": "issuecomment-",
        "review": "pullrequestreview-",
    }[feedback.kind]
    metadata = {
        "repository": policy.repository,
        "pr_number": pull.number,
        "expected_head_sha": pull.head_sha,
        "action": "inspect",
        "feedback_kind": feedback.kind,
        "feedback_id": feedback.feedback_id,
        "feedback_url": f"https://github.com/{policy.repository}/pull/{pull.number}#{fragment}{feedback.feedback_id}",
        "body_sha256": _sha(feedback.body),
        "reviewer": feedback.reviewer.login,
        "created_at": feedback.created_at.astimezone(UTC).isoformat(),
        "instruction": "Read-only intake assessment. Separate authorization required for subsequent actions.",
    }
    workspace = policy.admission.targets[policy.repository].local_path
    if not workspace.is_absolute() or not workspace.is_dir():
        raise IntakeError("existing directory workspace unavailable")
    return IntakeCard(
        SCOPES[policy.repository],
        f"PR feedback intake: {policy.repository}#{pull.number}",
        _json(metadata),
        str(workspace.resolve()),
        key,
    )


def _acknowledged(card, row, *, new=False):
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
        return False
    if any(row.get(k) != v for k, v in card.as_dict().items() if k != "board"):
        return False
    if "board" in row and row["board"] != card.board:
        return False
    if row.get("status") not in (("triage",) if new else ("triage", "archived")):
        return False
    return all(
        row.get(k) is None
        for k in (
            "model_override",
            "provider_override",
            "reasoning_effort",
            "branch_name",
            "current_run_id",
            "worker_pid",
            "claim_lock",
            "skills",
        )
    ) and row.get("goal_mode") in (None, 0)


class ReceiptLedger:
    """Private SQLite receipts anchored outside the DB against rollback/loss.

    The lock spans remote lookup, durable pending state, create, and ACK. Any
    DB/anchor disagreement holds. An absent uncertain delivery is never retried.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.anchor = self.path.with_suffix(self.path.suffix + ".anchor")

    def _snapshot(self):
        identity = self.db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
        if not identity:
            raise IntakeError("receipt identity missing")
        rows = list(
            self.db.execute(
                "SELECT key,state,task_id,card_hash,identity_hash FROM receipts ORDER BY key"
            )
        )
        return {"identity": identity[0], "sha256": _sha(_json(rows))}

    def _persist_anchor(self):
        temporary = self.anchor.with_name(self.anchor.name + "." + uuid.uuid4().hex)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(_json(self._snapshot()))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.anchor)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()

    @contextmanager
    def locked(self):
        import fcntl

        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.is_symlink() or stat.S_IMODE(self.path.parent.stat().st_mode) != 0o700:
            raise IntakeError("private receipt directory required")
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
                raise IntakeError("private receipt lock required")
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise IntakeError("intake serialization unavailable") from None
                    time.sleep(0.02)
            exists = self.path.exists()
            if exists != self.anchor.exists():
                raise IntakeError("receipt store or durable anchor missing")
            for file in (self.path, self.anchor):
                if file.is_symlink() or (
                    file.exists()
                    and (not file.is_file() or stat.S_IMODE(file.stat().st_mode) != 0o600)
                ):
                    raise IntakeError("private regular receipt files required")
            if not exists:
                temporary_fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(temporary_fd)
            self.db = sqlite3.connect(self.path, timeout=3)
            try:
                self.db.execute("PRAGMA synchronous=FULL")
                if exists:
                    if self.db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                        raise IntakeError("receipt store corrupt")
                    if json.loads(self.anchor.read_text(encoding="utf-8-sig")) != self._snapshot():
                        raise IntakeError("receipt rollback or corruption detected")
                else:
                    self.db.executescript(
                        "CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE receipts(key TEXT PRIMARY KEY,state TEXT NOT NULL CHECK(state IN ('pending','uncertain','acknowledged')),task_id TEXT,card_hash TEXT NOT NULL,identity_hash TEXT NOT NULL);"
                    )
                    self.db.execute(
                        "INSERT INTO metadata VALUES('identity',?)", (uuid.uuid4().hex,)
                    )
                    self.db.commit()
                    self._persist_anchor()
                yield self
            finally:
                self.db.close()
        finally:
            os.close(fd)

    def get(self, key):
        return self.db.execute(
            "SELECT state,task_id,card_hash FROM receipts WHERE key=?", (key,)
        ).fetchone()

    @staticmethod
    def identity_hash(card):
        body = json.loads(card.body)
        return _sha(
            _json([body[k] for k in ("repository", "pr_number", "feedback_kind", "feedback_id")])
        )

    def identity_rows(self, card):
        return self.db.execute(
            "SELECT key,state FROM receipts WHERE identity_hash=?", (self.identity_hash(card),)
        ).fetchall()

    def set(self, card, state, task_id=None):
        self.db.execute(
            "INSERT INTO receipts VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET state=excluded.state,task_id=excluded.task_id,card_hash=excluded.card_hash",
            (card.idempotency_key, state, task_id, _sha(card.body), self.identity_hash(card)),
        )
        self.db.commit()
        self._persist_anchor()


def _deliver(card, ledger, kanban):
    previous = ledger.get(card.idempotency_key)
    if previous and previous[2] != _sha(card.body):
        raise IntakeError("changed feedback requires explicit reconciliation")
    identity_rows = ledger.identity_rows(card)
    if any(state == "acknowledged" for _, state in identity_rows):
        return False
    if any(key != card.idempotency_key for key, _ in identity_rows):
        raise IntakeError("uncertain delivery on another head; automatic recreation forbidden")
    # Safe host lookup includes archived cards and performs no readiness update.
    rows = kanban.lookup(card.board, card.idempotency_key)
    if not isinstance(rows, list) or len(rows) > 1:
        raise IntakeError("exact durable task acknowledgment unavailable")
    if rows:
        if not _acknowledged(card, rows[0]):
            raise IntakeError("existing task binding differs")
        ledger.set(card, "acknowledged", rows[0]["id"])
        return False
    if previous:
        raise IntakeError("pending or uncertain delivery absent; automatic recreation forbidden")
    ledger.set(card, "pending")  # durable and anchored BEFORE the remote create
    try:
        task_id = kanban.create(card)
        rows = kanban.lookup(card.board, card.idempotency_key)
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or rows[0].get("id") != task_id
            or not _acknowledged(card, rows[0], new=True)
        ):
            raise IntakeError("new task acknowledgment unavailable")
    except Exception:  # health: allow BLE001 -- Unknown transport/ACK errors must durably hold; exception text may contain task data.
        ledger.set(card, "uncertain")
        raise IntakeError("task create acknowledgment uncertain; held") from None
    ledger.set(card, "acknowledged", task_id)
    return True


def _same_pr_snapshot(first, second):
    # The list converter alone supplies updated_at. Every shared security and
    # admission field remains strict, including head/base identity and labels.
    return replace(first, updated_at=None) == replace(second, updated_at=None)


def _collect_intake_cards(policy, github):
    target = policy.admission.targets[policy.repository]
    pulls = github.list_open_pull_requests(policy.repository, target.owner_login)
    if not isinstance(pulls, (tuple, list)) or len(pulls) >= MAX_DISCOVERED_PULL_REQUESTS:
        raise IntakeError("complete PR listing unavailable")
    cards = {}
    for pull in pulls:
        if not isinstance(pull, PullRequest):
            raise IntakeError("malformed PR listing")
        if not policy.admission.admit_pull_request(pull).admitted:
            continue
        current = github.get_pull_request(policy.repository, pull.number)
        if not _same_pr_snapshot(current, pull):
            raise IntakeError("PR changed during retrieval")
        items = github.list_actionable_feedback(
            policy.repository, pull.number, expected_head_sha=current.head_sha
        )
        if (
            not isinstance(items, (tuple, list))
            or not _same_pr_snapshot(github.get_pull_request(policy.repository, pull.number), current)
        ):
            raise IntakeError("feedback retrieval incomplete or head changed")
        for feedback in items:
            if (
                not isinstance(feedback, Feedback)
                or not isinstance(feedback.body, str)
                or len(feedback.body) >= MAX_FEEDBACK_BODY_CHARS
                or not isinstance(feedback.created_at, datetime)
                or feedback.created_at.tzinfo is None
            ):
                raise IntakeError("malformed feedback")
            if feedback.created_at.astimezone(UTC) < policy.admission.not_before:
                continue
            if (
                feedback.reviewed_head_sha is not None
                and feedback.reviewed_head_sha != current.head_sha
            ):
                continue
            receipt = FeedbackReceipt(
                policy.repository,
                pull.number,
                feedback.kind,
                feedback.feedback_id,
                current.head_sha,
            )
            if not policy.admission.admit(
                current, feedback.reviewer, receipt, is_bot=feedback.is_bot
            ).admitted:
                continue
            if not feedback.body.strip() or feedback.body.strip().casefold().rstrip(".! ") in (
                "lgtm",
                "looks good",
                "thanks",
                "thank you",
                "approved",
            ):
                continue
            if non_actionable_feedback_reason(
                policy.admission, feedback, owner_login=target.owner_login
            ):
                continue
            card = render_card(policy, current, feedback)
            if card.idempotency_key in cards and cards[card.idempotency_key][0] != card:
                raise IntakeError("conflicting duplicate feedback")
            cards[card.idempotency_key] = (card, current, receipt)
    return cards


def scan_intake(policy, github, kanban, ledger_path, *, now, legacy_path=None):
    result = {"status": "rejected", "created": 0, "acknowledged": 0}
    try:
        policy.check(now)
    except Exception:  # health: allow BLE001 -- Policy/plugin failures reject without exposing configuration or exception data.
        return result
    try:
        cards = _collect_intake_cards(policy, github)
    except Exception:  # health: allow BLE001 -- Unknown retrieval/parser errors mean incomplete evidence; suppress potentially private payloads.
        result["status"] = "partial"
        return result
    try:
        if cards:
            with ReceiptLedger(ledger_path).locked() as ledger:
                for card, pull, receipt in cards.values():
                    if not _same_pr_snapshot(github.get_pull_request(policy.repository, pull.number), pull):
                        raise IntakeError("PR head changed before intake delivery")
                    from .intake_legacy import LegacyReceipts

                    disposition = LegacyReceipts(legacy_path).disposition(receipt.key)
                    if disposition == "accounted":
                        result["acknowledged"] += 1
                        continue
                    if disposition != "absent":
                        raise IntakeError("prior legacy delivery is held")
                    identity = dict(
                        zip(
                            ("repository", "pr_number", "feedback_kind", "feedback_id"),
                            receipt.key[:4],
                        )
                    )
                    try:
                        old_cards = kanban.legacy_feedback_lookup(card.board, identity)
                    except Exception:  # health: allow BLE001 -- Unqualified host history must hold; errors can contain unrelated private task data.
                        result["reason"] = "legacy_card_history_unqualified"
                        raise IntakeError("complete legacy card history unavailable") from None
                    if not isinstance(old_cards, list) or old_cards:
                        result["reason"] = "legacy_delivery_provenance_held"
                        raise IntakeError("legacy task provenance requires operator reconciliation")
                    created = _deliver(card, ledger, kanban)
                    result["created"] += int(created)
                    result["acknowledged"] += 1
        result["status"] = "ok"
    except Exception:  # health: allow BLE001 -- Unknown persistence/adapter errors hold delivery; never expose private task or ledger data.
        result["status"] = "held"
    return result
