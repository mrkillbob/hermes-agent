"""Regression test for #97905 / carrier PR #92489.

A multiplex ticker (desktop dashboard backend, multiplex gateway) resolves
``hermes_time`` under the process's own startup profile, then ticks OTHER
profiles' cron stores via ``set_hermes_home_override()`` + ``use_cron_store()``.
Before the profile-keyed timezone cache, the first profile's resolved zone
was process-global, so ``compute_next_run`` / ``create_job`` / ``mark_job_run``
persisted ``next_run_at`` into the ticked profile's jobs.json with the FOREIGN
process's UTC offset — e.g. ``0 14 * * *`` for an America/New_York profile
stored as ``14:00+00:00``, which becomes due at 10:00 ET and fires four hours
early (#97905; single-profile sibling report #88220).

The invariant pinned here: ``next_run_at`` written during a foreign-process
tick carries the JOB-OWNING profile's configured UTC offset at the due instant,
not the offset on the day the test happens to run.
"""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

import hermes_time


@pytest.fixture(autouse=True)
def _fresh_tz_cache(monkeypatch):
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    hermes_time.reset_cache()
    yield
    hermes_time.reset_cache()


@pytest.mark.parametrize(
    ("frozen_instant", "expected_due"),
    [
        pytest.param(
            "2026-03-08T04:00:00+00:00",
            "2026-03-08T14:00:00-04:00",
            id="spring-forward",
        ),
        pytest.param(
            "2026-10-31T23:00:00+00:00",
            "2026-11-01T14:00:00-05:00",
            id="fall-back",
        ),
        pytest.param(
            "2026-01-09T23:00:00+00:00",
            "2026-01-10T14:00:00-05:00",
            id="winter-control",
        ),
    ],
)
def test_foreign_process_tick_persists_owning_profile_offset(
    tmp_path, monkeypatch, frozen_instant, expected_due
):
    """next_run_at persisted during a multiplex tick uses the ticked
    profile's timezone, not the backend process's."""
    frozen_now = datetime.fromisoformat(frozen_instant)
    due = datetime.fromisoformat(expected_due)

    class FrozenClock:
        @staticmethod
        def now(tz=None):
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    # Freeze only time: keep real profile/config resolution and timezone caching,
    # or the foreign-process regression would be masked by a configured-zone stub.
    monkeypatch.setattr(hermes_time, "datetime", FrozenClock)

    backend_home = tmp_path / "backend"  # desktop backend's own profile: UTC
    profile_a = tmp_path / "north-caribbean"  # job-owning profile: Eastern
    backend_home.mkdir()
    profile_a.mkdir()
    (backend_home / "config.yaml").write_text(
        "timezone: UTC\n", encoding="utf-8"
    )
    (profile_a / "config.yaml").write_text(
        "timezone: America/New_York\n", encoding="utf-8"
    )

    monkeypatch.setenv("HERMES_HOME", str(backend_home))

    # Backend process resolves its own timezone first (process startup).
    backend_now = hermes_time.now()
    assert backend_now.utcoffset() == frozen_now.utcoffset()
    assert backend_now.astimezone(UTC) == frozen_now

    from hermes_constants import (
        reset_hermes_home_override,
        set_hermes_home_override,
    )
    from cron.jobs import create_job, load_jobs, use_cron_store

    # Exactly the scoping the multiplex ticker applies per profile
    # (cron/scheduler_provider.py::_tick_profiles).
    token = set_hermes_home_override(str(profile_a))
    try:
        with use_cron_store(profile_a):
            create_job(name="daily-2pm", prompt="x", schedule="0 14 * * *")
            job = load_jobs()[0]
    finally:
        reset_hermes_home_override(token)

    next_run = datetime.fromisoformat(job["next_run_at"])
    expected_offset = due.utcoffset()
    assert next_run.utcoffset() == expected_offset, (
        f"next_run_at {job['next_run_at']} persisted with foreign offset "
        f"{next_run.utcoffset()} instead of the owning profile's "
        f"{expected_offset} at the due instant (#97905)"
    )
    # And the wall clock honors the cron expression in the profile's zone.
    assert (next_run.hour, next_run.minute) == (14, 0)
    assert next_run.astimezone(UTC) == due.astimezone(UTC)
    assert next_run.astimezone(ZoneInfo("America/New_York")).hour == 14
