"""The realistic-home acceptance contract rejects hidden maintenance failures on every host."""

from subprocess import CompletedProcess

import pytest

from tests.e2e.core.upgrade.test_update_userstate_realistic import _assert_userstate_partial


def _partial():
    return CompletedProcess(["hermes", "update"], 1, stdout="maintenance requires user action", stderr=""), {
        "outcome": "partial",
        "user_action": [
            {"step": "left_core_migration", "reason": "profile badyaml: while parsing a flow sequence: model: [unclosed"},
            {"step": "state_db_health", "reason": "profile badyaml: while parsing a flow sequence: model: [unclosed"},
        ],
    }


def test_committed_update_with_both_profile_obligations_is_partial():
    _assert_userstate_partial(*_partial())


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda update, receipt: setattr(update, "returncode", 0), id="exit"),
    pytest.param(lambda update, receipt: receipt.update(outcome="success"), id="outcome"),
    pytest.param(lambda update, receipt: receipt["user_action"].pop(), id="missing_action"),
    pytest.param(lambda update, receipt: receipt["user_action"][0].update(
        reason=receipt["user_action"][0]["reason"].replace("badyaml", "other")), id="wrong_profile"),
    pytest.param(lambda update, receipt: receipt["user_action"][0].update(
        reason="profile badyaml: ImportError: missing helper"), id="wrong_reason"),
    pytest.param(lambda update, receipt: setattr(update, "stderr", "Traceback (most recent call last):"),
                 id="traceback"),
])
def test_unproven_or_unexpected_partial_update_is_rejected(mutate):
    update, receipt = _partial()
    mutate(update, receipt)
    with pytest.raises(AssertionError):
        _assert_userstate_partial(update, receipt)
