"""Job-scoped observation contracts for release-N1 cron swap issue #113293."""

from types import SimpleNamespace

import pytest

from tests.e2e.core.upgrade.handoff._scenario import HandoffProperties, cron_failure


def _observation(tmp_path, *, column="n1", calls=1, scoped=True, import_error=True, outputs=1, readback_error=False):
    home = tmp_path / "home"
    (home / "logs").mkdir(parents=True)
    job = {"id": "cron-fixture", "last_run_at": None, "last_status": None}
    error = "cannot import name 'is_recurring' from 'cron.constants'" if import_error else "disk full"
    logged_id = job["id"] if scoped else "another-job"
    (home / "logs" / "gateway.log").write_text(
        f"ERROR cron.scheduler: Error processing job {logged_id}: {error}\n", encoding="utf-8")
    inst = SimpleNamespace(root=tmp_path, hermes_home=home)
    return SimpleNamespace(
        column=column, relaunch="", cron_calls=list(range(calls)), cron_outputs=list(range(outputs)),
        cron_list=SimpleNamespace(returncode=int(readback_error), stdout="", stderr=""), cron_job_after=job,
        cron_error=cron_failure(inst, "fixture", job), diag="fixture diagnostics")


def test_n1_bookkeeping_swap_is_recognized_for_its_own_job(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_E2E_STRICT_ACCEPTANCE", raising=False)
    observation = _observation(tmp_path)
    assert "is_recurring" in observation.cron_error
    with pytest.raises(pytest.xfail.Exception, match="113293"):
        HandoffProperties().test_cron_job_due_mid_update_runs_exactly_once(observation)


@pytest.mark.parametrize("kwargs", [
    {"column": "head"}, {"column": "next"}, {"scoped": False},
    {"import_error": False}, {"calls": 2}, {"outputs": 2}, {"readback_error": True},
])
def test_swap_gate_never_excuses_other_failures(tmp_path, monkeypatch, kwargs):
    monkeypatch.delenv("HERMES_E2E_STRICT_ACCEPTANCE", raising=False)
    observation = _observation(tmp_path, **kwargs)
    with pytest.raises(AssertionError):
        HandoffProperties().test_cron_job_due_mid_update_runs_exactly_once(observation)
