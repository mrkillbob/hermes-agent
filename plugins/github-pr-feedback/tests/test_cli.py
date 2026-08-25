from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from github_pr_feedback.controller import KanbanTask
from github_pr_feedback.ledger import FeedbackLedger
from github_pr_feedback.merge_controller import MergeDecision
from github_pr_feedback.policy import FeedbackReceipt, PullRequest


def _plugin_module():
    root = Path(__file__).resolve().parents[1]
    name = "test_github_pr_feedback_plugin"
    spec = importlib.util.spec_from_file_location(
        name, root / "__init__.py", submodule_search_locations=[str(root)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class RecordingContext:
    def __init__(self, settings: dict[str, object] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.settings = settings or {}
        self.config_reads: list[str] = []

    def register_cli_command(self, **kwargs: object) -> None:
        self.calls.append(kwargs)

    def get_config(self, key: str, default: object = None) -> object:
        self.config_reads.append(key)
        return self.settings.get(key, default)


def test_register_exposes_the_github_feedback_cli_command() -> None:
    context = RecordingContext()

    _plugin_module().register(context)

    assert len(context.calls) == 1
    command = context.calls[0]
    assert command["name"] == "github-pr-feedback"
    assert command["help"] == "Scan governed GitHub pull-request feedback"
    assert callable(command["setup_fn"])
    assert callable(command["handler_fn"])
    parser = argparse.ArgumentParser()
    command["setup_fn"](parser)
    assert parser.parse_args(["scan"]).github_pr_feedback_action == "scan"


class RecordingKanbanRunner:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def run(self, argv: list[str]):
        from github_pr_feedback.cli import KanbanCommandResult

        self.calls.append(argv)
        return KanbanCommandResult(self.returncode, self.stdout)


class RecordingDoctorRunner:
    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str]]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def which(self, executable: str) -> str | None:
        return {
            "gh": "/opt/tools/gh",
            "hermes": "/opt/tools/hermes",
            "git": "/opt/tools/git",
        }.get(executable)

    def run(self, argv: list[str]):
        from github_pr_feedback.cli import DoctorCommandResult

        self.calls.append(argv)
        returncode, stdout = self.responses[tuple(argv)]
        return DoctorCommandResult(returncode, stdout)


def kanban_task() -> KanbanTask:
    hostile = "--skill github-auth; gh pr merge 17; $(printenv GH_TOKEN)"
    return KanbanTask(
        title="GitHub PR feedback: acme/widgets#17",
        instructions="GitHub push/reply/merge require operator approval.",
        board="repairs",
        assignee="repair-agent",
        repository_path=Path("/repositories/widgets"),
        head_sha="a" * 40,
        branch="hermes/github-pr-feedback/receipt-branch",
        idempotency_key="github-pr-feedback:key",
        evidence={"untrusted": True, "body": hostile},
    )


def test_kanban_client_creates_only_a_blocked_card_with_inert_hostile_evidence() -> None:
    from github_pr_feedback.cli import KanbanSubprocessClient

    runner = RecordingKanbanRunner('{"id": "task-123"}')

    task_id = KanbanSubprocessClient(runner).create_or_get_task(kanban_task())

    assert task_id == "task-123"
    argv = runner.calls == [
        [
            "hermes",
            "kanban",
            "--board",
            "repairs",
            "create",
            "GitHub PR feedback: acme/widgets#17",
            "--body",
            runner.calls[0][7],
            "--assignee",
            "repair-agent",
            "--workspace",
            "dir:/repositories/widgets",
            "--idempotency-key",
            "github-pr-feedback:key",
            "--max-retries",
            "1",
            "--initial-status",
            "blocked",
            "--json",
        ]
    ]
    assert argv
    assert "operator approval" in runner.calls[0][7]
    assert '"untrusted": true' in runner.calls[0][7]
    assert "--skill github-auth; gh pr merge 17; $(printenv GH_TOKEN)" in runner.calls[0][7]
    assert "--skill" not in runner.calls[0]
    assert "github-code-review" not in runner.calls[0]


def test_kanban_client_dispatches_an_opted_in_repair_as_ready() -> None:
    from github_pr_feedback.cli import KanbanSubprocessClient

    runner = RecordingKanbanRunner('{"id": "task-123"}')
    task = replace(kanban_task(), initial_status="running", max_retries=3)

    task_id = KanbanSubprocessClient(runner).create_or_get_task(task)

    assert task_id == "task-123"
    status_index = runner.calls[0].index("--initial-status") + 1
    assert runner.calls[0][status_index] == "running"
    retries_index = runner.calls[0].index("--max-retries") + 1
    assert runner.calls[0][retries_index] == "3"


def test_kanban_client_uses_the_task_specific_evidence_heading() -> None:
    from github_pr_feedback.cli import KanbanSubprocessClient

    runner = RecordingKanbanRunner('{"id": "task-123"}')
    task = replace(
        kanban_task(),
        evidence_heading="Canonical PR audit receipt (JSON)",
        evidence={"repository": "acme/widgets", "pr_number": 17},
    )

    KanbanSubprocessClient(runner).create_or_get_task(task)

    assert "Canonical PR audit receipt (JSON):" in runner.calls[0][7]
    assert "Untrusted evidence" not in runner.calls[0][7]


def test_auto_dispatch_argv_is_accepted_by_the_real_kanban_parser() -> None:
    from github_pr_feedback.cli import _kanban_create_argv
    from hermes_cli.kanban import build_parser

    root = argparse.ArgumentParser()
    subcommands = root.add_subparsers(dest="command", required=True)
    build_parser(subcommands)
    task = replace(kanban_task(), initial_status="running", max_retries=3)

    parsed = root.parse_args(_kanban_create_argv(task)[1:])

    assert parsed.command == "kanban"
    assert parsed.kanban_action == "create"
    assert parsed.initial_status == "running"
    assert parsed.max_retries == 3


@pytest.mark.parametrize("stdout", ["{}", "[]", '{"id": ""}', '{"id": 17}', "not json"])
def test_kanban_client_fails_closed_on_an_invalid_create_response(stdout: str) -> None:
    from github_pr_feedback.cli import KanbanSubprocessClient

    with pytest.raises(RuntimeError, match="Kanban task creation failed"):
        KanbanSubprocessClient(RecordingKanbanRunner(stdout)).create_or_get_task(kanban_task())


def test_ledger_status_counts_each_receipt_state(tmp_path: Path) -> None:
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    claimed = FeedbackReceipt("acme/widgets", 1, "issue_comment", "claimed", "a" * 40)
    completed = FeedbackReceipt("acme/widgets", 2, "review", "completed", "b" * 40)
    failed = FeedbackReceipt("acme/widgets", 3, "review_comment", "failed", "c" * 40)
    claimed_lease = _claim(ledger, claimed, owner="claimed")
    completed_lease = _claim(ledger, completed, owner="completed")
    assert claimed_lease is not None and completed_lease is not None
    ledger.finalize(completed, "kanban-2", completed_lease)
    failed_lease = _claim(ledger, failed, owner="failed")
    assert failed_lease is not None
    ledger.fail(failed, "Kanban unavailable", failed_lease)

    assert ledger.status_counts() == {"claimed": 1, "completed": 1, "failed": 1}

    ledger.close()


def test_scan_reads_disabled_policy_through_the_plugin_config_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    context = RecordingContext({"enabled": False})
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    _plugin_module().register(context)
    command = context.calls[0]
    parser = argparse.ArgumentParser()
    command["setup_fn"](parser)

    exit_code = command["handler_fn"](parser.parse_args(["scan"]))

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "created": 0,
        "skipped": {},
        "status": "ok",
    }
    assert context.config_reads == ["enabled"]


def test_cli_exposes_status_doctor_and_an_exact_immutable_retry_identity() -> None:
    context = RecordingContext()
    _plugin_module().register(context)
    parser = argparse.ArgumentParser()
    context.calls[0]["setup_fn"](parser)

    assert parser.parse_args(["status"]).github_pr_feedback_action == "status"
    assert parser.parse_args(["doctor"]).github_pr_feedback_action == "doctor"
    audit = parser.parse_args(
        [
            "audit-pr",
            "--repository",
            "acme/widgets",
            "--pr-number",
            "17",
            "--head-sha",
            "a" * 40,
            "--worktree",
            "/repositories/widgets",
        ]
    )
    assert audit.github_pr_feedback_action == "audit-pr"
    assert parser.parse_args(["merge-scan"]).github_pr_feedback_action == "merge-scan"
    assert parser.parse_args(["merge-status"]).github_pr_feedback_action == "merge-status"
    completed = parser.parse_args(
        [
            "complete-feedback",
            "--repository",
            "acme/widgets",
            "--pr-number",
            "17",
            "--feedback-kind",
            "review_comment",
            "--feedback-id",
            "120",
            "--receipt-head-sha",
            "a" * 40,
            "--resolved-head-sha",
            "b" * 40,
        ]
    )
    assert completed.github_pr_feedback_action == "complete-feedback"
    retry = parser.parse_args(
        [
            "retry",
            "--repository",
            "acme/widgets",
            "--pr-number",
            "17",
            "--feedback-kind",
            "review_comment",
            "--feedback-id",
            "120",
            "--head-sha",
            "a" * 40,
        ]
    )
    assert retry.github_pr_feedback_action == "retry"
    assert retry.repository == "acme/widgets"
    with pytest.raises(SystemExit):
        parser.parse_args(["retry", "--repository", "acme/widgets"])


def test_status_reports_durable_receipt_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    ledger = FeedbackLedger.for_current_profile()
    failed = FeedbackReceipt("acme/widgets", 1, "issue_comment", "failed", "a" * 40)
    failed_lease = _claim(ledger, failed)
    assert failed_lease is not None
    ledger.fail(failed, "Kanban unavailable", failed_lease)
    ledger.close()
    context = RecordingContext()
    _plugin_module().register(context)
    parser = argparse.ArgumentParser()
    context.calls[0]["setup_fn"](parser)

    exit_code = context.calls[0]["handler_fn"](parser.parse_args(["status"]))

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "claimed": 0,
        "completed": 0,
        "failed": 1,
    }


def test_doctor_reports_a_disabled_plugin_without_scanning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    context = RecordingContext({"enabled": False})
    _plugin_module().register(context)
    parser = argparse.ArgumentParser()
    context.calls[0]["setup_fn"](parser)

    exit_code = context.calls[0]["handler_fn"](parser.parse_args(["doctor"]))

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"status": "disabled"}
    assert context.config_reads == ["enabled"]


def test_namespaced_context_loads_assignee_rules_for_runtime_routing(tmp_path: Path) -> None:
    from github_pr_feedback.cli import _load_policy_from_context

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    settings = enabled_settings(repository)
    settings["assignee_rules"] = [
        {"assignee": "performance-patch-steward", "match_any": ["latency"]}
    ]

    policy = _load_policy_from_context(RecordingContext(settings))

    assert policy.assignee_for("Reduce latency") == "performance-patch-steward"


def test_namespaced_context_preserves_auto_dispatch_and_local_ci_audit_settings(
    tmp_path: Path,
) -> None:
    from github_pr_feedback.cli import _load_policy_from_context

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    settings = enabled_settings(repository)
    settings["auto_dispatch"] = True
    settings["local_ci_audit"] = {
        "enabled": True,
        "assignee": "pr-local-ci-auditor",
        "post_results": True,
    }

    policy = _load_policy_from_context(RecordingContext(settings))

    assert policy.auto_dispatch is True
    assert policy.local_ci_audit is not None
    assert policy.local_ci_audit.assignee == "pr-local-ci-auditor"


def test_namespaced_context_preserves_strict_merge_maintainer_settings(tmp_path: Path) -> None:
    from github_pr_feedback.cli import _load_policy_from_context

    repository = tmp_path / "repository"
    deployment = tmp_path / "deployment"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    subprocess.run(["git", "init", "--quiet", str(deployment)], check=True)
    settings = enabled_settings(repository)
    settings["merge_maintainer"] = {
        "enabled": True,
        "assignee": "pr-merge-maintainer",
        "repository": "acme/widgets",
        "author_login": "owner",
        "base_branch": "stable",
        "merge_methods": ["squash", "rebase", "merge"],
        "receipt_max_age_seconds": 3600,
        "report_only": True,
        "post_merge": {"enabled": False},
    }

    loaded = _load_policy_from_context(RecordingContext(settings))

    assert loaded.merge_maintainer is not None
    assert loaded.merge_maintainer.assignee == "pr-merge-maintainer"
    assert loaded.merge_maintainer.merge_methods == ("squash", "rebase", "merge")
    assert loaded.merge_maintainer.report_only is True


def test_merge_maintainer_task_has_no_model_merge_authority(tmp_path: Path) -> None:
    from github_pr_feedback.cli import _load_policy_from_context, _merge_maintainer_task

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    settings = enabled_settings(repository)
    settings["merge_maintainer"] = {
        "enabled": True,
        "assignee": "pr-merge-maintainer",
        "repository": "acme/widgets",
        "author_login": "owner",
        "base_branch": "stable",
        "merge_methods": ["squash", "rebase", "merge"],
        "receipt_max_age_seconds": 3600,
        "report_only": True,
        "post_merge": {"enabled": False},
    }
    loaded = _load_policy_from_context(RecordingContext(settings))
    pull = PullRequest(
        17,
        "OPEN",
        "acme/widgets",
        "acme/widgets",
        "owner",
        "codex/fix",
        "a" * 40,
    )
    decision = MergeDecision(False, ("ci_receipt_missing",), None, "d" * 64)

    task = _merge_maintainer_task(loaded, pull, decision)

    assert task.assignee == "pr-merge-maintainer"
    assert task.initial_status == "running"
    assert task.evidence["blockers"] == ["ci_receipt_missing"]
    assert "Do not edit source, push, reply, approve, merge" in task.instructions
    assert "Model output cannot waive" in task.instructions


def test_merge_scan_skips_expensive_github_reads_without_exact_head_ci_receipt(
    tmp_path: Path,
) -> None:
    from github_pr_feedback.cli import _load_policy_from_context, _run_merge_scan

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    manifest = repository / "tests" / "manifests" / "test_lanes.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("version = 1\n", encoding="utf-8")
    settings = enabled_settings(repository)
    settings["merge_maintainer"] = {
        "enabled": True,
        "assignee": "pr-merge-maintainer",
        "repository": "acme/widgets",
        "author_login": "owner",
        "base_branch": "stable",
        "merge_methods": ["squash"],
        "receipt_max_age_seconds": 3600,
        "report_only": False,
        "post_merge": {"enabled": False},
    }
    policy = _load_policy_from_context(RecordingContext(settings))

    class ListingOnlyGitHub:
        def list_open_pull_requests(self, repository: str, owner_login: str):
            return (
                PullRequest(
                    17,
                    "OPEN",
                    "acme/widgets",
                    "acme/widgets",
                    "owner",
                    "codex/fix",
                    "a" * 40,
                ),
            )

        def __getattr__(self, name: str):
            raise AssertionError(f"unexpected expensive GitHub read: {name}")

    class NoTasks:
        def create_or_get_task(self, task):
            raise AssertionError("missing CI receipt must not create an observability task")

    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    try:
        result = _run_merge_scan(
            policy,
            ledger,
            github=ListingOnlyGitHub(),
            kanban=NoTasks(),
        )
    finally:
        ledger.close()

    assert result["status"] == "ok"
    assert result["blocked"] == {"17": ["ci_receipt_missing"]}
    assert result["maintainer_tasks_created"] == 0


def test_merge_scan_reports_failed_exact_head_receipt_as_not_passing(
    tmp_path: Path,
) -> None:
    from github_pr_feedback.cli import _load_policy_from_context, _run_merge_scan

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    manifest = repository / "tests" / "manifests" / "test_lanes.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("version = 1\n", encoding="utf-8")
    settings = enabled_settings(repository)
    settings["merge_maintainer"] = {
        "enabled": True,
        "assignee": "pr-merge-maintainer",
        "repository": "acme/widgets",
        "author_login": "owner",
        "base_branch": "stable",
        "merge_methods": ["squash"],
        "receipt_max_age_seconds": 3600,
        "report_only": False,
        "post_merge": {"enabled": False},
    }
    policy = _load_policy_from_context(RecordingContext(settings))

    class ListingOnlyGitHub:
        def list_open_pull_requests(self, repository: str, owner_login: str):
            return (
                PullRequest(
                    17,
                    "OPEN",
                    "acme/widgets",
                    "acme/widgets",
                    "owner",
                    "codex/fix",
                    "a" * 40,
                ),
            )

        def __getattr__(self, name: str):
            raise AssertionError(f"unexpected expensive GitHub read: {name}")

    class FailedReceipt:
        status = "failed"

    class FailedLedger:
        def latest_ci_receipt(self, *args, **kwargs):
            return FailedReceipt()

    class NoTasks:
        def create_or_get_task(self, task):
            raise AssertionError("failed CI receipt must not create an observability task")

    result = _run_merge_scan(
        policy,
        FailedLedger(),
        github=ListingOnlyGitHub(),
        kanban=NoTasks(),
    )

    assert result["status"] == "ok"
    assert result["blocked"] == {"17": ["ci_receipt_not_passing"]}
    assert result["maintainer_tasks_created"] == 0


def test_doctor_read_only_verifies_every_runtime_dependency(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from github_pr_feedback.cli import DoctorProbe, _doctor

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    profile_root = tmp_path / "profile"
    (profile_root / "kanban" / "boards" / "repairs").mkdir(parents=True)
    (profile_root / "kanban" / "boards" / "repairs" / "board.json").write_text(
        '{"slug":"repairs"}\n', encoding="utf-8"
    )
    (profile_root / "profiles" / "repair-agent").mkdir(parents=True)
    (profile_root / "profiles" / "repair-agent" / "config.yaml").write_text(
        "profile: repair-agent\n", encoding="utf-8"
    )
    (profile_root / "profiles" / "pr-local-ci-auditor").mkdir(parents=True)
    (profile_root / "profiles" / "pr-local-ci-auditor" / "config.yaml").write_text(
        "profile: pr-local-ci-auditor\n", encoding="utf-8"
    )
    ledger_path = profile_root / "github-pr-feedback" / "ledger.sqlite3"
    settings = enabled_settings(repository)
    settings["local_ci_audit"] = {
        "enabled": True,
        "assignee": "pr-local-ci-auditor",
        "post_results": True,
    }
    context = RecordingContext(settings)
    responses = {
        ("/opt/tools/gh", "auth", "status", "--hostname", "github.com"): (0, ""),
        ("/opt/tools/hermes", "--version"): (0, "Hermes Agent test"),
        ("/opt/tools/git", "-C", str(repository), "rev-parse", "--show-toplevel"): (
            0,
            f"{repository}\n",
        ),
        ("/opt/tools/git", "-C", str(repository), "rev-parse", "--git-common-dir"): (
            0,
            ".git\n",
        ),
        ("/opt/tools/git", "-C", str(repository), "worktree", "list", "--porcelain"): (
            0,
            f"worktree {repository}\n",
        ),
    }
    runner = RecordingDoctorRunner(responses)
    before = profile_snapshot(profile_root)

    exit_code = _doctor(
        context,
        probe=DoctorProbe(profile_root, runner),
        ledger_path=ledger_path,
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ready"
    assert payload["checks"] == {
        "assignee": "ok",
        "board": "ok",
        "gh_auth": "ok",
        "gh_executable": "ok",
        "hermes_executable": "ok",
        "ledger_access": "ok",
        "repository_worktree": "ok",
    }
    assert runner.calls == [
        ["/opt/tools/gh", "auth", "status", "--hostname", "github.com"],
        ["/opt/tools/hermes", "--version"],
        ["/opt/tools/git", "-C", str(repository), "rev-parse", "--show-toplevel"],
        ["/opt/tools/git", "-C", str(repository), "rev-parse", "--git-common-dir"],
        ["/opt/tools/git", "-C", str(repository), "worktree", "list", "--porcelain"],
    ]
    assert profile_snapshot(profile_root) == before


def test_doctor_reports_degraded_but_still_runs_all_read_only_checks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from github_pr_feedback.cli import DoctorProbe, _doctor

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    ledger_path = profile_root / "github-pr-feedback" / "ledger.sqlite3"
    responses = {
        ("/opt/tools/gh", "auth", "status", "--hostname", "github.com"): (1, ""),
        ("/opt/tools/hermes", "--version"): (0, "Hermes Agent test"),
        ("/opt/tools/git", "-C", str(repository), "rev-parse", "--show-toplevel"): (
            0,
            f"{repository}\n",
        ),
        ("/opt/tools/git", "-C", str(repository), "rev-parse", "--git-common-dir"): (
            0,
            ".git\n",
        ),
        ("/opt/tools/git", "-C", str(repository), "worktree", "list", "--porcelain"): (
            0,
            f"worktree {repository}\n",
        ),
    }
    runner = RecordingDoctorRunner(responses)

    exit_code = _doctor(
        RecordingContext(enabled_settings(repository)),
        probe=DoctorProbe(profile_root, runner),
        ledger_path=ledger_path,
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "degraded"
    assert payload["checks"]["gh_auth"] == "failed"
    assert set(payload["checks"]) == {
        "assignee",
        "board",
        "gh_auth",
        "gh_executable",
        "hermes_executable",
        "ledger_access",
        "repository_worktree",
    }
    assert len(runner.calls) == 5


def test_doctor_requires_the_configured_local_ci_auditor_profile(tmp_path: Path) -> None:
    from github_pr_feedback.cli import DoctorProbe
    from github_pr_feedback.policy import load_policy

    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    profile_root = tmp_path / "profile"
    (profile_root / "profiles" / "repair-agent").mkdir(parents=True)
    (profile_root / "profiles" / "repair-agent" / "config.yaml").write_text(
        "profile: repair-agent\n", encoding="utf-8"
    )
    settings = enabled_settings(repository)
    settings["local_ci_audit"] = {
        "enabled": True,
        "assignee": "pr-local-ci-auditor",
        "post_results": True,
    }
    policy = load_policy(settings)
    responses = {
        ("/opt/tools/gh", "auth", "status", "--hostname", "github.com"): (0, ""),
        ("/opt/tools/hermes", "--version"): (0, "Hermes Agent test"),
        ("/opt/tools/git", "-C", str(repository), "rev-parse", "--show-toplevel"): (
            0,
            f"{repository}\n",
        ),
        ("/opt/tools/git", "-C", str(repository), "rev-parse", "--git-common-dir"): (
            0,
            ".git\n",
        ),
        ("/opt/tools/git", "-C", str(repository), "worktree", "list", "--porcelain"): (
            0,
            f"worktree {repository}\n",
        ),
    }
    probe = DoctorProbe(profile_root, RecordingDoctorRunner(responses))

    checks = probe.checks(policy, profile_root / "ledger.sqlite3")

    assert checks["assignee"] == "failed"


def test_retry_passes_the_exact_immutable_receipt_to_controller_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from github_pr_feedback import cli
    from github_pr_feedback.controller import ScanResult

    seen: list[FeedbackReceipt] = []

    class RevalidatingController:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def retry_failed(self, receipt: FeedbackReceipt) -> ScanResult:
            seen.append(receipt)
            return ScanResult(1, {})

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(cli, "ScanController", RevalidatingController)
    context = RecordingContext({"enabled": False})
    parser = argparse.ArgumentParser()
    cli.setup_cli(context, parser)

    exit_code = cli.handle_cli_with_context(
        context,
        parser.parse_args(
            [
                "retry",
                "--repository",
                "acme/widgets",
                "--pr-number",
                "17",
                "--feedback-kind",
                "review_comment",
                "--feedback-id",
                "120",
                "--head-sha",
                "a" * 40,
            ]
        )
    )

    assert exit_code == 0
    assert seen == [FeedbackReceipt("acme/widgets", 17, "review_comment", "120", "a" * 40)]
    assert json.loads(capsys.readouterr().out) == {
        "created": 1,
        "skipped": {},
        "status": "ok",
    }


@pytest.mark.parametrize("action", ["scan", "retry"])
def test_scan_and_retry_exit_nonzero_and_report_degraded_on_incomplete_work(
    action: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from github_pr_feedback import cli
    from github_pr_feedback.controller import ScanResult

    class DegradedController:
        def scan(self) -> ScanResult:
            return ScanResult(0, {"github_error": 1}, degraded=True)

        def retry_failed(self, _receipt: FeedbackReceipt) -> ScanResult:
            return ScanResult(0, {"dispatch_failed": 1}, degraded=True)

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(cli, "_controller", lambda _policy, _ledger: DegradedController())
    parser = argparse.ArgumentParser()
    cli.setup_cli(RecordingContext({"enabled": False}), parser)
    argv = [action]
    if action == "retry":
        argv.extend(
            [
                "--repository",
                "acme/widgets",
                "--pr-number",
                "17",
                "--feedback-kind",
                "review_comment",
                "--feedback-id",
                "120",
                "--head-sha",
                "a" * 40,
            ]
        )

    exit_code = cli.handle_cli_with_context(
        RecordingContext({"enabled": False}),
        parser.parse_args(argv),
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "degraded"
    assert payload["created"] == 0


def test_doctor_fails_closed_for_an_incomplete_enabled_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    context = RecordingContext({"enabled": True})
    _plugin_module().register(context)
    parser = argparse.ArgumentParser()
    context.calls[0]["setup_fn"](parser)

    exit_code = context.calls[0]["handler_fn"](parser.parse_args(["doctor"]))

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {"status": "invalid_configuration"}
    assert context.config_reads == [
        "enabled",
        "repositories",
        "reviewer_logins",
        "reviewer_associations",
        "include_self_feedback",
        "include_bot_feedback",
        "auto_dispatch",
        "assignee_rules",
        "local_ci_audit",
        "merge_maintainer",
        "repair_steward",
        "not_before",
        "assignee",
        "board",
    ]


def test_scan_fails_closed_for_an_incomplete_enabled_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    context = RecordingContext({"enabled": True})
    _plugin_module().register(context)
    parser = argparse.ArgumentParser()
    context.calls[0]["setup_fn"](parser)

    exit_code = context.calls[0]["handler_fn"](parser.parse_args(["scan"]))

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {"status": "invalid_configuration"}


def test_real_hermes_discovers_temp_profile_plugin_and_dry_scan_never_invokes_gh(
    tmp_path: Path,
) -> None:
    hermes = shutil.which("hermes")
    if hermes is None:
        pytest.skip("Hermes executable is not installed on this host")
    plugin_root = Path(__file__).resolve().parents[1]
    profile = tmp_path / "profile"
    installed = profile / "plugins" / "github-pr-feedback"
    shutil.copytree(
        plugin_root,
        installed,
        ignore=shutil.ignore_patterns(
            ".git",
            ".superpowers",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "tests",
        ),
    )
    profile.mkdir(exist_ok=True)
    (profile / "config.yaml").write_text(
        "plugins:\n"
        "  enabled:\n"
        "    - github-pr-feedback\n"
        "  entries:\n"
        "    github-pr-feedback:\n"
        "      settings:\n"
        "        enabled: false\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    marker = tmp_path / "gh-was-invoked"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        f"#!/bin/sh\nprintf invoked > {marker}\nexit 97\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "HERMES_HOME": str(profile),
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{environment.get('PATH', '')}",
        }
    )
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "HERMES_PROFILE"):
        environment.pop(name, None)

    completed = subprocess.run(
        [hermes, "github-pr-feedback", "scan"],
        check=False,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"created": 0, "skipped": {}, "status": "ok"}
    assert marker.exists() is False


def test_real_hermes_registers_the_fixed_card_as_blocked_dir_workspace_without_skills(
    tmp_path: Path,
) -> None:
    from github_pr_feedback.cli import _kanban_create_argv

    hermes = shutil.which("hermes")
    if hermes is None:
        pytest.skip("Hermes executable is not installed on this host")
    profile = tmp_path / "profile"
    workspace = tmp_path / "exact-head-worktree"
    workspace.mkdir()
    environment = os.environ.copy()
    environment.update({"HERMES_HOME": str(profile), "HOME": str(tmp_path / "home")})
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "HERMES_PROFILE"):
        environment.pop(name, None)
    board = subprocess.run(
        [hermes, "kanban", "boards", "create", "repairs"],
        check=False,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert board.returncode == 0, board.stderr
    task = replace(kanban_task(), repository_path=workspace)
    argv = _kanban_create_argv(task)

    created = subprocess.run(
        [hermes, *argv[1:]],
        check=False,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert created.returncode == 0, created.stderr
    payload = json.loads(created.stdout)
    assert payload["status"] == "blocked"
    assert payload["workspace_kind"] == "dir"
    assert payload["workspace_path"] == str(workspace)
    assert payload["skills"] == []
    assert payload["started_at"] is None
    assert payload["session_id"] is None


def test_cron_wrapper_invokes_only_the_fixed_scan_argv_with_an_absolute_hermes_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "github-pr-feedback-scan.py"
    spec = importlib.util.spec_from_file_location("github_pr_feedback_cron", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls: list[tuple[list[str], bool]] = []

    class Completed:
        returncode = 0

    def run(argv: list[str], *, check: bool) -> Completed:
        calls.append((argv, check))
        return Completed()

    monkeypatch.setattr(module.subprocess, "run", run)
    executable = tmp_path / "bin" / "hermes"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("HERMES_EXECUTABLE", str(executable))

    assert module.main() == 0
    assert calls == [([str(executable), "github-pr-feedback", "scan"], False)]


@pytest.mark.parametrize("configured", [None, "hermes", "/missing/hermes"])
def test_cron_wrapper_fails_cleanly_without_an_executable_absolute_hermes_path(
    configured: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "github-pr-feedback-scan.py"
    spec = importlib.util.spec_from_file_location("github_pr_feedback_cron_invalid", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if configured is None:
        monkeypatch.delenv("HERMES_EXECUTABLE", raising=False)
    else:
        monkeypatch.setenv("HERMES_EXECUTABLE", configured)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("invalid scheduler configuration invoked Hermes"),
    )

    assert module.main() == 127
    assert "HERMES_EXECUTABLE must name an executable absolute path" in capsys.readouterr().err


def _claim(ledger: FeedbackLedger, receipt: FeedbackReceipt, *, owner: str = "test-scanner"):
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    return ledger.claim(
        receipt,
        owner=owner,
        claimed_at=now,
        stale_before=now - timedelta(minutes=5),
    )


def enabled_settings(repository: Path) -> dict[str, object]:
    return {
        "enabled": True,
        "repositories": [
            {
                "base_repository": "acme/widgets",
                "head_repository": "acme/widgets",
                "local_path": str(repository),
                "owner_login": "owner",
                "branch_prefixes": ["codex/"],
            }
        ],
        "reviewer_logins": ["reviewer"],
        "reviewer_associations": [],
        "not_before": "2026-08-24T00:00:00Z",
        "assignee": "repair-agent",
        "board": "repairs",
    }


def profile_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_ci_audit_handoff_completes_current_task_without_waiting_for_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from github_pr_feedback.ci_runner import (
        CIAuditIdentity,
        CIAuditReceipt,
        CheckState,
    )
    from github_pr_feedback.cli import _complete_current_ci_task

    receipt = CIAuditReceipt(
        receipt_id="r" * 64,
        identity=CIAuditIdentity("acme/widgets", 17, "a" * 40, "b" * 40),
        manifest_digest="m" * 64,
        status="failed",
        started_at=datetime(2026, 8, 25, tzinfo=UTC),
        completed_at=datetime(2026, 8, 25, tzinfo=UTC),
        actions_state=CheckState(False, True, 0),
        commands=(),
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    class Completed:
        returncode = 0

    def run(argv: list[str], **kwargs: object) -> Completed:
        calls.append((argv, kwargs))
        return Completed()

    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_exact")
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "repairs")
    monkeypatch.setattr("github_pr_feedback.cli.subprocess.run", run)

    _complete_current_ci_task(receipt)

    assert calls[0][0] == [
        "hermes",
        "kanban",
        "--board",
        "repairs",
        "complete",
        "t_exact",
        "--result",
        f"Exact-head local CI receipt {'r' * 64}: failed.",
    ]


def test_ci_audit_handoff_terminates_only_a_task_scoped_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from github_pr_feedback.cli import _terminate_current_ci_worker

    signals: list[tuple[int, object]] = []
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setattr("github_pr_feedback.cli.os.getppid", lambda: 4321)
    monkeypatch.setattr(
        "github_pr_feedback.cli.os.kill", lambda pid, sig: signals.append((pid, sig))
    )

    _terminate_current_ci_worker()
    assert signals == []

    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_exact")
    _terminate_current_ci_worker()
    assert signals == [(4321, signal.SIGTERM)]
