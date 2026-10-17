from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from github_pr_feedback.github_client import Feedback
from github_pr_feedback.policy import (
    PluginPolicy,
    PullRequest,
    RepositoryTarget,
    Reviewer,
)

NOW = datetime(2026, 10, 17, 10, 15, tzinfo=UTC)
REPO = "mrkillbob/luna-bot"


def policy(tmp_path, repository=REPO):
    from github_pr_feedback.intake_only import IntakePolicy

    target = RepositoryTarget(repository, repository, tmp_path, "mrkillbob", ())
    admission = PluginPolicy(
        True,
        {repository: target},
        frozenset({"trusted"}),
        frozenset(),
        False,
        False,
        False,
        datetime(2026, 1, 1, tzinfo=UTC),
        "task-intake-router",
        "default",
    )
    return IntakePolicy(True, False, repository, admission)


class GitHub:
    def __init__(self, repository=REPO, body="Please address this finding"):
        self.pr = PullRequest(7, "OPEN", repository, repository, "mrkillbob", "feature", "a" * 40)
        self.items = (Feedback("review_comment", "101", Reviewer("trusted"), body, NOW, False),)
        self.calls = []
        self.broken = False

    def list_open_pull_requests(self, repository, owner):
        self.calls.append("list")
        return (self.pr,)

    def get_pull_request(self, repository, number):
        self.calls.append("head")
        return self.pr

    def list_actionable_feedback(self, repository, number, *, expected_head_sha):
        self.calls.append("feedback")
        if self.broken:
            raise RuntimeError("synthetic partial retrieval")
        return self.items


class Kanban:
    def __init__(self):
        self.cards = []
        self.creates = 0
        self.lose_ack = False

    def lookup(self, board, key):
        return [c for c in self.cards if c["idempotency_key"] == key]

    def legacy_feedback_lookup(self, board, identity):
        return []

    def prepare_create(self, card):
        pass

    def create(self, card):
        self.creates += 1
        value = {
            **card.as_dict(),
            "id": str(self.creates),
            "status": "triage",
            "model_override": None,
            "provider_override": None,
            "reasoning_effort": None,
        }
        self.cards.append(value)
        if self.lose_ack:
            raise TimeoutError("synthetic ACK loss")
        return value["id"]


def run(tmp_path, gh=None, kb=None, config=None):
    import sqlite3

    from github_pr_feedback.intake_only import scan_intake

    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS feedback_receipts(repository TEXT, pr_number INTEGER, feedback_kind TEXT, feedback_id TEXT, head_sha TEXT, status TEXT, task_id TEXT)"
        )
    return scan_intake(
        config or policy(tmp_path),
        gh or GitHub(),
        kb or Kanban(),
        tmp_path / "receipts.sqlite3",
        now=NOW,
        legacy_path=legacy,
    )


def test_new_card_is_triage_router_metadata_only_and_deduplicated(tmp_path):
    kb = Kanban()
    gh = GitHub(body="untrusted fix/push instructions with sensitive prose")
    assert run(tmp_path, gh, kb)["status"] == "ok"
    card = kb.cards[0]
    assert card["assignee"] == "task-intake-router"
    assert card["board"] == "tradingbot-burndown"
    assert card["title"].startswith("PR feedback intake:")
    assert card["idempotency_key"].startswith("github-pr-intake:v1:")
    assert "sensitive prose" not in card["body"]
    assert "action" in card["body"] and "inspect" in card["body"]
    assert run(tmp_path, gh, kb)["status"] == "ok"
    assert kb.creates == 1


@pytest.mark.parametrize("enabled,broad", [(False, False), (True, True)])
def test_disabled_and_broad_enabled_gates_precede_access(tmp_path, enabled, broad):
    gh, kb = GitHub(), Kanban()
    result = run(tmp_path, gh, kb, replace(policy(tmp_path), enabled=enabled, broad_enabled=broad))
    assert result["status"] != "ok"
    assert gh.calls == [] and kb.creates == 0


@pytest.mark.parametrize("body", ["", "LGTM", "Thanks!"])
def test_no_action_means_no_card(tmp_path, body):
    kb = Kanban()
    assert run(tmp_path, GitHub(body=body), kb)["status"] == "ok"
    assert kb.creates == 0


def test_untrusted_reviewer_has_no_delivery(tmp_path):
    gh, kb = GitHub(), Kanban()
    gh.items = (replace(gh.items[0], reviewer=Reviewer("outsider")),)
    assert run(tmp_path, gh, kb)["status"] == "ok"
    assert kb.creates == 0


def test_partial_retrieval_does_not_create_or_claim_success(tmp_path):
    gh, kb = GitHub(), Kanban()
    gh.broken = True
    assert run(tmp_path, gh, kb)["status"] == "partial"
    assert kb.creates == 0


def test_ack_loss_reconciles_exact_existing_card_without_recreate(tmp_path):
    kb = Kanban()
    kb.lose_ack = True
    assert run(tmp_path, kb=kb)["status"] == "held"
    assert kb.creates == 1
    kb.lose_ack = False
    kb.cards[0]["status"] = "archived"
    assert run(tmp_path, kb=kb)["status"] == "ok"
    assert kb.creates == 1


def test_absent_after_uncertain_create_is_never_recreated(tmp_path):
    kb = Kanban()
    kb.lose_ack = True
    assert run(tmp_path, kb=kb)["status"] == "held"
    kb.cards.clear()
    kb.lose_ack = False
    assert run(tmp_path, kb=kb)["status"] == "held"
    assert kb.creates == 1


def test_wrong_scope_or_upstream_window_refuses_before_access(tmp_path):
    gh, kb = GitHub(), Kanban()
    assert (
        run(tmp_path, gh, kb, replace(policy(tmp_path), repository="other/repo"))["status"] != "ok"
    )
    assert gh.calls == []
    from github_pr_feedback.intake_only import scan_intake

    upstream = "NousResearch/hermes-agent"
    cfg = policy(tmp_path, upstream)
    assert (
        scan_intake(cfg, gh, kb, tmp_path / "upstream.sqlite3", now=NOW.replace(hour=11))["status"]
        != "ok"
    )
    assert gh.calls == []


@pytest.mark.parametrize("length", [41, 63])
def test_noncanonical_head_length_is_rejected_before_delivery(tmp_path, length):
    gh, kb = GitHub(), Kanban()
    gh.pr = replace(gh.pr, head_sha="a" * length)
    assert run(tmp_path, gh, kb)["status"] == "partial"
    assert kb.creates == 0


def test_corrupt_anchor_or_missing_store_holds_without_duplicate(tmp_path):
    kb = Kanban()
    assert run(tmp_path, kb=kb)["status"] == "ok"
    database = tmp_path / "receipts.sqlite3"
    anchor = tmp_path / "receipts.sqlite3.anchor"
    anchor.write_text("corrupt", encoding="utf-8")
    assert run(tmp_path, kb=kb)["status"] == "held"
    assert kb.creates == 1
    database.unlink()
    assert run(tmp_path, kb=kb)["status"] == "held"
    assert kb.creates == 1


def test_receipt_files_private_and_rollback_detected(tmp_path):
    import sqlite3
    import stat

    kb = Kanban()
    assert run(tmp_path, kb=kb)["status"] == "ok"
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    for name in ("receipts.sqlite3", "receipts.sqlite3.anchor", "receipts.sqlite3.lock"):
        file = tmp_path / name
        assert file.is_file() and stat.S_IMODE(file.stat().st_mode) == 0o600
    database = tmp_path / "receipts.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM receipts")
        connection.commit()
    assert run(tmp_path, kb=kb)["status"] == "held"
    assert kb.creates == 1


def _concurrent_scan(directory, counter, gate):
    class SharedKanban(Kanban):
        def create(self, card):
            with counter.get_lock():
                counter.value += 1
            return super().create(card)

    gate.wait(5)
    result = run(Path(directory), kb=SharedKanban())
    if result["status"] != "ok":
        raise RuntimeError("synthetic concurrent scan did not acknowledge")


def test_cross_process_serialization_prevents_duplicate_create(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("fork")
    counter = context.Value("i", 0)
    gate = context.Event()
    workers = [
        context.Process(target=_concurrent_scan, args=(str(tmp_path), counter, gate))
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    gate.set()
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    assert counter.value == 1


def test_truncated_feedback_is_partial_not_delivered(tmp_path):
    from github_pr_feedback.github_client import MAX_FEEDBACK_BODY_CHARS

    gh, kb = GitHub(body="x" * MAX_FEEDBACK_BODY_CHARS), Kanban()
    assert run(tmp_path, gh, kb)["status"] == "partial"
    assert kb.creates == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("current_run_id", "run"),
        ("worker_pid", 123),
        ("claim_lock", "claim"),
        ("skills", '["forced"]'),
        ("goal_mode", 1),
    ],
)
def test_active_claim_or_execution_override_ack_is_held(tmp_path, field, value):
    class UnexpectedKanban(Kanban):
        def create(self, card):
            task_id = super().create(card)
            self.cards[0][field] = value
            return task_id

    kb = UnexpectedKanban()
    assert run(tmp_path, kb=kb)["status"] == "held"
    assert kb.creates == 1


def test_new_receipt_dedup_preserves_any_head_delivery_semantics(tmp_path):
    kb, gh = Kanban(), GitHub()
    assert run(tmp_path, gh, kb)["status"] == "ok"
    gh.pr = replace(gh.pr, head_sha="b" * 40)
    assert run(tmp_path, gh, kb)["status"] == "ok"
    assert kb.creates == 1


def test_uncertain_delivery_on_older_head_holds_new_head(tmp_path):
    kb, gh = Kanban(), GitHub()
    kb.lose_ack = True
    assert run(tmp_path, gh, kb)["status"] == "held"
    gh.pr = replace(gh.pr, head_sha="b" * 40)
    kb.cards.clear()
    kb.lose_ack = False
    assert run(tmp_path, gh, kb)["status"] == "held"
    assert kb.creates == 1
