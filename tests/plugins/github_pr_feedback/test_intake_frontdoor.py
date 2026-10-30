from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from github_pr_feedback.intake_cli import handle_cli, load_intake_policy
from github_pr_feedback.intake_kanban import IntakeKanban
from github_pr_feedback.intake_only import IntakeError, render_card
from hermes_constants import get_default_hermes_root
from .test_intake_only import GitHub, policy
from .support import actual_context


class Context:
    def __init__(self, values):
        self.values = values
        self.commands = []

    def get_config(self, key, default=None):
        return self.values.get(key, default)

    def register_cli_command(self, **kwargs):
        self.commands.append(kwargs)


def context(tmp_path):
    return Context(
        {
            "enabled": False,
            "auto_dispatch": False,
            "intake_only": {"enabled": True},
            "repositories": [
                {
                    "base_repository": "mrkillbob/luna-bot",
                    "head_repository": "mrkillbob/luna-bot",
                    "local_path": str(tmp_path),
                    "owner_login": "mrkillbob",
                    "branch_prefixes": [],
                }
            ],
            "reviewer_logins": ["trusted"],
            "reviewer_associations": ["MEMBER"],
            "not_before": "2026-01-01T00:00:00+00:00",
        }
    )


def entry():
    path = Path(__file__).resolve().parents[3] / "plugins/github-pr-feedback/__init__.py"
    spec = importlib.util.spec_from_file_location("intake_plugin_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_intake_registration_never_requests_broad_cli_or_hooks(tmp_path, monkeypatch):
    module, ctx = entry(), context(tmp_path)
    imported = []

    def selected(name):
        imported.append(name)
        assert name == "intake_cli"
        from github_pr_feedback.intake_cli import cli_bindings

        return SimpleNamespace(cli_bindings=cli_bindings)

    monkeypatch.setattr(module, "_module", selected)
    module.register(ctx)
    assert imported == ["intake_cli"]
    parser = argparse.ArgumentParser()
    ctx.commands[0]["setup_fn"](parser)
    assert (
        parser.parse_args(["scan-intake", "--repository", "mrkillbob/luna-bot"]).repository
        == "mrkillbob/luna-bot"
    )
    for action in ("scan", "post-comment", "submit-review", "dispatch-feedback", "merge"):
        with pytest.raises(SystemExit):
            parser.parse_args([action])


@pytest.mark.parametrize(
    "enabled,intake", [(False, False), (False, True), (True, True), (True, False)]
)
def test_stored_broad_handler_cannot_gain_authority_after_toggle(
    tmp_path, monkeypatch, enabled, intake
):
    from github_pr_feedback import cli

    ctx = context(tmp_path)
    ctx.values.update(enabled=enabled, intake_only={"enabled": intake})
    monkeypatch.setattr(cli, "_scan", lambda *_: pytest.fail("broad controller reached"))
    assert cli.handle_cli_with_context(ctx, SimpleNamespace(github_pr_feedback_action="scan")) != 0


def test_capability_gates_precede_credential_factory(tmp_path, monkeypatch):
    from github_pr_feedback import intake_cli

    ctx = context(tmp_path)
    ctx.values["enabled"] = True
    monkeypatch.setattr(
        intake_cli, "automation_client", lambda *_: pytest.fail("credential factory reached")
    )
    assert (
        handle_cli(
            ctx,
            SimpleNamespace(
                github_pr_feedback_action="scan-intake", repository="mrkillbob/luna-bot"
            ),
        )
        != 0
    )


def test_policy_preserves_scope_trust_and_refuses_override(tmp_path, monkeypatch):
    import github_pr_feedback.policy as source

    monkeypatch.setattr(source, "_is_git_worktree", lambda _: True)
    ctx = context(tmp_path)
    cfg = load_intake_policy(ctx, "mrkillbob/luna-bot")
    assert cfg.admission.reviewer_logins == frozenset({"trusted"})
    assert cfg.admission.reviewer_associations == frozenset({"MEMBER"})
    assert cfg.admission.targets["mrkillbob/luna-bot"].head_repository == "mrkillbob/luna-bot"
    assert cfg.admission.auto_dispatch is False
    assert ctx.values["enabled"] is False
    ctx.values["intake_only"]["assignee"] = "other"
    with pytest.raises(IntakeError):
        load_intake_policy(ctx, "mrkillbob/luna-bot")


def test_narrow_adapter_only_lookup_and_triage_create(tmp_path, configured_hermes_executable):
    github = GitHub()
    card = render_card(policy(tmp_path), github.pr, github.items[0])
    calls = []

    def runner(argv):
        calls.append(argv)
        return {"id": "task-1"} if "create" in argv else []

    kb = IntakeKanban(runner, expected_root=get_default_hermes_root(), context=actual_context())
    assert kb.lookup(card.board, card.idempotency_key) == []
    assert kb.create(card) == "task-1"
    assert calls[0][0] == str(configured_hermes_executable)
    assert len(calls) == 1
    create = calls[0]
    assert "--triage" in create and create[create.index("--assignee") + 1] == "task-intake-router"
    assert create[create.index("--workspace") + 1] == "dir:" + str(tmp_path.resolve())
    assert not {
        "list",
        "show",
        "reconcile",
        "--model",
        "--provider",
        "--initial-status",
        "--parent",
    } & set(create)


@pytest.mark.parametrize("result", [None, {}, "partial", [{"id": "missing-fields"}]])
def test_partial_or_malformed_lookup_never_appears_complete(result):
    root = get_default_hermes_root()
    ctx = SimpleNamespace(kanban_readonly_snapshot=lambda board, **_: {
        "control_root": str(root), "board": board, "tasks": result})
    with pytest.raises(IntakeError):
        IntakeKanban(lambda _: pytest.fail("CLI lookup reached"), expected_root=root, context=ctx).lookup(
            "default", "github-pr-intake:v1:" + "a" * 64)


def test_intake_never_reads_or_passes_actions_identity(tmp_path, monkeypatch):
    import github_pr_feedback.policy as source

    monkeypatch.setattr(source, "_is_git_worktree", lambda _: True)
    ctx = context(tmp_path)
    seen = []
    original = ctx.get_config

    def get_config(key, default=None):
        seen.append(key)
        assert key != "github_actions_permissions_identity"
        return original(key, default)

    ctx.get_config = get_config
    cfg = load_intake_policy(ctx, "mrkillbob/luna-bot")
    assert cfg.admission.github_actions_permissions_identity is None
    assert "github_actions_permissions_identity" not in seen


def test_intake_factory_passes_only_ordinary_bot_identity(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace

    from github_pr_feedback.automation_client import automation_client

    captured = {}

    class Client:
        @classmethod
        def for_automation_identity(cls, **kwargs):
            captured.update(kwargs)
            return cls()

    admission = replace(
        policy(tmp_path).admission,
        github_identity=SimpleNamespace(
            expected_login="ordinary-bot", token_env="EXISTING_BOT_ENV"
        ),
    )
    automation_client(admission, client_type=Client, allow_actions=False)
    assert captured == {"expected_login": "ordinary-bot", "token_env": "EXISTING_BOT_ENV"}


def test_host_explicit_deny_wins_over_enabled_list():
    from hermes_cli.plugins_discovery import IN_BOTH_LISTS, gate_manifest
    from hermes_cli.plugins_manifest import PluginManifest

    manifest = PluginManifest(name="github-pr-feedback", source="user")
    verdict = gate_manifest(manifest, {"github-pr-feedback"}, {"github-pr-feedback"})
    assert verdict.action == "placeholder" and not verdict.enabled
    assert verdict.error == IN_BOTH_LISTS


def test_complete_legacy_identity_lookup_is_exact_and_head_independent():
    calls = []
    ctx = actual_context()
    original = ctx.kanban_readonly_snapshot
    def snapshot(board, **selectors):
        calls.append((board, selectors))
        return original(board, **selectors)
    ctx.kanban_readonly_snapshot = snapshot
    identity = {"repository": "mrkillbob/luna-bot", "pr_number": 7,
                "feedback_kind": "review_comment", "feedback_id": "101"}
    adapter = IntakeKanban(lambda _: pytest.fail("CLI history reached"),
                           expected_root=get_default_hermes_root(), context=ctx)
    assert adapter.legacy_feedback_lookup("tradingbot-burndown", identity) == []
    from github_pr_feedback.intake_history import project_legacy_receipt
    assert calls == [("tradingbot-burndown", {
        "idempotency_prefix": "github-pr-feedback:", "row_projector": project_legacy_receipt})]
    assert "head_sha" not in identity


@pytest.mark.parametrize(
    "field,value",
    [
        ("pr_number", True),
        ("feedback_id", True),
        ("feedback_id", "1" * 1025),
        ("repository", "other/repo"),
        ("feedback_kind", "pr_repair"),
    ],
)
def test_legacy_identity_lookup_rejects_scope_or_malformed_identity(field, value):
    identity = {
        "repository": "mrkillbob/luna-bot",
        "pr_number": 7,
        "feedback_kind": "review_comment",
        "feedback_id": "101",
    }
    identity[field] = value
    with pytest.raises(IntakeError):
        IntakeKanban(
            lambda _: pytest.fail("transport reached"), expected_root=get_default_hermes_root(), context=actual_context()
        ).legacy_feedback_lookup("tradingbot-burndown", identity)


def test_direct_lightweight_entry_loader_preserves_enabled_mode_imports():
    module = entry()  # spec loader intentionally does not register in sys.modules
    assert module._module("automation_client").__name__ == "github_pr_feedback.automation_client"


@pytest.mark.parametrize(
    "present,intake,enabled,expected",
    [
        (True, True, False, "intake_cli"),
        (True, False, False, "intake_cli"),
        (True, False, True, "intake_cli"),
        (False, None, False, "cli"),
    ],
)
def test_explicit_intake_mode_has_no_hooks_legacy_absence_preserves_protective_hooks(
    tmp_path, monkeypatch, present, intake, enabled, expected
):
    module, ctx = entry(), context(tmp_path)
    ctx.values["enabled"] = enabled
    if present:
        ctx.values["intake_only"] = {"enabled": intake}
    else:
        ctx.values.pop("intake_only")
    imports = []

    def selected(name):
        imports.append(name)
        return SimpleNamespace(
            cli_bindings=lambda _: (None, None),
            register_completion_guard=lambda _: None,
            register_repair_completion_policy=lambda _: None,
        )

    monkeypatch.setattr(module, "_module", selected)
    module.register(ctx)
    assert imports[-1] == expected
    assert ("completion_guard" in imports) is (not present)
    assert ("repair_completion_policy" in imports) is (not present)


def test_shared_factory_preserves_legacy_actions_identity(tmp_path):
    from dataclasses import replace

    from github_pr_feedback.automation_client import automation_client

    captured = {}

    class Client:
        @classmethod
        def for_automation_identity(cls, **kwargs):
            captured.update(kwargs)
            return cls()

    ordinary = SimpleNamespace(expected_login="ordinary-bot", token_env="EXISTING_BOT_ENV")
    actions = SimpleNamespace(
        expected_login="actions-bot",
        gh_config_dir=tmp_path,
        repositories=frozenset({"mrkillbob/luna-bot"}),
    )
    admission = replace(
        policy(tmp_path).admission,
        github_identity=ordinary,
        github_actions_permissions_identity=actions,
    )
    automation_client(admission, client_type=Client)
    assert captured == {
        "expected_login": "ordinary-bot",
        "token_env": "EXISTING_BOT_ENV",
        "actions_permissions_expected_login": "actions-bot",
        "actions_permissions_gh_config_dir": tmp_path,
        "actions_permissions_repositories": frozenset({"mrkillbob/luna-bot"}),
    }
