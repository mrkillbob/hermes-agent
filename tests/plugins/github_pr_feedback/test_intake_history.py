"""Plugin-owned qualification uses real legacy producers and generic SQLite snapshots."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from github_pr_feedback import intake_history as history
from github_pr_feedback.intake_only import IntakeError
from hermes_constants import get_default_hermes_root
from .support import actual_context


@pytest.fixture
def database():
    ctx = actual_context()
    return ctx, get_default_hermes_root() / "kanban.db"


def identity(**changes):
    value = {"repository": "acme/widgets", "pr_number": 17,
             "feedback_kind": "review_comment", "feedback_id": "99"}
    return {**value, **changes}


def add_rows(path, pairs):
    with closing(sqlite3.connect(path)) as db:
        for number, (key, body) in enumerate(pairs):
            db.execute("INSERT INTO tasks(id,title,body,status,assignee,workspace_kind,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (f"legacy-{number}", "inert", body, "archived", "owner", "dir", key, 100))
        db.commit()


def lookup(ctx, value=None):
    rows = ctx.kanban_readonly_snapshot("default", idempotency_prefix="github-pr-feedback:")["tasks"]
    return history.legacy_matches(rows, identity() if value is None else value)


@pytest.fixture
def legacy_producer():
    # Use the actual legacy receipt/hash/renderer, never a surrogate SQL or
    # a fake predicate that assumes the new lookup has proved completeness.
    from github_pr_feedback.cli import _kanban_create_argv, _kanban_reconcile_argv
    from github_pr_feedback.controller import KanbanTask, _intent_review_task, _receipt_idempotency_key
    from github_pr_feedback.policy import FeedbackReceipt
    from github_pr_feedback.repair_controller import _actions_needed_task

    def produce(*, repository="acme/widgets", kind="review_comment", feedback_id="99", head="a" * 40,
                suffix="", evidence=None, intent=False, actions=False, whole_json=False, reconcile=False):
        receipt = FeedbackReceipt(repository, 17, kind, feedback_id, head)
        policy = SimpleNamespace(board="feedback", assignee="legacy-owner", merge_maintainer=None)
        if intent:
            task = _intent_review_task(policy, receipt, "inert decision", Path("/inert"))
        elif actions:
            task = _actions_needed_task(policy, receipt, Path("/inert"),
                                        SimpleNamespace(base_branch="main", head_ref_name="inert"))
        else:
            data = evidence if evidence is not None else {
                "repository": receipt.repository, "pr_number": receipt.pr_number,
                "feedback_kind": receipt.feedback_kind, "feedback_id": receipt.feedback_id,
                "expected_head_sha": receipt.head_sha,
            }
            task = KanbanTask("inert", "inert", "feedback", "legacy-owner", Path("/inert"), head,
                              "inert", _receipt_idempotency_key(receipt) + suffix, data)
        argv = _kanban_reconcile_argv("inert-task", task) if reconcile else _kanban_create_argv(task)
        body = json.dumps(task.evidence) if whole_json else argv[argv.index("--body") + 1]
        return task.idempotency_key, body

    return produce



def test_complete_identity_index_binds_all_heads_case_and_intent_before_excluding(database, legacy_producer):
    ctx, path = database
    pairs = [legacy_producer(head="a" * 40), legacy_producer(head="b" * 40, suffix=":dispatch-2"),
             legacy_producer(repository="Acme/Widgets", head="c" * 40),
             legacy_producer(head="d" * 40, intent=True),
             legacy_producer(repository="other/repository", whole_json=True)]
    add_rows(path, pairs)
    rows = lookup(ctx)
    assert {row["idempotency_key"] for row in rows} == {key for key, _ in pairs[:4]}
    assert all(row["status"] == "archived" for row in rows)


def test_absence_requires_source_qualified_other_identities(database, legacy_producer):
    ctx, path = database
    add_rows(path, [legacy_producer(feedback_id="100"), legacy_producer(repository="other/repo")])
    assert lookup(ctx) == []


def test_typed_ci_and_actions_are_excluded_only_after_hash_binding(database, legacy_producer):
    ctx, path = database
    typed = {"repository": "acme/widgets", "pr_number": 17, "expected_head_sha": "a" * 40,
             "ci_receipt_id": "f" * 64}
    add_rows(path, [legacy_producer(kind="pr_repair", feedback_id="ci-receipt:" + "f" * 64,
                                   suffix=":typed-fixer-v3:dispatch-2", evidence=typed),
                    legacy_producer(kind="pr_actions_needed", feedback_id="github-action-required", actions=True)])
    assert lookup(ctx) == []


@pytest.mark.parametrize("problem", ["malformed", "unknown-suffix", "dispatch-one", "hash", "duplicate-json", "supervised", "deep-json"])
def test_unclassifiable_row_holds_whole_lookup_without_unrelated_data(database, legacy_producer, problem):
    ctx, path = database
    key, body = legacy_producer(repository="other/repository")
    variants = {
        "malformed": lambda: (key, "UNRELATED-PRIVATE-TASK-BODY"),
        "unknown-suffix": lambda: (key + ":unknown", body),
        "dispatch-one": lambda: (key + ":dispatch-1", body),
        "hash": lambda: (key, body.replace("other/repository", "forged/repository")),
        "duplicate-json": lambda: (key, '{"repository":"other/repository","repository":"forged/repository"}'),
        "supervised": lambda: legacy_producer(kind="pr_local_ci", feedback_id="local-ci-audit-v2:" + "b" * 40,
                                               suffix=":supervised-v4", evidence={"repository": "acme/widgets", "pr_number": 17,
                                                                                   "expected_head_sha": "a" * 40}),
        "deep-json": lambda: (key, '{"x":' + '[' * 12000 + '0' + ']' * 12000 + '}'),
    }
    add_rows(path, [legacy_producer(), variants[problem]()])
    with pytest.raises(IntakeError, match="^legacy data qualification required$"):
        lookup(ctx)


def test_supervised_reconstructible_identity_and_colon_intent(database, legacy_producer):
    ctx, path = database
    evidence = {"repository": "acme/widgets", "pr_number": 17, "expected_head_sha": "a" * 40}
    add_rows(path, [legacy_producer(kind="pr_local_ci", feedback_id="local-ci-audit-v2",
                                   suffix=":supervised-v4", evidence=evidence),
                    legacy_producer(feedback_id="99:colon", intent=True, reconcile=True)])
    assert lookup(ctx) == []
    assert len(lookup(ctx, identity(feedback_id="99:colon"))) == 1


@pytest.mark.parametrize("bound", ["rows", "scan-bytes", "output-bytes", "time"])
def test_plugin_qualification_bounds_hold_without_partial_result(database, legacy_producer, monkeypatch, bound):
    ctx, path = database
    add_rows(path, [legacy_producer(), legacy_producer(head="b" * 40)])
    patches = {"rows": ("_MAX_LEGACY_ROWS", 1), "scan-bytes": ("_MAX_LEGACY_SCAN_BYTES", 1),
               "output-bytes": ("_MAX_OUTPUT_BYTES", 1)}
    if bound == "time":
        ticks = iter([10.0, 12.0])
        monkeypatch.setattr(history, "_monotonic", lambda: next(ticks, 12.0))
    else:
        name, value = patches[bound]
        monkeypatch.setattr(history, name, value)
    with pytest.raises(IntakeError):
        lookup(ctx)


def test_qualification_deadline_is_checked_after_classifying_excluded_row(database, legacy_producer, monkeypatch):
    ctx, path = database
    add_rows(path, [legacy_producer(repository="other/repository")])
    classified = [False]
    original = history._qualified_legacy_identity
    def classify(task):
        value = original(task)
        classified[0] = True
        return value
    monkeypatch.setattr(history, "_qualified_legacy_identity", classify)
    monkeypatch.setattr(history, "_monotonic", lambda: 12.0 if classified[0] else 10.0)
    with pytest.raises(IntakeError):
        lookup(ctx)
