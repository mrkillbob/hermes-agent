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
                                   "read_metadata_write_multiline", "rendered_unknown_write",
                                   "rendered_read_unknown", "read_edit", "read_approve", "read_merge",
                                   "read_prohibited_mutations", "read_merge_target", "ordinary_pr_record",
                                   "producer_missing_repository", "producer_missing_pr_number",
                                   "producer_missing_head"])
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
    if intent in {"rendered_unknown_write", "rendered_read_unknown"}:
        payload.pop("action")
    if intent == "rendered_unknown_write":
        title = "Repair and push ExampleApp PR #132"
    if intent in {"read_edit", "read_approve", "read_merge"}:
        payload["instructions"] = {"read_edit": "Edit the source", "read_approve": "Approve it",
                                   "read_merge": "Merge this pull request"}[intent]
    if intent == "read_prohibited_mutations":
        payload["instructions"] = "Do not edit, approve, or merge."
    if intent == "read_merge_target":
        title = "Inspect the proposed merge conflicts"
    if intent == "ordinary_pr_record":
        payload = {"pr_number": 12, "note": "summarize this imported record"}
        title = "Summarize imported record"
    if intent.startswith("producer_missing_"):
        payload.pop({"producer_missing_repository": "repository",
                     "producer_missing_pr_number": "pr_number",
                     "producer_missing_head": "expected_head_sha"}[intent])
    body = json.dumps(payload)
    if intent in {"rendered_unknown_write", "rendered_read_unknown"}:
        body = ("Repair and push the repository." if intent == "rendered_unknown_write"
                else "Review exact-head evidence only.") + "\n" + body
    if intent == "read_body_write":
        body = "Repair and push the repository.\n" + body
    owner = ("unknown-steward" if intent in {"read_unknown_write", "rendered_unknown_write",
                                              "rendered_read_unknown"}
             else "review-verification-steward")
    with _hermes_cli_kanban_db_connect.connect() as conn:
        if intent in {"read_only", "read_prohibition", "read_target",
                      "read_prohibition_multiline", "read_target_multiline", "rendered_read_unknown",
                      "read_prohibited_mutations", "read_merge_target", "ordinary_pr_record"}:
            tid = kb.create_task(conn, title=title, body=body, assignee=owner,
                                 idempotency_key=("imported-record:12" if intent == "ordinary_pr_record"
                                                  else "github-pr-feedback:review:132:abc"))
            assert kb.get_task(conn, tid).assignee == owner
        else:
            error = ("requires an exact PR identity" if intent.startswith("producer_missing_")
                     else "read-only profile|cannot verify write authority")
            with pytest.raises(ValueError, match=error):
                kb.create_task(conn, title=title, body=body, assignee=owner,
                               idempotency_key="github-pr-feedback:repair:132:abc")



@pytest.mark.parametrize("path", ["manual", "worker_block", "router", "generated", "default"])
@pytest.mark.parametrize("authority", ["read_only", "unknown", "write"])
@pytest.mark.parametrize("rendered", [False, True])
def test_reassign_rejects_read_only_owner_and_preserves_current_owner(
    kanban_home, monkeypatch, path, authority, rendered,
):
    from types import SimpleNamespace
    import hermes_cli.kanban_db_connect as _hermes_cli_kanban_db_connect
    from hermes_cli import kanban_db_dispatch as dispatch
    from hermes_cli import kanban_repair_routing as repair_routing
    from hermes_cli import kanban_worker_routing as routing

    target = "task-intake-router" if path == "worker_block" else "review-verification-steward"
    _write_profile(kanban_home, target,
                   "Read-only verifier" if authority == "read_only" else "Task owner")
    if authority == "write":
        profile = kanban_home / "profiles" / target / "profile.yaml"
        profile.write_text(profile.read_text() + "execution_authority: write\n")
    _write_profile(kanban_home, "pr-repair-steward", "Repairs pull requests and pushes fixes")
    payload = json.loads(_repair_body())
    if rendered:
        payload.pop("action")
    body = ("Repair and push the repository.\n" if rendered else "") + json.dumps(payload)
    monkeypatch.setattr(repair_routing, "repair_profile_for_task", lambda *_: target)
    result = SimpleNamespace(auto_reassigned_invalid=[], routed_to_specialist=[])
    with _hermes_cli_kanban_db_connect.connect() as conn:
        tid = kb.create_task(conn, title="Repair and push PR #132", body=body,
                             assignee="pr-repair-steward",
                             idempotency_key="github-pr-feedback:repair:132:abc")
        if path == "router":
            conn.execute("UPDATE tasks SET assignee = 'task-intake-router' WHERE id = ?", (tid,))
        elif path == "generated":
            conn.execute("UPDATE tasks SET assignee = 'missing-profile' WHERE id = ?", (tid,))
        elif path == "default":
            conn.execute("UPDATE tasks SET assignee = NULL WHERE id = ?", (tid,))
        conn.commit()
        before = dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone())
        events = len(kb.list_events(conn, tid))
        if path == "manual":
            if authority != "write":
                with pytest.raises(ValueError, match="read-only profile|cannot verify write authority"):
                    kb.reassign_task(conn, tid, target)
            else:
                kb.reassign_task(conn, tid, target)
        elif path == "worker_block":
            landed = kb.route_worker_block_to_orchestrator(conn, tid, reason="provider failure")
            assert landed[0] == (authority == "write")
        elif path == "router":
            landed = routing.route_orchestrator_task(conn, before, dry_run=False, result=result)
            assert landed == (target if authority == "write" else None)
        elif path == "generated":
            landed = routing.recover_generated_assignee(conn, before, target, dry_run=False, result=result)
            assert landed == (target if authority == "write" else "missing-profile")
        else:
            assert dispatch._apply_default_assignee(conn, tid, target, dry_run=False) == (authority == "write")
        after = dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone())
        if authority == "write":
            assert after["assignee"] == target
        else:
            assert after == before
            assert len(kb.list_events(conn, tid)) == events
            assert result.auto_reassigned_invalid == result.routed_to_specialist == []



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
    "local_ci_no_post", "local_ci_post", "reconcile_repository", "reconcile_pr_number",
    "reconcile_expected_head_sha", "reconcile_action", "reconcile_reader", "reconcile_unknown",
    "reconcile_metadata",
])
def test_specification_retains_exact_identity_and_effective_authority(kanban_home, monkeypatch, change):
    from hermes_cli.kanban_db_connect import connect

    for name, authority in (("writer", "write"), ("reader", "read_only")):
        profile = kanban_home / "profiles" / name
        profile.mkdir(parents=True)
        (profile / "profile.yaml").write_text(f"execution_authority: {authority}\n")
    if change in {"local_ci_no_post", "local_ci_post"}:
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "plugins/github-pr-feedback"))
        from github_pr_feedback.cli import _kanban_create_argv
        from github_pr_feedback.controller import PreparedWorktree, _local_ci_task
        from github_pr_feedback.policy import FeedbackReceipt, LocalCIAuditPolicy, PluginPolicy

        post = change == "local_ci_post"
        policy = PluginPolicy(enabled=True, targets={}, reviewer_logins=frozenset(),
                              reviewer_associations=frozenset(), include_self_feedback=False,
                              include_bot_feedback=False, auto_dispatch=False, not_before=None,
                              assignee="reader", board=None,
                              local_ci_audit=LocalCIAuditPolicy(assignee="reader", post_results=post))
        receipt = FeedbackReceipt("acme/widgets", 132, "pr_local_ci", "local-ci", "a" * 40)
        prepared = PreparedWorktree(kanban_home, "codex/test", "a" * 40)
        task = _local_ci_task(policy, receipt, prepared, control_home=kanban_home, post_results=post)
        argv = _kanban_create_argv(task)
        body = argv[argv.index("--body") + 1]
        assert "Do not publish, approve, or merge any change" in body
        with connect() as conn:
            tid = kb.create_task(conn, title=task.title, body=body, assignee=task.assignee,
                                 idempotency_key=task.idempotency_key, triage=True)
            assert kb.specify_triage_task(conn, tid, title="Clarified read-only audit")
            assert kb.get_task(conn, tid).assignee == "reader"
        return
    if change.startswith("reconcile_"):
        field = change.removeprefix("reconcile_")
        original = ("This card is intake-only and starts blocked; an operator must validate\n"
                    + _repair_body())
        payload = json.loads(_repair_body())
        if field in {"repository", "pr_number", "expected_head_sha", "action"}:
            payload[field] = {"repository": "other/widgets", "pr_number": 133,
                              "expected_head_sha": "b" * 40, "action": "verify_ci_receipt"}[field]
        payload["note"] = "Authorized clarification"
        body = "Repair and push the repository.\n" + json.dumps(payload)
        assignee = {"reader": "reader", "unknown": "missing-profile"}.get(field, "writer")
        with connect() as conn:
            tid = kb.create_task(conn, title="Repair PR #132", body=original, assignee="writer",
                                 idempotency_key="github-pr-feedback:repair:132:abc", initial_status="blocked")
            before = {table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                      for table in ("tasks", "task_events", "task_comments", "task_links")}
            args = dict(idempotency_key="github-pr-feedback:repair:132:abc", head_sha="a" * 40,
                        body=body, assignee=assignee, workspace_path=str(kanban_home),
                        branch_name="codex/test", max_retries=2, max_runtime_seconds=60)
            if field == "metadata":
                assert kb.reconcile_legacy_dispatch_task(conn, tid, **args)
                task = kb.get_task(conn, tid)
                assert task.body == body and task.assignee == "writer" and task.status == "ready"
            else:
                with pytest.raises(ValueError, match="preserve exact|read-only profile|cannot verify write authority"):
                    kb.reconcile_legacy_dispatch_task(conn, tid, **args)
                assert {table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                        for table in before} == before
        return
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
