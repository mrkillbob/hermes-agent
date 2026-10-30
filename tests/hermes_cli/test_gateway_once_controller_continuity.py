"""A rebuilt gateway proxy keeps only its own still-current one-turn restore receipt."""

from pathlib import Path

import pytest

from tests.hermes_cli.test_cli_gateway_model_route import RecordingConnection
from tests.hermes_cli.test_cli_gateway_model_scope import _chat_cli, _submits


def _once(monkeypatch):
    from agent.runtime_mutation import get_runtime_mutation_state

    connection = RecordingConnection({"model": "old/model", "provider": "openrouter"})
    cli_mod, cli, _printed = _chat_cli(monkeypatch, connection)
    cli_mod.HermesCLI._handle_model_switch(cli, "/model claude-sonnet-4.6 --provider anthropic --once")
    previous = cli.agent
    snapshot = cli._pending_one_turn_model_restore
    controller = get_runtime_mutation_state(previous)
    return cli, previous, snapshot, controller, connection


def test_rebuilt_proxy_restores_the_current_receipt(monkeypatch):
    cli, previous, _snapshot, _controller, connection = _once(monkeypatch)
    assert cli.chat("one turn") == "ok"
    assert cli.agent is not previous
    assert cli.model == cli.agent.model == "old/model"
    import cli as cli_module
    from hermes_cli import cli_chat_turn_mixin, cli_one_turn_runtime
    root = Path(__file__).resolve().parents[2]
    for module in (cli_module, cli_chat_turn_mixin, cli_one_turn_runtime):
        assert Path(module.__file__).resolve().is_relative_to(root)
    assert len(_submits(connection)) == 1
    assert not [params for method, params in connection.calls if method == "session.mutate"]


@pytest.mark.parametrize("boundary", ["connection", "session", "existing_controller"])
def test_unrelated_replacement_cannot_admit_the_pending_override(monkeypatch, boundary):
    from agent.primary_runtime_snapshot import RuntimeRecoveryPendingError
    from agent.runtime_mutation import get_runtime_mutation_state

    cli, _previous, snapshot, controller, connection = _once(monkeypatch)
    initialize = cli._init_agent
    replacements = []

    def replace(**kwargs):
        assert initialize(**kwargs)
        if boundary == "connection":
            cli.agent.connection = RecordingConnection({"model": "other", "provider": "openrouter"})
        elif boundary == "session":
            from hermes_cli.gateway_agent import GatewayAgent
            cli.agent = GatewayAgent(connection=connection, session_id="different-session",
                                     model=cli.agent.model, provider=cli.agent.provider)
        else:
            get_runtime_mutation_state(cli.agent)
        replacements.append(cli.agent)
        return True

    cli._init_agent = replace

    def refuse_staging(*_args):
        raise AssertionError("unrelated proxy reached turn admission")

    cli._chat_stage_user_message = refuse_staging
    for _attempt in range(2):
        with pytest.raises(RuntimeRecoveryPendingError):
            cli.chat("must not submit")
    assert not _submits(connection)
    assert cli._pending_one_turn_model_restore is snapshot
    assert vars(replacements[0]).get("_runtime_mutation_state") is not controller


@pytest.mark.parametrize("superseded", ["snapshot", "selection"])
def test_superseded_receipt_does_not_transfer_to_a_replacement(monkeypatch, superseded):
    from agent.runtime_mutation import RuntimeMutationStaleError, runtime_mutation

    cli, previous, snapshot, controller, connection = _once(monkeypatch)
    initialize = cli._init_agent

    def replace(**kwargs):
        assert initialize(**kwargs)
        if superseded == "snapshot":
            cli._pending_one_turn_model_restore = dict(snapshot, _runtime_selection_token="new-receipt")
        else:
            with runtime_mutation(previous, "switch_model") as lease:
                lease.successful_selection()
        return True

    cli._init_agent = replace
    assert cli.chat("newer selection owns this turn") == "ok"
    assert vars(cli.agent).get("_runtime_mutation_state") is not controller
    with pytest.raises(RuntimeMutationStaleError):
        cli._restore_model_runtime_snapshot(snapshot)
    assert cli.model == "claude-sonnet-4.6"
    assert not [params for method, params in connection.calls if method == "session.mutate"]


def test_retry_after_initialization_refusal_keeps_the_current_receipt(monkeypatch):
    cli, _previous, snapshot, _controller, connection = _once(monkeypatch)
    initialize = cli._init_agent
    attempts = []

    def refuse_then_initialize(**kwargs):
        attempts.append(True)
        return False if len(attempts) == 1 else initialize(**kwargs)

    cli._init_agent = refuse_then_initialize
    assert cli.chat("initialization refused") is None
    assert not _submits(connection)
    assert cli._pending_one_turn_model_restore is snapshot
    assert cli.chat("retry the once turn") == "ok"
    assert cli.model == cli.agent.model == "old/model"
    assert len(_submits(connection)) == 1
