"""The realistic-home acceptance contract rejects hidden maintenance failures on every host."""

from subprocess import CompletedProcess

import pytest

from tests.e2e.core.upgrade.test_update_userstate_realistic import _assert_userstate_partial


def _partial():
    followups = [
        {"step": "left_core_migration", "reason": "profile badyaml: while parsing a flow sequence: model: [unclosed"},
        {"step": "state_db_health", "reason": "profile badyaml: while parsing a flow sequence: model: [unclosed"},
    ]
    return CompletedProcess(["hermes", "update"], 1, stdout="maintenance requires user action", stderr=""), {
        "outcome": "partial",
        "user_action": dict(followups[-1]),
        "followups": followups,
    }


def test_committed_update_with_both_profile_obligations_is_partial():
    _assert_userstate_partial(*_partial())


def test_partial_acceptance_reads_real_persisted_maintenance_receipt(tmp_path, monkeypatch):
    from hermes_cli import update_receipt as receipts
    from hermes_cli.maintenance_policy import report_pending

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(receipts, "_code_identity", lambda **kwargs: {})
    update, expected = _partial()
    with receipts.update_receipt_scope():
        receipts.begin_update_receipt()
        report_pending([(action["step"], action["reason"]) for action in expected["followups"]], [])
        path = receipts.finalize_update_receipt("success")
    assert path is not None and path.is_relative_to(tmp_path)
    receipt = receipts.read_latest_receipt()
    assert receipt is not None
    _assert_userstate_partial(update, receipt)


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda update, receipt: setattr(update, "returncode", 0), id="exit"),
    pytest.param(lambda update, receipt: receipt.update(outcome="success"), id="outcome"),
    pytest.param(lambda update, receipt: receipt["followups"].pop(0), id="missing_action"),
    pytest.param(lambda update, receipt: receipt["followups"][0].update(
        reason=receipt["followups"][0]["reason"].replace("badyaml", "other")), id="wrong_profile"),
    pytest.param(lambda update, receipt: receipt["followups"][0].update(
        reason="profile badyaml: ImportError: missing helper"), id="wrong_reason"),
    pytest.param(lambda update, receipt: receipt.pop("user_action"), id="missing_terminal_action"),
    pytest.param(lambda update, receipt: setattr(update, "stderr", "Traceback (most recent call last):"),
                 id="traceback"),
])
def test_unproven_or_unexpected_partial_update_is_rejected(mutate):
    update, receipt = _partial()
    mutate(update, receipt)
    with pytest.raises(AssertionError):
        _assert_userstate_partial(update, receipt)
