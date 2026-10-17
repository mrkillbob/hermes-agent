"""Read-only job limits distinguish Windows launch policy without changing it."""
from __future__ import annotations

import ctypes

import pytest


@pytest.mark.parametrize("in_job, membership_error, query_error", [
    (True, 0, 0), (False, 0, 0), (False, 5, 0), (True, 0, 5),
])
def test_job_query_preserves_unknown_results_and_only_safe_facts(in_job, membership_error, query_error):
    from hermes_platform.host import windows_job as job

    class Api:
        def GetCurrentProcess(self):
            return -1

        def IsProcessInJob(self, process, handle, result):
            assert process == -1 and handle is None
            ctypes.cast(result, ctypes.POINTER(ctypes.c_int32)).contents.value = in_job
            return not membership_error

        def QueryInformationJobObject(self, handle, info_class, result, size, returned):
            assert handle is None and info_class == 9 and returned is None
            assert size == ctypes.sizeof(job._ExtendedLimits)
            ctypes.cast(result, ctypes.POINTER(job._ExtendedLimits)).contents.basic.limit_flags = 0x2000
            return not query_error

    snapshot = job._query_current_job(Api(), lambda: membership_error or query_error)
    assert snapshot == {
        "scope": "immediate_job", "in_job": None if membership_error else in_job,
        "limit_flags": 0x2000 if in_job and not membership_error and not query_error else None,
        "membership_error": membership_error or None,
        "query_error": query_error if in_job and not membership_error and query_error else None,
    }


@pytest.mark.platforms("windows")
def test_native_current_job_query_is_readable_without_changing_policy():
    from hermes_platform.host.windows_job import current_process_job_limits

    snapshot = current_process_job_limits()
    assert snapshot is not None
    assert snapshot["scope"] == "immediate_job"
    assert snapshot["membership_error"] is None, snapshot
    assert isinstance(snapshot["in_job"], bool)
    if snapshot["in_job"]:
        assert snapshot["query_error"] is None, snapshot
        assert isinstance(snapshot["limit_flags"], int)
