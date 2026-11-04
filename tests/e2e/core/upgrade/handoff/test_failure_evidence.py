"""Failed handoff premises retain outcomes without changing timing or process control."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from tests.e2e.core.upgrade.handoff import _handoff as X
from tests.e2e.core.upgrade.handoff import _ns_agent as agent
from tests.e2e.core.upgrade.handoff._scenario import _require_update_premise


@pytest.mark.parametrize(
    ("satisfied", "finished", "returncode", "raises", "diagnostics_refused"),
    [
        (True, False, 0, False, False),
        (True, True, 0, False, False),
        (False, False, 0, False, False),
        (False, True, 0, False, False),
        (False, True, 1, False, False),
        (False, True, 1, True, False),
        (False, False, 0, False, True),
        (False, True, 1, False, True),
    ],
    ids=[
        "live", "satisfied", "missing-marker", "early-success", "early-failure", "update-raised",
        "missing-marker-log-refused", "early-failure-log-refused",
    ],
)
def test_failed_update_premise_retains_the_actual_outcome(
    tmp_path, satisfied, finished, returncode, raises, diagnostics_refused
):
    inst = X.Install("n1", tmp_path, tmp_path / "origin", {"HERMES_HOME": str(tmp_path / "home")})
    if diagnostics_refused:
        (tmp_path / "ns-agent.stderr").mkdir()
        with pytest.raises(OSError) as refused:
            (tmp_path / "ns-agent.stderr").read_text(encoding="utf-8")
    else:
        (tmp_path / "ns-agent.stderr").write_text("synthetic agent failure", encoding="utf-8")
    pending = Mock()
    pending.done.return_value = finished
    completed = subprocess.CompletedProcess(
        ["hermes", "update"], returncode, "synthetic updater stdout", "synthetic updater stderr"
    )
    failure = RuntimeError("synthetic update exception")
    pending.result.side_effect = failure if raises else None
    pending.result.return_value = completed

    if satisfied:
        _require_update_premise(True, pending, inst, "premise must remain satisfied")
        pending.done.assert_not_called()
        pending.result.assert_not_called()
        return

    expected = RuntimeError if raises else AssertionError
    with pytest.raises(expected) as caught:
        _require_update_premise(False, pending, inst, "premise: synthetic update fence")
    pending.done.assert_called_once_with()
    if raises:
        assert caught.value is failure
        return
    message = str(caught.value)
    assert "premise: synthetic update fence" in message
    expected_diagnostic = (
        f"handoff diagnostics failed: {type(refused.value).__name__}"
        if diagnostics_refused else "synthetic agent failure"
    )
    assert expected_diagnostic in message
    if finished:
        pending.result.assert_called_once_with()
        assert f"rc={returncode}" in message
        assert "synthetic updater stdout" in message
        assert "synthetic updater stderr" in message
    else:
        pending.result.assert_not_called()
        assert "--- stdout" not in message


@pytest.mark.parametrize(
    ("returncode", "append_fails"),
    [(None, False), (0, False), (1, False), (-9, False), (1, True)],
    ids=["running", "clean-exit", "error-exit", "signal-exit", "log-refused"],
)
def test_reaper_keeps_one_exit_outcome_without_changing_child_control(
    tmp_path, monkeypatch, capsys, returncode, append_fails
):
    log = tmp_path / "gateway.log"
    original = "synthetic gateway traceback\n"
    log.write_text(original, encoding="utf-8")
    child = Mock()
    child.poll.return_value = returncode
    children = {123: (child, str(log))}
    monkeypatch.setattr(agent, "_CHILDREN", children)

    def refused_append(*args, **kwargs):
        raise OSError("synthetic log refusal")

    if append_fails:
        monkeypatch.setattr(agent, "open", refused_append, raising=False)
    agent._reap()
    agent._reap()

    child.terminate.assert_not_called()
    child.kill.assert_not_called()
    child.wait.assert_not_called()
    content = log.read_text(encoding="utf-8")
    if returncode is None:
        assert children == {123: (child, str(log))}
        assert content == original
        assert not capsys.readouterr().err
        return
    assert not children
    evidence = {"pid": 123, "returncode": returncode}
    if append_fails:
        assert content == original
        error = capsys.readouterr().err
        assert error.count("handoff child exit") == 1
        assert json.dumps(evidence) in error
        assert "synthetic log refusal" in error
    else:
        lines = content.splitlines()
        assert lines[0] == original.rstrip()
        records = [line.removeprefix("[handoff child exit] ") for line in lines if line.startswith("[handoff child exit] ")]
        assert [json.loads(record) for record in records] == [evidence]
        assert not capsys.readouterr().err
