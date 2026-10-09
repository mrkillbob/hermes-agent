"""Authority boundaries for exact-head pull-request automation tasks."""

from pathlib import Path
import json

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _write_profile(home: Path, name: str, description: str) -> None:
    profile_dir = home / "profiles" / name
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.yaml").write_text(
        f"name: {name}\ndescription: {description!r}\n"
        + ("execution_authority: write\n" if name == "pr-repair-steward" else ""),
        encoding="utf-8",
    )


def _repair_body() -> str:
    return (
        '{"repository":"mrkillbob/luna-bot","pr_number":132,'
        '"expected_head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"action":"repair_and_push"}'
    )


@pytest.mark.parametrize("intent", ["typed_write", "read_title_write", "read_body_write",
                                   "read_metadata_write", "read_only", "read_unknown_write",
                                   "read_prohibition", "read_target", "read_mixed",
                                   "read_prohibition_multiline", "read_target_multiline",
                                   "read_metadata_write_multiline"])
def test_create_rejects_read_only_owner_for_atomic_pr_repair(kanban_home, intent):
    import hermes_cli.kanban_db_connect as _hermes_cli_kanban_db_connect
    _write_profile(
        kanban_home,
        "review-verification-steward",
        "Read-only verifier; never edits, pushes, replies, refreshes, or merges.",
    )

    payload = json.loads(_repair_body())
    title = "Repair and push ExampleApp PR #132"
    if intent != "typed_write":
        payload["action"] = "verify_ci_receipt"
        title = "Review exact-head CI evidence for PR #132"
    if intent in {"read_title_write", "read_unknown_write"}:
        title = "Repair and push ExampleApp PR #132"
    if intent == "read_prohibition":
        payload["instructions"] = "Review only. Do not push or reply."
    if intent == "read_prohibition_multiline":
        payload["instructions"] = "Review only.\nDo not push or reply."
    if intent == "read_target_multiline":
        payload["instructions"] = "Read only.\nReview the proposed fix."
    if intent == "read_metadata_write_multiline":
        payload["instructions"] = "Review the evidence.\nFix the repository."
    if intent in {"read_target", "read_mixed"}:
        title = "Review the proposed fix" + (" then push the repository" if intent == "read_mixed" else "")
    if intent == "read_metadata_write":
        payload["instructions"] = "Repair and push the repository."
    body = json.dumps(payload)
    if intent == "read_body_write":
        body = "Repair and push the repository.\n" + body
    owner = "unknown-steward" if intent == "read_unknown_write" else "review-verification-steward"
    with _hermes_cli_kanban_db_connect.connect() as conn:
        if intent in {"read_only", "read_prohibition", "read_target",
                      "read_prohibition_multiline", "read_target_multiline"}:
            tid = kb.create_task(conn, title=title, body=body, assignee=owner,
                                 idempotency_key="github-pr-feedback:review:132:abc")
            assert kb.get_task(conn, tid).assignee == owner
        else:
            with pytest.raises(ValueError, match="read-only profile|cannot verify write authority"):
                kb.create_task(conn, title=title, body=body, assignee=owner,
                               idempotency_key="github-pr-feedback:repair:132:abc")



def test_reassign_rejects_read_only_owner_and_preserves_current_owner(kanban_home):
    import hermes_cli.kanban_db_connect as _hermes_cli_kanban_db_connect
    _write_profile(
        kanban_home,
        "review-verification-steward",
        "Read-only verifier; never edits, pushes, replies, refreshes, or merges.",
    )
    _write_profile(
        kanban_home,
        "pr-repair-steward",
        "Repairs pull requests, pushes exact-head fixes, and posts factual replies.",
    )

    with _hermes_cli_kanban_db_connect.connect() as conn:
        tid = kb.create_task(
            conn,
            title="Resolve merge conflict and push PR #132",
            body=_repair_body(),
            assignee="pr-repair-steward",
            idempotency_key="github-pr-feedback:repair:132:abc",
        )
        with pytest.raises(ValueError, match="read-only profile"):
            kb.reassign_task(conn, tid, "review-verification-steward")
        assert kb.get_task(conn, tid).assignee == "pr-repair-steward"


def test_read_only_profile_may_own_exact_head_verification(kanban_home):
    import hermes_cli.kanban_db_connect as _hermes_cli_kanban_db_connect
    _write_profile(
        kanban_home,
        "review-verification-steward",
        "Read-only verifier; never edits, pushes, replies, refreshes, or merges.",
    )

    with _hermes_cli_kanban_db_connect.connect() as conn:
        tid = kb.create_task(
            conn,
            title="Review exact-head CI evidence for PR #132",
            body=(
                '{"repository":"mrkillbob/luna-bot","pr_number":132,'
                '"expected_head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
                '"action":"verify_ci_receipt"}'
            ),
            assignee="review-verification-steward",
            idempotency_key="github-pr-feedback:review:132:abc",
        )
        assert kb.get_task(conn, tid).assignee == "review-verification-steward"


def test_blocked_intent_review_negative_contract_may_use_read_only_owner(
    kanban_home,
):
    import hermes_cli.kanban_db_connect as _hermes_cli_kanban_db_connect
    _write_profile(
        kanban_home,
        "intent-review-readonly-test",
        "Read-only intent reviewer; never edits, pushes, replies, or merges.",
    )
    body = (
        '{"repository":"mrkillbob/luna-bot","pr_number":132,'
        '"expected_head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
        "\nDo not edit, push, reply, approve, or merge. "
        "Record only the operator intent decision."
    )

    with _hermes_cli_kanban_db_connect.connect() as conn:
        tid = kb.create_task(
            conn,
            title="Intent review for PR #132",
            body=body,
            assignee="intent-review-readonly-test",
            idempotency_key="github-pr-feedback:intent-review:repo:132:abc",
            initial_status="blocked",
        )
        assert kb.get_task(conn, tid).status == "blocked"


def test_intent_review_exception_does_not_allow_runnable_write_task(kanban_home):
    import hermes_cli.kanban_db_connect as _hermes_cli_kanban_db_connect
    _write_profile(
        kanban_home,
        "intent-review-readonly-test",
        "Read-only intent reviewer; never edits, pushes, replies, or merges.",
    )
    with _hermes_cli_kanban_db_connect.connect() as conn, pytest.raises(ValueError, match="read-only profile"):
        kb.create_task(
            conn,
            title="Intent review for PR #132",
            body=_repair_body() + "\nRepair and push the exact head.",
            assignee="intent-review-readonly-test",
            idempotency_key="github-pr-feedback:intent-review:repo:132:abc",
            initial_status="running",
        )


@pytest.mark.parametrize("change", [
    "persisted_reader", "body_write_reader", "assign_reader", "erase_identity",
    "repository", "pr_number", "expected_head_sha", "action", "metadata",
    "ordinary", "unknown_owner", "rendered_head", "rendered_metadata", "profile_switch",
    "release_audit", "release_final", "review_push", "review_and_delete", "verify_ci_receipt",
    "rendered_nl_repository", "rendered_nl_pr_number", "rendered_nl_expected_head_sha",
    "rendered_nl_action", "rendered_nl_metadata", "rendered_nl_metadata_multiple",
])
def test_specification_retains_exact_identity_and_effective_authority(kanban_home, monkeypatch, change):
    from hermes_cli.kanban_db_connect import connect

    for name, authority in (("writer", "write"), ("reader", "read_only")):
        profile = kanban_home / "profiles" / name
        profile.mkdir(parents=True)
        (profile / "profile.yaml").write_text(f"execution_authority: {authority}\n")
    if change in {"release_audit", "release_final"}:
        from types import SimpleNamespace

        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "plugins/github-pr-feedback"))
        from github_pr_feedback.cli import _kanban_create_argv
        from github_pr_feedback.policy import ReleaseMaintenanceLane, ReleaseMaintenancePolicy, RepositoryTarget
        from github_pr_feedback.release_maintenance import ReleaseMaintenanceController

        lane = ReleaseMaintenanceLane("local-test", "reader", ("python", "-V"))
        policy = ReleaseMaintenancePolicy(assignee="reader", repository="acme/widgets",
                                          base_branch="main", quiet_period_seconds=900,
                                          max_runtime_seconds=7200, lanes=(lane,))
        target = RepositoryTarget(base_repository="acme/widgets", head_repository="acme/widgets",
                                  local_path=kanban_home, owner_login="owner", branch_prefixes=("codex/",))
        controller = ReleaseMaintenanceController(
            policy, target, None, None, None,
            SimpleNamespace(prepare_maintenance_worktree=lambda *_args: kanban_home),
            control_home=kanban_home,
        )
        task = controller._audit_task("a" * 40, lane) if change == "release_audit" else controller._final_task("a" * 40)
        argv = _kanban_create_argv(task)
        body = argv[argv.index("--body") + 1]
        with connect() as conn:
            tid = kb.create_task(conn, title=task.title, body=body, assignee=task.assignee,
                                 idempotency_key=task.idempotency_key, triage=True)
            assert kb.specify_triage_task(conn, tid, title="Clarified release audit")
            assert kb.get_task(conn, tid).body == body
        return
    if change == "profile_switch":
        other_home = kanban_home.parent / "other-home"
        profile = other_home / "profiles" / "writer"
        profile.mkdir(parents=True)
        (profile / "profile.yaml").write_text("execution_authority: read_only\n")
        for home, read_only in ((kanban_home, False), (other_home, True), (kanban_home, False)):
            monkeypatch.setenv("HERMES_HOME", str(home))
            kb.init_db()
            with connect() as conn:
                tid = kb.create_task(conn, title="Ordinary scope", body="Report.",
                                     assignee="writer", triage=True)
                if read_only:
                    with pytest.raises(ValueError, match="read-only profile"):
                        kb.specify_triage_task(conn, tid, body=_repair_body())
                    assert kb.get_task(conn, tid).status == "triage"
                else:
                    assert kb.specify_triage_task(conn, tid, body=_repair_body())
                    assert kb.get_task(conn, tid).body == _repair_body()
        return
    original = _repair_body()
    if change in {"body_write_reader", "ordinary", "review_push", "review_and_delete", "verify_ci_receipt"}:
        original = "Ordinary report."
    updates = {"title": "Clarified scope"}
    rejected = change not in {"metadata", "ordinary", "rendered_metadata", "verify_ci_receipt",
                              "rendered_nl_metadata", "rendered_nl_metadata_multiple"}
    if change == "body_write_reader":
        updates = {"body": _repair_body(), "assignee": "reader"}
    elif change in {"review_push", "review_and_delete", "verify_ci_receipt"}:
        payload = json.loads(_repair_body())
        payload["action"] = change
        updates = {"body": json.dumps(payload), "assignee": "reader"}
    elif change in {"assign_reader", "unknown_owner"}:
        updates = {"assignee": "reader" if change == "assign_reader" else "unknown"}
    elif change == "erase_identity":
        updates = {"body": "Untyped review prose."}
    elif change in {"repository", "pr_number", "expected_head_sha", "action", "metadata"}:
        payload = json.loads(original)
        payload.update({change: {
            "repository": "other/repo", "pr_number": 133,
            "expected_head_sha": "b" * 40, "action": "verify_ci_receipt",
            "metadata": "Additional local evidence.",
        }[change]})
        updates = {"body": json.dumps(payload)}
    elif change.startswith("rendered_nl_"):
        field = change.removeprefix("rendered_nl_")
        payload = json.loads(original)
        if field.startswith("metadata"):
            payload.pop("action")  # Retain legacy rendered-card authority policy.
        prefix = "Verify local evidence.\nCanonical PR audit receipt (JSON):\n"
        suffix = "\r\n \t\n\n" if field == "metadata_multiple" else "\n"
        original = prefix + json.dumps(payload) + suffix
        payload.update({
            "repository": {"repository": "other/repo"},
            "pr_number": {"pr_number": 133},
            "expected_head_sha": {"expected_head_sha": "b" * 40},
            "action": {"action": "verify_ci_receipt"},
            "metadata": {"diagnostic_note": "Additional local evidence."},
            "metadata_multiple": {"diagnostic_note": "Additional local evidence."},
        }[field])
        updates = {"body": prefix + json.dumps(payload) + suffix}
    elif change.startswith("rendered_"):
        payload = json.loads(original)
        payload.pop("action")
        prefix = "Verify local evidence.\nCanonical PR audit receipt (JSON):\n"
        original = prefix + json.dumps(payload)
        payload.update({"expected_head_sha": "b" * 40} if change == "rendered_head"
                       else {"diagnostic_note": "Additional local evidence."})
        updates = {"body": prefix + json.dumps(payload)}
    with connect() as conn:
        tid = kb.create_task(conn, title="Original scope", body=original,
                             assignee="writer", triage=True)
        if change == "persisted_reader":
            conn.execute("UPDATE tasks SET assignee = 'reader' WHERE id = ?", (tid,))
            conn.commit()
        before = {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("tasks", "task_links", "task_events", "task_comments")
        }
        if rejected:
            with pytest.raises(ValueError):
                kb.specify_triage_task(conn, tid, author="specifier", **updates)
            assert {
                table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in before
            } == before
        else:
            assert kb.specify_triage_task(conn, tid, author="specifier", **updates)
            task = kb.get_task(conn, tid)
            assert task.body == updates.get("body", original)
            assert task.title == updates.get("title", "Original scope")
            assert task.assignee == updates.get("assignee", "writer")
            assert task.status in {"todo", "ready"}


@pytest.mark.parametrize("entrypoint", ["facade", "graph"])
@pytest.mark.parametrize("body", [_repair_body(), _repair_body().replace(
    "repair_and_push", "verify_ci_receipt"), "Ordinary work.",
    "child_read_title_write", "child_read_body_write", "child_read_metadata_write", "child_read_only"])
def test_decomposition_cannot_split_atomic_root_or_write_child(kanban_home, entrypoint, body):
    from hermes_cli.kanban_db_connect import connect
    from hermes_cli.kanban_db_graph import decompose_triage_task

    profile = kanban_home / "profiles" / "writer"
    profile.mkdir(parents=True)
    (profile / "profile.yaml").write_text("execution_authority: write\n")
    reader = kanban_home / "profiles" / "reader"
    reader.mkdir(parents=True)
    (reader / "profile.yaml").write_text("execution_authority: read_only\n")
    decompose = kb.decompose_triage_task if entrypoint == "facade" else decompose_triage_task
    with connect() as conn:
        root_body = "Ordinary work." if body.startswith("child_read_") else body
        tid = kb.create_task(conn, title="Root scope", body=root_body, assignee="writer", triage=True)
        before = {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("tasks", "task_links", "task_events", "task_comments")
        }
        children = [{"title": "Child", "body": "Ordinary work.", "assignee": "reader"}]
        if body.startswith("child_read_"):
            payload = json.loads(_repair_body())
            payload["action"] = "verify_ci_receipt"
            if body == "child_read_metadata_write":
                payload["instructions"] = "Repair and push the repository."
            child_body = json.dumps(payload)
            if body == "child_read_body_write":
                child_body = "Repair and push the repository.\n" + child_body
            children = [{"title": "Repair and push the repository." if body == "child_read_title_write"
                         else "Review exact-head CI evidence", "body": child_body, "assignee": "reader"}]
        if root_body != "Ordinary work." or body.startswith("child_read_") and body != "child_read_only":
            expected_error = "atomic PR automation" if root_body != "Ordinary work." else "read-only profile"
            with pytest.raises(ValueError, match=expected_error):
                decompose(conn, tid, root_assignee="reader", children=children,
                          author="decomposer", auto_promote=False)
        else:
            bad_children = children + [{"title": "Repair", "body": _repair_body(),
                                       "assignee": "reader"}]
            with pytest.raises(ValueError, match="read-only profile"):
                decompose(conn, tid, root_assignee="writer", children=bad_children,
                          author="decomposer", auto_promote=False)
        assert {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in before
        } == before
        if body in {"Ordinary work.", "child_read_only"}:
            child_ids = decompose(conn, tid, root_assignee="writer", children=children,
                                  author="decomposer", auto_promote=False)
            assert len(child_ids) == len(children)
            assert kb.get_task(conn, tid).status == "todo"
            assert kb.get_task(conn, child_ids[0]).assignee == "reader"
