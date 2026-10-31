"""Tests for fire_overdue_jobs — misfire catch-up for external cron providers.

External providers (Chronos) deliver scheduled fires over HTTP; when the
loopback hop is down at fire time and the scheduler's retry budget exhausts,
the job's next_run_at stays parked in the past and nothing ever runs it
(external providers have no local tick loop). fire_overdue_jobs, called from
gateway housekeeping, claims and fires those jobs after a grace window.
"""

import threading
from datetime import timedelta

import pytest

from cron.jobs import _hermes_now, claim_job_for_fire, create_job, get_due_jobs, get_job, load_jobs, save_jobs
from cron.scheduler_provider import (
    CronScheduler,
    InProcessCronScheduler,
    fire_overdue_jobs,
)


@pytest.fixture()
def tmp_cron_dir(tmp_path, monkeypatch):
    """Redirect cron storage to a temp directory."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    monkeypatch.setattr("cron.executions.EXECUTIONS_FILE", tmp_path / "cron" / "executions.db")
    return tmp_path


class RecordingProvider(CronScheduler):
    """External-provider stand-in: real base-class claim_fire (store CAS),
    recorded fire_claimed instead of the blocking run_one_job."""

    def __init__(self):
        self.fired = []
        self.changed = 0
        self.change_contexts = []
        self._done = threading.Event()

    @property
    def name(self):
        return "recording"

    def start(self, stop_event, **kw):  # pragma: no cover - unused
        return None

    def on_jobs_changed(self):
        from cron.jobs import _backstop_expected_slot

        self.changed += 1
        self.change_contexts.append(_backstop_expected_slot.get())

    def fire_claimed(self, claimed_job, *, adapters=None, loop=None,
                     cancel_event=None):
        self.fired.append(claimed_job["id"])
        self._done.set()
        return True

    def wait_fired(self, timeout=5.0):
        return self._done.wait(timeout)


def _park_in_past(job_id, minutes):
    """Rewind a job's next_run_at into the past (simulates missed fires)."""
    jobs = load_jobs()
    for j in jobs:
        if j["id"] == job_id:
            j["next_run_at"] = (
                _hermes_now() - timedelta(minutes=minutes)
            ).isoformat()
    save_jobs(jobs)


class TestFireOverdueJobs:
    def test_delayed_external_webhook_skips_and_rearms_recurring_miss(self, tmp_cron_dir):
        """A provider webhook arriving before housekeeping cannot bypass catch-up policy."""
        job = create_job(prompt="p", schedule="every 1h", catch_up=False)
        _park_in_past(job["id"], minutes=90)
        first = get_job(job["id"])["next_run_at"]
        provider = RecordingProvider()

        assert provider.claim_fire(job["id"]) is None
        assert provider.changed == 1
        assert provider.change_contexts == [None]
        assert get_job(job["id"])["next_run_at"] != first
        assert get_job(job["id"]).get("fire_claim") is None
        from cron.executions import list_executions
        assert any(row["status"] == "missed" for row in list_executions(job_id=job["id"]))

    def test_delayed_external_webhook_respects_oneshot_catch_up(self, tmp_cron_dir):
        stale = create_job(prompt="p", schedule="in 1h")
        _park_in_past(stale["id"], minutes=90)
        provider = RecordingProvider()
        assert provider.claim_fire(stale["id"]) is None
        assert get_job(stale["id"]) is None
        assert provider.changed == 1

        opted_in = create_job(prompt="p", schedule="in 1h", catch_up=True)
        _park_in_past(opted_in["id"], minutes=90)
        assert isinstance(provider.claim_fire(opted_in["id"]), dict)
        assert provider.changed == 1

    def test_delayed_external_webhook_respects_expired_cron_grace(self, tmp_cron_dir):
        pytest.importorskip("croniter")
        job = create_job(prompt="p", schedule="*/5 * * * *", grace_minutes=0)
        _park_in_past(job["id"], minutes=30)
        provider = RecordingProvider()

        assert provider.claim_fire(job["id"]) is None
        assert provider.changed == 1
        assert get_job(job["id"])["next_run_at"] > _hermes_now().isoformat()

    def test_admitted_builtin_oneshot_survives_worker_queue_delay(self, tmp_cron_dir, monkeypatch):
        """The due scan admitted this slot before another worker held the only queue lane."""
        clock = {"now": _hermes_now()}
        monkeypatch.setattr("cron.jobs._hermes_now", lambda: clock["now"])
        job = create_job(prompt="p", schedule="in 1h")
        jobs = load_jobs()
        jobs[0]["next_run_at"] = (clock["now"] - timedelta(seconds=30)).isoformat()
        save_jobs(jobs)
        assert [due["id"] for due in get_due_jobs()] == [job["id"]]
        assert get_job(job["id"])["run_claim"] is not None

        clock["now"] += timedelta(minutes=3)
        claimed = claim_job_for_fire(job["id"], return_job=True)
        assert isinstance(claimed, dict)
        assert claimed["fire_claim"] is not None

    def test_noop_for_builtin_provider(self, tmp_cron_dir):
        """The in-process ticker self-heals past-due jobs — the sweep must
        never double-dispatch under it."""
        job = create_job(prompt="p", schedule="every 1h")
        _park_in_past(job["id"], minutes=60)
        assert fire_overdue_jobs(InProcessCronScheduler()) == 0

    def test_fires_job_past_grace(self, tmp_cron_dir):
        job = create_job(prompt="p", schedule="every 1h")
        _park_in_past(job["id"], minutes=30)
        provider = RecordingProvider()
        assert fire_overdue_jobs(provider) == 1
        assert provider.wait_fired()
        assert provider.fired == [job["id"]]

    def test_respects_grace_window(self, tmp_cron_dir):
        """A job only a few minutes overdue is still the external
        scheduler's to retry — the backstop must not race it."""
        job = create_job(prompt="p", schedule="every 1h")
        _park_in_past(job["id"], minutes=5)  # < default 10 min grace
        provider = RecordingProvider()
        assert fire_overdue_jobs(provider) == 0
        assert provider.fired == []

    def test_grace_zero_disables(self, tmp_cron_dir, monkeypatch):
        monkeypatch.setattr(
            "cron.scheduler_provider._misfire_grace_minutes", lambda: 0.0
        )
        job = create_job(prompt="p", schedule="every 1h")
        _park_in_past(job["id"], minutes=600)
        assert fire_overdue_jobs(RecordingProvider()) == 0

    def test_future_job_not_fired(self, tmp_cron_dir):
        create_job(prompt="p", schedule="every 1h")  # next_run_at in future
        provider = RecordingProvider()
        assert fire_overdue_jobs(provider) == 0

    def test_paused_job_not_fired(self, tmp_cron_dir):
        from cron.jobs import pause_job

        job = create_job(prompt="p", schedule="every 1h")
        _park_in_past(job["id"], minutes=30)
        pause_job(job["id"])
        assert fire_overdue_jobs(RecordingProvider()) == 0

    def test_fresh_external_claim_wins(self, tmp_cron_dir):
        """A concurrent external fire holds the store claim — the sweep's
        claim_fire loses the CAS and must not dispatch (at-most-once)."""
        from cron.jobs import claim_job_for_fire

        job = create_job(prompt="p", schedule="every 1h")
        _park_in_past(job["id"], minutes=30)
        assert claim_job_for_fire(job["id"]) is True  # external fire claims

        # The external claim also advanced next_run_at (recurring bump), so
        # re-park it to isolate the claim-CAS as the thing that blocks us.
        _park_in_past(job["id"], minutes=30)

        provider = RecordingProvider()
        assert fire_overdue_jobs(provider) == 0
        assert provider.fired == []

    def test_claim_advances_next_run_no_refire(self, tmp_cron_dir):
        """After a catch-up fire, the recurring job's next_run_at moved to
        the future — the next sweep pass must not fire it again."""
        job = create_job(prompt="p", schedule="every 1h")
        _park_in_past(job["id"], minutes=30)
        provider = RecordingProvider()
        assert fire_overdue_jobs(provider) == 1
        assert provider.wait_fired()

        stamped = get_job(job["id"])
        assert stamped["next_run_at"] > _hermes_now().isoformat()

        provider2 = RecordingProvider()
        assert fire_overdue_jobs(provider2) == 0

    def test_catch_up_off_skips_and_books_recurring_miss(self, tmp_cron_dir):
        job = create_job(prompt="p", schedule="every 1h", catch_up=False)
        _park_in_past(job["id"], minutes=90)
        first = get_job(job["id"])["next_run_at"]
        stored_jobs = load_jobs()
        stored_jobs[0]["pending_slot"] = {"scheduled_at": first, "at": first, "by": "dead-owner"}
        save_jobs(stored_jobs)
        provider = RecordingProvider()
        assert fire_overdue_jobs(provider) == 0
        assert provider.fired == []
        assert provider.changed == 1  # external provider must arm the new stored slot
        assert provider.change_contexts == [None]
        stored = get_job(job["id"])
        assert stored["next_run_at"] > _hermes_now().isoformat()
        assert stored.get("pending_slot") is None
        from cron.executions import list_executions
        assert any(row["status"] == "missed" for row in list_executions(job_id=job["id"]))
        assert fire_overdue_jobs(provider) == 0
        assert get_job(job["id"])["next_run_at"] == stored["next_run_at"]
        assert first != stored["next_run_at"]

    def test_expired_per_job_cron_grace_skips_external_fire(self, tmp_cron_dir):
        pytest.importorskip("croniter")
        job = create_job(prompt="p", schedule="*/5 * * * *", grace_minutes=0)
        _park_in_past(job["id"], minutes=30)
        provider = RecordingProvider()
        assert fire_overdue_jobs(provider) == 0
        assert provider.fired == []
        assert get_job(job["id"])["next_run_at"] > _hermes_now().isoformat()

    def test_explicit_catch_up_overrides_expired_grace(self, tmp_cron_dir):
        pytest.importorskip("croniter")
        job = create_job(
            prompt="p", schedule="*/5 * * * *", grace_minutes=0, catch_up=True)
        _park_in_past(job["id"], minutes=30)
        provider = RecordingProvider()
        assert fire_overdue_jobs(provider) == 1
        assert provider.wait_fired()
        assert provider.fired == [job["id"]]

    def test_opted_in_stale_oneshot_fires_once(self, tmp_cron_dir):
        job = create_job(prompt="p", schedule="in 1h", catch_up=True)
        _park_in_past(job["id"], minutes=90)
        provider = RecordingProvider()
        assert fire_overdue_jobs(provider) == 1
        assert provider.wait_fired()
        assert provider.fired == [job["id"]]
        assert fire_overdue_jobs(provider) == 0

    def test_default_stale_oneshot_is_retired_and_booked(self, tmp_cron_dir):
        job = create_job(prompt="p", schedule="in 1h")
        _park_in_past(job["id"], minutes=90)
        provider = RecordingProvider()
        assert fire_overdue_jobs(provider) == 0
        assert provider.fired == []
        assert provider.changed == 1
        assert get_job(job["id"]) is None
        from cron.executions import list_executions
        assert any(row["status"] == "missed" for row in list_executions(job_id=job["id"]))

    @pytest.mark.parametrize("completed", [True, False])
    def test_stale_oneshot_keeps_completion_evidence_before_retirement(
        self, tmp_cron_dir, monkeypatch, completed,
    ):
        from cron.occurrences import LedgerUnavailable

        job = create_job(prompt="p", schedule="in 1h")
        _park_in_past(job["id"], minutes=90)

        def evidence(*_args, **_kwargs):
            if completed:
                return True
            raise LedgerUnavailable("fixture: ledger unavailable")

        monkeypatch.setattr("cron.occurrences._completion_evidence", evidence)
        provider = RecordingProvider()
        assert fire_overdue_jobs(provider) == 0
        assert get_job(job["id"]) is not None
        assert provider.fired == []
        from cron.executions import list_executions
        assert not any(row["status"] == "missed" for row in list_executions(job_id=job["id"]))

    def test_backstop_does_not_claim_slot_changed_after_scan(self, tmp_cron_dir):
        job = create_job(prompt="p", schedule="every 1h")
        _park_in_past(job["id"], minutes=30)

        class EditingProvider(RecordingProvider):
            def claim_fire(self, job_id, **kwargs):
                stored = load_jobs()
                stored[0]["next_run_at"] = (_hermes_now() + timedelta(hours=1)).isoformat()
                save_jobs(stored)
                return super().claim_fire(job_id, **kwargs)

        provider = EditingProvider()
        assert fire_overdue_jobs(provider) == 0
        assert provider.fired == []
        assert get_job(job["id"]).get("fire_claim") is None

    def test_failed_skip_save_does_not_book_miss_or_rearm(self, tmp_cron_dir, monkeypatch):
        job = create_job(prompt="p", schedule="every 1h", catch_up=False)
        _park_in_past(job["id"], minutes=90)
        original = get_job(job["id"])["next_run_at"]

        def fail_save(*_args, **_kwargs):
            raise OSError("fixture: store unavailable")

        monkeypatch.setattr("cron.jobs.save_jobs", fail_save)
        provider = RecordingProvider()
        assert fire_overdue_jobs(provider) == 0
        assert provider.changed == 0
        assert get_job(job["id"])["next_run_at"] == original
        from cron.executions import list_executions
        assert not any(row["status"] == "missed" for row in list_executions(job_id=job["id"]))

    def test_dispatch_is_nonblocking(self, tmp_cron_dir):
        """fire_claimed runs off-thread — a slow job must not stall the
        sweep (housekeeping loop) for the length of an agent run."""

        release = threading.Event()

        class BlockedProvider(RecordingProvider):
            def fire_claimed(self, claimed_job, **kw):
                assert release.wait(10)
                return super().fire_claimed(claimed_job, **kw)

        job = create_job(prompt="p", schedule="every 1h")
        _park_in_past(job["id"], minutes=30)
        provider = BlockedProvider()
        try:
            # Returns while fire_claimed is still blocked -> dispatched off-thread.
            assert fire_overdue_jobs(provider) == 1
            assert provider.fired == []
        finally:
            release.set()
        assert provider.wait_fired(timeout=10)
        assert provider.fired == [job["id"]]

    def test_estop_skips_sweep_and_next_sweep_after_resume_catches_up(
        self, tmp_cron_dir, tmp_path, monkeypatch
    ):
        """`hermes pause` must silence the backstop too — otherwise it force-fires every job
        that ESTOP held back. Nothing to unwind: the first sweep after `hermes resume`
        catches up through the ordinary claim_fire path."""
        from agent import estop

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        estop._logged_components.clear()
        job = create_job(prompt="p", schedule="every 1h")
        _park_in_past(job["id"], minutes=30)
        parked_at = get_job(job["id"])["next_run_at"]
        provider = RecordingProvider()

        estop.engage(reason="runaway fan-out")
        assert fire_overdue_jobs(provider) == 0
        assert provider.fired == []
        assert get_job(job["id"])["next_run_at"] == parked_at  # nothing claimed or re-armed

        estop.disengage()
        assert fire_overdue_jobs(provider) == 1
        assert provider.wait_fired()
        assert provider.fired == [job["id"]]
