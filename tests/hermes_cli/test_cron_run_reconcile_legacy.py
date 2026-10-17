"""A pre-upgrade cron session stands for one execution, not every script-only run near it."""

from hermes_cli.web_routers.cron import _reconcile_cron_runs

JOB = "job"


def _attempt(exec_id, claimed, finished, status="completed"):
    return {"id": exec_id, "status": status, "error": "", "claimed_at": claimed, "finished_at": finished}


def _legacy_session(started, ended):
    return {"id": f"cron_{JOB}_old", "source": "cron", "started_at": started, "ended_at": ended,
            "last_active": ended, "cron_execution_id": None}


def _doc(ts):
    return {"id": f"cron_output:{JOB}:{int(ts)}", "started_at": ts, "title": "Script-only run"}


def test_legacy_session_absorbs_one_script_run_and_the_neighbour_stays_visible():
    session = _legacy_session(1000.0, 1010.0)
    attempts = [_attempt("e1", 1000.0, 1005.0), _attempt("e2", 1100.0, 1105.0)]
    docs = [_doc(1004.0), _doc(1104.0)]

    rows = _reconcile_cron_runs([session], docs, attempts, JOB, limit=10)

    assert [r["started_at"] for r in rows] == [1104.0, 1000.0]  # the run the session does not stand for, then it


def test_failed_fire_without_a_doc_near_a_legacy_session_keeps_its_ledger_row():
    session = _legacy_session(1000.0, 1010.0)
    attempts = [_attempt("e1", 1000.0, 1005.0), _attempt("e2", 1100.0, 1105.0, status="failed")]

    rows = _reconcile_cron_runs([session], [], attempts, JOB, limit=10)

    assert [r["started_at"] for r in rows][-1] == 1000.0 and len(rows) == 2
