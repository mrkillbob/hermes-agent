"""Finite chat exit evidence must preserve the real loop's result and exceptions."""
import argparse
import asyncio
import json
import logging

import pytest


def _prepare_launch(monkeypatch, failure):
    from hermes_cli import gateway_chat, gateway_chat_startup, main

    # Keep launcher, asyncio.run, and main's exit boundary real; replace operations
    # that would register a terminal, read operator config, or contact a gateway.
    monkeypatch.setattr(gateway_chat, "_register_terminal_process", lambda: None)
    monkeypatch.setattr(gateway_chat_startup, "ensure_launch_provider", lambda args: True)
    monkeypatch.setattr(main, "_confirm_startup_expensive_model_override", lambda args: None)
    cleaned = []

    async def turn(args, emitter=None):
        entered = asyncio.Event()

        async def background():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleaned.append(True)

        asyncio.create_task(background())
        await entered.wait()
        if failure is not None:
            raise failure
        return 7

    monkeypatch.setattr(gateway_chat, "run_gateway_chat", turn)
    return main, argparse.Namespace(oneshot="private-prompt"), cleaned


@pytest.mark.parametrize("raises", [False, True])
def test_main_exit_preserves_loop_result_and_phase_evidence(monkeypatch, caplog, raises):
    from hermes_cli.gateway_client_diagnostics import PREFIX

    failure = RuntimeError("private-loop-error") if raises else None
    main, args, cleaned = _prepare_launch(monkeypatch, failure)
    with caplog.at_level(logging.INFO, logger="hermes_cli.gateway_client_diagnostics"):
        with pytest.raises(RuntimeError if raises else SystemExit) as caught:
            main._run_oneshot_from_args(args)
    assert cleaned == [True]  # the real asyncio.run completed task cancellation
    if raises:
        assert caught.value is failure
    else:
        assert caught.value.code == 7
    rows = [json.loads(record.message.split(PREFIX, 1)[1])
            for record in caplog.records if PREFIX in record.message]
    expected = ["chat_loop_started", "chat_loop_raised"] if raises else [
        "chat_loop_started", "chat_loop_returned", "oneshot_launch_returned"]
    assert [row["phase"] for row in rows] == expected
    assert all(set(row) == {"phase", "monotonic_s"} for row in rows)
    assert "private" not in caplog.text


@pytest.mark.parametrize("raises", [False, True])
def test_unavailable_phase_logging_does_not_replace_loop_result(monkeypatch, raises):
    from hermes_cli import gateway_client_diagnostics as diagnostics

    failure = RuntimeError("private-loop-error") if raises else None
    main, args, cleaned = _prepare_launch(monkeypatch, failure)

    def unavailable(*args, **kwargs):
        raise OSError("private-log-path")

    monkeypatch.setattr(diagnostics.logger, "info", unavailable)
    with pytest.raises(RuntimeError if raises else SystemExit) as caught:
        main._run_oneshot_from_args(args)
    assert cleaned == [True]
    if raises:
        assert caught.value is failure
    else:
        assert caught.value.code == 7
