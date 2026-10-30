"""Configured launcher qualification without running any executable."""

import sqlite3
from contextlib import closing

import pytest
from github_pr_feedback.intake_kanban import IntakeKanban
from github_pr_feedback.intake_only import IntakeError
from hermes_constants import get_default_hermes_root
from .support import actual_context
from .test_intake_only import GitHub, policy, run


@pytest.mark.parametrize("kind", ["missing", "empty", "relative", "absent", "directory", "nonexecutable"])
def test_invalid_launcher_rejected_before_transport(tmp_path, monkeypatch, kind):
    path = tmp_path / "launcher"
    if kind == "directory":
        path.mkdir()
    if kind == "nonexecutable":
        path.write_text("inert", encoding="utf-8")
        path.chmod(0o600)
    if kind == "missing":
        monkeypatch.delenv("HERMES_EXECUTABLE")
    else:
        monkeypatch.setenv("HERMES_EXECUTABLE", {"empty": "", "relative": "hermes"}.get(kind, str(path)))
    with pytest.raises(IntakeError, match="HERMES_EXECUTABLE"):
        IntakeKanban(lambda _: pytest.fail("transport reached"),
                     expected_root=get_default_hermes_root(), context=actual_context())


@pytest.mark.parametrize("change", ["removed", "not_executable", "changed_configuration"])
def test_launcher_loss_before_pending_does_not_poison_receipt(
    tmp_path, monkeypatch, configured_hermes_executable, change
):
    ctx = actual_context()
    adapter = IntakeKanban(lambda _: pytest.fail("transport reached"),
                           expected_root=get_default_hermes_root(), context=ctx)
    # Qualify actual passive reads; no host create/process is reachable.
    if change == "removed":
        configured_hermes_executable.unlink()
    elif change == "not_executable":
        configured_hermes_executable.chmod(0o600)
    else:
        monkeypatch.setenv("HERMES_EXECUTABLE", str(tmp_path / "other-launcher"))
    result = run(tmp_path, GitHub(), adapter, policy(tmp_path))
    assert result["created"] == 0 and result["status"] == "held"
    with closing(sqlite3.connect(tmp_path / "receipts.sqlite3")) as db:
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0


def test_launcher_repair_never_replays_an_existing_uncertain_receipt(
    tmp_path, configured_hermes_executable
):
    attempts = []

    def lost_ack(argv):
        attempts.append(argv)
        raise OSError("synthetic ambiguous launch outcome")

    adapter = IntakeKanban(lost_ack, expected_root=get_default_hermes_root(),
                           context=actual_context())
    assert run(tmp_path, GitHub(), adapter)["status"] == "held"
    configured_hermes_executable.unlink()
    assert run(tmp_path, GitHub(), adapter)["status"] == "held"
    configured_hermes_executable.write_text("inert", encoding="utf-8")
    configured_hermes_executable.chmod(0o700)
    assert run(tmp_path, GitHub(), adapter)["status"] == "held"
    assert len(attempts) == 1
    with closing(sqlite3.connect(tmp_path / "receipts.sqlite3")) as db:
        assert db.execute("SELECT state FROM receipts").fetchall() == [("uncertain",)]
