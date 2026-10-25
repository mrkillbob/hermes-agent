"""Unreadable completion evidence cannot dispatch or retire a scheduled one-shot.

Behavior adapted from ANG-Ventures/hermes-agent@da48a9e338e944c17647159596418d609666e8cf
(MIT, Nous Research). Uses the maintained 120-second one-shot grace and store API.
"""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import sqlite3

import pytest

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


@pytest.fixture
def missed(monkeypatch):
    events = []
    monkeypatch.setattr('cron.jobs.record_cron_missed', lambda job: events.append(job['id']))
    return events


@pytest.fixture
def store(tmp_path, monkeypatch, missed):
    from cron import executions, jobs

    monkeypatch.setattr(jobs, '_hermes_now', lambda: NOW)
    monkeypatch.setattr(executions, '_hermes_now', lambda: NOW)
    monkeypatch.setattr(executions, 'EXECUTIONS_FILE', tmp_path / 'executions.db')
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: {'cron': {}})
    with jobs.use_cron_store(tmp_path / 'cron'):
        yield jobs


def save_once(jobs, age):
    slot = (NOW - timedelta(seconds=age)).isoformat()
    jobs.save_jobs([{
        'id': 'once', 'name': 'once', 'prompt': 'reminder',
        'schedule': {'kind': 'once', 'run_at': slot}, 'next_run_at': slot,
        'enabled': True, 'state': 'scheduled', 'last_run_at': None,
        'repeat': {'times': 1, 'completed': 0}, 'deliver': 'local',
    }])
    return slot


@contextmanager
def unreadable(monkeypatch):
    from cron import executions

    @contextmanager
    def broken():
        raise sqlite3.OperationalError('unable to open database file')
        yield  # pragma: no cover

    with monkeypatch.context() as patch:
        patch.setattr(executions, '_transaction', broken)
        yield


@pytest.mark.parametrize('age', [30, 900])
def test_unreadable_due_scan_neither_dispatches_nor_retires(store, monkeypatch, age, missed):
    save_once(store, age)
    before = store.load_jobs()
    with unreadable(monkeypatch):
        assert store.get_due_jobs() == []
        assert store.get_due_jobs() == []
    assert store.load_jobs() == before
    assert missed == []
    assert not (store._current_cron_store().cron_dir / 'output' / 'once').exists()


def test_unreadable_fire_claim_leaves_scheduled_record_untouched(store, monkeypatch, missed):
    save_once(store, 30)
    before = store.load_jobs()
    with unreadable(monkeypatch):
        assert store.claim_job_for_fire('once') is False
    assert store.load_jobs() == before
    assert missed == []


def test_recovered_completed_ledger_blocks_duplicate_and_missed_notice(store, monkeypatch, missed):
    from cron import executions

    slot = save_once(store, 900)
    row = executions.create_execution('once', source='builtin', scheduled_instant=slot)
    executions.finish_execution(row['id'], success=True)
    before = store.load_jobs()
    with unreadable(monkeypatch):
        assert store.get_due_jobs() == []
    assert store.load_jobs() == before
    assert store.get_due_jobs() == []
    assert store.claim_job_for_fire('once') is False
    assert missed == []


@pytest.mark.parametrize('age', [30, 900])
def test_readable_empty_ledger_preserves_existing_grace_policy(store, age, missed):
    save_once(store, age)
    due = store.get_due_jobs()
    if age == 30:
        assert [job['id'] for job in due] == ['once']
        assert missed == []
    else:
        assert due == []
        assert store.load_jobs() == []
        assert missed == ['once']


def test_manual_one_shot_claim_keeps_existing_explicit_bypass(store, monkeypatch):
    save_once(store, 30)
    with unreadable(monkeypatch):
        assert store.claim_job_for_fire('once', manual=True) is True


def test_recurring_ledger_failure_keeps_existing_due_behavior(store, monkeypatch):
    save_once(store, 30)
    records = store.load_jobs()
    records[0]['schedule'] = {'kind': 'interval', 'minutes': 60}
    records[0]['repeat'] = {'times': None, 'completed': 0}
    store.save_jobs(records)
    with unreadable(monkeypatch):
        assert [job['id'] for job in store.get_due_jobs()] == ['once']
        assert store.claim_job_for_fire('once') is True


@pytest.mark.parametrize('claim', [False, True])
def test_legacy_naive_one_shot_also_waits_for_readable_ledger(store, monkeypatch, claim):
    save_once(store, 30 if claim else 900)
    records = store.load_jobs()
    slot = datetime.fromisoformat(records[0]['next_run_at']).replace(tzinfo=None).isoformat()
    records[0]['next_run_at'] = slot
    records[0]['schedule']['run_at'] = slot
    store.save_jobs(records)
    before = store.load_jobs()
    with unreadable(monkeypatch):
        if claim:
            assert store.claim_job_for_fire('once') is False
        else:
            assert store.get_due_jobs() == []
    assert store.load_jobs() == before


@pytest.mark.parametrize('kind', ['once', 'interval'])
def test_legacy_completion_api_preserves_cached_scheduler_fallback(store, monkeypatch, kind):
    from cron.occurrences import completed_occurrence

    slot = save_once(store, 30)
    with unreadable(monkeypatch):
        assert completed_occurrence({'id': 'once', 'schedule': {'kind': kind}}, slot) is False
