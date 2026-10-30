"""``cronjob_manage action=runs`` through real registry dispatch against an isolated ledger."""

import json

import pytest

from agent.llm_egress_cron import _runs_projection
from cron.executions import create_execution, finish_execution
import tools.cronjob_tools  # registers cronjob_manage
from tools.registry import registry


@pytest.fixture(autouse=True)
def _isolated_cron(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    monkeypatch.setattr("cron.executions.EXECUTIONS_FILE", tmp_path / "cron" / "executions.db")


def _create(name):
    out = json.loads(registry.dispatch(
        "cronjob_manage",
        {"action": "create", "name": name, "schedule": "1h", "deliver": "local", "prompt": "Reply exactly: ok"},
    ))
    assert out["success"] is True
    return out["job_id"]


def _runs(job):
    return json.loads(registry.dispatch("cronjob_manage", {"action": "runs", "job_id": job}))


def test_runs_returns_only_that_jobs_bounded_outcomes():
    first, other = _create("first job"), _create("other job")
    failed = create_execution(first, source="builtin")
    finish_execution(failed["id"], success=False, error="HTTP 429: fair-share rate limit")
    finish_execution(create_execution(first, source="builtin")["id"], success=True)
    finish_execution(create_execution(other, source="builtin")["id"], success=True)

    result = _runs(first)

    assert result["success"] is True and result["count"] == 2
    assert [run["status"] for run in result["runs"]] == ["completed", "failed"]
    assert all(set(run) <= {"status", "claimed_at", "finished_at"} for run in result["runs"])
    text = json.dumps(result)
    assert failed["id"] not in text and "429" not in text and other not in text
    assert _runs_projection(json.dumps(result)) is not None


def test_runs_is_resolved_by_name_and_caps_at_ten():
    job = _create("busy job")
    for _ in range(12):
        finish_execution(create_execution(job, source="builtin")["id"], success=True)
    result = _runs("busy job")
    assert result["count"] == 10 and len(result["runs"]) == 10


def test_runs_for_unknown_job_is_an_error_not_an_empty_history():
    assert _runs("no-such-job")["success"] is False
    out = json.loads(registry.dispatch("cronjob_manage", {"action": "runs"}))
    assert out["success"] is False
