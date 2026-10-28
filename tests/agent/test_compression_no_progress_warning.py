"""Rejected no-op compaction must report its unchanged-history outcome."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent import conversation_compression as compression


def _agent(monkeypatch):
    agent = SimpleNamespace(
        session_id="synthetic-session",
        context_compressor=SimpleNamespace(
            _last_compress_aborted=False,
            _record_structural_no_op=Mock(),
        ),
        _emit_warning=Mock(),
    )
    monkeypatch.setattr(compression, "_emit_aborted_attempt_telemetry", Mock())
    monkeypatch.setattr(compression, "_working_attempt_is_current", lambda *args: True)
    return agent


def _reject(agent, candidate, before):
    return compression._candidate_rejected(
        agent, candidate, before, deepcopy(before),
        attempt=compression._Attempt({}, 1, 0.0, {}),
    )


@pytest.mark.parametrize("marker_only", [False, True])
def test_unchanged_candidate_warns_without_mutating_or_dropping_history(monkeypatch, marker_only):
    agent = _agent(monkeypatch)
    before = [{"role": "user", "content": "synthetic protected tail", "_db_persisted": True}]
    saved = deepcopy(before)
    candidate = deepcopy(before)
    if marker_only:
        candidate[0].pop("_db_persisted")

    assert _reject(agent, candidate, before) is True

    assert before == saved
    agent.context_compressor._record_structural_no_op.assert_called_once()
    agent._emit_warning.assert_called_once()
    warning = agent._emit_warning.call_args.args[0]
    assert "no progress" in warning.lower()
    assert "No messages were dropped" in warning
    assert "synthetic protected tail" not in warning


def test_productive_candidate_does_not_emit_no_progress_warning(monkeypatch):
    agent = _agent(monkeypatch)
    before = [{"role": "user", "content": "before"}]
    assert _reject(agent, [{"role": "user", "content": "summary"}], before) is False
    agent._emit_warning.assert_not_called()
    agent.context_compressor._record_structural_no_op.assert_not_called()


def test_repeated_rejected_attempts_each_report_their_outcome(monkeypatch):
    agent = _agent(monkeypatch)
    before = [{"role": "user", "content": "protected"}]
    assert _reject(agent, before, before) is True
    assert _reject(agent, before, before) is True
    assert agent._emit_warning.call_count == 2


def test_warning_delivery_error_does_not_turn_no_op_into_failed_compression(monkeypatch):
    agent = _agent(monkeypatch)
    agent._emit_warning.side_effect = RuntimeError("synthetic presentation failure")
    before = [{"role": "user", "content": "protected"}]
    assert _reject(agent, before, before) is True
    agent.context_compressor._record_structural_no_op.assert_called_once()
