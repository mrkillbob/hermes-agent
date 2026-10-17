"""Context admission uses real host root/fence helpers and inert transports."""

import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from github_pr_feedback.intake_cli import handle_cli
from github_pr_feedback.intake_context import PROTECTED_CONTEXT, registered_control_root
from github_pr_feedback.intake_kanban import IntakeKanban
from github_pr_feedback.intake_only import IntakeError, render_card
from .test_intake_only import GitHub, policy
from .support import actual_context


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.delenv("HERMES_CONTROL_HOME", raising=False)
    for name in PROTECTED_CONTEXT:
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.mark.parametrize("marker", sorted(PROTECTED_CONTEXT))
@pytest.mark.parametrize("value", ["", "0", "false", "inert"])
def test_context_markers_reject_before_credential_factory(
    root, tmp_path, monkeypatch, marker, value
):
    from github_pr_feedback import intake_cli

    ctx = actual_context(root)
    monkeypatch.setattr(intake_cli, "load_intake_policy", lambda *_: policy(tmp_path))
    monkeypatch.setenv(marker, value)
    monkeypatch.setattr(
        intake_cli, "automation_client", lambda *a, **k: pytest.fail("credentials reached")
    )
    assert (
        handle_cli(
            ctx,
            SimpleNamespace(
                github_pr_feedback_action="scan-intake", repository="mrkillbob/luna-bot"
            ),
        )
        == 1
    )
    assert marker in os.environ and os.environ[marker] == value


@pytest.mark.parametrize(
    "kind",
    ["kanban_home", "control_home", "active_root", "missing_manager", "registered_roots_differ"],
)
def test_mismatched_roots_reject_before_credentials(root, tmp_path, monkeypatch, kind):
    from github_pr_feedback import intake_cli

    ctx = actual_context(root)
    monkeypatch.setattr(intake_cli, "load_intake_policy", lambda *_: policy(tmp_path))
    other = tmp_path / "other"
    other.mkdir()
    if kind == "kanban_home":
        monkeypatch.setenv("HERMES_KANBAN_HOME", str(other))
    if kind == "control_home":
        monkeypatch.setenv("HERMES_CONTROL_HOME", str(other))
    if kind == "active_root":
        monkeypatch.setenv("HERMES_HOME", str(other))
    if kind == "missing_manager":
        ctx = SimpleNamespace()
    if kind == "registered_roots_differ":
        ctx = SimpleNamespace(_manager=SimpleNamespace(home_path=root, scope_key=other))
    monkeypatch.setattr(
        intake_cli, "automation_client", lambda *a, **k: pytest.fail("credentials reached")
    )
    assert (
        handle_cli(
            ctx,
            SimpleNamespace(
                github_pr_feedback_action="scan-intake", repository="mrkillbob/luna-bot"
            ),
        )
        == 1
    )


def test_actual_registered_default_root_and_nonfenced_board_are_accepted(
    root, tmp_path, monkeypatch
):
    from github_pr_feedback import intake_cli

    ctx = actual_context(root)
    monkeypatch.setenv("HERMES_CONTROL_HOME", str(root))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    assert registered_control_root(ctx) == root.resolve()
    credentials = []
    monkeypatch.setattr(intake_cli, "load_intake_policy", lambda *_: policy(tmp_path))
    monkeypatch.setattr(
        intake_cli, "automation_client", lambda *a, **k: credentials.append(True) or GitHub()
    )
    monkeypatch.setattr(intake_cli, "scan_intake", lambda *a, **k: {"status": "ok", "created": 0})
    assert (
        handle_cli(
            ctx,
            SimpleNamespace(
                github_pr_feedback_action="scan-intake", repository="mrkillbob/luna-bot"
            ),
        )
        == 0
    )
    assert credentials == [True]
    assert os.environ["HERMES_KANBAN_BOARD"] == "default"


@pytest.mark.parametrize("operation", ["lookup", "legacy", "create"])
@pytest.mark.parametrize(
    "changed",
    ["worker", "kanban_home", "control_home", "active_root", "machine_fence", "delegate_fence"],
)
def test_every_adapter_operation_rechecks_context(root, tmp_path, monkeypatch, operation, changed):
    from agent.delegation_context import delegated_child_context
    from hermes_cli import kanban_db as kb

    calls = []
    adapter = IntakeKanban(lambda argv: calls.append(argv) or [], expected_root=root, context=actual_context(root))
    card = render_card(policy(tmp_path), GitHub().pr, GitHub().items[0])
    other = tmp_path / "other"
    other.mkdir()
    if changed == "worker":
        monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "0")
    if changed == "kanban_home":
        monkeypatch.setenv("HERMES_KANBAN_HOME", str(other))
    if changed == "control_home":
        monkeypatch.setenv("HERMES_CONTROL_HOME", str(other))
    if changed == "active_root":
        monkeypatch.setenv("HERMES_HOME", str(other))
    fence = (
        kb.pin_first_board_resolution()
        if changed == "machine_fence"
        else delegated_child_context()
        if changed == "delegate_fence"
        else nullcontext()
    )
    with fence, pytest.raises(IntakeError):
        if operation == "lookup":
            adapter.lookup(card.board, card.idempotency_key)
        elif operation == "legacy":
            adapter.legacy_feedback_lookup(
                card.board,
                {
                    "repository": "mrkillbob/luna-bot",
                    "pr_number": 7,
                    "feedback_kind": "review_comment",
                    "feedback_id": "101",
                },
            )
        else:
            adapter.create(card)
    assert calls == []


def test_real_transport_never_infers_registration_authority_from_ambient_root(root):
    with pytest.raises(IntakeError):
        IntakeKanban()


def test_context_is_rechecked_after_policy_loading_before_credentials(root, tmp_path, monkeypatch):
    from github_pr_feedback import intake_cli

    ctx = actual_context(root)

    def load(*_):
        monkeypatch.setenv("HERMES_KANBAN_TASK", "context-changed-during-policy")
        return policy(tmp_path)

    monkeypatch.setattr(intake_cli, "load_intake_policy", load)
    monkeypatch.setattr(
        intake_cli,
        "automation_client",
        lambda *a, **k: pytest.fail("credentials reached after context changed"),
    )
    assert (
        handle_cli(
            ctx,
            SimpleNamespace(
                github_pr_feedback_action="scan-intake", repository="mrkillbob/luna-bot"
            ),
        )
        == 1
    )


@pytest.mark.parametrize("marker", sorted(PROTECTED_CONTEXT))
@pytest.mark.parametrize("value", ["", "false"])
def test_local_presence_guard_refuses_before_any_parent_api_call(tmp_path, monkeypatch, marker, value):
    ctx = SimpleNamespace(kanban_readonly_snapshot=lambda *_a, **_k: pytest.fail("parent API reached"))
    monkeypatch.setenv(marker, value)
    with pytest.raises(IntakeError):
        registered_control_root(ctx)
    assert os.environ[marker] == value


def test_host_isolation_refuses_intake_before_credentials(tmp_path, monkeypatch):
    from hermes_cli.plugin_host_child import RemotePluginContext
    from hermes_cli.plugins import PluginContext
    from github_pr_feedback import intake_cli

    runtime = SimpleNamespace(ctx_call=lambda *_a, **_k: pytest.fail("native authority crossed RPC"))
    proxy = RemotePluginContext(runtime, "github-pr-feedback", {
        "ctx_methods": [name for name in dir(PluginContext) if not name.startswith("_")],
    })
    monkeypatch.setattr(intake_cli, "automation_client", lambda *_a, **_k: pytest.fail("credentials reached"))
    args = SimpleNamespace(github_pr_feedback_action="scan-intake", repository="mrkillbob/luna-bot")
    assert handle_cli(proxy, args) == 1
